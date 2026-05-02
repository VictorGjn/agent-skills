"""P4 Auditor tests — three rules + proposals.md rendering.

Maps to AC3 (stale supersession), AC4 (freshness expired), AC7-equiv
(slug collision near-miss) in plan/prd-closed-loop.md.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.audit import (  # noqa: E402
    run_audit, find_stale_supersessions, find_freshness_expired,
    find_slug_collision_near_misses, render_proposals,
)
from wiki.events import append_event  # noqa: E402
from wiki.wiki_init import write_wiki  # noqa: E402


def _write_decision_page(wiki_dir: Path, slug: str, **fm) -> Path:
    """Write a kind:decision page with the required schema."""
    base = {
        "id": f"ent_{slug.replace('-', '_')[:8]}",
        "kind": "decision",
        "title": slug.replace("-", " ").title(),
        "slug": slug,
        "scope": "default",
        "schema_version": "1.1",
        "confidence": "0.85",
        "updated": "2026-05-01T00:00:00Z",
        "last_verified_at": "2026-05-01T00:00:00Z",
        "supersedes": "null",
        "superseded_by": "null",
        "valid_until": "null",
    }
    base.update(fm)
    lines = ["---"]
    for k, v in base.items():
        lines.append(f"{k}: {v}")
    lines.append("sources:")
    lines.append("  - { type: rfc, ref: docs/decision.md, ts: 2026-05-01T00:00:00Z }")
    lines.append("---")
    lines.append("")
    lines.append(f"# {base['title']}")
    lines.append("")
    target = wiki_dir / f"{slug}.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _write_concept_page(
    wiki_dir: Path, slug: str, *, body: str = "", source_type: str = "code",
    last_verified: str = "2026-05-01T00:00:00Z", source_ref: str = "src/foo.ts",
) -> Path:
    fm = {
        "id": f"ent_{slug.replace('-', '_')[:8]}",
        "kind": "concept",
        "title": slug.replace("-", " ").title(),
        "slug": slug,
        "scope": "default",
        "schema_version": "1.1",
        "confidence": "0.85",
        "updated": "2026-05-01T00:00:00Z",
        "last_verified_at": last_verified,
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines.append("sources:")
    lines.append(f"  - {{ type: {source_type}, ref: {source_ref}, ts: {last_verified} }}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {fm['title']}")
    if body:
        lines.append("")
        lines.append(body)
    lines.append("")
    target = wiki_dir / f"{slug}.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


# ── Rule 1: stale supersession ─────────────────────────────────────


class StaleSupersessionTests(unittest.TestCase):
    """AC3 from PRD: a current entity referencing a superseded decision
    must be flagged in audit/proposals.md."""

    def test_ac3_stale_reference_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()

            # Decision v1 superseded by v2
            _write_decision_page(wiki, "decision-acme-v1",
                                 superseded_by="ent_dec_v2")
            _write_decision_page(wiki, "decision-acme-v2")
            # An entity links to the SUPERSEDED v1 — should flag
            _write_concept_page(wiki, "lead-acme",
                                body="Pricing per [[decision-acme-v1]].")

            result = run_audit(brain)
            stale = result["stale_supersessions"]
            self.assertEqual(len(stale), 1)
            self.assertEqual(stale[0]["source_slug"], "lead-acme")
            self.assertEqual(stale[0]["target_slug"], "decision-acme-v1")

            proposals = (brain / "audit" / "proposals.md").read_text(encoding="utf-8")
            self.assertIn("Stale references", proposals)
            self.assertIn("lead-acme", proposals)
            self.assertIn("decision-acme-v1", proposals)

    def test_link_to_current_decision_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            _write_decision_page(wiki, "decision-acme-v2")  # current; not superseded
            _write_concept_page(wiki, "lead-acme",
                                body="Pricing per [[decision-acme-v2]].")

            result = run_audit(brain)
            self.assertEqual(result["stale_supersessions"], [])

    def test_decision_with_null_superseded_by_not_flagged(self):
        """A decision whose `superseded_by: null` is current; references to
        it must NOT be flagged."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            _write_decision_page(wiki, "decision-x", superseded_by="null")
            _write_concept_page(wiki, "y", body="See [[decision-x]].")

            result = run_audit(brain)
            self.assertEqual(result["stale_supersessions"], [])

    def test_no_decisions_no_flags(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            _write_concept_page(wiki, "a", body="See [[b]].")
            _write_concept_page(wiki, "b")

            result = run_audit(brain)
            self.assertEqual(result["stale_supersessions"], [])


# ── Rule 2: freshness expired ──────────────────────────────────────


class FreshnessExpiredTests(unittest.TestCase):
    """AC4 from PRD: entities with computed freshness < 0.3 AND elapsed
    > shortest_half_life are flagged."""

    def test_ac4_web_44_days_flagged(self):
        """Web (30d), elapsed 44 days -> score 0.267 -> flag."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            _write_concept_page(
                wiki, "competitor-acme",
                source_type="web", last_verified="2026-04-01T00:00:00Z",
            )
            now = datetime(2026, 5, 15, tzinfo=timezone.utc)
            result = run_audit(brain, now=now)
            self.assertEqual(len(result["freshness_expired"]), 1)
            f = result["freshness_expired"][0]
            self.assertEqual(f["slug"], "competitor-acme")
            self.assertLess(f["score"], 0.3)
            self.assertGreater(f["elapsed_days"], 30)

    def test_recent_page_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            _write_concept_page(
                wiki, "fresh", source_type="code",
                last_verified="2026-05-01T00:00:00Z",
            )
            now = datetime(2026, 5, 15, tzinfo=timezone.utc)
            result = run_audit(brain, now=now)
            self.assertEqual(result["freshness_expired"], [])

    def test_double_guard_low_half_life_not_flagged_within_half_life(self):
        """Email (21d half-life); elapsed=10d -> score>0.3 OR elapsed<half_life
        means NOT flagged. Verifies the double-guard against false positives."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            _write_concept_page(
                wiki, "recent-email", source_type="email",
                last_verified="2026-05-05T00:00:00Z",
            )
            now = datetime(2026, 5, 15, tzinfo=timezone.utc)  # 10 days elapsed
            result = run_audit(brain, now=now)
            self.assertEqual(result["freshness_expired"], [])

    def test_long_half_life_just_past_midpoint_not_flagged(self):
        """RFC (180d); elapsed=100d -> score=0.72 — still fresh, NOT flagged.
        Even past midpoint, the score is far above floor."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            _write_concept_page(
                wiki, "rfc-stable", source_type="rfc",
                last_verified="2026-01-01T00:00:00Z",
            )
            now = datetime(2026, 5, 15, tzinfo=timezone.utc)
            result = run_audit(brain, now=now)
            self.assertEqual(result["freshness_expired"], [])


# ── Rule 3: slug collision near-misses ─────────────────────────────


class SlugCollisionTests(unittest.TestCase):
    """Auditor surfaces wiki/_index.md collision footnotes."""

    def test_collision_footnote_surfaced(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "events").mkdir()
            # Use wiki_init to actually generate a collision via two entity_hints
            for hint, ts in [("data-processing", 1700000000),
                             ("Data_Processing", 1700001000)]:
                append_event(
                    brain / "events",
                    source_type="manual", source_ref=f"src-{hint}",
                    file_id=hint, claim="x", entity_hint=hint, ts=ts,
                )
            write_wiki(brain, now_iso="2026-05-01T00:00:00Z")

            result = run_audit(brain)
            self.assertEqual(len(result["slug_collisions"]), 1)
            c = result["slug_collisions"][0]
            self.assertEqual(c["final_slug"], "data-processing-2")
            self.assertEqual(c["original"], "data-processing")

            proposals = (brain / "audit" / "proposals.md").read_text(encoding="utf-8")
            self.assertIn("data-processing-2", proposals)


# ── Integration / rendering ────────────────────────────────────────


class AuditIntegrationTests(unittest.TestCase):
    def test_empty_brain_produces_well_formed_proposals(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            (brain / "wiki").mkdir()
            result = run_audit(brain)
            self.assertEqual(result["stale_supersessions"], [])
            self.assertEqual(result["freshness_expired"], [])
            self.assertEqual(result["slug_collisions"], [])
            proposals = (brain / "audit" / "proposals.md").read_text(encoding="utf-8")
            for section in ("Stale references", "Freshness expired",
                            "Slug collisions"):
                self.assertIn(section, proposals)
                # Each section reads "_(none)_" when empty
            self.assertIn("_(none)_", proposals)

    def test_multi_rule_all_flagged_in_one_run(self):
        """Single run surfaces flags from all three rules at once."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            (brain / "events").mkdir()

            # Stale supersession setup
            _write_decision_page(wiki, "old-decision", superseded_by="ent_new")
            _write_concept_page(wiki, "consumer", body="See [[old-decision]].")

            # Freshness expired setup
            _write_concept_page(
                wiki, "ancient-web", source_type="web",
                last_verified="2026-01-01T00:00:00Z",
            )

            # Collision footnote setup
            (wiki / "_index.md").write_text(
                "## Collision footnotes\n\n"
                "- `data-processing-2` collided with `data-processing` on 2026-05-15\n",
                encoding="utf-8",
            )

            now = datetime(2026, 5, 15, tzinfo=timezone.utc)
            result = run_audit(brain, now=now)

            self.assertEqual(len(result["stale_supersessions"]), 1)
            # ancient-web (web 30d, elapsed ~134d) flags; old-decision and
            # consumer have last_verified 2026-05-01 (fresh) so don't flag.
            self.assertGreaterEqual(len(result["freshness_expired"]), 1)
            self.assertEqual(len(result["slug_collisions"]), 1)

    def test_stale_schema_page_surfaced_as_warning(self):
        """Pages that fail validate_page() show up under "Validation warnings"
        rather than being silently dropped or crashing the run."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()
            (wiki / "stale.md").write_text(
                '---\nschema_version: "0.9"\nkind: concept\n---\n# stale\n',
                encoding="utf-8",
            )
            _write_concept_page(wiki, "ok")
            result = run_audit(brain)
            self.assertEqual(len(result["warnings"]), 1)
            self.assertIn("stale.md", result["warnings"][0])
            proposals = (brain / "audit" / "proposals.md").read_text(encoding="utf-8")
            self.assertIn("Validation warnings", proposals)


class RenderProposalsTests(unittest.TestCase):
    def test_empty_inputs_renders_clean(self):
        out = render_proposals(
            stale_supersessions=[], freshness_expired=[],
            slug_collisions=[], warnings=[],
            now_iso="2026-05-15T00:00:00Z",
        )
        self.assertIn("# audit/proposals.md", out)
        self.assertIn("Generated: 2026-05-15T00:00:00Z", out)
        # All three sections present even when empty
        for section in ("Stale references", "Freshness expired",
                        "Slug collisions"):
            self.assertIn(section, out)

    def test_warnings_section_only_when_warnings_present(self):
        out_with = render_proposals(
            stale_supersessions=[], freshness_expired=[],
            slug_collisions=[], warnings=["wiki/x.md: schema mismatch"],
        )
        self.assertIn("Validation warnings", out_with)

        out_without = render_proposals(
            stale_supersessions=[], freshness_expired=[],
            slug_collisions=[], warnings=[],
        )
        self.assertNotIn("Validation warnings", out_without)


class CodexRegressionTests(unittest.TestCase):
    """Regressions for Codex P1+P2 findings on PR #23."""

    def test_malformed_last_verified_does_not_crash_audit(self):
        """Codex P1: a single page with malformed last_verified_at must not
        abort the entire run. Surface as a warning; continue auditing."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()

            # Good page
            _write_concept_page(wiki, "good", source_type="code")

            # Bad page — invalid ISO timestamp
            (wiki / "bad.md").write_text(
                "---\n"
                "id: ent_bad\n"
                "kind: concept\n"
                "title: Bad\n"
                "slug: bad\n"
                "scope: default\n"
                "schema_version: \"1.1\"\n"
                "confidence: 0.85\n"
                "updated: 2026-05-01T00:00:00Z\n"
                "last_verified_at: NOT-A-VALID-ISO-TIMESTAMP\n"
                "sources:\n"
                "  - { type: web, ref: bad.com, ts: nope }\n"
                "---\n"
                "# Bad\n",
                encoding="utf-8",
            )

            # Audit must complete without raising
            result = run_audit(brain)
            # The bad page got skipped via warning, not crashed
            self.assertTrue(any("bad" in w for w in result["warnings"]),
                            f"expected a warning about bad.md, got: {result['warnings']}")
            # The good page still got its freshness checked (no flag because fresh)
            proposals = (brain / "audit" / "proposals.md").read_text(encoding="utf-8")
            self.assertIn("Validation warnings", proposals)

    def test_body_extraction_survives_dashes_in_frontmatter_ref(self):
        """C1 fix: body extraction via FRONTMATTER_RE (not naive split('---', 2))
        must preserve body content when a frontmatter `ref:` value contains
        the literal `---` token (Notion URLs, date ranges, etc.). Naive split
        would orphan frontmatter content into the body and the wikilink past
        the bad split point would never be found."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()

            _write_decision_page(wiki, "decision-y", superseded_by="ent_y_v2")
            _write_decision_page(wiki, "decision-y-v2")

            # Concept page with `---` inside the source ref. The pre-C1
            # body parser would chop the body at the first body `---`
            # encountered after the second outer `---`; with C1 we use
            # FRONTMATTER_RE which only consumes the OUTER block.
            slug = "consumer-with-dashes"
            tricky_ref = "https://notion.so/--Page-Title---abc123def"
            page = (
                "---\n"
                f"id: ent_{slug.replace('-', '_')[:8]}\n"
                "kind: concept\n"
                f"title: Consumer With Dashes\n"
                f"slug: {slug}\n"
                "scope: default\n"
                "schema_version: \"1.1\"\n"
                "confidence: 0.85\n"
                "updated: 2026-05-01T00:00:00Z\n"
                "last_verified_at: 2026-05-01T00:00:00Z\n"
                "sources:\n"
                "  -\n"
                "    type: notion\n"
                f"    ref: {tricky_ref}\n"
                "    ts: 2026-05-01T00:00:00Z\n"
                "---\n"
                "\n"
                "# Consumer With Dashes\n"
                "\n"
                "Refers to [[decision-y]] which is superseded.\n"
            )
            (wiki / f"{slug}.md").write_text(page, encoding="utf-8")

            result = run_audit(brain)
            stale = result["stale_supersessions"]
            self.assertEqual(
                len(stale), 1,
                f"the wikilink past the dashy ref must still be found; "
                f"got {len(stale)}: {stale}",
            )
            self.assertEqual(stale[0]["source_slug"], slug)

    def test_block_style_sources_parsed_correctly(self):
        """M4 fix: wiki_init now emits block-style YAML for sources;
        audit's _parse_source_rows must handle it. Cover the canonical
        emit shape (`  -` then `    type/ref/ts:` lines) plus a ref
        containing characters that broke the old inline regex."""
        from wiki.audit import _parse_source_rows
        text = (
            "---\n"
            "id: ent_x\n"
            "sources:\n"
            "  -\n"
            "    type: notion\n"
            "    ref: https://notion.so/page,with-comma#anchor\n"
            "    ts: 2026-05-01T00:00:00Z\n"
            "  -\n"
            "    type: code\n"
            "    ref: src/foo.ts\n"
            "    ts: 2026-05-02T00:00:00Z\n"
            "---\n"
            "# body\n"
        )
        sources = _parse_source_rows(text)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["type"], "notion")
        self.assertEqual(
            sources[0]["ref"],
            "https://notion.so/page,with-comma#anchor",
            "comma in ref must not split — block style is the whole point of M4",
        )
        self.assertEqual(sources[1]["type"], "code")

    def test_superseded_by_id_resolved_to_slug_in_report(self):
        """M5 fix: when superseded_by is an entity_id (e.g. ent_a4f3a4f3a4f3),
        the audit report should print the resolved target slug instead of
        the opaque id, so the operator can navigate to the new decision."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()

            # decision-z is superseded; superseded_by points at v2's ID
            _write_decision_page(wiki, "decision-z-v2",
                                 id="ent_decision_z_v2_aabb")
            _write_decision_page(wiki, "decision-z",
                                 superseded_by="ent_decision_z_v2_aabb")
            _write_concept_page(
                wiki, "consumer-z",
                body="Per [[decision-z]] we should...",
            )

            result = run_audit(brain)
            stale = result["stale_supersessions"]
            self.assertEqual(len(stale), 1)
            self.assertEqual(
                stale[0]["superseded_by_slug"], "decision-z-v2",
                "id must be resolved to the target page's slug",
            )

            proposals = (brain / "audit" / "proposals.md").read_text(encoding="utf-8")
            self.assertIn(
                "superseded by `decision-z-v2`", proposals,
                "report must print the slug, not the raw id",
            )
            self.assertNotIn(
                "ent_decision_z_v2_aabb", proposals,
                "the raw id should NOT leak into the operator-facing report",
            )

    def test_repeated_wikilink_in_one_page_dedupes(self):
        """Codex P2: repeated `[[same-decision]]` links in one page must
        produce ONE flag, not N."""
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td)
            wiki = brain / "wiki"
            wiki.mkdir()

            _write_decision_page(wiki, "decision-x",
                                 superseded_by="ent_x_v2")
            _write_decision_page(wiki, "decision-x-v2")
            # Body links to decision-x THREE times
            _write_concept_page(
                wiki, "consumer",
                body=("Body says [[decision-x]] then says [[decision-x]] "
                      "and again [[decision-x]] for emphasis."),
            )

            result = run_audit(brain)
            stale = result["stale_supersessions"]
            self.assertEqual(len(stale), 1,
                             f"expected 1 stale flag (deduped), got {len(stale)}: {stale}")
            self.assertEqual(stale[0]["source_slug"], "consumer")
            self.assertEqual(stale[0]["target_slug"], "decision-x")


if __name__ == "__main__":
    unittest.main()
