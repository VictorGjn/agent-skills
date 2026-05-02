"""P7 / Wave 1 tests — GraphifyWikiSource.

Maps to AC9 from plan/prd-closed-loop.md: given graphify-out/wiki/ exists
with N entity pages, list_artifacts() returns those N paths and
emit_events() produces CE-schema-compliant events.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.source_adapter import (  # noqa: E402
    GraphifyWikiSource, Source, EventStreamSource,
)
from wiki.events import read_events  # noqa: E402


def _seed_graphify_out(root: Path, pages: list[tuple[str, str]]) -> None:
    """Write a fake graphify-out/wiki/ tree."""
    wiki = root / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    for name, content in pages:
        (wiki / name).write_text(content, encoding="utf-8")


class GraphifyWikiSourceContractTests(unittest.TestCase):
    """Source ABC contract for the pull-shaped graphify consumer."""

    def test_inherits_source_abc(self):
        self.assertTrue(issubclass(GraphifyWikiSource, Source))

    def test_list_artifacts_empty_when_dir_missing(self):
        with tempfile.TemporaryDirectory() as td:
            src = GraphifyWikiSource(
                graphify_out_dir=Path(td) / "missing",
                events_dir=Path(td) / "events",
            )
            self.assertEqual(src.list_artifacts(), [])

    def test_list_artifacts_skips_underscore_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "graphify-out"
            _seed_graphify_out(root, [
                ("auth.md", "# Auth\nReal page.\n"),
                ("_index.md", "# Index\nShould be skipped.\n"),
                ("payments.md", "# Payments\nAnother real page.\n"),
            ])
            src = GraphifyWikiSource(
                graphify_out_dir=root,
                events_dir=Path(td) / "events",
            )
            arts = src.list_artifacts()
            self.assertEqual(len(arts), 2,
                             f"_-prefixed files must be skipped, got {arts}")
            self.assertTrue(all("/_index.md" not in a for a in arts))


class GraphifyWikiSourceEmitTests(unittest.TestCase):
    """AC9: emit_events produces CE-schema events from graphify pages."""

    def test_ac9_three_pages_three_events(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "graphify-out"
            events_dir = Path(td) / "events"
            _seed_graphify_out(root, [
                ("auth-middleware.md",
                 "# Auth Middleware\n\nJWT verifier mounted at /api/*.\n"),
                ("payment-service.md",
                 "# Payment Service\n\nStripe wrapper with idempotency keys.\n"),
                ("token-store.md",
                 "# Token Store\n\nRedis with 7d TTL.\n"),
            ])
            src = GraphifyWikiSource(graphify_out_dir=root, events_dir=events_dir)

            # AC9 part 1: 3 paths
            arts = src.list_artifacts()
            self.assertEqual(len(arts), 3)

            # AC9 part 2: walking all artifacts emits 3 events
            n = src.emit_events()
            self.assertEqual(n, 3)

            events = read_events(events_dir)
            self.assertEqual(len(events), 3)
            for e in events:
                self.assertEqual(e["source_type"], "graphify-wiki")
                self.assertTrue(e["source_ref"].startswith("wiki/"))
                self.assertTrue(e["file_id"].startswith("graphify-"))
                self.assertTrue(e["claim"])
                self.assertIsNotNone(e["entity_hint"])

    def test_emit_with_explicit_ref_and_content(self):
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td) / "events"
            src = GraphifyWikiSource(
                graphify_out_dir=Path(td) / "graphify-out",
                events_dir=events_dir,
            )
            content = b"# Inline\n\nThis page was passed in via content kwarg.\n"
            n = src.emit_events(ref="wiki/inline.md", content=content)
            self.assertEqual(n, 1)

            events = read_events(events_dir)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["entity_hint"], "inline")
            self.assertIn("passed in", events[0]["claim"])

    def test_emit_skips_frontmatter_uses_first_paragraph(self):
        page = (
            "---\n"
            "kind: concept\n"
            "title: Demo\n"
            "---\n"
            "# Demo\n"
            "\n"
            "This is the prose paragraph CE wants as the claim.\n"
            "\n"
            "Second paragraph should not be in the claim.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "graphify-out"
            events_dir = Path(td) / "events"
            _seed_graphify_out(root, [("demo.md", page)])
            src = GraphifyWikiSource(graphify_out_dir=root, events_dir=events_dir)
            src.emit_events()

            events = read_events(events_dir)
            self.assertEqual(len(events), 1)
            self.assertEqual(
                events[0]["claim"],
                "This is the prose paragraph CE wants as the claim.",
            )

    def test_emit_falls_back_to_title_when_no_paragraph(self):
        """A page with only a heading + no prose still produces a claim
        (we fall back to the slugified title)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "graphify-out"
            events_dir = Path(td) / "events"
            _seed_graphify_out(root, [("only-heading.md", "# Only Heading\n")])
            src = GraphifyWikiSource(graphify_out_dir=root, events_dir=events_dir)
            n = src.emit_events()
            self.assertEqual(n, 1)
            events = read_events(events_dir)
            self.assertIn("Only Heading", events[0]["claim"])

    def test_passing_events_uses_eventstream_path(self):
        """When called with explicit events list, GraphifyWikiSource
        delegates to EventStreamSource semantics — no parsing."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td) / "events"
            src = GraphifyWikiSource(
                graphify_out_dir=Path(td) / "graphify-out",
                events_dir=events_dir,
            )
            n = src.emit_events([
                {
                    "source_type": "manual",
                    "source_ref": "test/explicit",
                    "file_id": "explicit-1",
                    "claim": "I supplied this directly.",
                    "entity_hint": "explicit",
                },
            ])
            self.assertEqual(n, 1)
            events = read_events(events_dir)
            self.assertEqual(events[0]["source_type"], "manual")

    def test_empty_events_list_is_noop_not_walk(self):
        """Codex P2 regression: emit_events(events=[]) MUST be a no-op,
        not a fall-through to walking artifacts. Otherwise a caller who
        intends `[]` as 'nothing to write' accidentally ingests every
        graphify page in the directory."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "graphify-out"
            events_dir = Path(td) / "events"
            _seed_graphify_out(root, [
                ("a.md", "# A\n\nshould NOT be ingested\n"),
                ("b.md", "# B\n\nshould NOT be ingested\n"),
            ])
            src = GraphifyWikiSource(graphify_out_dir=root, events_dir=events_dir)
            n = src.emit_events(events=[])
            self.assertEqual(n, 0)
            # No events file should exist
            self.assertFalse(list(events_dir.glob("*.jsonl")))

    def test_metadata_returns_size_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "graphify-out"
            _seed_graphify_out(root, [("foo.md", "content")])
            src = GraphifyWikiSource(
                graphify_out_dir=root, events_dir=Path(td) / "events",
            )
            meta = src.metadata("wiki/foo.md")
            self.assertTrue(meta["exists"])
            self.assertGreater(meta["size"], 0)
            self.assertGreater(meta["mtime"], 0)

    def test_metadata_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            src = GraphifyWikiSource(
                graphify_out_dir=Path(td) / "missing",
                events_dir=Path(td) / "events",
            )
            meta = src.metadata("wiki/ghost.md")
            self.assertFalse(meta["exists"])


