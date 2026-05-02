"""P3 unit tests — freshness_policy + validate_page + wiki_init.

Maps to AC2, AC4, AC6, AC7 in plan/prd-closed-loop.md.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.freshness_policy import (  # noqa: E402
    HALF_LIVES, compute_freshness, compute_freshness_multi_source,
    half_life_days, shortest_half_life,
)
from wiki.validate_page import (  # noqa: E402
    SCHEMA_VERSION, ValidationError, validate_page,
)
from wiki.wiki_init import (  # noqa: E402
    consolidate, slugify, write_wiki, render_page, make_id,
)
from wiki.events import append_event  # noqa: E402


# ── freshness_policy ────────────────────────────────────────────────


class FreshnessPolicyCurveTests(unittest.TestCase):
    """The decay formula at canonical points."""

    def test_at_t_zero_is_one(self):
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        score = compute_freshness("2026-05-01T00:00:00Z", "code", now=now)
        self.assertEqual(score, 1.0)

    def test_at_half_life_is_half(self):
        # code half-life is 90 days
        verified = datetime(2026, 1, 1, tzinfo=timezone.utc)
        now = verified + timedelta(days=90)
        score = compute_freshness(verified, "code", now=now)
        self.assertAlmostEqual(score, 0.5, places=4)

    def test_at_two_half_lives_is_zero(self):
        verified = datetime(2026, 1, 1, tzinfo=timezone.utc)
        now = verified + timedelta(days=180)  # 2 * code half-life
        score = compute_freshness(verified, "code", now=now)
        self.assertEqual(score, 0.0)

    def test_beyond_two_half_lives_clamped_to_zero(self):
        verified = datetime(2026, 1, 1, tzinfo=timezone.utc)
        now = verified + timedelta(days=365)
        score = compute_freshness(verified, "code", now=now)
        self.assertEqual(score, 0.0)

    def test_future_last_verified_clamped_to_one(self):
        """Clock skew: don't bias freshness up."""
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        future = now + timedelta(days=10)
        score = compute_freshness(future, "code", now=now)
        self.assertEqual(score, 1.0)

    def test_ac4_fixture_math(self):
        """AC4 fixture: web (30-day half-life), elapsed=44 days -> < 0.3."""
        verified = datetime(2026, 4, 1, tzinfo=timezone.utc)
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        elapsed = (now - verified).days
        self.assertEqual(elapsed, 44)
        score = compute_freshness(verified, "web", now=now)
        self.assertAlmostEqual(score, 1 - 44/60, places=3)
        self.assertLess(score, 0.3, f"expected AC4 to flag (<0.3), got {score}")


class HalfLifeLookupTests(unittest.TestCase):
    def test_known_source_type(self):
        self.assertEqual(half_life_days("code"), 90)
        self.assertEqual(half_life_days("web"), 30)

    def test_unknown_source_type_falls_back_to_default(self):
        self.assertEqual(half_life_days("nonsense_type"), HALF_LIVES["default"])

    def test_rfc_and_department_spec_separate_keys(self):
        """Both must be lookup-able after the table-row split fix."""
        self.assertEqual(half_life_days("rfc"), 180)
        self.assertEqual(half_life_days("department-spec"), 180)


class MultiSourceFreshnessTests(unittest.TestCase):
    def test_shortest_half_life_governs(self):
        verified = datetime(2026, 4, 1, tzinfo=timezone.utc)
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        # Mix of code (90d) and web (30d) — web governs because it's shortest.
        score_multi = compute_freshness_multi_source(verified, ["code", "web"], now=now)
        score_web = compute_freshness(verified, "web", now=now)
        self.assertAlmostEqual(score_multi, score_web, places=4)

    def test_shortest_half_life_helper(self):
        self.assertEqual(shortest_half_life(["code", "web"]), 30)
        self.assertEqual(shortest_half_life(["rfc", "department-spec"]), 180)

    def test_empty_sources_falls_back_to_default(self):
        self.assertEqual(shortest_half_life([]), HALF_LIVES["default"])


# ── validate_page ───────────────────────────────────────────────────


def _valid_page(kind="concept", **overrides):
    fm = {
        "id": "ent_a4f3",
        "kind": kind,
        "title": "Test Entity",
        "slug": "test-entity",
        "scope": "default",
        "schema_version": SCHEMA_VERSION,
        "confidence": "0.85",
        "updated": "2026-05-01T00:00:00Z",
        "last_verified_at": "2026-05-01T00:00:00Z",
        "sources": "(see body)",
    }
    if kind == "decision":
        fm["supersedes"] = "null"
        fm["superseded_by"] = "null"
        fm["valid_until"] = "null"
    fm.update(overrides)
    lines = ["---"]
    for k, v in fm.items():
        if v is None:
            lines.append(f"{k}: null")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append("# Test Entity")
    lines.append("")
    return "\n".join(lines)


