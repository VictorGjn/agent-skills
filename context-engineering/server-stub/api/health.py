"""Vercel Function — GET /api/health
Returns server liveness, version, and provider availability.
Mirrors the v1 spec's `health` tool shape (CE-1.0-draft).
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import time

VERSION = "1.0.0-stub"
GIT_SHA = os.environ.get("VERCEL_GIT_COMMIT_SHA", "local")[:7]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        start = time.time()
        body = {
            "ok": True,
            "version": VERSION,
            "commit_sha": GIT_SHA,
            "brain_head_sha": None,
            "providers_available": _available_providers(),
            "took_ms": int((time.time() - start) * 1000),
            "stub_warning": (
                "This is a YC-demo stub. Two tools wired (pack_context, list_corpora). "
                "Production v1 lives at SPEC-mcp.md."
            ),
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))


def _available_providers():
    out = []
    for env, name in [
        ("OPENAI_API_KEY", "openai"),
        ("MISTRAL_API_KEY", "mistral"),
        ("VOYAGE_API_KEY", "voyage"),
    ]:
        if os.environ.get(env):
            out.append(name)
    return out
