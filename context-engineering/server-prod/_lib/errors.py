"""Error envelope per SPEC-mcp.md § 7.

Three layers, three shapes:
  1. Tool errors (§ 7.1) — returned in `result.isError: true` so the agent self-corrects.
  2. Protocol errors (§ 7.2) — JSON-RPC `error` block, aborts the call.
  3. HTTP-direct (§ 7.3) — flat envelope for non-MCP HTTP clients.

Boundary rule (§ 7): auth + transport failures → § 7.2; everything else → § 7.1.
"""
from __future__ import annotations

from typing import Any


# § 7.1 tool errors — codes returned in result.isError envelope
TOOL_ERROR_CODES = {
    "INVALID_ARGUMENT": (400, False),
    "BUDGET_TOO_SMALL": (400, False),
    "EMBEDDING_MISMATCH": (400, False),
    "EMBEDDING_PROVIDER_MISMATCH": (400, False),
    "CORPUS_PREFIX_COLLISION": (400, False),
    "CORPUS_NOT_FOUND": (404, False),
    "CORPUS_ARCHIVED": (410, False),
    "CORPUS_LOCKED": (409, True),
    "JOB_NOT_FOUND": (404, False),
    "BUDGET_EXCEEDED": (408, False),  # use async
    "EMBEDDING_PROVIDER_ERROR": (502, True),
    "EMBEDDING_PROVIDER_PARTIAL": (502, True),
    "SOURCE_FORBIDDEN": (403, False),
    "SOURCE_NOT_FOUND": (404, False),
    "SOURCE_MISMATCH": (409, False),
    "WRITE_CONFLICT": (409, True),
    "BRAIN_RATE_LIMITED": (503, True),
    "PAYLOAD_TOO_LARGE": (413, False),
    "RATE_LIMITED": (429, True),
    "NOT_IMPLEMENTED": (501, False),  # Phase 2 placeholder for tools landing in Phase 3/4
    "PROVIDER_UNAVAILABLE": (503, False),  # missing config (e.g. MISTRAL_API_KEY for embed=true)
    # Last-resort fallback used by tool_error() when an unknown code is passed in.
    # Without this entry the fallback path itself raises KeyError on the lookup below.
    "INTERNAL": (500, True),
}

# § 7.2 protocol errors — emitted as JSON-RPC `error`
# Map: string code → (jsonrpc_code, http_status, retryable)
PROTOCOL_ERROR_CODES = {
    "UNAUTHENTICATED": (-32001, 401, False),
    "PERMISSION_DENIED": (-32002, 403, False),
    "BRAIN_UNAVAILABLE": (-32009, 503, True),
    "WEBHOOK_SECRET_MISMATCH": (-32011, 500, False),
    "INTERNAL": (-32603, 500, True),
}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602


def tool_error(code: str, message: str, details: dict | None = None) -> dict[str, Any]:
    """Build a tool-error envelope (§ 7.1) for inclusion in MCP `result.isError: true`."""
    if code not in TOOL_ERROR_CODES:
        # Fall back to INTERNAL — never silently leak unknown codes
        code, message = "INTERNAL", f"unknown error code: {code}"
    http_status, retryable = TOOL_ERROR_CODES[code]
    structured = {
        "code": code,
        "details": details or {},
        "retryable": retryable,
        "retry_after_seconds": None,
    }
    return {
        "isError": True,
        "content": [{"type": "text", "text": f"{code}: {message}"}],
        "structuredContent": structured,
        "_http_status": http_status,  # internal hint for HTTP-direct shape
    }


def protocol_error(code: str, message: str, request_id: Any = None,
                    details: dict | None = None) -> dict[str, Any]:
    """Build a JSON-RPC `error` envelope (§ 7.2)."""
    if code not in PROTOCOL_ERROR_CODES:
        code, message = "INTERNAL", f"unknown protocol error code: {code}"
    jsonrpc_code, http_status, retryable = PROTOCOL_ERROR_CODES[code]
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": jsonrpc_code,
            "message": message,
            "data": {
                "code_name": code,
                "details": details or {},
                "retryable": retryable,
                "retry_after_seconds": None,
            },
        },
        "_http_status": http_status,
    }


def http_direct(code: str, message: str, details: dict | None = None) -> dict[str, Any]:
    """§ 7.3 — flat HTTP envelope for non-MCP clients calling functions directly."""
    if code in TOOL_ERROR_CODES:
        http_status, retryable = TOOL_ERROR_CODES[code]
    elif code in PROTOCOL_ERROR_CODES:
        _jsonrpc, http_status, retryable = PROTOCOL_ERROR_CODES[code]
    else:
        http_status, retryable = 500, True
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "retryable": retryable,
            "retry_after_seconds": None,
        },
        "_http_status": http_status,
    }
