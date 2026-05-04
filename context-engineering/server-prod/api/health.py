"""GET /api/health — HTTP-direct liveness check.

Parallel to the MCP `ce_get_health` tool. Useful for load-balancer probes,
uptime monitors, and humans hitting curl. Does NOT require auth; advertises
auth methods + spec version + commit only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib.tools.health import embedding_providers, auth_methods  # noqa: E402
from _lib.version import GIT_SHA, SERVER_VERSION, SPEC_VERSION  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        start = time.time()
        body = {
            "ok": True,
            "version": SERVER_VERSION,
            "spec_version": SPEC_VERSION,
            "commit_sha": GIT_SHA,
            "brain_head_sha": None,
            "providers_available": embedding_providers(),
            "auth_methods_supported": auth_methods(),
            "took_ms": int((time.time() - start) * 1000),
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, format, *args):
        return
