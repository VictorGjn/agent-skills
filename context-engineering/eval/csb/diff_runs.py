"""Phase 8 — diff bench runs + emit per-hypothesis verdicts.

Reads two or more JSONL run files produced by `run_ir_bench.py` and emits:
1. A side-by-side per-task delta table (markdown)
2. Aggregate metric deltas with bootstrap confidence intervals (lightweight)
3. Verdict per hypothesis from `plan/codescalebench-bench-plan.md`

Usage:

    python diff_runs.py \\
        --runs runs/ir-keyword.jsonl runs/ir-codestral.jsonl \\
        --names C1 C2 \\
        --output docs/bench-c1-vs-c2.md

The first run is the baseline; deltas are reported as `other - baseline`.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Iterable


def load_run(path: Path) -> tuple[list[dict], dict]:
    """Returns (per_task_records, summary_or_empty)."""
    records: list[dict] = []
    summary: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_summary" in obj:
                summary = obj["_summary"]
            else:
                records.append(obj)
    return records, summary


def pair_by_task(runs: list[list[dict]]) -> dict[str, list[dict | None]]:
    """Index records by task_id across all runs. Missing tasks → None."""
    all_ids: set[str] = set()
    by_run: list[dict[str, dict]] = []
    for r in runs:
        idx = {rec["task_id"]: rec for rec in r if "task_id" in rec}
        by_run.append(idx)
        all_ids |= set(idx.keys())
    return {tid: [idx.get(tid) for idx in by_run] for tid in sorted(all_ids)}


def metric_means(records: list[dict], metric: str = "file_recall") -> float:
    vals = [r["metrics"][metric] for r in records
             if r.get("metrics") and r["metrics"].get("n_truth", 0) > 0]
    return statistics.fmean(vals) if vals else 0.0


def jaccard_top_k(a: list[str], b: list[str], k: int = 5) -> float:
    """Top-K Jaccard for the H2-IR test (MMR composition test)."""
    s, t = set(a[:k]), set(b[:k])
    if not s and not t:
        return 1.0
    return len(s & t) / max(len(s | t), 1)


def bootstrap_ci(values: list[float], n_boot: int = 1000, alpha: float = 0.05) -> tuple[float, float]:
    """Lightweight percentile bootstrap CI on the mean. Stdlib-only (no numpy)."""
    import random
    if not values:
        return (0.0, 0.0)
    rng = random.Random(0xC0DE)
    means = []
    n = len(values)
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot)]
    return (lo, hi)


def render_per_task_table(paired: dict, names: list[str], top_n: int | None = None) -> str:
    """Per-task file_recall table. Cap to top-N by |Δ last-vs-baseline|.

    Codex P2 fix: aggregate Δ in render_aggregate_table is `runs[-1] - runs[0]`,
    so per-task Δ must match — otherwise 3+-run reports show inconsistent
    rankings. We compute the delta against the LAST run, not the second one.
    """
    rows = []
    for tid, recs in paired.items():
        recalls = [r["metrics"]["file_recall"] if r else None for r in recs]
        if len(recalls) >= 2 and recalls[0] is not None and recalls[-1] is not None:
            delta = recalls[-1] - recalls[0]
        else:
            delta = 0.0
        rows.append((tid, recalls, delta))
    rows.sort(key=lambda x: -abs(x[2]))
    if top_n:
        rows = rows[:top_n]

    out = ["| task_id | " + " | ".join(names) + " | Δ |", "|---|" + ("---|" * (len(names) + 1))]
    for tid, recalls, delta in rows:
        cells = [f"{r:.3f}" if r is not None else "—" for r in recalls]
        out.append(f"| {tid} | " + " | ".join(cells) + f" | {delta:+.3f} |")
    return "\n".join(out)


def render_aggregate_table(runs: list[list[dict]], names: list[str]) -> str:
    """Aggregate file_recall + P@5 + F1@5 means across runs, with bootstrap CIs."""
    metrics = ["file_recall", "precision_at_k", "recall_at_k", "f1_at_k"]
    out = ["| metric | " + " | ".join(names) + " | Δ (vs " + names[0] + ") |",
           "|---|" + ("---|" * (len(names) + 1))]
    base_means = {}
    for m in metrics:
        cells = []
        for i, run in enumerate(runs):
            vals = [r["metrics"][m] for r in run
                     if r.get("metrics") and r["metrics"].get("n_truth", 0) > 0]
            mean = statistics.fmean(vals) if vals else 0.0
            ci = bootstrap_ci(vals)
            cells.append(f"{mean:.3f} ({ci[0]:.3f}–{ci[1]:.3f})")
            if i == 0:
                base_means[m] = mean
        # Delta vs baseline (rightmost only — single most useful summary).
        # Codex P2 fix: guard against empty filtered list — fmean([]) raises.
        last_vals = [r["metrics"][m] for r in runs[-1]
                      if r.get("metrics") and r["metrics"].get("n_truth", 0) > 0]
        last_mean = statistics.fmean(last_vals) if last_vals else 0.0
        delta = last_mean - base_means[m]
        cells.append(f"{delta:+.3f}")
        out.append(f"| {m} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def evaluate_hypotheses(runs: list[list[dict]], names: list[str]) -> str:
    """Spit out per-hypothesis verdict from plan/codescalebench-bench-plan.md.

    H1: codestral > keyword on file recall. Pass: ≥+10% absolute (≥+0.10).
    H2-IR: MMR meaningfully changes top-K composition.
       Pass: top-K Jaccard < 0.7 on ≥30% of tasks (between C2 and C3).
    H4 / H7: need Haiku reward signal — IR-only path can't decide them.

    Configs are inferred from `names`: we try to identify {C1, C2, C3, C4} by
    convention (run files named accordingly).
    """
    by_name = {n: r for n, r in zip(names, runs)}

    out = ["## Hypothesis verdicts\n"]

    # H1: ce-codestral (C2) vs ce-keyword (C1) → Δ file_recall ≥ +0.10
    if "C1" in by_name and "C2" in by_name:
        c1, c2 = by_name["C1"], by_name["C2"]
        c1_mean = metric_means(c1)
        c2_mean = metric_means(c2)
        delta = c2_mean - c1_mean
        verdict = "PASS ✓" if delta >= 0.10 else f"FAIL ✗ (Δ={delta:+.3f}, gate ≥+0.10)"
        out.append(f"### H1 — codestral > keyword\nfile_recall: C1={c1_mean:.3f}, C2={c2_mean:.3f}, Δ={delta:+.3f} → **{verdict}**\n")

    # H2-IR: top-K Jaccard < 0.7 between C2 and C3 on ≥30% of tasks
    if "C2" in by_name and "C3" in by_name:
        paired = pair_by_task([by_name["C2"], by_name["C3"]])
        rolled = 0
        total = 0
        for _tid, recs in paired.items():
            if recs[0] is None or recs[1] is None:
                continue
            j = jaccard_top_k(recs[0].get("retrieved", []), recs[1].get("retrieved", []), k=5)
            if j < 0.7:
                rolled += 1
            total += 1
        frac = rolled / total if total else 0.0
        verdict = "PASS ✓" if frac >= 0.30 else f"FAIL ✗ (frac={frac:.2%}, gate ≥30%)"
        out.append(f"### H2-IR — MMR changes top-K composition\nJaccard<0.7 on {rolled}/{total} = {frac:.1%} → **{verdict}**\n")

    out.append("### H4 / H7\nReward + budget-sweep gates require Haiku (Phase 1 / Phase 3 of bench plan). IR-only path can't decide them.\n")

    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description="Diff bench runs + per-hypothesis verdict.")
    p.add_argument("--runs", nargs="+", required=True, type=Path,
                   help="JSONL files from run_ir_bench.py, in baseline-first order")
    p.add_argument("--names", nargs="+", required=True,
                   help="Short labels for each run (e.g. C1 C2 C3)")
    p.add_argument("--output", type=Path, default=Path("bench-report.md"))
    p.add_argument("--top-n", type=int, default=20,
                   help="Top-N tasks by abs delta to include in per-task table")
    args = p.parse_args()

    if len(args.runs) != len(args.names):
        p.error("--runs and --names must have equal length")

    runs: list[list[dict]] = []
    summaries: list[dict] = []
    for path in args.runs:
        recs, summary = load_run(path)
        runs.append(recs)
        summaries.append(summary)

    paired = pair_by_task(runs)

    md = ["# Bench diff", "", "## Aggregate metrics", ""]
    md.append(render_aggregate_table(runs, args.names))
    md.append("")
    md.append(f"## Per-task deltas (top {args.top_n} by |Δ file_recall|)")
    md.append("")
    md.append(render_per_task_table(paired, args.names, top_n=args.top_n))
    md.append("")
    md.append(evaluate_hypotheses(runs, args.names))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