class ValidatePageTests(unittest.TestCase):
    def test_valid_page_passes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(_valid_page(), encoding="utf-8")
            fm = validate_page(p)
            self.assertEqual(fm["kind"], "concept")
            self.assertEqual(fm["schema_version"], SCHEMA_VERSION)

    def test_schema_version_mismatch_raises_with_remediation(self):
        """AC6: refusal carries the rebuild remediation."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(_valid_page(schema_version="0.9"), encoding="utf-8")
            with self.assertRaises(ValidationError) as cm:
                validate_page(p)
            msg = str(cm.exception)
            self.assertIn("0.9", msg)
            self.assertIn("wiki_init.py --rebuild", msg)

    def test_missing_frontmatter_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text("# No frontmatter here", encoding="utf-8")
            with self.assertRaises(ValidationError) as cm:
                validate_page(p)
            self.assertIn("missing YAML frontmatter", str(cm.exception))

    def test_missing_required_key_raises(self):
        text = _valid_page().replace("title: Test Entity\n", "")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(ValidationError) as cm:
                validate_page(p)
            self.assertIn("title", str(cm.exception))

    def test_decision_requires_continuity_fields(self):
        """kind=decision needs supersedes/superseded_by/valid_until."""
        text = _valid_page(kind="decision").replace("supersedes: null\n", "")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(ValidationError) as cm:
                validate_page(p)
            self.assertIn("supersedes", str(cm.exception))

    def test_decision_with_null_continuity_passes(self):
        """Tri-state: null is valid."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(_valid_page(kind="decision"), encoding="utf-8")
            fm = validate_page(p)
            self.assertEqual(fm["kind"], "decision")
            # null parses to Python None
            self.assertIsNone(fm["supersedes"])

    def test_invalid_kind_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            p.write_text(_valid_page(kind="alien"), encoding="utf-8")
            with self.assertRaises(ValidationError):
                validate_page(p)


# ── wiki_init ──────────────────────────────────────────────────────


class SlugifyTests(unittest.TestCase):
    def test_lowercase_kebab(self):
        self.assertEqual(slugify("Authentication Middleware"), "authentication-middleware")

    def test_idempotent(self):
        self.assertEqual(slugify(slugify("Foo Bar")), slugify("Foo Bar"))

    def test_special_chars(self):
        self.assertEqual(slugify("Acme Pricing 2026Q2!"), "acme-pricing-2026q2")

    def test_empty_falls_back(self):
        self.assertEqual(slugify(""), "untitled")


