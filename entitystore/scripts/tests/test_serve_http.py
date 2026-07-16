#!/usr/bin/env python3
"""cb_serve.py — read-only streamable-http server (M12): middleware, wiring,
and the end-to-end concurrent-cap-isolation proof PR #84 deferred here.

All tokens/roles here are obviously-fake fixtures (this repo, VictorGjn/
agent-skills, is PUBLIC) — "widget-reader-token-001" etc, never a real
company-brain secret. Hermetic: builds its own token-map JSON under a
TemporaryDirectory and reuses the synthetic golden_corpus fixture (same one
test_classification_gate.py / test_golden_queries.py use) — no network, no
real corpus.

Three layers, cheapest/most-deterministic first:

1. TestBearerAuthMiddleware — the actual cb_serve.BearerAuthMiddleware class
   against a trivial synthetic downstream ASGI app, driving many genuinely
   concurrent requests (asyncio.gather + a forced await-point in the
   downstream handler) with different tokens. Deterministic: every request
   is guaranteed to overlap with every other, so this is the sharpest proof
   that the ContextVar can't cross-leak between concurrent Tasks under the
   real middleware.
2. TestBuildAppWiring — the actual cb_serve.build_app() composition via
   Starlette's TestClient: unauthenticated request 401s with no data,
   /health answers unauthenticated with version 1.0.0, and wiki_add is not
   in the served tool list (THE WRITER RULE).
3. TestEndToEndConcurrentSessions — the full stack: two real MCP
   ClientSession connections (mcp.client.streamable_http, in-process over
   httpx.ASGITransport, no real socket) opened concurrently with different
   Bearer tokens, each calling the real `stats` tool concurrently against
   the real golden_corpus fixture, asserting the two sessions observe two
   different effective_cap values / entity_counts with no cross-
   contamination — the literal scenario PR #84 flagged as out of scope for
   unit tests alone.

Run: python -m pytest entitystore/scripts/tests/test_serve_http.py -v
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

import httpx

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import cb_serve  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
CORPUS = FIXTURES / "golden_corpus"

FAKE_READER_TOKEN = "widget-reader-token-001"   # -> role reader   -> cap public
FAKE_ADMIN_TOKEN = "widget-admin-token-002"     # -> role admin    -> cap restricted
FAKE_ANALYST_TOKEN = "widget-analyst-token-003"  # -> role analyst -> cap confidential


def _sha256_key(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_corpus_built() -> None:
    if (CORPUS / "entities").exists():
        return
    sys.path.insert(0, str(FIXTURES))
    import build_golden_corpus  # noqa: E402
    build_golden_corpus.build(write=True)


def _write_token_map(tmp_path: pathlib.Path) -> pathlib.Path:
    tokens_path = tmp_path / "tokens.json"
    tokens_path.write_text(json.dumps({
        _sha256_key(FAKE_READER_TOKEN): {"role": "reader"},
        _sha256_key(FAKE_ADMIN_TOKEN): {"role": "admin"},
        _sha256_key(FAKE_ANALYST_TOKEN): {"role": "analyst"},
    }), encoding="utf-8")
    return tokens_path


# ──────────────────────────────────────────────────────────────────
# 1. BearerAuthMiddleware in isolation — deterministic concurrency proof
# ──────────────────────────────────────────────────────────────────

async def _cap_reporting_downstream(scope, receive, send):
    """Synthetic downstream ASGI app: reads the ContextVar-backed cap via
    cb_engine._classification_cap() (same accessor cb_engine's own
    classification-gate tests use directly — see test_classification_gate.py),
    awaits (forcing a scheduler yield so concurrent requests interleave),
    reads it AGAIN, and returns both reads. If the middleware ever leaked
    one request's cap into another's Task, the two reads inside ONE request
    would still agree with each other but disagree with that request's own
    token — which is exactly what the assertions below check across many
    concurrent requests."""
    import cb_engine
    first = cb_engine._classification_cap()
    await asyncio.sleep(0.01)
    second = cb_engine._classification_cap()
    body = json.dumps({"first": first, "second": second}).encode("utf-8")
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


class TestBearerAuthMiddlewareConcurrency(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cb_serve_test_")
        self.tokens_path = _write_token_map(pathlib.Path(self._tmp.name))
        self.app = cb_serve.BearerAuthMiddleware(
            _cap_reporting_downstream, tokens_path=self.tokens_path)

    def tearDown(self):
        self._tmp.cleanup()

    async def _get(self, token: str | None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        ) as client:
            return await client.get("/mcp", headers=headers)

    async def test_concurrent_requests_with_different_tokens_never_cross_leak(self):
        # 12 requests, 3 distinct tokens/caps, all genuinely concurrent
        # (asyncio.gather + the forced sleep above guarantees overlap).
        plan = ([FAKE_READER_TOKEN] * 4 + [FAKE_ADMIN_TOKEN] * 4
                 + [FAKE_ANALYST_TOKEN] * 4)
        responses = await asyncio.gather(*(self._get(tok) for tok in plan))

        expected_cap = {
            FAKE_READER_TOKEN: "public",
            FAKE_ADMIN_TOKEN: "restricted",
            FAKE_ANALYST_TOKEN: "confidential",
        }
        for token, response in zip(plan, responses):
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            want = expected_cap[token]
            # Both reads inside this one request agree with EACH OTHER...
            self.assertEqual(payload["first"], payload["second"])
            # ...AND with this request's own token, never a sibling's.
            self.assertEqual(payload["first"], want)

    async def test_missing_token_fails_closed_401_no_data(self):
        response = await self._get(None)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("first", response.json())

    async def test_unknown_token_fails_closed_401_no_data(self):
        response = await self._get("widget-never-issued-token-999")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("first", response.json())

    async def test_health_path_bypasses_auth_entirely(self):
        # /health must reach the downstream app with NO Authorization header
        # at all — proves the middleware's bypass, independent of build_app's
        # actual /health route (covered separately in TestBuildAppWiring).
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(cb_serve.HEALTH_PATH)
        self.assertEqual(response.status_code, 200)


# ──────────────────────────────────────────────────────────────────
# 2. build_app() wiring — auth, /health, WRITER RULE tool-set
# ──────────────────────────────────────────────────────────────────

class TestBuildAppWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cb_serve_test_")
        self.tokens_path = _write_token_map(pathlib.Path(self._tmp.name))
        self.app = cb_serve.build_app(tokens_path=self.tokens_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_health_is_unauthenticated_and_reports_version_1_0_0(self):
        from starlette.testclient import TestClient
        with TestClient(self.app) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "1.0.0")

    def test_mcp_endpoint_401s_with_no_authorization_header(self):
        from starlette.testclient import TestClient
        with TestClient(self.app) as client:
            response = client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
        self.assertEqual(response.status_code, 401)

    def test_served_tool_list_excludes_wiki_add(self):
        # THE WRITER RULE, structurally: wiki_add must never even be
        # registered on the HTTP-facing FastMCP instance, not merely
        # rejected at call time.
        served = set(cb_serve._list_tools())
        self.assertEqual(
            served, {"wiki_ask", "wiki_pack", "wiki_audit", "stats", "resolve"})
        self.assertNotIn("wiki_add", served)


# ──────────────────────────────────────────────────────────────────
# 3. Full-stack proof: two real MCP ClientSessions, concurrent, over the
#    real BearerAuthMiddleware + FastMCP dispatch + cb_engine.request_cap.
# ──────────────────────────────────────────────────────────────────

async def _call_stats_over_mcp(app, token: str) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    # FastMCP auto-enables DNS-rebinding Host-header protection for the
    # default host=127.0.0.1 (see FastMCP.__init__): the Host header must be
    # "127.0.0.1:<port>" (or localhost/::1). ASGITransport never binds a real
    # socket, so the port here is nominal — only the header text is checked.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8000",
        headers={"Authorization": f"Bearer {token}"},
    ) as http_client:
        async with streamable_http_client(
            "http://127.0.0.1:8000/mcp", http_client=http_client,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("stats", {"corpus_dir": str(CORPUS)})
                return json.loads(result.content[0].text)


class TestEndToEndConcurrentSessions(unittest.IsolatedAsyncioTestCase):
    """The proof PR #84 explicitly deferred: "concurrent requests with
    different tokens observe different caps (ContextVar isolation under the
    real middleware, not just unit tests)" — driven through the actual MCP
    wire protocol (ClientSession + streamable-http), not by calling
    cb_engine.request_cap() directly."""

    @classmethod
    def setUpClass(cls):
        _ensure_corpus_built()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cb_serve_test_")
        self.tokens_path = _write_token_map(pathlib.Path(self._tmp.name))
        self.app = cb_serve.build_app(tokens_path=self.tokens_path)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_concurrent_sessions_different_tokens_different_caps(self):
        async with self.app.router.lifespan_context(self.app):
            reader_stats, admin_stats = await asyncio.gather(
                _call_stats_over_mcp(self.app, FAKE_READER_TOKEN),
                _call_stats_over_mcp(self.app, FAKE_ADMIN_TOKEN),
            )

        # golden_corpus/manifest.json: concept/** -> public (minus one
        # confidential override), org/** -> internal (corpus default),
        # person/** -> restricted. reader (public) must see strictly fewer
        # entities than admin (restricted, i.e. unfiltered).
        self.assertEqual(reader_stats["effective_cap"], "public")
        self.assertEqual(admin_stats["effective_cap"], "restricted")
        self.assertEqual(admin_stats["withheld_count"], 0)  # restricted = unfiltered read
        self.assertGreater(reader_stats["withheld_count"], 0)  # public withholds internal+/confidential+/restricted
        self.assertLess(reader_stats["entity_count"], admin_stats["entity_count"])

    async def test_unauthenticated_session_cannot_initialize(self):
        with self.assertRaises(Exception):
            async with self.app.router.lifespan_context(self.app):
                await _call_stats_over_mcp(self.app, "widget-never-issued-token-999")


if __name__ == "__main__":
    unittest.main()
