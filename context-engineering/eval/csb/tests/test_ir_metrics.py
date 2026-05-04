"""Tests for IR metrics + bench driver."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
CSB = HERE.parent.parent
sys.path.insert(0, str(CSB))

import ir_metrics  # noqa: E402


# ── ir_metrics ──

def test_file_recall_perfect():
    assert ir_metrics.file_recall(["a", "b", "c"], ["a", "b"]) == 1.0


def test_file_recall_partial():
    # Only 1 of 2 truth paths hit
    assert ir_metrics.file_recall(["a"], ["a", "b"]) == 0.5


def test_file_recall_zero():
    assert ir_metrics.file_recall(["x", "y"], ["a", "b"]) == 0.0


def test_file_recall_empty_truth_returns_zero():
    """Degenerate case — no truth means we can't measure recall."""
    assert ir_metrics.file_recall(["a", "b"], []) == 0.0


def test_precision_at_k_honors_order():
    # k=2 cuts off at first 2; only 1 of those is truth
    assert ir_metrics.precision_at_k(["a", "x", "b"], ["a", "b"], k=2) == 0.5


def test_precision_at_k_perfect():
    assert ir_metrics.precision_at_k(["a", "b", "c"], ["a", "b", "c"], k=3) == 1.0


def test_recall_at_k_honors_order():
    """Top-2 only sees 'a'; truth has 2 → recall = 0.5."""
    assert ir_metrics.recall_at_k(["a", "x", "b"], ["a", "b"], k=2) == 0.5


def test_f1_at_k_zero_when_both_zero():
    assert ir_metrics.f1_at_k([], ["a"], k=5) == 0.0


def test_f1_at_k_balances():
    """k=2: precision=0.5 (1/2), recall=0.5 (1/2) → F1=0.5."""
    assert ir_metrics.f1_at_k(["a", "x"], ["a", "b"], k=2) == 0.5


def test_strip_corpus_prefix_in_multi_corpus_mode():
    """`<corpus_id>:<path>` must match bare ground-truth paths."""
    retrieved = ["alpha:src/auth.py", "beta:docs/README.md"]
    truth = ["src/auth.py"]
    assert ir_metrics.file_recall(retrieved, truth) == 1.0


def test_strip_does_not_break_paths_with_colons_in_directories():
    """Colons inside path segments (rare on Unix, but possible) don't get stripped."""
    # `weird:dir/file.py` — colon BEFORE a slash, so the prefix splits at first ':'
    # We accept this — multi-corpus convention is `<corpus_id>:<path>` and corpus_ids
    # can't contain slashes per § 4.1, so any '/' before the ':' means the colon is
    # path-internal.
    retrieved = ["already/has:colon.py"]
    truth = ["already/has:colon.py"]
    assert ir_metrics.file_recall(retrieved, truth) == 1.0


def test_normalize_strips_leading_dot_slash_and_backslashes():
    """Windows + relative-path tolerance."""
    retrieved = ["./src\\auth.py"]
    truth = ["src/auth.py"]
    assert ir_metrics.file_recall(retrieved, truth) == 1.0


def test_score_bundle_shape():
    s = ir_metrics.score(["a", "b"], ["a", "c"], k=5)
    assert {"file_recall", "precision_at_k", "recall_at_k", "f1_at_k", "k", "n_retrieved", "n_truth"} <= set(s.keys())


def test_aggregate_excludes_zero_truth_tasks_from_recall():
    s = [
        {"file_recall": 1.0, "precision_at_k": 1.0, "recall_at_k": 1.0, "f1_at_k": 1.0, "n_truth": 1},
        {"file_recall": 0.0, "precision_at_k": 0.0, "recall_at_k": 0.0, "f1_at_k": 0.0, "n_truth": 0},  # excluded
    ]
    agg = ir_metrics.aggregate(s)
    assert agg["file_recall_mean"] == 1.0
    assert agg["n_zero_truth_tasks"] == 1
    assert agg["n_tasks"] == 2


# ── bench driver (with stubbed HTTP) ──

def test_derive_corpus_id():
    import run_ir_bench
    assert run_ir_bench._derive_corpus_id({"repo": "Foo/Bar.repo", "branch": "main"}) == "gh-foo-bar-repo-main"


def test_extract_paths_from_response_success():
    import run_ir_bench
    resp = {
        "result": {
            "structuredContent": {
                "files": [{"path": "a.py", "relevance": 0.5}, {"path": "b.py", "relevance": 0.4}],
            },
        },
    }
    paths, err = run_ir_bench._extract_paths_from_response(resp)
    assert paths == ["a.py", "b.py"]
    assert err is None


def test_extract_paths_from_response_tool_error():
    import run_ir_bench
    resp = {"result": {"structuredContent": {"isError": True, "code": "CORPUS_NOT_FOUND", "message": "x"}}}
    paths, err = run_ir_bench._extract_paths_from_response(resp)
    assert paths == []
    assert err["layer"] == "tool"
    assert err["code"] == "CORPUS_NOT_FOUND"


def test_extract_paths_from_response_http_error():
    import run_ir_bench
    resp = {"_http_error": 401, "_body": "unauthorized"}
    paths, err = run_ir_bench._extract_paths_from_response(resp)
    assert paths == []
    assert err["layer"] == "http" and err["code"] == 401


def test_run_one_end_to_end(tmp_path, monkeypatch):
    """Drive run_one against a stubbed _post; verify metrics are computed."""
    import run_ir_bench

    task_dir = tmp_path / "task-1"
    task_dir.mkdir()
    (task_dir / "spec.json").write_text(json.dumps({
        "description": "find the auth handler",
        "repo": "Foo/Bar", "branch": "main",
        "corpus_id": "gh-foo-bar-main",
    }))
    (task_dir / "ground_truth.json").write_text(json.dumps([
        "src/auth.py", "src/middleware.py",
    ]))

    def fake_post(url, token, payload, timeout=60):
        return {
            "jsonrpc": "2.0", "id": 1,
            "result": {"structuredContent": {
                "files": [
                    {"path": "src/auth.py", "relevance": 0.9},
                    {"path": "src/middleware.py", "relevance": 0.7},
                    {"path": "tests/something.py", "relevance": 0.3},
                ],
            }},
        }
    monkeypatch.setattr(run_ir_bench, "_post", fake_post)

    rec = run_ir_bench.run_one(task_dir, "http://x", "tok", "ce-keyword", top_k=5, budget=8000)
    assert rec["task_id"] == "task-1"
    assert rec["error"] is None
    assert rec["metrics"]["file_recall"] == 1.0  # both truth files retrieved
    assert rec["metrics"]["precision_at_k"] >= 0.4   # 2/5 = 0.4 minimum


def test_run_one_records_tool_error(tmp_path, monkeypatch):
    import run_ir_bench
    task_dir = tmp_path / "task-2"
    task_dir.mkdir()
    (task_dir / "spec.json").write_text(json.dumps({"description": "x", "repo": "f/b", "branch": "main"}))
    (task_dir / "ground_truth.json").write_text(json.dumps(["a"]))

    def fake_post(url, token, payload, timeout=60):
        return {"result": {"structuredContent": {"isError": True, "code": "CORPUS_NOT_FOUND"}}}
    monkeypatch.setattr(run_ir_bench, "_post", fake_post)

    rec = run_ir_bench.run_one(task_dir, "http://x", "tok", "ce-keyword", top_k=5, budget=8000)
    assert rec["error"]["code"] == "CORPUS_NOT_FOUND"
    assert rec["metrics"]["file_recall"] == 0.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
