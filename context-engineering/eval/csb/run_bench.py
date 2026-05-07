"""End-to-end bench launcher: preflight -> index -> IR sweep (4 configs) -> diff_runs.

Pipelines what was previously four manual commands:

  1. preflight.py                              # validity check
  2. index_all_repos.py --parallel 1           # corpora
  3. run_ir_bench.py × 4 configs               # IR scores
  4. diff_runs.py --both-views                 # verdict report

Each step writes to a per-run directory under eval/csb/runs/<tag>/. Skip
already-completed steps via --resume. The script exits at the first hard
failure; soft warnings (partial reach, missing branches) print but don't
stop unless --strict is set.

Usage:

    python eval/csb/run_bench.py \\
        --tag v1.1-2026-05-07 \\
        --tasks-dir eval/csb/converted-tasks \\
        --mcp-url https://ce-mcp-prod.vercel.app \\
        --token-file ~/.claude/handoffs/secrets/ce_mcp_bootstrap_token.txt
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

CONFIGS = ["ce-keyword", "ce-codestral", "ce-codestral-mmr", "ce-shipping"]
NAMES = ["C1", "C2", "C3", "C4"]


def ir_jsonl_complete(path: Path) -> bool:
    """True iff the file ends with run_ir_bench.py's `_summary` sentinel.

    `run_ir_bench.py` writes one task record per line, then a final
    `{"_summary": {...}}` row before closing the file. A run that crashed
    or was interrupted leaves a truncated file with task rows but no
    summary — diffing it scores against an incomplete task set and
    silently produces wrong aggregate metrics. We detect completeness
    by reading the last non-empty line and checking for the sentinel.
    """
    if not path.is_file():
        return False
    try:
        last = ""
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return False
            # Read backwards a chunk at a time until we find a non-empty line.
            chunk = 4096
            offset = 0
            buf = b""
            while offset < size:
                read = min(chunk, size - offset)
                offset += read
                f.seek(size - offset)
                buf = f.read(read) + buf
                if b"\n" in buf.rstrip(b"\n"):
                    break
            for raw in reversed(buf.splitlines()):
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    last = line
                    break
        if not last:
            return False
        obj = json.loads(last)
        return isinstance(obj, dict) and "_summary" in obj
    except Exception:
        return False


def run(cmd: list[str], *, env: dict | None = None, check: bool = True) -> int:
    """Echo + invoke; raise on non-zero unless check=False."""
    pretty = " ".join(shlex.quote(a) for a in cmd)
    print(f"\n$ {pretty}", flush=True)
    rc = subprocess.run(cmd, env=env or os.environ).returncode
    if check and rc != 0:
        print(f"\n[X] command failed (exit {rc}); aborting bench.", file=sys.stderr)
        sys.exit(rc)
    return rc


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tag", required=True, help="Run identifier (e.g. v1.1-2026-05-07)")
    p.add_argument("--tasks-dir", default="eval/csb/converted-tasks", type=Path)
    p.add_argument("--mcp-url", default="https://ce-mcp-prod.vercel.app")
    p.add_argument("--token-file", required=True, type=Path)
    p.add_argument("--runs-dir", default="eval/csb/runs", type=Path)
    p.add_argument("--docs-dir", default="docs", type=Path)
    p.add_argument("--python", default=sys.executable,
                   help="Python interpreter to invoke step-scripts with.")
    p.add_argument("--parallel", type=int, default=1,
                   help="Index parallelism. Default 1 (rate-limit safe).")
    p.add_argument("--strict", action="store_true",
                   help="Fail bench on preflight warnings (partial reach, missing branches).")
    p.add_argument("--check-branches", action="store_true",
                   help="Validate spec.json branches against GitHub during preflight.")
    p.add_argument("--resume", action="store_true",
                   help="Skip steps whose output files already exist.")
    p.add_argument("--skip-preflight", action="store_true",
                   help="Bypass preflight entirely (CI / scripted reruns).")
    args = p.parse_args()

    run_dir = args.runs_dir / args.tag
    run_dir.mkdir(parents=True, exist_ok=True)
    py = args.python
    csb = "eval/csb"

    # ── Step 1: Preflight ──
    # Defaults match preflight standalone: fully-unreachable, missing branches,
    # and stale corpus_ids are HARD blocks. Don't silently neuter them via
    # --allow-* on the way in. --strict adds partial-reach as a block AND
    # auto-enables --check-branches so the missing-branch gate actually fires
    # (it's documented as part of strict mode but only ran when the user also
    # remembered to pass --check-branches explicitly — silent gap).
    if not args.skip_preflight:
        cmd = [py, f"{csb}/preflight.py", "--tasks-dir", str(args.tasks_dir)]
        if args.check_branches or args.strict:
            cmd.append("--check-branches")
        if args.strict:
            cmd.append("--fail-on-partial-reach")
        run(cmd)

    # ── Step 2: Index 44 repos ──
    # On --resume, we always re-invoke the indexer with --skip-existing rather
    # than short-circuiting on file existence: index_all_repos.py writes
    # incrementally, so a failed mid-run leaves a partial JSONL (some repos
    # OK, some errored, some never tried). --skip-existing reads OK rows out
    # and re-tries the rest, leaving the bench correct against the full set
    # instead of scoring against missing corpora.
    index_jsonl = run_dir / f"index-{args.tag}.jsonl"
    cmd = [py, f"{csb}/index_all_repos.py",
           "--tasks-dir", str(args.tasks_dir),
           "--token-file", str(args.token_file),
           "--mcp-url", args.mcp_url,
           "--output", str(index_jsonl),
           "--parallel", str(args.parallel)]
    if args.resume and index_jsonl.exists():
        cmd.append("--skip-existing")
    run(cmd)

    # ── Step 3: IR sweep × 4 configs ──
    # On --resume, only skip a config if its JSONL is COMPLETE (last line is
    # the run_ir_bench `_summary` sentinel). Truncated files from a crash get
    # re-run rather than diffed-as-if-complete.
    ir_jsonls = []
    token = args.token_file.read_text(encoding="utf-8-sig").strip()
    for cfg in CONFIGS:
        out = run_dir / f"ir-{cfg}-{args.tag}.jsonl"
        ir_jsonls.append(out)
        if args.resume and ir_jsonl_complete(out):
            print(f"[skip] {out} complete (--resume).")
            continue
        if args.resume and out.exists():
            print(f"[redo] {out} exists but truncated (no _summary); re-running config.")
        run([py, f"{csb}/run_ir_bench.py",
             "--tasks-dir", str(args.tasks_dir),
             "--mcp-url", args.mcp_url,
             "--token", token,
             "--config", cfg,
             "--top-k", "5",
             "--output", str(out)])

    # ── Step 4: Diff + verdict ──
    report = args.docs_dir / f"benchmarks-{args.tag}.md"
    run([py, f"{csb}/diff_runs.py",
         "--runs", *[str(p) for p in ir_jsonls],
         "--names", *NAMES,
         "--both-views",
         "--output", str(report)])

    print(f"\n[+] Bench complete: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
