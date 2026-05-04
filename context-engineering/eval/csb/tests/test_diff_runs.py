"""Tests for the bench diff + verdict tool."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
CSB = HERE.parent.parent
sys.path.insert(0, str(CSB))


def _make_run_file(path: Path, records: list[dict], summary: dict | None = None) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        if summary:
            f.write(json.dumps({"_summary": summary}) + "\n")


def _rec(task_id: str, retrieved: list[str], truth: list[str], file_recall: float,
          p_at_k: float = 0.0, r_at_k: float = 0.0, f1_at_k: float = 0.0) -> dict:
    return {
        "task_id": task_id,
        "retrieved": retrieved,
        "ground_truth": truth,
        "metrics": {
            "file_recall": file_recall,
            "precision_at_k": p_at_k,
            "recall_at_k": r_at_k,
            "f1_at_k": f1_at_k,
            "k": 5,
            "n_retrieved": len(retrieved),
            "n_truth": len(truth),
        },
    }


# ── load_run ──

def test_load_run_handles_summary_line(tmp_path):
    import diff_runs
    p = tmp_path / "run.jsonl"
    _make_run_file(p, [_rec("t1", ["a"], ["a"], 1.0)], summary={"file_recall_mean": 1.0})
    records, summary = diff_runs.load_run(p)
    assert len(records) == 1
    assert summary["file_recall_mean"] == 1.0


def test_load_run_skips_blank_lines(tmp_path):
    import diff_runs
    p = tmp_path / "run.jsonl"
    p.write_text(
        json.dumps(_rec("t1", ["a"], ["a"], 1.0)) + "\n"
        "\n"
        "\n"
        + json.dumps(_rec("t2", [], ["b"], 0.0)) + "\n",
        encoding="utf-8",
    )
    records, _ = diff_runs.load_run(p)
    assert len(records) == 2


# ── pair_by_task ──

def test_pair_by_task_aligns_records():
    import diff_runs
    a = [_rec("t1", [], [], 0.5), _rec("t2", [], [], 0.7)]
    b = [_rec("t1", [], [], 0.6), _rec("t3", [], [], 0.4)]
    paired = diff_runs.pair_by_task([a, b])
    assert paired["t1"][0]["metrics"]["file_recall"] == 0.5
    assert paired["t1"][1]["metrics"]["file_recall"] == 0.6
    assert paired["t2"][0] is not None and paired["t2"][1] is None
    assert paired["t3"][0] is None and paired["t3"][1] is not None


# ── jaccard_top_k ──

def test_jaccard_identical_top_k():
    import diff_runs
    assert diff_runs.jaccard_top_k(["a", "b", "c"], ["a", "b", "c"], k=3) == 1.0


def test_jaccard_disjoint_top_k():
    import diff_runs
    assert diff_runs.jaccard_top_k(["a", "b"], ["x", "y"], k=2) == 0.0


def test_jaccard_partial_overlap():
    import diff_runs
    # k=2 → both top sets = {a, b} vs {a, c} → |∩|=1, |∪|=3 → 1/3
    assert diff_runs.jaccard_top_k(["a", "b"], ["a", "c"], k=2) == pytest.approx(1 / 3)


def test_jaccard_only_compares_top_k():
    import diff_runs
    # First k=2: both are {a, b} → Jaccard 1.0, even though tail differs
    assert diff_runs.jaccard_top_k(["a", "b", "X"], ["a", "b", "Y"], k=2) == 1.0


# ── bootstrap_ci ──

def test_bootstrap_ci_constant_values_yields_tight_ci():
    """All values equal → CI bounds must equal the value."""
    import diff_runs
    lo, hi = diff_runs.bootstrap_ci([0.5, 0.5, 0.5, 0.5], n_boot=100)
    assert lo == 0.5 and hi == 0.5


def test_bootstrap_ci_empty_returns_zeros():
    import diff_runs
    assert diff_runs.bootstrap_ci([]) == (0.0, 0.0)


def test_bootstrap_ci_seeded_is_deterministic():
    """Same input → same CI across calls (we hard-code seed)."""
    import diff_runs
    v = [0.1, 0.5, 0.9, 0.2, 0.7]
    a = diff_runs.bootstrap_ci(v, n_boot=200)
    b = diff_runs.bootstrap_ci(v, n_boot=200)
    assert a == b


# ── render tables ──

def test_render_aggregate_table_shows_per_run_means(tmp_path):
    import diff_runs
    a = [_rec("t1", [], ["a"], 0.5)]
    b = [_rec("t1", [], ["a"], 0.8)]
    md = diff_runs.render_aggregate_table([a, b], ["C1", "C2"])
    assert "C1" in md and "C2" in md
    assert "0.500" in md
    assert "0.800" in md
    # Δ = +0.3
    assert "+0.300" in md


def test_render_per_task_table_sorts_by_abs_delta(tmp_path):
    import diff_runs
    a = [_rec("t1", [], [], 0.1), _rec("t2", [], [], 0.5), _rec("t3", [], [], 0.9)]
    b = [_rec("t1", [], [], 0.9), _rec("t2", [], [], 0.5), _rec("t3", [], [], 0.1)]
    paired = diff_runs.pair_by_task([a, b])
    md = diff_runs.render_per_task_table(paired, ["C1", "C2"], top_n=10)
    # t1 (Δ +0.8) and t3 (Δ -0.8) should appear before t2 (Δ 0)
    import re
    lines = md.splitlines()
    body = [l for l in lines if re.match(r"\|\s*t\d", l)]
    assert "t1" in body[0] or "t3" in body[0]
    assert "t2" in body[2]  # smallest delta last


# ── evaluate_hypotheses ──

def test_h1_passes_when_delta_above_threshold(tmp_path):
    import diff_runs
    c1 = [_rec(f"t{i}", [], ["x"], 0.4) for i in range(5)]   # mean 0.4
    c2 = [_rec(f"t{i}", [], ["x"], 0.55) for i in range(5)]  # mean 0.55, Δ=+0.15
    out = diff_runs.evaluate_hypotheses([c1, c2], ["C1", "C2"])
    assert "H1" in out
    assert "PASS ✓" in out


def test_h1_fails_when_delta_below_threshold():
    import diff_runs
    c1 = [_rec(f"t{i}", [], ["x"], 0.4) for i in range(5)]
    c2 = [_rec(f"t{i}", [], ["x"], 0.45) for i in range(5)]   # Δ=+0.05
    out = diff_runs.evaluate_hypotheses([c1, c2], ["C1", "C2"])
    assert "FAIL ✗" in out


def test_h2_ir_passes_when_jaccard_low_on_majority():
    """C3 retrieves wildly different top-K from C2 on most tasks."""
    import diff_runs
    c2 = [_rec(f"t{i}", ["a", "b", "c", "d", "e"], ["x"], 0.0) for i in range(10)]
    # C3 top-K is fully disjoint on 5 tasks (50% > 30% threshold)
    c3 = [
        _rec(f"t{i}", ["w", "x", "y", "z", "q"], ["x"], 0.0) if i < 5
        else _rec(f"t{i}", ["a", "b", "c", "d", "e"], ["x"], 0.0)
        for i in range(10)
    ]
    out = diff_runs.evaluate_hypotheses([c2, c3], ["C2", "C3"])
    assert "H2-IR" in out
    assert "PASS ✓" in out


def test_h2_ir_fails_when_jaccard_high_on_most_tasks():
    import diff_runs
    same_top_k = ["a", "b", "c", "d", "e"]
    c2 = [_rec(f"t{i}", same_top_k, ["x"], 0.0) for i in range(10)]
    c3 = [_rec(f"t{i}", same_top_k, ["x"], 0.0) for i in range(10)]
    out = diff_runs.evaluate_hypotheses([c2, c3], ["C2", "C3"])
    # 0/10 = 0% changed → fail
    assert "FAIL ✗" in out


def test_h4_h7_marked_undecidable_on_ir_only():
    import diff_runs
    out = diff_runs.evaluate_hypotheses([[], []], ["C0", "C4"])
    assert "Reward" in out  # IR-only can't decide H4/H7
    # And H1/H2 don't fire when names don't include them
    assert "H1" not in out


# ── End-to-end CLI ──

def test_diff_runs_cli_smoke(tmp_path, monkeypatch):
    """Run the script main() with two real run files."""
    import diff_runs

    a_file = tmp_path / "a.jsonl"
    b_file = tmp_path / "b.jsonl"
    _make_run_file(a_file, [
        _rec("t1", ["a"], ["a"], 1.0, p_at_k=0.2, r_at_k=1.0, f1_at_k=0.33),
    ])
    _make_run_file(b_file, [
        _rec("t1", ["a", "b"], ["a"], 1.0, p_at_k=0.4, r_at_k=1.0, f1_at_k=0.57),
    ])

    out_file = tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", [
        "diff_runs.py",
        "--runs", str(a_file), str(b_file),
        "--names", "C1", "C2",
        "--output", str(out_file),
    ])
    rc = diff_runs.main()
    assert rc == 0
    body = out_file.read_text(encoding="utf-8")
    assert "Aggregate metrics" in body
    assert "C1" in body and "C2" in body
    assert "t1" in body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
