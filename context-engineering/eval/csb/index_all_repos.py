"""Index every unique repo@branch across converted-tasks against ce-mcp-prod.

Parallel batches of 4 (Hobby tier safety). Logs each result to a JSONL file
so a partial-failure mid-run can resume.
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


def index_one(mcp_url: str, token: str, repo: str, branch: str) -> dict:
    t0 = time.time()
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ce_index_github_repo",
                   "arguments": {"repo": repo, "branch": branch,
                                 "data_classification": "public"}},
    }
    out = post(f"{mcp_url}/api/mcp", token, payload, timeout=320)
    elapsed = time.time() - t0
    record = {"repo": repo, "branch": branch, "elapsed_s": round(elapsed, 1)}
    if "error" in out:
        record["status"] = "error"
        record["error"] = out["error"].get("message", "")[:300]
        record["details"] = out["error"].get("data", {}).get("details", {})
    elif "result" in out:
        sc = out["result"].get("structuredContent", {})
        if "stats" in sc:
            record["status"] = "ok"
            record["corpus_id"] = sc.get("corpus_id")
            record["commit_sha"] = sc.get("commit_sha")
            record["file_count"] = sc["stats"].get("file_count")
            record["embedded_count"] = sc["stats"].get("embedded_count")
        elif sc.get("isError"):
            record["status"] = "tool_error"
            record["error"] = sc.get("error_message", "")
        else:
            record["status"] = "unknown"
            record["raw"] = sc
    else:
        record["status"] = "transport"
        record["raw"] = out
    return record


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
            tag = "OK " if rec.get("status") == "ok" else f"ERR({rec.get('status')})"
            extra = f"  files={rec.get('file_count', '-')} emb={rec.get('embedded_count', '-')}" if rec.get("status") == "ok" else f"  err={str(rec.get('error',''))[:120]!r}"
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
