"""P5 tests — wiki.ask / wiki.add / wiki.audit MCP tool functions.

We exercise the underlying Python functions directly (the @mcp.tool
decorator wraps them but the wrapped callables remain callable). FastMCP
wire-protocol testing is overkill for unit; runtime smoke happens via the
end-to-end demo.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

# Importing mcp_server runs FastMCP setup; that's fine for testing.
import mcp_server  # noqa: E402
from wiki.events import append_event  # noqa: E402
from wiki.wiki_init import write_wiki  # noqa: E402


class WikiAskTests(unittest.TestCase):
    """M7 — namespace primitive via wiki.ask --scope."""

    def _seed_brain(self, brain: Path) -> None:
        wiki = brain / "wiki"
        wiki.mkdir(parents=True, exist_ok=True)
        # Default-scope page
        (wiki / "default-thing.md").write_text(
            "---\n"
            "id: ent_d1\n"
            "kind: concept\n"
            "title: Default Thing\n"
            "slug: default-thing\n"
            "scope: default\n"
            "schema_version: \"1.0\"\n"
            "confidence: 0.85\n"
            "updated: 2026-05-01T00:00:00Z\n"
            "last_verified_at: 2026-05-01T00:00:00Z\n"
            "sources: []\n"
            "---\n# Default Thing\nBody about acme.\n",
            encoding="utf-8",
        )
        # Competitive-intel-scoped page
        (wiki / "competitor-pricing.md").write_text(
            "---\n"
            "id: ent_c1\n"
            "kind: concept\n"
            "title: Competitor Pricing\n"
            "slug: competitor-pricing\n"
            "scope: competitive-intel\n"
            "schema_version: \"1.0\"\n"
            "confidence: 0.85\n"
            "updated: 2026-05-01T00:00:00Z\n"
            "last_verified_at: 2026-05-01T00:00:00Z\n"
            "sources: []\n"
            "---\n# Competitor Pricing\nAcme raised tier.\n",
            encoding="utf-8",
        )

    def test_default_scope_filter(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            self._seed_brain(brain)
            out = mcp_server.wiki_ask("acme", scope=None, brain=str(brain))
            self.assertIn("default-thing", out)
            self.assertNotIn("competitor-pricing", out,
                             "default scope must NOT leak competitive-intel pages")

    def test_explicit_scope_filter(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            self._seed_brain(brain)
            out = mcp_server.wiki_ask("acme", scope="competitive-intel",
                                      brain=str(brain))
            self.assertIn("competitor-pricing", out)
            self.assertNotIn("default-thing", out,
                             "competitive-intel scope must NOT leak default pages")

    def test_query_substring_filter(self):
        """V0.1 query filter is case-insensitive substring on whole content."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            self._seed_brain(brain)
            # Query "raised" only matches the competitor-pricing body
            out = mcp_server.wiki_ask("raised", scope="competitive-intel",
                                      brain=str(brain))
            self.assertIn("competitor-pricing", out)

    def test_no_match_returns_descriptive_marker(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            self._seed_brain(brain)
            out = mcp_server.wiki_ask("nonexistent-string-zzz",
                                      scope="default", brain=str(brain))
            self.assertIn("no entities in scope", out)

    def test_missing_wiki_dir_returns_marker(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            # No wiki/ at all
            out = mcp_server.wiki_ask("anything", brain=str(brain))
            self.assertIn("no wiki/ directory", out)

    def test_malformed_page_not_treated_as_default_scope(self):
        """Codex P2: a wiki/*.md without proper frontmatter / scope: line
        MUST NOT silently default to scope="default". Otherwise random
        markdown in brain/wiki/ leaks into default-scope queries."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            # File 1: no frontmatter at all — pure markdown
            (wiki / "scratch.md").write_text(
                "# Scratchpad\n\nNotes about acme.\n", encoding="utf-8",
            )
            # File 2: opens frontmatter but never closes it
            (wiki / "broken.md").write_text(
                "---\nkind: concept\n# Body without --- close\n",
                encoding="utf-8",
            )
            # File 3: frontmatter closed but no `scope:` line
            (wiki / "no-scope.md").write_text(
                "---\nkind: concept\nid: ent_x\n---\n# Body\nacme content\n",
                encoding="utf-8",
            )
            out = mcp_server.wiki_ask("acme", scope="default", brain=str(brain))
            self.assertIn("no entities", out,
                          "malformed pages must not surface as default-scope")

    def test_skips_underscore_index_files(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            (wiki / "_index.md").write_text(
                "---\nscope: default\n---\n# Index\n", encoding="utf-8",
            )
            out = mcp_server.wiki_ask("Index", scope="default", brain=str(brain))
            # _-prefixed files must not be returned even if they would match
            self.assertIn("no entities", out)


class WikiAddTests(unittest.TestCase):
    """S2 — wiki.add MCP verb (alias to EventStreamSource.emit_events)."""

    def test_emit_returns_count_and_path(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            out = mcp_server.wiki_add([
                {
                    "source_type": "manual",
                    "source_ref": "test/x",
                    "file_id": "f1",
                    "claim": "Acme tier raised.",
                    "entity_hint": "acme",
                },
            ], brain=str(brain))
            payload = json.loads(out)
            self.assertEqual(payload["appended"], 1)
            self.assertIn("events", payload["events_file"])
            # Round-trip: the events file actually exists with the claim
            events_dir = brain / "events"
            files = list(events_dir.glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            content = files[0].read_text(encoding="utf-8")
            self.assertIn("Acme tier raised.", content)

    def test_empty_events_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            out = mcp_server.wiki_add([], brain=str(brain))
            payload = json.loads(out)
            self.assertEqual(payload["appended"], 0)
            # No events directory created on no-op
            self.assertFalse((brain / "events").exists() and
                             list((brain / "events").glob("*.jsonl")))

    def test_invalid_event_returns_error_payload(self):
        """Missing required keys produce a structured error response, not
        a raised exception (MCP callers shouldn't blow up on bad input)."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            out = mcp_server.wiki_add([
                {"source_type": "manual"},  # missing source_ref / file_id / claim
            ], brain=str(brain))
            payload = json.loads(out)
            self.assertEqual(payload["error"], "INVALID_EVENT")
            self.assertIn("source_ref", payload["message"])


class WikiAuditTests(unittest.TestCase):
    """S3 — wiki.audit MCP verb (reads/refreshes audit/proposals.md)."""

    def test_returns_existing_proposals(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            audit_dir = brain / "audit"
            audit_dir.mkdir(parents=True)
            (audit_dir / "proposals.md").write_text(
                "# audit/proposals.md\n_Generated: 2026-05-01_\n",
                encoding="utf-8",
            )
            out = mcp_server.wiki_audit(brain=str(brain))
            self.assertIn("audit/proposals.md", out)
            self.assertIn("2026-05-01", out)

    def test_missing_proposals_returns_marker(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            out = mcp_server.wiki_audit(brain=str(brain))
            self.assertIn("no audit/proposals.md", out)

    def test_refresh_runs_audit(self):
        """refresh=True regenerates proposals.md from current wiki/."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            # Empty wiki -> all sections "_(none)_"
            out = mcp_server.wiki_audit(brain=str(brain), refresh=True)
            self.assertIn("# audit/proposals.md", out)
            self.assertIn("Stale references", out)
            self.assertIn("_(none)_", out)


class EndToEndLoopViaMCPTests(unittest.TestCase):
    """The closed loop, all four wiki MCP verbs in sequence."""

    def test_full_tick_emit_consolidate_audit(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()

            # Step 1: wiki.add -> EventStreamSource appends events
            r1 = json.loads(mcp_server.wiki_add([
                {
                    "source_type": "web",
                    "source_ref": "https://acme.com/pricing",
                    "file_id": "acme-pricing-2026q2",
                    "claim": "Acme raised tier from $5k to $7k.",
                    "entity_hint": "acme-pricing",
                    "ts": 1700000000,
                },
            ], brain=str(brain)))
            self.assertEqual(r1["appended"], 1)

            # Step 2: wiki_init consolidates (no MCP verb for consolidation in
            # P5; that's a CLI / cron-driven step. Tested directly here.)
            actions = write_wiki(brain, scope="competitive-intel",
                                 now_iso="2026-05-01T00:00:00Z")
            self.assertIn("acme-pricing", actions)

            # Step 3: wiki.ask returns the consolidated entity
            ask = mcp_server.wiki_ask("Acme", scope="competitive-intel",
                                      brain=str(brain))
            self.assertIn("acme-pricing", ask)
            self.assertIn("$5k", ask)

            # Step 4: wiki.audit refresh produces proposals.md
            audit = mcp_server.wiki_audit(brain=str(brain), refresh=True)
            self.assertIn("# audit/proposals.md", audit)


if __name__ == "__main__":
    unittest.main()
