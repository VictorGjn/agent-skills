"""Streamable HTTP MCP transport — JSON-RPC dispatcher.

Implements the MCP 2025-06-18 spec for tools surface:
  - initialize
  - tools/list
  - tools/call

Resources, prompts, sampling are out of scope for v1. Per SPEC § 3.0:
  capabilities = { tools: { listChanged: false }, resources: { listChanged: true, subscribe: false }, ... }

Phase 2 ships ce_get_health as the only callable tool; all others return
NOT_IMPLEMENTED until Phase 3/4 land.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from . import errors
from .annotations import ANNOTATIONS, canonical, is_alias
from .auth import TokenInfo, authenticate, role_can_call
from .version import SERVER_VERSION, SPEC_VERSION


PROTOCOL_VERSION = "2025-06-18"

SERVER_INFO = {
    "name": "context-engineering-mcp-server",
    "version": SERVER_VERSION,
}

CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {"listChanged": True, "subscribe": False},
    "prompts": {"listChanged": False},
    "logging": {},
}


# Tool registry — canonical_name → handler(args: dict, token: TokenInfo) -> result_dict
# Phase 2 only registers ce_get_health; Phase 3 + 4 add the rest.
_REGISTRY: dict[str, Callable] = {}


def register_tool(name: str, handler: Callable) -> None:
    if name not in ANNOTATIONS:
        raise ValueError(f"tool {name} missing from annotations table")
    _REGISTRY[name] = handler


def _tool_descriptor(name: str) -> dict[str, Any]:
    """Build the tools/list descriptor entry per MCP spec."""
    annot = ANNOTATIONS[name]
    return {
        "name": name,
        "description": _DESCRIPTIONS.get(name, name),
        "inputSchema": _INPUT_SCHEMAS.get(name, {"type": "object", "properties": {}}),
        "annotations": {
            "readOnlyHint": annot["readOnlyHint"],
            "destructiveHint": annot["destructiveHint"],
            "idempotentHint": annot["idempotentHint"],
            "openWorldHint": annot["openWorldHint"],
        },
    }


# Phase 2 sketches — full schemas land per-tool in Phase 3/4.
_DESCRIPTIONS: dict[str, str] = {
    "ce_get_health": "Liveness + version + auth methods. Use when monitoring or smoke-testing the server.",
    "ce_pack_context": "Pack relevant files for a query into a token-budgeted markdown bundle (or structured JSON). [Phase 3]",
    "ce_find_relevant_files": "Return ranked file paths only — no content. [Phase 3]",
    "ce_list_corpora": "List corpora visible to the caller. [Phase 3]",
    "ce_upload_corpus": "Register a client-supplied indexed corpus. [Phase 4]",
    "ce_index_github_repo": "Server-side index a GitHub repo. [Phase 4]",
    "ce_get_job_status": "Poll an async job. [Phase 4]",
}

_INPUT_SCHEMAS: dict[str, dict] = {
    "ce_get_health": {"type": "object", "properties": {}, "additionalProperties": False},
}


def dispatch(payload: dict, token: TokenInfo) -> tuple[dict, int]:
    """
    Route a JSON-RPC request to the right handler.

    Returns (response_dict, http_status). Caller writes both to the wire.
    """
    request_id = payload.get("id")
    method = payload.get("method")

    if not method:
        # JSON-RPC INVALID_REQUEST — payload shape violation, not a server bug.
        # Returns HTTP 200 per JSON-RPC convention so client reads the envelope.
        err = {
            "jsonrpc": "2.0", "id": request_id,
            "error": {
                "code": errors.JSONRPC_INVALID_REQUEST,
                "message": "missing 'method' in JSON-RPC request",
            },
        }
        return err, 200

    if method == "initialize":
        return _initialize(payload, request_id), 200

    if method == "tools/list":
        return _tools_list(request_id), 200

    if method == "tools/call":
        return _tools_call(payload, token, request_id)

    # Method not in our v1 surface — JSON-RPC convention: HTTP 200 with -32601 error,
    # so the client reads the JSON-RPC envelope rather than treating it as transport failure.
    err = {
        "jsonrpc": "2.0", "id": request_id,
        "error": {
            "code": errors.JSONRPC_METHOD_NOT_FOUND,
            "message": f"method {method!r} not supported in v1.0",
        },
    }
    return err, 200


def _initialize(payload: dict, request_id: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": CAPABILITIES,
            "serverInfo": SERVER_INFO,
            "instructions": (
                "CE MCP server (SPEC-mcp.md " + SPEC_VERSION + "). "
                "7 tools per § 3 — call tools/list to enumerate. "
                "Phase 2: only ce_get_health is implemented; other tools return NOT_IMPLEMENTED."
            ),
        },
    }


def _tools_list(request_id: Any) -> dict:
    tools = [_tool_descriptor(name) for name in ANNOTATIONS.keys()]
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"tools": tools},
    }


def _tools_call(payload: dict, token: TokenInfo, request_id: Any) -> tuple[dict, int]:
    params = payload.get("params") or {}
    raw_name = params.get("name", "")
    args = params.get("arguments") or {}

    name = canonical(raw_name)
    if name not in ANNOTATIONS:
        result = errors.tool_error(
            "INVALID_ARGUMENT",
            f"unknown tool: {raw_name!r}",
            details={"known_tools": list(ANNOTATIONS.keys())},
        )
        http_status = result.pop("_http_status")
        return _wrap_tool_result(request_id, result), http_status

    if not role_can_call(token.role, name):
        # § 7.2 protocol error — role insufficient.
        err = errors.protocol_error(
            "PERMISSION_DENIED",
            f"role {token.role!r} cannot call {name!r}",
            request_id=request_id,
        )
        return err, err.pop("_http_status")

    handler = _REGISTRY.get(name)
    if handler is None:
        # Tool is in the spec but Phase 2 doesn't implement it yet.
        result = errors.tool_error(
            "NOT_IMPLEMENTED",
            f"{name} not yet implemented in production server (lands in Phase 3 or 4)",
            details={"phase": "2", "spec_section": "§ 3"},
        )
        http_status = result.pop("_http_status")
        return _wrap_tool_result(request_id, result), http_status

    try:
        out = handler(args, token)
    except Exception as exc:  # noqa: BLE001 — last-ditch
        err = errors.protocol_error(
            "INTERNAL",
            f"unhandled exception in tool {name}: {type(exc).__name__}",
            request_id=request_id,
            details={"exception_type": type(exc).__name__},
        )
        return err, err.pop("_http_status")

    # Tool handlers return either a dict (success) or a `tool_error` envelope.
    # `isError is True` is the spec-grade signal — accept nothing else as an error envelope.
    if isinstance(out, dict) and out.get("isError") is True:
        http_status = out.get("_http_status", 400)
        out = {k: v for k, v in out.items() if k != "_http_status"}
        return _wrap_tool_result(request_id, out), http_status

    return _wrap_tool_result(request_id, out, alias_called=is_alias(raw_name)), 200


def _wrap_tool_result(request_id: Any, result: dict, alias_called: bool = False) -> dict:
    """Wrap the tool-handler result into a JSON-RPC `result` envelope."""
    if result.get("isError") is True:
        wrapped = result
    else:
        wrapped = {
            "content": [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}],
            "structuredContent": result,
        }
    envelope = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": wrapped,
    }
    if alias_called:
        envelope["_x_ce_deprecated"] = True
    return envelope