class EndToEndAC9Tests(unittest.TestCase):
    """The Wave 1 acceptance test from prd-closed-loop.md."""

    def test_graphify_to_wiki_init_round_trip(self):
        """Graphify pages -> CE events -> wiki_init -> CE wiki pages.

        The full hybrid pipeline that proves graphify-as-input works.
        """
        from wiki.wiki_init import write_wiki

        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            brain.mkdir()
            graphify_out = Path(td) / "graphify-out"
            _seed_graphify_out(graphify_out, [
                ("alpha.md", "# Alpha\n\nAlpha is the first concept.\n"),
                ("beta.md", "# Beta\n\nBeta depends on Alpha and Gamma.\n"),
                ("gamma.md", "# Gamma\n\nGamma is referenced by Beta.\n"),
            ])

            # Step 1: GraphifyWikiSource ingests graphify-out/ -> events
            src = GraphifyWikiSource(
                graphify_out_dir=graphify_out,
                events_dir=brain / "events",
            )
            n_emitted = src.emit_events()
            self.assertEqual(n_emitted, 3)

            # Step 2: wiki_init consolidates events into CE entity pages
            actions = write_wiki(
                brain, scope="default", now_iso="2026-05-02T00:00:00Z",
            )
            self.assertEqual(set(actions.keys()), {"alpha", "beta", "gamma"})

            # Step 3: produced pages have CE schema (not graphify schema)
            alpha_page = (brain / "wiki" / "alpha.md").read_text(encoding="utf-8")
            self.assertIn("schema_version: \"1.0\"", alpha_page)
            # wiki_init renders sources rows with "type: <source_type>"; the
            # graphify ingest path tagged events with source_type=graphify-wiki
            self.assertIn("graphify-wiki", alpha_page)
            self.assertIn("last_verified_at:", alpha_page)


if __name__ == "__main__":
    unittest.main()
