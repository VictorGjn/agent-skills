"""End-to-end tests for the 5 lat.md MCP tools (Phase 4 of CE x lat.md).

Mirrors test_wiki_mcp.py's harness: drive each tool function directly
(rather than over the MCP transport) so we keep tests fast.

Validates PRD AC4:

> Given the MCP server starts after Phase 4 lands, when
> ``mcp_server.py --list-tools`` is run, then it lists 14 tools
> (9 existing + 5 new).
> Given each new MCP tool is invoked, when a request completes, then
> ``cache/usage.jsonl`` (or telemetry stream) contains a tool.call and
> tool.result event with no errors.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

# mcp_server reads tool functions directly via @mcp.tool decorator. Import
# them by name -- they are top-level Python callables.
import mcp_server  # noqa: E402

lat_locate = mcp_server.lat_locate
lat_section = mcp_server.lat_section
lat_refs = mcp_server.lat_refs
lat_search = mcp_server.lat_search
lat_expand = mcp_server.lat_expand


_PAGE_HEADER = (
    "---\n"
    "id: ent_test1234\n"
    "kind: concept\n"
    "title: {title}\n"
    "slug: {slug}\n"
    "scope: default\n"
    "schema_version: \"1.2\"\n"
    "confidence: 0.80\n"
    "updated: 2026-05-01T00:00:00Z\n"
    "last_verified_at: 2026-05-01T00:00:00Z\n"
    "sources:\n"
    "  -\n"
    "    type: code\n"
    "    ref: src/test.ts\n"
    "    ts: 2026-05-01T00:00:00Z\n"
    "---\n\n"
)


def _seed_brain(brain: Path) -> None:
    wiki = brain / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "auth-middleware.md").write_text(
        _PAGE_HEADER.format(title="Auth Middleware", slug="auth-middleware")
        + "# Auth Middleware\n\n"
        "The auth middleware is mounted at /api/*.\n\n"
        "## OAuth Flow\n\n"
        "OAuth handshake details. Refers to [[token-store]].\n\n"
        "## Refresh Tokens\n\n"
        "Refresh handling.\n",
        encoding="utf-8",
    )
    (wiki / "token-store.md").write_text(
        _PAGE_HEADER.format(title="Token Store", slug="token-store")
        + "# Token Store\n\nReferenced by [[auth-middleware]].\n",
        encoding="utf-8",
    )


def _seed_codebase_and_index(repo: Path, brain: Path) -> None:
    """Seed a repo + write a real code_index.json the lat.* tools can load."""
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "auth.ts").write_text(
        "export function validateToken(t: string) {\n"
        "  return t.length > 0;\n"
        "}\n"
        "function helper() {}\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(SCRIPTS))
    from wiki.code_index import build_code_index
    cache = brain.parent / "cache" / "code_index.json"
    build_code_index(repo, cache_path=cache)


class ListToolsCLITests(unittest.TestCase):
    """PRD AC4: `python mcp_server.py --list-tools` returns 14 names."""

    def test_list_tools_returns_14(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "mcp_server.py"), "--list-tools"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        names = [line for line in result.stdout.splitlines() if line.strip()]
        # 9 existing + 5 new lat.* tools.
        self.assertEqual(len(names), 14, f"got: {names}")
        for expected in (
            "pack", "index_workspace", "index_github_repo",
            "build_embeddings", "resolve", "stats",
            "wiki.ask", "wiki.add", "wiki.audit",
            "lat.locate", "lat.section", "lat.refs", "lat.search", "lat.expand",
        ):
            self.assertIn(expected, names)


class LatLocateTests(unittest.TestCase):

    def test_slug_ref_resolves_to_page(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()) as buf:
                out = lat_locate(ref="auth-middleware", brain=str(brain))
            self.assertIn("auth-middleware.md", out)
            # Telemetry: tool.call + tool.result emitted.
            telemetry = buf.getvalue()
            self.assertIn('"tool": "lat.locate"', telemetry)
            self.assertIn('"event": "tool.call"', telemetry)
            self.assertIn('"event": "tool.result"', telemetry)

    def test_section_ref_includes_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_locate(ref="auth-middleware#OAuth Flow", brain=str(brain))
            self.assertIn("OAuth Flow", out)

    def test_missing_page_reports_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_locate(ref="ghost-page", brain=str(brain))
            self.assertIn("not found", out)

    def test_code_ref_resolves_via_code_index(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            repo = Path(td) / "repo"
            _seed_brain(brain)
            _seed_codebase_and_index(repo, brain)
            with redirect_stderr(io.StringIO()):
                out = lat_locate(ref="src/auth.ts#validateToken", brain=str(brain))
            self.assertIn("validateToken", out)
            self.assertIn("src/auth.ts", out)

    def test_malformed_ref_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()) as buf:
                out = lat_locate(ref="[[]]", brain=str(brain))
            self.assertIn("malformed", out)
            self.assertIn("error", buf.getvalue())


class LatSectionTests(unittest.TestCase):

    def test_slug_ref_returns_full_body(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_section(ref="auth-middleware", brain=str(brain))
            self.assertIn("OAuth Flow", out)
            self.assertIn("Refresh Tokens", out)

    def test_section_ref_slices_to_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_section(ref="auth-middleware#OAuth Flow", brain=str(brain))
            self.assertIn("OAuth Flow", out)
            self.assertIn("OAuth handshake details", out)
            # Should NOT include the next sibling section.
            self.assertNotIn("Refresh handling", out)

    def test_budget_truncates_long_section(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            wiki = brain / "wiki"
            wiki.mkdir(parents=True)
            big_body = "# Big\n\n" + ("lorem " * 500) + "\n"
            (wiki / "big.md").write_text(
                _PAGE_HEADER.format(title="Big", slug="big") + big_body,
                encoding="utf-8",
            )
            with redirect_stderr(io.StringIO()):
                out = lat_section(ref="big", brain=str(brain), budget=100)
            self.assertIn("truncated at budget*4=400", out)

    def test_code_ref_returns_symbol_slice(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            repo = Path(td) / "repo"
            _seed_brain(brain)
            _seed_codebase_and_index(repo, brain)
            with redirect_stderr(io.StringIO()):
                out = lat_section(ref="src/auth.ts#validateToken", brain=str(brain))
            self.assertIn("validateToken", out)
            self.assertIn("```", out)


class LatRefsTests(unittest.TestCase):

    def test_finds_inbound_refs(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()) as buf:
                out = lat_refs(target="token-store", brain=str(brain))
            # auth-middleware page links to token-store.
            self.assertIn("auth-middleware", out)
            self.assertIn("token-store", out)
            self.assertIn('"event": "tool.result"', buf.getvalue())

    def test_no_refs_returns_empty_marker(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_refs(target="nonexistent-target", brain=str(brain))
            self.assertIn("no inbound refs", out)


class LatSearchTests(unittest.TestCase):

    def test_finds_wiki_pages(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_search(query="OAuth", brain=str(brain))
            self.assertIn("auth-middleware", out)

    def test_finds_code_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            repo = Path(td) / "repo"
            _seed_brain(brain)
            _seed_codebase_and_index(repo, brain)
            with redirect_stderr(io.StringIO()):
                out = lat_search(query="validateToken", brain=str(brain))
            self.assertIn("src/auth.ts", out)
            self.assertIn("validateToken", out)

    def test_empty_query_handled(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_search(query="", brain=str(brain))
            self.assertIn("empty query", out)


class LatExpandTests(unittest.TestCase):

    def test_depth_zero_returns_only_seed(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_expand(ref="auth-middleware", brain=str(brain), depth=0)
            self.assertIn("auth-middleware", out)
            self.assertIn("(depth=0)", out)
            # No depth=1 expansion.
            self.assertNotIn("(depth=1)", out)

    def test_depth_one_follows_links(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)
            with redirect_stderr(io.StringIO()):
                out = lat_expand(ref="auth-middleware", brain=str(brain), depth=1)
            self.assertIn("(depth=0)", out)
            self.assertIn("(depth=1)", out)
            # token-store is linked from auth-middleware -> appears at depth 1.
            self.assertIn("token-store", out)


if __name__ == "__main__":
    unittest.main()
