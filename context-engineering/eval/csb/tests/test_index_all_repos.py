"""Tests for index_all_repos.py helpers (sync→async fallback + poll loop).

Covers the Codex round-3 P1 fix on PR #60: `_poll_async` must NOT keep polling
through a structured tool/jsonrpc error from `ce_get_job_status` — those
won't recover by waiting and silently stalling the bench for the full
30-min budget per affected repo hides the real failure reason.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
CSB = HERE.parent.parent
sys.path.insert(0, str(CSB))


def _ok_response(structured: dict) -> dict:
    """Build the JSON-RPC envelope that `_parse_tool_response` classifies as 'ok'."""
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"isError": False, "structuredContent": structured}}


def _tool_error_response(code: str, msg: str, details: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"isError": True,
                       "structuredContent": {"code": code, "message": msg,
                                              "details": details or {}}}}


def _jsonrpc_error_response(code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1,
            "error": {"code": code, "message": msg}}


def _transport_blip() -> dict:
    return {"_transport_error": "TimeoutError: read timeout"}


# ── _poll_async ───────────────────────────────────────────────────────────────

def test_poll_async_fails_fast_on_tool_error():
    """ce_get_job_status returns JOB_NOT_FOUND → return immediately, don't
    sit through the 30-min budget."""
    import index_all_repos as m

    with mock.patch.object(m, "post",
                           return_value=_tool_error_response(
                               "JOB_NOT_FOUND", "no job with id 'abc'",
                               {"job_id": "abc"})), \
         mock.patch.object(m, "time") as t:
        # time.time() must advance so the loop actually enters but doesn't
        # exit by deadline. monotonic-ish: 0, 0.1, 0.2, ...
        ticks = iter([0.0, 0.0, 0.1, 0.2, 0.3])
        t.time.side_effect = lambda: next(ticks)
        t.sleep.return_value = None

        rec = m._poll_async("https://x", "tok", "abc", "owner/r", "main", 0.0)

    assert rec["status"] == "async_tool_error"
    assert rec["error_code"] == "JOB_NOT_FOUND"
    assert rec["job_id"] == "abc"
    # Should NOT have run for the whole 30-min budget — elapsed < 1s
    assert rec["elapsed_s"] < 1.0


def test_poll_async_fails_fast_on_jsonrpc_error():
    """Top-level JSON-RPC error (e.g. -32602 invalid params) is also terminal."""
    import index_all_repos as m

    with mock.patch.object(m, "post",
                           return_value=_jsonrpc_error_response(
                               -32602, "invalid params")), \
         mock.patch.object(m, "time") as t:
        ticks = iter([0.0, 0.0, 0.1, 0.2])
        t.time.side_effect = lambda: next(ticks)
        t.sleep.return_value = None

        rec = m._poll_async("https://x", "tok", "abc", "owner/r", "main", 0.0)

    assert rec["status"] == "async_jsonrpc_error"
    assert rec["error"] and "invalid params" in rec["error"]


def test_poll_async_keeps_polling_on_transport_blip():
    """A genuine network blip should NOT collapse the bench — we keep polling
    until the deadline OR the next call succeeds."""
    import index_all_repos as m

    # Sequence: blip, blip, then complete.
    responses = [
        _transport_blip(),
        _transport_blip(),
        _ok_response({"status": "complete",
                      "corpus_id": "gh-owner-r-main",
                      "result_commit_sha": "deadbeef",
                      "progress": {"files_indexed": 42, "embedded_count": 0}}),
    ]
    with mock.patch.object(m, "post", side_effect=responses), \
         mock.patch.object(m, "time") as t:
        # tick forward but never past the deadline
        ticks = iter([0.0] + [i * 0.5 for i in range(20)])
        t.time.side_effect = lambda: next(ticks)
        t.sleep.return_value = None

        rec = m._poll_async("https://x", "tok", "abc", "owner/r", "main", 0.0)

    assert rec["status"] == "ok"
    assert rec["mode"] == "async"
    assert rec["corpus_id"] == "gh-owner-r-main"
    assert rec["file_count"] == 42
    assert rec["async_keyword_only"] is True


def test_poll_async_returns_async_failed_on_terminal_status():
    """status='failed' from ce_get_job_status returns async_failed (not _tool_error)."""
    import index_all_repos as m

    resp = _ok_response({
        "status": "failed",
        "corpus_id": "gh-owner-r-main",
        "error": {"code": "INDEXER_FAILURE", "message": "tree fetch 500"},
        "progress": {"files_indexed": 0},
    })
    with mock.patch.object(m, "post", return_value=resp), \
         mock.patch.object(m, "time") as t:
        ticks = iter([0.0, 0.0, 0.1])
        t.time.side_effect = lambda: next(ticks)
        t.sleep.return_value = None

        rec = m._poll_async("https://x", "tok", "abc", "owner/r", "main", 0.0)

    assert rec["status"] == "async_failed"
    assert rec["error_code"] == "INDEXER_FAILURE"
    assert "tree fetch 500" in rec["error"]
