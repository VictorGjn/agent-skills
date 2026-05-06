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
import os
import shlex
import subprocess
import sys
from pathlib import Path

CONFIGS = ["ce-keyword", "ce-codestral", "ce-codestral-mmr", "ce-shipping"]
NAMES = ["C1", "C2", "C3", "C4"]


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
    if not args.skip_preflight:
        cmd = [py, f"{csb}/preflight.py", "--tasks-dir", str(args.tasks_dir)]
        if args.check_branches:
            cmd.append("--check-branches")
        if not args.strict:
            cmd += ["--allow-unreachable", "--allow-missing-branches"]
        run(cmd)

    # ── Step 2: Index 44 repos ──
    index_jsonl = run_dir / f"index-{args.tag}.jsonl"
    if not (args.resume and index_jsonl.exists()):
        run([py, f"{csb}/index_all_repos.py",
             "--tasks-dir", str(args.tasks_dir),
             "--token-file", str(args.token_file),
             "--mcp-url", args.mcp_url,
             "--output", str(index_jsonl),
             "--parallel", str(args.parallel)])
    else:
        print(f"[skip] {index_jsonl} exists (--resume).")

    # ── Step 3: IR sweep × 4 configs ──
    ir_jsonls = []
    token = args.token_file.read_text(encoding="utf-8-sig").strip()
    for cfg in CONFIGS:
        out = run_dir / f"ir-{cfg}-{args.tag}.jsonl"
        ir_jsonls.append(out)
        if args.resume and out.exists():
            print(f"[skip] {out} exists (--resume).")
            continue
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
