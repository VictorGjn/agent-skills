"""Bearer-token auth for the stub. Reads CE_MCP_TOKEN from env.

Production v1 will use a hashed token map (CONTEXT_ENG_TOKENS_JSON) with
role-based caps; the stub has a single shared token. See SPEC-mcp.md §6.
"""
import json
import os


def require_bearer(handler):
    """Returns True if auth is OK; otherwise writes a 401 and returns False.

    Fail closed when CE_MCP_TOKEN is unset, unless CE_MCP_ALLOW_NO_TOKEN=1
    is explicitly set for local dev. Previously the unset-token path returned
    True, which meant any preview/production deployment with a misconfigured
    env was open to unauthenticated callers — a direct data-exposure path
    flagged in PR review.
    """
    expected = os.environ.get("CE_MCP_TOKEN", "").strip()
    if not expected:
        if os.environ.get("CE_MCP_ALLOW_NO_TOKEN", "").strip() == "1":
            return True
        _send_401(handler, "authentication not configured")
        return False

    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        _send_401(handler, "missing Bearer token")
        return False
    presented = auth[len("Bearer "):].strip()
    if presented != expected:
        _send_401(handler, "invalid token")
        return False
    return True


def _send_401(handler, message):
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(json.dumps({
        "error": {
            "code": "UNAUTHENTICATED",
            "message": message,
            "retryable": False,
        }
    }).encode("utf-8"))
