"""POST/GET /api/mcp — Streamable HTTP MCP transport.

Single Vercel function handling all MCP JSON-RPC traffic per SPEC § 3.0.
- POST: receive a JSON-RPC request, route via transport.dispatch, return response
- GET: SSE stream for server-pushed events (initialize handshake support; v1.0 keeps it minimal)

Auth: Bearer token in Authorization header (§ 6.1). UNAUTHENTICATED on missing/invalid.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Vercel Functions: this file IS the package root for the function. Make local libs importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib import errors  # noqa: E402
from _lib.auth import authenticate  # noqa: E402
from _lib.transport import dispatch  # noqa: E402
from _lib import tools as _tools  # noqa: E402,F401  — side-effect: registers handlers

MAX_INLINE_BODY_BYTES = 32 * 1024 * 1024  # § 3.3 cap; PAYLOAD_TOO_LARGE above this


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Body length check first — § 3.3 PAYLOAD_TOO_LARGE
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_INLINE_BODY_BYTES:
            # PAYLOAD_TOO_LARGE is a tool error (§ 7.1) but we reject before parsing JSON-RPC,
            # so we have no request_id. Return HTTP-direct envelope (§ 7.3).
            body = errors.http_direct(
                "PAYLOAD_TOO_LARGE",
                f"request body {length} bytes exceeds max {MAX_INLINE_BODY_BYTES}",
            )
            self._write_error(body.pop("_http_status"), body)
            return

        # Auth — § 6.1 / § 7.2
        token = authenticate(self.headers.get("Authorization"))
        if token is None:
            err = errors.protocol_error(
                "UNAUTHENTICATED",
                "missing or invalid Authorization header",
            )
            self._write_error(err.pop("_http_status"), err)
            return

        # Parse JSON-RPC body
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            err = {
                "jsonrpc": "2.0", "id": None,
                "error": {
                    "code": errors.JSONRPC_PARSE_ERROR,
                    "message": f"invalid JSON: {exc}",
                },
            }
            self._write_error(400, err)
            return

        # Dispatch
        response, http_status = dispatch(payload, token)

        # Strip internal hints before serializing
        deprecated = response.pop("_x_ce_deprecated", False)
        etag = response.pop("_x_etag", None)
        cache_control = response.pop("_x_cache_control", None) or "no-store"

        # § 3.1 conditional GET: If-None-Match against our computed ETag → 304.
        if etag:
            client_etag = self.headers.get("If-None-Match")
            if client_etag and client_etag.strip('"') == etag:
                self.send_response(304)
                self.send_header("ETag", f'"{etag}"')
                self.send_header("Cache-Control", cache_control)
                self.end_headers()
                return

        body = json.dumps(response, separators=(",", ":")).encode("utf-8")
        self.send_response(http_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", cache_control)
        if etag:
            self.send_header("ETag", f'"{etag}"')
        if deprecated:
            self.send_header("X-CE-Deprecated", "true")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Streamable HTTP clients GET with `Accept: text/event-stream` to open
        # a server-initiated stream. This server is stateless and emits no
        # server-initiated events, so we return 405 — clients fall back to
        # POST-only. A keep-alive SSE that emits no real events bills the
        # full maxDuration × reconnect rate on Fluid compute. Mirrors
        # syroco-connect's GET handler.
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b'{"ok":false,"error":"method_not_allowed"}')

    def log_message(self, format, *args):
        # Quiet the default Vercel log spam; tool.call telemetry lands in v1.1
        return

    def _write_error(self, http_status: int, body: dict) -> None:
        body.pop("_http_status", None)
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(http_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
