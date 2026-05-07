"""Tests for run_bench.py helpers.

Focused on the resume-safety contracts (Codex round-3 findings):
- ir_jsonl_complete distinguishes complete vs truncated IR JSONL files
- truncated IR runs get re-run on --resume instead of diffed as-if-complete
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CSB = HERE.parent.parent
sys.path.insert(0, str(CSB))


# ── ir_jsonl_complete ────────────────────────────────────────────────────────

def test_ir_jsonl_complete_returns_false_on_missing(tmp_path):
    import run_bench
    assert run_bench.ir_jsonl_complete(tmp_path / "nope.jsonl") is False


def test_ir_jsonl_complete_returns_false_on_empty(tmp_path):
    import run_bench
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert run_bench.ir_jsonl_complete(p) is False


def test_ir_jsonl_complete_returns_false_on_truncated(tmp_path):
    """Records present but no `_summary` sentinel — interrupted run."""
    import run_bench
    p = tmp_path / "trunc.jsonl"
    rows = [
        {"task": "a", "metrics": {"recall": 0.5}},
        {"task": "b", "metrics": {"recall": 0.7}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert run_bench.ir_jsonl_complete(p) is False


def test_ir_jsonl_complete_returns_true_on_summary_sentinel(tmp_path):
    """Records + final `_summary` row — complete run."""
    import run_bench
    p = tmp_path / "good.jsonl"
    rows = [
        {"task": "a", "metrics": {"recall": 0.5}},
        {"task": "b", "metrics": {"recall": 0.7}},
        {"_summary": {"config": "ce-keyword", "recall": 0.6}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert run_bench.ir_jsonl_complete(p) is True


def test_ir_jsonl_complete_tolerates_trailing_blank_lines(tmp_path):
    """A run that flushed and added a trailing newline is still complete."""
    import run_bench
    p = tmp_path / "good.jsonl"
    rows = [
        {"task": "a", "metrics": {"recall": 0.5}},
        {"_summary": {"config": "ce-keyword"}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n\n", encoding="utf-8")
    assert run_bench.ir_jsonl_complete(p) is True


def test_ir_jsonl_complete_returns_false_on_corrupt_last_line(tmp_path):
    """Last line is malformed JSON — treat as incomplete and re-run."""
    import run_bench
    p = tmp_path / "corrupt.jsonl"
    p.write_text(
        json.dumps({"task": "a"}) + "\n"
        + "{not-json\n",
        encoding="utf-8",
    )
    assert run_bench.ir_jsonl_complete(p) is False


def test_ir_jsonl_complete_handles_large_file(tmp_path):
    """Reads tail correctly even when file exceeds the read-back chunk size."""
    import run_bench
    p = tmp_path / "big.jsonl"
    # ~10KB of records + final summary
    rows = [{"task": f"t{i}", "payload": "x" * 100} for i in range(100)]
    rows.append({"_summary": {"recall": 0.5}})
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert run_bench.ir_jsonl_complete(p) is True


# ── Token must NOT appear in argv (Codex round-4 finding) ─────────────────────

def test_run_ir_bench_accepts_token_file(tmp_path, monkeypatch, capsys):
    """run_ir_bench.py reads the bearer from --token-file, not --token, so
    the secret never lands on the command line / `ps` listing."""
    import importlib

    token_file = tmp_path / "tok.txt"
    token_file.write_text("secret-bearer-xyz\n", encoding="utf-8")

    # Empty tasks dir → main() exits early without making MCP calls.
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    out = tmp_path / "ir.jsonl"

    sys.argv = [
        "run_ir_bench.py",
        "--tasks-dir", str(tasks),
        "--mcp-url", "https://example.invalid",
        "--token-file", str(token_file),
        "--config", "ce-keyword",
        "--output", str(out),
    ]
    rib = importlib.import_module("run_ir_bench")
    # Empty tasks → exits 1 with "no tasks found", which is fine; we just
    # need to confirm argparse accepts --token-file (it would exit 2 otherwise).
    rc = rib.main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "no tasks found" in captured.err
    # argv must not contain the token literal.
    assert "secret-bearer-xyz" not in " ".join(sys.argv)


def test_run_ir_bench_errors_without_any_token(tmp_path, monkeypatch, capsys):
    """Neither --token nor --token-file is a clean error, not a crash."""
    import importlib

    tasks = tmp_path / "tasks"
    tasks.mkdir()
    out = tmp_path / "ir.jsonl"

    sys.argv = [
        "run_ir_bench.py",
        "--tasks-dir", str(tasks),
        "--mcp-url", "https://example.invalid",
        "--config", "ce-keyword",
        "--output", str(out),
    ]
    rib = importlib.import_module("run_ir_bench")
    rc = rib.main()
    captured = capsys.readouterr()
    assert rc == 2
    assert "--token-file" in captured.err