class WikiInitTests(unittest.TestCase):
    def _seed_events(self, events_dir: Path, events: list[dict]) -> None:
        for e in events:
            append_event(
                events_dir,
                source_type=e.get("source_type", "manual"),
                source_ref=e.get("source_ref", "test"),
                file_id=e.get("file_id", "fid"),
                claim=e.get("claim", "test claim"),
                entity_hint=e.get("entity_hint"),
                ts=e.get("ts"),
            )

    def test_consolidates_events_by_entity_hint(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "auth-middleware", "claim": "JWT verifier", "ts": 1700000000},
                {"entity_hint": "auth-middleware", "claim": "Refresh-token flow", "ts": 1700001000},
                {"entity_hint": "token-store", "claim": "Stores tokens in Redis", "ts": 1700002000},
            ])
            actions = write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            self.assertEqual(set(actions.keys()), {"auth-middleware", "token-store"})
            self.assertTrue((brain / "wiki" / "auth-middleware.md").exists())
            self.assertTrue((brain / "wiki" / "token-store.md").exists())

    def test_idempotent_second_run_unchanged(self):
        """AC for M2 idempotency: same events -> same output."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "auth-middleware", "claim": "JWT", "ts": 1700000000},
            ])
            actions1 = write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            self.assertEqual(actions1, {"auth-middleware": "created"})

            actions2 = write_wiki(brain, now_iso="2026-05-02T00:00:00Z")
            self.assertEqual(actions2, {"auth-middleware": "unchanged"})

    def test_new_event_updates_existing_page(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "x", "claim": "first", "ts": 1700000000},
            ])
            write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            self._seed_events(brain / "events", [
                {"entity_hint": "x", "claim": "second", "ts": 1700001000, "source_ref": "other"},
            ])
            actions = write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            self.assertEqual(actions, {"x": "updated"})

    def test_slug_collision_appends_numeric_suffix(self):
        """AC7: title-colliding entities -> -2 suffix, distinct ids."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                # entity_hint differs (so they're separate entities) but
                # both Title-Case to the same slug "data-processing"
                {"entity_hint": "data-processing", "claim": "v1", "ts": 1700000000,
                 "source_ref": "src-a"},
                {"entity_hint": "Data_Processing", "claim": "v2", "ts": 1700001000,
                 "source_ref": "src-b"},
            ])
            actions = write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            slugs = set(actions.keys())
            # Both entities written; one keeps base slug, other gets -2.
            self.assertEqual(len(slugs), 2)
            base_count = sum(1 for s in slugs if s == "data-processing")
            suffixed_count = sum(1 for s in slugs if s == "data-processing-2")
            self.assertEqual(base_count, 1)
            self.assertEqual(suffixed_count, 1)
            # Index has the collision footnote
            index = (brain / "wiki" / "_index.md").read_text(encoding="utf-8")
            self.assertIn("collided with", index)

    def test_rebuild_deletes_existing_pages(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "wiki").mkdir()
            stale = brain / "wiki" / "stale-entity.md"
            stale.write_text("stale content", encoding="utf-8")
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "fresh-entity", "claim": "x", "ts": 1700000000},
            ])
            write_wiki(brain, rebuild=True, now_iso="2026-05-01T00:00:00Z")
            self.assertFalse(stale.exists(), "rebuild should delete pre-existing wiki/*.md")
            self.assertTrue((brain / "wiki" / "fresh-entity.md").exists())

    def test_rebuild_preserves_underscore_files(self):
        """_index.md, _contradictions.md etc. survive --rebuild."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "wiki").mkdir()
            (brain / "wiki" / "_index.md").write_text("kept", encoding="utf-8")
            (brain / "events").mkdir()
            write_wiki(brain, rebuild=True, now_iso="2026-05-01T00:00:00Z")
            self.assertTrue((brain / "wiki" / "_index.md").exists())

    def test_events_without_entity_hint_skipped(self):
        """V0.1 only consolidates by entity_hint. Hint-less events drop."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": None, "claim": "drift", "ts": 1700000000},
                {"entity_hint": "kept", "claim": "kept-claim", "ts": 1700001000},
            ])
            actions = write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            self.assertEqual(set(actions.keys()), {"kept"})

    def test_rendered_page_validates(self):
        """AC: pages produced by wiki_init pass validate_page."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "x", "claim": "c", "ts": 1700000000},
            ])
            write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            page = brain / "wiki" / "x.md"
            fm = validate_page(page)  # raises if invalid
            self.assertEqual(fm["schema_version"], SCHEMA_VERSION)
            self.assertEqual(fm["kind"], "concept")


class CodexRegressionTests(unittest.TestCase):
    """Regressions for the three Codex P1+P2 findings on PR #22."""

    def _seed_events(self, events_dir: Path, events: list[dict]) -> None:
        for e in events:
            append_event(
                events_dir,
                source_type=e.get("source_type", "manual"),
                source_ref=e.get("source_ref", "test"),
                file_id=e.get("file_id", "fid"),
                claim=e.get("claim", "test claim"),
                entity_hint=e.get("entity_hint"),
                ts=e.get("ts"),
            )

    def test_id_distinct_across_slug_collisions_with_shared_sources(self):
        """Codex P1: two distinct entity_hints whose titles slugify to the
        same base AND share source_refs MUST produce distinct ids
        (otherwise supersedes/superseded_by chains break)."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "data-processing", "claim": "v1", "ts": 1700000000,
                 "source_ref": "shared-src"},
                {"entity_hint": "Data_Processing", "claim": "v2", "ts": 1700001000,
                 "source_ref": "shared-src"},
            ])
            write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            page_a = (brain / "wiki" / "data-processing.md").read_text(encoding="utf-8")
            page_b = (brain / "wiki" / "data-processing-2.md").read_text(encoding="utf-8")
            id_a = next(
                line.split("id:", 1)[1].strip()
                for line in page_a.splitlines() if line.startswith("id:")
            )
            id_b = next(
                line.split("id:", 1)[1].strip()
                for line in page_b.splitlines() if line.startswith("id:")
            )
            self.assertNotEqual(
                id_a, id_b,
                "colliding entities must have distinct ids; shared sources_sig "
                "previously collapsed them.",
            )

    def test_existing_stale_schema_page_blocks_incremental(self):
        """Codex P1: refusal-and-rebuild policy means an existing page with
        bad schema must error out, not silently get overwritten."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            (brain / "wiki").mkdir()
            stale = brain / "wiki" / "x.md"
            # Write a stale-schema page in place
            stale.write_text(
                "---\n"
                "id: ent_old\n"
                "kind: concept\n"
                "title: X\n"
                "slug: x\n"
                "scope: default\n"
                "schema_version: \"0.9\"\n"
                "confidence: 0.85\n"
                "updated: 2026-01-01T00:00:00Z\n"
                "last_verified_at: 2026-01-01T00:00:00Z\n"
                "sources: []\n"
                "---\n# X\n",
                encoding="utf-8",
            )
            self._seed_events(brain / "events", [
                {"entity_hint": "x", "claim": "new", "ts": 1700000000},
            ])
            with self.assertRaises(RuntimeError) as cm:
                write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            msg = str(cm.exception)
            self.assertIn("--rebuild", msg)

    def test_existing_stale_schema_page_can_be_rebuilt(self):
        """Codex P1 corollary: --rebuild deletes the stale page, regen succeeds."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            (brain / "wiki").mkdir()
            stale = brain / "wiki" / "x.md"
            stale.write_text(
                '---\nschema_version: "0.9"\n---\n', encoding="utf-8",
            )
            self._seed_events(brain / "events", [
                {"entity_hint": "x", "claim": "new", "ts": 1700000000},
            ])
            actions = write_wiki(brain, rebuild=True, now_iso="2026-05-01T00:00:00Z")
            self.assertEqual(actions, {"x": "created"})

    def test_existing_page_scope_preserved_across_runs(self):
        """Wave 0 demo regression: wiki_init was overwriting per-page
        scope with the caller's `scope` arg on every run, breaking
        idempotency for brains where scope was set per-entity (manually
        or by a future source-aware indexer). Now: existing page's scope
        is preserved; only fresh pages take the caller's `scope`.
        """
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "x", "claim": "first", "ts": 1700000000},
            ])
            # First run with scope="alpha"
            write_wiki(brain, scope="alpha", now_iso="2026-05-01T00:00:00Z")
            page = brain / "wiki" / "x.md"
            self.assertIn("scope: alpha", page.read_text(encoding="utf-8"))

            # Manually rewrite scope to "beta" — simulates an operator
            # reassigning scope or a Wave 1 source-aware classifier
            text = page.read_text(encoding="utf-8")
            text = text.replace("scope: alpha", "scope: beta")
            page.write_text(text, encoding="utf-8")

            # Second run with scope="alpha" again — but the existing
            # page's scope is "beta" and MUST be preserved.
            actions = write_wiki(brain, scope="alpha", now_iso="2026-05-02T00:00:00Z")
            self.assertEqual(actions, {"x": "unchanged"},
                             f"existing scope must be preserved -> unchanged: {actions}")
            self.assertIn("scope: beta", page.read_text(encoding="utf-8"))


    def test_collision_footnotes_idempotent_across_runs(self):
        """Codex P2: rerunning with same colliding inputs must not append
        duplicate collision footnotes. _index.md should be byte-identical
        on the second run."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            self._seed_events(brain / "events", [
                {"entity_hint": "data-processing", "claim": "v1",
                 "ts": 1700000000, "source_ref": "src-a"},
                {"entity_hint": "Data_Processing", "claim": "v2",
                 "ts": 1700001000, "source_ref": "src-b"},
            ])
            write_wiki(brain, now_iso="2026-05-01T00:00:00Z")
            index1 = (brain / "wiki" / "_index.md").read_text(encoding="utf-8")
            self.assertIn("collided with", index1)

            # Second run with same inputs
            write_wiki(brain, now_iso="2026-05-02T00:00:00Z")
            index2 = (brain / "wiki" / "_index.md").read_text(encoding="utf-8")
            self.assertEqual(
                index1, index2,
                "_index.md must be byte-identical across runs with same input",
            )
            # And not duplicated
            self.assertEqual(
                index2.count("data-processing-2"),
                1,
                "collision entry must appear exactly once",
            )


class ConsolidateTests(unittest.TestCase):
    def test_groups_by_entity_hint(self):
        events = [
            {"entity_hint": "a", "ts": 1, "claim": "1"},
            {"entity_hint": "b", "ts": 2, "claim": "2"},
            {"entity_hint": "a", "ts": 3, "claim": "3"},
        ]
        groups = consolidate(events)
        self.assertEqual(set(groups.keys()), {"a", "b"})
        self.assertEqual(len(groups["a"]), 2)
        self.assertEqual([e["claim"] for e in groups["a"]], ["1", "3"])

    def test_drops_events_without_hint(self):
        events = [
            {"entity_hint": None, "ts": 1, "claim": "1"},
            {"entity_hint": "a", "ts": 2, "claim": "2"},
        ]
        groups = consolidate(events)
        self.assertEqual(set(groups.keys()), {"a"})

    def test_make_id_idempotent_same_inputs(self):
        a = make_id("auth-middleware", "src/auth.ts")
        b = make_id("auth-middleware", "src/auth.ts")
        self.assertEqual(a, b)

    def test_make_id_changes_with_sources(self):
        a = make_id("auth-middleware", "src/auth.ts")
        b = make_id("auth-middleware", "src/auth-v2.ts")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
