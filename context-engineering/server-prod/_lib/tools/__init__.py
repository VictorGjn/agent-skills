"""Tool handlers per SPEC-mcp.md § 3.

Each tool exports a `handle(args: dict, token: TokenInfo) -> dict` function.
On success: returns the tool's structured result dict.
On tool error: returns `errors.tool_error(...)` envelope with `_http_status`.

Register all handlers via _lib.transport.register_tool() at import time.
"""
from .. import transport
from . import health

transport.register_tool("ce_get_health", health.handle)
