"""IR-only bench driver — Phase 2 of plan/codescalebench-bench-plan.md.

For each CSB task, calls ce_find_relevant_files (or ce_pack_context, depending
on `--metric`) and scores the returned paths against `ground_truth.json`.

Phase 2 of the bench plan: NO Haiku inference. Pure embed + pack + diff
against ground truth. Costs are codestral embeds amortised over the indexed
corpora (~$3 total).

Inputs (one CSB-style task dir):

    tasks/<task_id>/
        spec.json         — { "description": "...", "repo": "owner/name",
                              "branch": "main", "corpus_id": "..." }
        ground_truth.json — ["path/to/file1", "path/to/file2", ...]

Output: JSONL, one record per task, plus a summary line at the end.

Usage:

    python run_ir_bench.py \\
        --tasks-dir ./csb/tasks/single-repo-sdlc \\
        --mcp-url https://ce-mcp.vercel.app \\
        --token $CE_MCP_TOKEN \\
        --config ce-keyword \\
        --top-k 5 \\
        --output runs/ir-keyword-2026-05-05.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Ensure ir_metrics is importable when run as a script
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))

import ir_metrics  # noqa: E402


VALID_CONFIGS = {
    "ce-keyword": {"mode": "keyword"},
    "ce-codestral": {"mode": "semantic"},
    "ce-codestral-mmr": {"mode": "semantic"},   # MMR is server-side; same wire shape
    "ce-shipping": {"mode": "auto"},            # let server pick semantic+graph
}


def _post(url: str, token: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", errors="replace")}
    except urllib.error.URLError as e:
        # Connection reset, DNS failure, refused, unreachable — keep the run alive.
        return {"_transport_error": f"URLError: {e.reason}"}
    except (TimeoutError, OSError) as e:
        # socket.timeout, socket.error, etc.
        return {"_transport_error": f"{type(e).__name__}: {e}"}
    except Exception as e:  # noqa: BLE001 — defensive last-resort
        return {"_transport_error": f"{type(e).__name__}: {e}"}


def _call_find(mcp_url: str, token: str, query: str, corpus_id: str,
                mode: str, top_k: int) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "ce_find_relevant_files",
            "arguments": {
                "query": query, "corpus_id": corpus_id,
                "mode": mode, "top_k": top_k,
            },
        },
    }
    return _post(f"{mcp_url}/api/mcp", token, payload, timeout=60)


def _call_pack(mcp_url: str, token: str, query: str, corpus_id: str,
                mode: str, budget: int) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "ce_pack_context",
            "arguments": {
                "query": query, "corpus_id": corpus_id,
                "mode": mode, "budget": budget,
                "response_format": "structured",
            },
        },
    }
    return _post(f"{mcp_url}/api/mcp", token, payload, timeout=120)


def _extract_paths_from_response(resp: dict) -> tuple[list[str], dict | None]:
    """Returns (ranked_paths, error_or_None). Handles HTTP, transport, JSON-RPC, and tool errors.

    Per server-prod transport, the wire shape on tool error is:
        {"result": {"isError": True,
                    "content": [...],
                    "structuredContent": {"code": ..., "details": ...}}}
    The `isError` lives at `result`-level, NOT `result.structuredContent` — Codex P1 fix.
    """
    if "_transport_error" in resp:
        return [], {"layer": "transport", "message": resp["_transport_error"]}
    if "_http_error" in resp:
        return [], {"layer": "http", "code": resp["_http_error"], "body": resp.get("_body", "")[:300]}
    if "error" in resp:
        return [], {"layer": "jsonrpc", "code": resp["error"].get("code"), "message": resp["error"].get("message")}
    result = resp.get("result", {}) or {}
    if result.get("isError") is True:
        s = result.get("structuredContent", {}) or {}
        return [], {"layer": "tool", "code": s.get("code"), "message": s.get("message"),
                    "details": s.get("details")}
    s = result.get("structuredContent", {}) or {}
    files = s.get("files", []) or []
    return [f.get("path", "") for f in files], None


def run_one(task_dir: Path, mcp_url: str, token: str,
            config: str, top_k: int, budget: int,
            metric_tool: str = "find") -> dict:
    spec = json.loads((task_dir / "spec.json").read_text(encoding="utf-8"))
    ground = json.loads((task_dir / "ground_truth.json").read_text(encoding="utf-8"))

    cfg = VALID_CONFIGS[config]
    mode = cfg["mode"]
    query = spec["description"]
    corpus_id = spec.get("corpus_id") or _derive_corpus_id(spec)

    start = time.time()
    if metric_tool == "find":
        resp = _call_find(mcp_url, token, query, corpus_id, mode, top_k)
    else:
        resp = _call_pack(mcp_url, token, query, corpus_id, mode, budget)
    elapsed = time.time() - start

    paths, err = _extract_paths_from_response(resp)
    metrics = ir_metrics.score(paths, ground, k=top_k)

    return {
        "task_id": task_dir.name,
        "config": config,
        "mode": mode,
        "metric_tool": metric_tool,
        "query": query[:200] + ("…" if len(query) > 200 else ""),
        "corpus_id": corpus_id,
        "top_k": top_k,
        "budget": budget if metric_tool == "pack" else None,
        "took_s": round(elapsed, 3),
        "retrieved": paths,
        "ground_truth": ground,
        "metrics": metrics,
        "error": err,
    }


def _derive_corpus_id(spec: dict) -> str:
    """gh-{owner}-{name}-{branch} slugified — matches CE's server-side derivation."""
    import re
    repo = spec.get("repo", "")
    branch = spec.get("branch") or "main"
    if "/" not in repo:
        return repo or "unknown"
    owner, name = repo.split("/", 1)
    raw = f"gh-{owner}-{name}-{branch}".lower()
    return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")


