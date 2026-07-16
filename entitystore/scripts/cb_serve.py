#!/usr/bin/env python3
"""
cb_serve — company-brain MCP server, READ-ONLY, over streamable-http (M12).

Serves five of cb_mcp.py's six endpoints — wiki_ask, wiki_pack, wiki_audit,
stats, resolve — over the MCP streamable-http transport, behind a Bearer
token auth middleware. wiki_add is DELIBERATELY NOT served here: THE WRITER
RULE (company-brain/CLAUDE.md, locked 2026-06-04) says entities/claims are
only ever written by enrichers, never a scribe, consumer, or hand-edit, and
"never a hand-edit" extends to "never a remote MCP client either" — there is
currently no reviewed design for a network write path that couldn't become a
backdoor around that rule. If a served-mode write path is ever wanted, that
needs its own design doc and its own review, not a tool flipped on here
because the transport happened to make it reachable. See the PR description
for the open design question this intentionally defers.

This module owns exactly two things cb_mcp.py's stdio server doesn't need:
  1. A second FastMCP instance exposing only the five read tools (each one a
     thin delegator to the already-decorated cb_mcp.py function of the same
     name — single source of truth for docstrings/JSON-shape/logic stays in
     cb_mcp.py + cb_engine.py; this file only decides WHICH tools reach the
     network).
  2. BearerAuthMiddleware: a pure-ASGI (not BaseHTTPMiddleware, so streamed
     SSE responses are never buffered) middleware that extracts a Bearer
     token from the Authorization header, resolves it via
     cb_auth.verify_token(), and holds cb_engine.request_cap(cap) for the
     lifetime of exactly that request's downstream call. Unauthenticated or
     unknown-token requests get 401 with no data and never reach the MCP
     dispatch layer at all — fail closed, not fail open.

Run:
    python cb_serve.py                  # streamable-http on CB_HTTP_HOST:CB_HTTP_PORT
    python cb_serve.py --list-tools     # print the five served tool names, exit

Configure (env vars):
    CB_CORPUS_DIR          — path to corpora/<id>/ (required by the engine;
                              individual calls may override via the
                              corpus_dir tool argument, same as cb_mcp.py).
    CB_AUTH_TOKENS_PATH     — path to the Bearer token-map JSON (see
                              cb_auth.py's module docstring for format).
                              Required for ANY authenticated request to
                              succeed. If unset, every request 401s (fails
                              closed) — the server still starts and /health
                              still answers, but no caller can read anything.
    CB_HTTP_HOST            — bind host, default 127.0.0.1.
    CB_HTTP_PORT            — bind port, default 8000.
    CB_CLASSIFICATION_CAP   — process-level fallback cap (M7). Only takes
                              effect for a request whose Bearer token maps to
                              no role->cap entry AND slips past auth, which
                              can't happen here since verify_token() already
                              fails closed on that case — kept only because
                              cb_engine._classification_cap() always consults
                              it as a fallback layer; harmless to leave unset.

/health is intentionally NOT behind the Bearer gate — it returns a static
version string, never corpus data, so there is nothing for the "no data on
401" rule to protect there; gating it would just break plain liveness probes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

import cb_auth  # noqa: E402
import cb_engine  # noqa: E402
import cb_mcp  # noqa: E402

VERSION = "1.0.0"
HEALTH_PATH = "/health"

READ_TOOLS = ("wiki_ask", "wiki_pack", "wiki_audit", "stats", "resolve")


def _build_http_mcp() -> FastMCP:
    """A fresh FastMCP instance carrying ONLY the five read tools — never
    reuses cb_mcp.mcp directly, so wiki_add can never end up reachable over
    HTTP by accident (e.g. a future edit to cb_mcp.py adding a seventh tool
    would need a matching, deliberate edit here to reach the network)."""
    http_mcp = FastMCP(
        "companybrain-http",
        instructions=(
            "Read-only company-brain EntityStore over streamable-http. "
            "Five endpoints — wiki_ask, wiki_pack, wiki_audit, stats, "
            "resolve. wiki_add is not served (THE WRITER RULE). Every "
            "request requires a valid Bearer token; the token's role "
            "determines the classification cap applied to that request "
            "(see cb_auth.py / SURFACE.md 'Classification cap')."
        ),
    )

    @http_mcp.tool()
    def wiki_ask(
        query: str,
        kind: str | None = None,
        topics: list[str] | None = None,
        depth: int = 1,
        budget: int = 8000,
        mode: str = "hybrid",
        top: int = 30,
        corpus_dir: str | None = None,
    ) -> str:
        """See cb_mcp.wiki_ask — identical contract, served read-only over HTTP."""
        return cb_mcp.wiki_ask(
            query=query, kind=kind, topics=topics, depth=depth,
            budget=budget, mode=mode, top=top, corpus_dir=corpus_dir,
        )

    @http_mcp.tool()
    def wiki_pack(
        query: str,
        budget: int = 8000,
        kind: str | None = None,
        topics: list[str] | None = None,
        mode: str = "hybrid",
        top: int = 50,
        include_neighbors: bool = True,
        corpus_dir: str | None = None,
    ) -> str:
        """See cb_mcp.wiki_pack — identical contract, served read-only over HTTP."""
        return cb_mcp.wiki_pack(
            query=query, budget=budget, kind=kind, topics=topics, mode=mode,
            top=top, include_neighbors=include_neighbors, corpus_dir=corpus_dir,
        )

    @http_mcp.tool()
    def wiki_audit(
        kinds: list[str] | None = None,
        corpus_dir: str | None = None,
    ) -> str:
        """See cb_mcp.wiki_audit — identical contract, served read-only over HTTP."""
        return cb_mcp.wiki_audit(kinds=kinds, corpus_dir=corpus_dir)

    @http_mcp.tool()
    def stats(corpus_dir: str | None = None) -> str:
        """See cb_mcp.stats — identical contract, served read-only over HTTP."""
        return cb_mcp.stats(corpus_dir=corpus_dir)

    @http_mcp.tool()
    def resolve(
        query: str,
        top_k: int = 10,
        corpus_dir: str | None = None,
    ) -> str:
        """See cb_mcp.resolve — identical contract, served read-only over HTTP."""
        return cb_mcp.resolve(query=query, top_k=top_k, corpus_dir=corpus_dir)

    return http_mcp


class BearerAuthMiddleware:
    """Pure-ASGI middleware: Authorization header -> cb_auth.verify_token()
    -> cb_engine.request_cap(cap) held for exactly the downstream call.

    Pure ASGI (implements __call__(scope, receive, send) directly) rather
    than Starlette's BaseHTTPMiddleware, which buffers the whole response
    body — that would break the SSE streaming responses streamable-http
    uses. This class wraps the downstream app call itself, so the
    ContextVar override (cb_engine._REQUEST_CAP, set inside request_cap())
    is scoped to the asyncio Task handling exactly this one connection —
    concurrent requests each get their own Task and therefore their own
    copy of the ContextVar, per contextvars semantics; see
    test_serve_http.py's concurrency proof.

    Fails closed: no Authorization header, a non-Bearer scheme, or a token
    verify_token() doesn't recognize all produce a 401 JSON response with no
    corpus data, and the downstream app is never invoked.
    """

    def __init__(self, app, tokens_path: str | os.PathLike | None = None):
        self.app = app
        self.tokens_path = tokens_path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return

        token = _extract_bearer_token(scope)
        verified = cb_auth.verify_token(token, tokens_path=self.tokens_path)
        if verified is None:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        _role, cap = verified
        with cb_engine.request_cap(cap):
            await self.app(scope, receive, send)


def _extract_bearer_token(scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            header = value.decode("latin-1")
            if header[:7].lower() == "bearer ":
                return header[7:].strip()
            return None
    return None


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": VERSION})


def build_app(
    *,
    tokens_path: str | os.PathLike | None = None,
) -> Starlette:
    """Compose the served-mode Starlette app: FastMCP's own streamable-http
    app (unmodified route + lifespan, so StreamableHTTPSessionManager's
    task-group startup/shutdown stays correct) plus /health and the Bearer
    middleware added directly onto that SAME app object — not a second,
    outer Starlette wrapping it, which would need its own lifespan wiring to
    keep the session manager's task group alive (see FastMCP.session_manager's
    docstring: "mounting multiple FastMCP servers ... single application").

    tokens_path overrides CB_AUTH_TOKENS_PATH for tests; production callers
    should leave it unset and rely on the env var (same convention as
    cb_auth.verify_token()).
    """
    http_mcp = _build_http_mcp()
    app = http_mcp.streamable_http_app()
    app.add_route(HEALTH_PATH, _health, methods=["GET"])
    app.add_middleware(BearerAuthMiddleware, tokens_path=tokens_path)
    return app


def _list_tools() -> list[str]:
    return list(READ_TOOLS)


def main() -> None:
    if "--list-tools" in sys.argv:
        for name in _list_tools():
            print(name)
        return

    import uvicorn

    tokens_path = os.environ.get("CB_AUTH_TOKENS_PATH")
    if not tokens_path:
        print(
            "cb_serve: CB_AUTH_TOKENS_PATH is unset — every request will "
            "401 (fail-closed). Set it to a token-map JSON path (see "
            "cb_auth.py) before pointing a real client at this server.",
            file=sys.stderr,
        )

    app = build_app(tokens_path=tokens_path)
    host = os.environ.get("CB_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("CB_HTTP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
