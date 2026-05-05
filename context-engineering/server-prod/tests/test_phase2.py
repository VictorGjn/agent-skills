"""Phase 2 smoke tests — server foundation.

Run: pytest server-prod/tests/test_phase2.py
or:  CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/

Tests focus on the wire contract:
- initialize handshake returns expected protocolVersion + capabilities
- tools/list returns 7 tools with correct annotations
- tools/call ce_get_health works
- tools/call other tools return NOT_IMPLEMENTED (Phase 2 placeholder)
- v1.0 aliases resolve to canonical names with X-CE-Deprecated hint
- Auth: missing token → UNAUTHENTICATED protocol error
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make _lib importable when running from repo root.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))

os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

# Import AFTER setting env so the bootstrap hash is computed against test-token.
from _lib import auth, errors  # noqa: E402
from _lib import tools as _tools  # noqa: E402,F401
from _lib.transport import dispatch  # noqa: E402


def _admin_token() -> auth.TokenInfo:
    info = auth.authenticate("Bearer test-token")
    assert info is not None and info.role == "admin"
    return info


def test_initialize_handshake():
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "test", "version": "0"}},
    }
    response, status = dispatch(payload, _admin_token())
    assert status == 200
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"]["name"] == "context-engineering-mcp-server"


def test_tools_list_returns_seven():
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    response, status = dispatch(payload, _admin_token())
    assert status == 200
    tools = response["result"]["tools"]
    names = [t["name"] for t in tools]
    expected = {
        "ce_pack_context", "ce_find_relevant_files", "ce_upload_corpus",
        "ce_index_github_repo", "ce_list_corpora", "ce_get_health", "ce_get_job_status",
    }
    assert set(names) == expected


def test_tools_list_annotations_present():
    payload = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    response, _ = dispatch(payload, _admin_token())
    tools = {t["name"]: t for t in response["result"]["tools"]}
    pack = tools["ce_pack_context"]
    assert pack["annotations"] == {
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    }
    upload = tools["ce_upload_corpus"]
    # destructiveHint MUST be false per § 3.0.3 (idempotency contract makes re-call a no-op)
    assert upload["annotations"]["destructiveHint"] is False
    assert upload["annotations"]["openWorldHint"] is True


def test_ce_get_health_works():
    payload = {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "ce_get_health", "arguments": {}},
    }
    response, status = dispatch(payload, _admin_token())
    assert status == 200
    structured = response["result"]["structuredContent"]
    assert structured["ok"] is True
    assert structured["version"] == "1.0.0"
    # Auth methods must include bearer at minimum
    assert "bearer" in structured["auth_methods_supported"]


def test_phase2_placeholder_async_index_returns_not_implemented():
    """Phase 4 lands write tools, but async=true is still NOT_IMPLEMENTED in v1
    (Vercel Cron + queue is v1.1)."""
    payload = {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "ce_index_github_repo", "arguments": {
            "repo": "x/y", "data_classification": "public", "async": True,
        }},
    }
    response, status = dispatch(payload, _admin_token())
    # § 7.1 tool errors return via result.isError, with HTTP 501
    assert status == 501
    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "NOT_IMPLEMENTED"


def test_alias_resolves_with_deprecated_hint():
    payload = {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "health", "arguments": {}},  # legacy alias
    }
    response, status = dispatch(payload, _admin_token())
    assert status == 200
    # Internal hint that the wire layer should emit X-CE-Deprecated
    assert response.get("_x_ce_deprecated") is True
    assert response["result"]["structuredContent"]["ok"] is True


def test_unknown_tool_invalid_argument():
    payload = {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "ce_does_not_exist", "arguments": {}},
    }
    response, status = dispatch(payload, _admin_token())
    assert status == 400
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_authenticate_rejects_missing_token():
    assert auth.authenticate(None) is None
    assert auth.authenticate("") is None
    assert auth.authenticate("Bearer") is None
    assert auth.authenticate("Basic foo") is None
    assert auth.authenticate("Bearer wrong-token") is None


def test_authenticate_accepts_bootstrap_token():
    info = auth.authenticate("Bearer test-token")
    assert info is not None
    assert info.role == "admin"
    assert info.data_classification_max == "restricted"


def test_role_can_call():
    assert auth.role_can_call("reader", "ce_pack_context") is True
    assert auth.role_can_call("reader", "ce_upload_corpus") is False
    assert auth.role_can_call("writer", "ce_upload_corpus") is True
    assert auth.role_can_call("admin", "ce_index_github_repo") is True


def test_permission_denied_on_role_mismatch():
    """Reader role calling a write tool → § 7.2 PERMISSION_DENIED protocol error."""
    reader = auth.TokenInfo(token_id="t", role="reader", data_classification_max="internal")
    payload = {
        "jsonrpc": "2.0", "id": 100, "method": "tools/call",
        "params": {"name": "ce_upload_corpus", "arguments": {}},
    }
    response, status = dispatch(payload, reader)
    assert status == 403
    assert response["error"]["data"]["code_name"] == "PERMISSION_DENIED"


def test_unknown_method_returns_jsonrpc_method_not_found():
    """Unknown JSON-RPC method → HTTP 200 + -32601 envelope (per JSON-RPC convention)."""
    payload = {"jsonrpc": "2.0", "id": 101, "method": "tools/nonexistent"}
    response, status = dispatch(payload, _admin_token())
    assert status == 200  # JSON-RPC convention: HTTP 200 even on protocol error
    assert response["error"]["code"] == errors.JSONRPC_METHOD_NOT_FOUND


def test_missing_method_field_returns_invalid_request():
    """JSON-RPC payload missing `method` → -32600 INVALID_REQUEST."""
    payload = {"jsonrpc": "2.0", "id": 102}
    response, status = dispatch(payload, _admin_token())
    assert status == 200
    assert response["error"]["code"] == errors.JSONRPC_INVALID_REQUEST


def test_tools_call_non_object_params_returns_invalid_params():
    """Non-object `params` must produce a -32602 envelope, not a 500/AttributeError."""
    for bad in ([], [1, 2, 3], "x", 7, True):
        payload = {"jsonrpc": "2.0", "id": 200, "method": "tools/call", "params": bad}
        response, status = dispatch(payload, _admin_token())
        assert status == 200, f"params={bad!r}"
        assert response["error"]["code"] == errors.JSONRPC_INVALID_PARAMS, f"params={bad!r}"


def test_tool_error_unknown_code_falls_back_safely():
    """Unknown code in tool_error() must fall back to INTERNAL without KeyError."""
    env = errors.tool_error("NOT_A_REAL_CODE", "boom")
    assert env["isError"] is True
    assert env["structuredContent"]["code"] == "INTERNAL"
    assert env["_http_status"] == 500


def test_authenticate_case_sensitive_bearer_scheme():
    """RFC 7235 says auth-scheme is case-insensitive, but our parser only accepts 'Bearer '.
    This documents the gap — clients SHOULD use canonical case.
    """
    # Lowercase 'bearer' is rejected today (known limitation, not a security issue):
    assert auth.authenticate("bearer test-token") is None
    # Tab separator is rejected (we require space):
    assert auth.authenticate("Bearer\ttest-token") is None
    # Whitespace tolerated via .strip() on the token portion:
    assert auth.authenticate("Bearer  test-token  ") is not None


if __name__ == "__main__":
    # Allow `python tests/test_phase2.py` for quick smoke
    import pytest
    sys.exit(pytest.main([__file__, "-xvs"]))
