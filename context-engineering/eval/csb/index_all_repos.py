"""Index every unique repo@branch across converted-tasks against ce-mcp-prod.

Two-stage strategy per repo:
  1. **Sync** (`async:false`) — fast path; returns embeddings (when
     MISTRAL_API_KEY is set on server) and a populated stats block.
  2. **Async fallback** when sync hits 504 / FUNCTION_INVOCATION_TIMEOUT /
     BUDGET_EXCEEDED — enqueue `async:true`, poll `ce_get_job_status` until
     `complete|failed|timeout`. Async path is **keyword-only** in v1.1
     (server-side) — corpora produced via fallback have `embedded_count=0`
     and the H1 semantic-vs-keyword analysis must filter them out.

Logs each result to a JSONL file so a partial-failure mid-run can resume.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# ─── Tunables (sync wall, async poll budget) ──────────────────────────────────

# Server's SYNC_TIMEOUT_S = 280 + Vercel maxDuration = 300. Add ~20s transit
# headroom on the client so we observe the structured BUDGET_EXCEEDED reply
# rather than tripping urllib's timeout first when the server is at the wire.
SYNC_REQUEST_TIMEOUT_S = 320

# Async polling: cron worker advances ~50 files / tick at 1-min cadence; 5K-file
# repos can take 30+ ticks. Set a generous per-repo wait but bounded so a
# stuck job can't pin the bench overnight.
ASYNC_POLL_BUDGET_S = 30 * 60  # 30 minutes
ASYNC_POLL_INTERVAL_S = 30
ASYNC_ENQUEUE_TIMEOUT_S = 60
ASYNC_STATUS_TIMEOUT_S = 30

# Heuristic: detect server-side timeout from a 504 transport error or the
# explicit BUDGET_EXCEEDED tool error, both of which mean "retry async."
_TIMEOUT_BODY_HINTS = ("FUNCTION_INVOCATION_TIMEOUT", "504", "Gateway Time-out")


def post(url: str, token: str, payload: dict, timeout: int = 320) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", errors="replace")}
    except Exception as e:
        return {"_transport_error": f"{type(e).__name__}: {e}"}


def _parse_tool_response(out: dict) -> tuple[str, dict]:
    """Return (kind, payload) where kind is one of:
       - 'ok'         — structuredContent has stats
       - 'tool_error' — MCP tool returned isError=True
       - 'jsonrpc_error' — top-level JSON-RPC error
       - 'transport'  — _http_error / _transport_error / unknown shape
       - 'unknown'    — result present but doesn't match any expected shape
    """
    if "error" in out:
        return "jsonrpc_error", out["error"]
    if "result" in out:
        result = out["result"] or {}
        sc = result.get("structuredContent") or {}
        if result.get("isError") is True:
            return "tool_error", sc
        if "stats" in sc or "job_id" in sc or "status" in sc:
            return "ok", sc
        return "unknown", sc
    return "transport", out


def _is_server_timeout(kind: str, payload: dict) -> bool:
    """Detect the 'sync exceeded the function budget — retry async' signal."""
    if kind == "tool_error" and payload.get("code") == "BUDGET_EXCEEDED":
        return True
    if kind == "transport":
        if payload.get("_http_error") == 504:
            return True
        body = str(payload.get("_body", ""))
        if any(h in body for h in _TIMEOUT_BODY_HINTS):
            return True
        # urllib socket timeout when the function holds the connection
        # past SYNC_REQUEST_TIMEOUT_S without sending bytes.
        if "TimeoutError" in str(payload.get("_transport_error", "")):
            return True
    return False


def _record_from_sync_ok(sc: dict, repo: str, branch: str, t0: float) -> dict:
    return {
        "repo": repo, "branch": branch,
        "elapsed_s": round(time.time() - t0, 1),
        "status": "ok",
        "mode": "sync",
        "corpus_id": sc.get("corpus_id"),
        "commit_sha": sc.get("commit_sha"),
        "file_count": (sc.get("stats") or {}).get("file_count"),
        "embedded_count": (sc.get("stats") or {}).get("embedded_count"),
    }


def _poll_async(mcp_url: str, token: str, job_id: str,
                repo: str, branch: str, t0: float) -> dict:
    """Block until the job reaches a terminal state OR the poll budget expires.

    Returns the final result dict (keyword-only on the v1.1 async path)."""
    deadline = time.time() + ASYNC_POLL_BUDGET_S
    last_progress: dict[str, int] = {}
    while time.time() < deadline:
        time.sleep(ASYNC_POLL_INTERVAL_S)
        poll = post(f"{mcp_url}/api/mcp", token, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "ce_get_job_status",
                       "arguments": {"job_id": job_id}},
        }, timeout=ASYNC_STATUS_TIMEOUT_S)
        kind, sc = _parse_tool_response(poll)
        if kind == "ok":
            status = sc.get("status")
            last_progress = sc.get("progress") or last_progress
            if status == "complete":
                return {
                    "repo": repo, "branch": branch,
                    "elapsed_s": round(time.time() - t0, 1),
                    "status": "ok",
                    "mode": "async",
                    "corpus_id": sc.get("corpus_id"),
                    "commit_sha": sc.get("result_commit_sha"),
                    "file_count": last_progress.get("files_indexed"),
                    "embedded_count": last_progress.get("embedded_count", 0),
                    "async_keyword_only": True,
                }
            if status in ("failed", "timeout"):
                err = sc.get("error") or {}
                return {
                    "repo": repo, "branch": branch,
                    "elapsed_s": round(time.time() - t0, 1),
                    "status": f"async_{status}",
                    "mode": "async",
                    "job_id": job_id,
                    "error_code": err.get("code"),
                    "error": (err.get("message") or "")[:300],
                    "progress": last_progress,
                }
            # queued / running — keep polling
            continue
        if kind in ("tool_error", "jsonrpc_error"):
            # ce_get_job_status returned a STRUCTURED error (e.g.
            # JOB_NOT_FOUND after KV TTL churn, or INVALID_ARGUMENT). The
            # job won't recover by waiting — fail fast and surface the
            # real reason instead of stalling the whole bench for 30 min
            # per affected repo. Codex P1 round-3 on PR #60.
            #
            # `sc` shape differs by kind (Codex P2 round-4): _parse_tool_response
            # passes the structuredContent body for tool_error and the top-level
            # JSON-RPC `error` object ({code, message, data}) for jsonrpc_error.
            # Both expose code+message at the top level; tool_error nests details
            # under "details", JSON-RPC nests them under "data.details".
            if kind == "tool_error":
                err_details = sc.get("details") or {}
            else:  # jsonrpc_error
                data = sc.get("data") if isinstance(sc, dict) else None
                err_details = (data.get("details") or {}) if isinstance(data, dict) else {}
            return {
                "repo": repo, "branch": branch,
                "elapsed_s": round(time.time() - t0, 1),
                "status": f"async_{kind}",
                "mode": "async",
                "job_id": job_id,
                "error_code": sc.get("code"),
                "error": (sc.get("message") or "")[:300],
                "details": err_details,
                "progress": last_progress,
            }
        # transport / unknown shape — true network blip; retry until deadline
    return {
        "repo": repo, "branch": branch,
        "elapsed_s": round(time.time() - t0, 1),
        "status": "async_polling_timeout",
        "mode": "async",
        "job_id": job_id,
        "progress": last_progress,
    }


def index_one(mcp_url: str, token: str, repo: str, branch: str) -> dict:
    t0 = time.time()
    sync_payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ce_index_github_repo",
                   "arguments": {"repo": repo, "branch": branch,
                                 "data_classification": "public"}},
    }
    out = post(f"{mcp_url}/api/mcp", token, sync_payload, timeout=SYNC_REQUEST_TIMEOUT_S)
    kind, payload = _parse_tool_response(out)

    if kind == "ok":
        return _record_from_sync_ok(payload, repo, branch, t0)

    if not _is_server_timeout(kind, payload):
        # Non-timeout error — surface it without retrying. Async won't help
        # for SOURCE_NOT_FOUND / SOURCE_FORBIDDEN / INVALID_ARGUMENT etc.
        elapsed = round(time.time() - t0, 1)
        if kind == "tool_error":
            return {"repo": repo, "branch": branch, "elapsed_s": elapsed,
                    "status": "tool_error", "mode": "sync",
                    "error_code": payload.get("code"),
                    "error": (payload.get("message") or "")[:300],
                    "details": payload.get("details", {})}
        if kind == "jsonrpc_error":
            return {"repo": repo, "branch": branch, "elapsed_s": elapsed,
                    "status": "error", "mode": "sync",
                    "error": (payload.get("message") or "")[:300],
                    "details": (payload.get("data") or {}).get("details", {})}
        return {"repo": repo, "branch": branch, "elapsed_s": elapsed,
                "status": kind, "mode": "sync", "raw": payload}

    # Sync timeout → enqueue async + poll.
    enqueue = post(f"{mcp_url}/api/mcp", token, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ce_index_github_repo",
                   "arguments": {"repo": repo, "branch": branch,
                                 "data_classification": "public",
                                 "async": True}},
    }, timeout=ASYNC_ENQUEUE_TIMEOUT_S)
    kind2, payload2 = _parse_tool_response(enqueue)
    if kind2 != "ok" or not payload2.get("job_id"):
        elapsed = round(time.time() - t0, 1)
        return {"repo": repo, "branch": branch, "elapsed_s": elapsed,
                "status": "async_enqueue_failed", "mode": "async",
                "sync_timeout": True,
                "enqueue_kind": kind2,
                "enqueue_payload": payload2}

    return _poll_async(mcp_url, token, payload2["job_id"], repo, branch, t0)


def collect_unique_repos(tasks_dir: Path) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    for d in sorted(tasks_dir.iterdir()):
        spec = d / "spec.json"
        if not spec.is_file():
            continue
        s = json.loads(spec.read_text(encoding="utf-8"))
        repo = s.get("repo")
        branch = s.get("branch", "main")
        if repo:
            seen.add((repo, branch))
    return sorted(seen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", required=True, type=Path)
    ap.add_argument("--mcp-url", default="https://ce-mcp-prod.vercel.app")
    ap.add_argument("--token-file", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip repos already in --output (for resumes).")
    args = ap.parse_args()

    token = args.token_file.read_text(encoding="utf-8-sig").strip()
    repos = collect_unique_repos(args.tasks_dir)
    print(f"[+] {len(repos)} unique repo@branch combos", flush=True)

    already: set[tuple[str, str]] = set()
    if args.skip_existing and args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if r.get("status") == "ok":
                    already.add((r["repo"], r["branch"]))
            except Exception:
                pass
        print(f"[+] Skipping {len(already)} already-OK repos", flush=True)

    todo = [(r, b) for r, b in repos if (r, b) not in already]
    print(f"[+] Indexing {len(todo)} repos in batches of {args.parallel}", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.skip_existing else "w"
    fh = args.output.open(mode, encoding="utf-8")

    ok = 0
    err = 0
    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as ex:
        futures = {ex.submit(index_one, args.mcp_url, token, r, b): (r, b)
                   for r, b in todo}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            r, b = futures[fut]
            try:
                rec = fut.result()
            except Exception as e:
                rec = {"repo": r, "branch": b, "status": "exception",
                       "error": f"{type(e).__name__}: {e}"}
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            mode = rec.get("mode", "?")
            if rec.get("status") == "ok":
                tag = "OK " + ("[async,kw]" if rec.get("async_keyword_only") else f"[{mode}]")
                extra = f"  files={rec.get('file_count', '-')} emb={rec.get('embedded_count', '-')}"
            else:
                tag = f"ERR({rec.get('status')}/{mode})"
                extra = f"  err={str(rec.get('error',''))[:120]!r}"
            print(f"[{i:3d}/{len(todo)}] {tag}  {r}@{b}  {rec.get('elapsed_s','?')}s{extra}", flush=True)
            if rec.get("status") == "ok":
                ok += 1
            else:
                err += 1
    fh.close()
    elapsed = time.time() - t_start
    print(f"[+] Done. ok={ok} err={err} elapsed={elapsed:.0f}s", flush=True)
    sys.exit(0 if err == 0 else 1)


if __name__ == "__main__":
    main()
