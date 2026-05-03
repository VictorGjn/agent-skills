"""Unit tests for SourceCommentBacklinkSource (Phase 3 of CE x lat.md).

Validates PRD AC3:

> Given 5 source files with `// @lat: [[entity-slug]]` comments sprinkled
> across `scripts/`, when `wiki_init.py --rebuild` is run, then each
> affected entity's `sources:` block lists the 5 code refs with line
> numbers and `source_type: code-backlink`.
>
> Given a 1.1-schema page after the bump to 1.2, when `validate_page.py`
> is run, then it raises ValidationError pointing to wiki_init.py --rebuild.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.events import read_events  # noqa: E402
from wiki.source_adapter import SourceCommentBacklinkSource  # noqa: E402


class WalkAndExtractTests(unittest.TestCase):
    """list_artifacts + emit_events end-to-end."""

    def _seed(self, repo: Path) -> None:
        (repo / "src").mkdir()
        (repo / "src" / "auth.ts").write_text(
            "// @lat: [[auth-middleware]]\n"
            "export function validateToken(t: string) {\n"
            "  return t.length > 0;\n"
            "}\n"
            "\n"
            "// @lat: [[token-store]]\n"
            "export class TokenStore {\n"
            "  add(t: string) { return t; }\n"
            "}\n",
            encoding="utf-8",
        )
        (repo / "scripts").mkdir()
        (repo / "scripts" / "service.py").write_text(
            "# @lat: [[user-service]]\n"
            "def get_user(uid):\n"
            "    return {'id': uid}\n"
            "\n"
            "# Not a backlink, just a normal comment\n"
            "def helper():\n"
            "    pass\n",
            encoding="utf-8",
        )
        # Vendor dir that must be skipped.
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "ignore.ts").write_text(
            "// @lat: [[should-not-emit]]\nfunction junk() {}", encoding="utf-8"
        )

    def test_list_artifacts_finds_files_with_at_lat_only(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            self._seed(repo)
            events = repo / "events"
            src = SourceCommentBacklinkSource(repo, events)
            artifacts = src.list_artifacts()
            self.assertIn("src/auth.ts", artifacts)
            self.assertIn("scripts/service.py", artifacts)
            # Vendor / build dirs skipped.
            self.assertFalse(any("node_modules" in a for a in artifacts))

    def test_emit_events_writes_one_event_per_comment(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            self._seed(repo)
            events_dir = repo / "events"
            src = SourceCommentBacklinkSource(repo, events_dir)
            count = src.emit_events()
            self.assertEqual(count, 3)  # 2 in TS + 1 in Python

            log = read_events(events_dir)
            hints = sorted(e["entity_hint"] for e in log)
            self.assertEqual(hints, ["auth-middleware", "token-store", "user-service"])
            for e in log:
                self.assertEqual(e["source_type"], "code-backlink")
                self.assertTrue(e["source_ref"].endswith(":1") or
                                ":" in e["source_ref"], f"missing line: {e['source_ref']}")

    def test_symbol_resolved_to_containing_function(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            self._seed(repo)
            events_dir = repo / "events"
            src = SourceCommentBacklinkSource(repo, events_dir)
            src.emit_events()
            log = read_events(events_dir)
            # `auth-middleware` comment is on line 1, ABOVE validateToken (line 2-4).
            # So at line 1 there is NO containing symbol: symbol=None.
            auth = next(e for e in log if e["entity_hint"] == "auth-middleware")
            self.assertIsNone(auth.get("symbol"))

    def test_malformed_ref_skipped_silently(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / "broken.ts").write_text(
                "// @lat: [[]]\n"  # malformed empty
                "// @lat: [[ok-slug]]\n"
                "function f(){}",
                encoding="utf-8",
            )
            events_dir = repo / "events"
            src = SourceCommentBacklinkSource(repo, events_dir)
            count = src.emit_events()
            self.assertEqual(count, 1)
            log = read_events(events_dir)
            self.assertEqual(log[0]["entity_hint"], "ok-slug")


class EventStreamSourceForwardsSymbolTests(unittest.TestCase):
    """Codex P2 (PR #31): EventStreamSource (and the
    SourceCommentBacklinkSource.emit_events(events=[...]) passthrough that
    delegates to it) must forward the new `symbol` field, not silently
    drop it.
    """

    def test_event_stream_source_persists_symbol(self):
        from wiki.source_adapter import EventStreamSource
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir)
            src.emit_events([{
                "source_type": "code-backlink",
                "source_ref": "src/foo.ts:42",
                "file_id": "sha256:0123456789ab",
                "claim": "claim text",
                "entity_hint": "auth-middleware",
                "symbol": "validateToken",
            }])
            log = read_events(events_dir)
            self.assertEqual(len(log), 1)
            self.assertEqual(log[0]["symbol"], "validateToken")

    def test_source_comment_backlink_passthrough_keeps_symbol(self):
        # SourceCommentBacklinkSource.emit_events(events=[...]) delegates
        # to EventStreamSource. Symbol must survive that delegation.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            events_dir = repo / "events"
            src = SourceCommentBacklinkSource(repo, events_dir)
            src.emit_events([{
                "source_type": "code-backlink",
                "source_ref": "src/bar.py:7",
                "file_id": "sha256:9876543210ab",
                "claim": "test",
                "entity_hint": "user-service",
                "symbol": "get_user",
            }])
            log = read_events(events_dir)
            self.assertEqual(log[0]["symbol"], "get_user")


class WikiInitWithCodeBacklinksTests(unittest.TestCase):
    """End-to-end: `// @lat:` -> events -> wiki/<slug>.md with kind=code."""

    def test_code_backlink_only_entity_gets_kind_code(self):
        from wiki.wiki_init import write_wiki

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / "src").mkdir()
            (repo / "src" / "validator.ts").write_text(
                "// @lat: [[validator-component]]\n"
                "export function validate(input: string) {\n"
                "  return true;\n"
                "}\n",
                encoding="utf-8",
            )
            brain = Path(td) / "brain"
            events_dir = brain / "events"
            src = SourceCommentBacklinkSource(repo, events_dir)
            src.emit_events()

            actions = write_wiki(brain, scope="default")
            self.assertIn("validator-component", actions)
            page_text = (brain / "wiki" / "validator-component.md").read_text(encoding="utf-8")
            # Phase 3 schema bump + new kind.
            self.assertIn("schema_version: \"1.2\"", page_text)
            self.assertIn("kind: code", page_text)
            # source_type from the event flows through.
            self.assertIn("type: code-backlink", page_text)

    def test_mixed_sources_keep_kind_concept(self):
        # An entity sourced from BOTH a code-backlink AND a graphify event
        # stays kind=concept (the more general form).
        from wiki.events import append_event
        from wiki.wiki_init import write_wiki

        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            events_dir = brain / "events"

            # Event 1: code-backlink
            append_event(
                events_dir,
                source_type="code-backlink",
                source_ref="src/foo.ts:5",
                file_id="sha256:0000000000000000",
                claim="from code",
                entity_hint="mixed-entity",
                symbol="foo",
            )
            # Event 2: graphify (different source_type)
            append_event(
                events_dir,
                source_type="graphify-wiki",
                source_ref="wiki/mixed-entity.md",
                file_id="graphify-mixed-entity",
                claim="from graphify",
                entity_hint="mixed-entity",
            )

            write_wiki(brain, scope="default")
            page_text = (brain / "wiki" / "mixed-entity.md").read_text(encoding="utf-8")
            self.assertIn("kind: concept", page_text)
            self.assertNotIn("kind: code", page_text)


class SchemaBumpRefusalTests(unittest.TestCase):
    """PRD AC3: 1.1-schema page must be refused with `--rebuild` remediation."""

    def test_validate_page_refuses_1_1_schema(self):
        from wiki.validate_page import validate_page, ValidationError, SCHEMA_VERSION
        self.assertEqual(SCHEMA_VERSION, "1.2")

        with tempfile.TemporaryDirectory() as td:
            page = Path(td) / "stale.md"
            page.write_text(
                "---\n"
                "id: ent_test\n"
                "kind: concept\n"
                "title: Stale\n"
                "slug: stale\n"
                "scope: default\n"
                "schema_version: \"1.1\"\n"
                "confidence: 0.50\n"
                "updated: 2026-05-01T00:00:00Z\n"
                "last_verified_at: 2026-05-01T00:00:00Z\n"
                "sources:\n"
                "  - { type: code, ref: foo.ts, ts: 2026-05-01T00:00:00Z }\n"
                "---\n# Stale\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError) as cm:
                validate_page(page)
            self.assertIn("--rebuild", str(cm.exception))
            self.assertIn("1.1", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