def main() -> int:
    p = argparse.ArgumentParser(description="IR-only CE bench driver (Phase 2 of bench plan).")
    p.add_argument("--tasks-dir", required=True, type=Path,
                   help="Dir containing one subdir per task with spec.json + ground_truth.json")
    p.add_argument("--mcp-url", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--config", required=True, choices=sorted(VALID_CONFIGS.keys()))
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--budget", type=int, default=100_000,
                   help="Pack budget when --metric-tool=pack (ignored for find)")
    p.add_argument("--metric-tool", choices=["find", "pack"], default="find",
                   help="`find` = ce_find_relevant_files (cheaper); `pack` = ce_pack_context")
    p.add_argument("--output", required=True, type=Path,
                   help="JSONL output path (one record per task + summary)")
    p.add_argument("--limit", type=int, default=None, help="Run only the first N tasks")
    args = p.parse_args()

    tasks = sorted([d for d in args.tasks_dir.iterdir() if d.is_dir()
                     and (d / "spec.json").exists()
                     and (d / "ground_truth.json").exists()])
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print(f"no tasks found under {args.tasks_dir}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    with open(args.output, "w", encoding="utf-8") as f:
        for i, task_dir in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task_dir.name}…", file=sys.stderr)
            rec = run_one(
                task_dir, args.mcp_url, args.token,
                args.config, args.top_k, args.budget, args.metric_tool,
            )
            records.append(rec)
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            if rec.get("error"):
                print(f"  ! error: {rec['error']}", file=sys.stderr)

        # Summary
        agg = ir_metrics.aggregate([r["metrics"] for r in records])
        agg["config"] = args.config
        agg["metric_tool"] = args.metric_tool
        agg["top_k"] = args.top_k
        agg["error_count"] = sum(1 for r in records if r.get("error"))
        f.write(json.dumps({"_summary": agg}, separators=(",", ":")) + "\n")

    print(f"\nWrote {len(records)} records to {args.output}", file=sys.stderr)
    print(f"Summary: {agg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
