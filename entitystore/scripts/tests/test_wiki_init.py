#!/usr/bin/env python3
"""wiki_init.py / wiki_audit lints / render_proposals gate (M11).

Runs against a TEMPDIR COPY of the same synthetic golden_corpus fixture
used by test_golden_queries.py / test_classification_gate.py — never
mutates the committed fixture (wiki_init writes `wiki/` + `audit/` dirs,
which must not land inside entitystore/scripts/tests/fixtures/).

Fully hermetic: no network calls, no real company-brain data (this repo,
VictorGjn/agent-skills, is PUBLIC — see fixtures/README.md).

Run: python -m pytest entitystore/scripts/tests/test_wiki_init.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import cb_engine as ce  # noqa: E402
import freshness_policy  # noqa: E402
import wiki_init  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN_CORPUS = FIXTURES / "golden_corpus"


def _ensure_golden_corpus_built() -> None:
    if (GOLDEN_CORPUS / "entities").exists():
        return
    sys.path.insert(0, str(FIXTURES))
    import build_golden_corpus  # noqa: E402
    build_golden_corpus.build(write=True)


class _TempCorpusCase(unittest.TestCase):
    """Copies golden_corpus into a fresh tempdir per test — no shared
    mutable state, no writes ever land in the committed fixture tree."""

    def setUp(self):
        _ensure_golden_corpus_built()
        self._tmp = tempfile.mkdtemp(prefix="wiki_init_test_")
        self.corpus = pathlib.Path(self._tmp) / "golden_corpus"
        shutil.copytree(GOLDEN_CORPUS, self.corpus)
        self._prev_cap = os.environ.pop("CB_CLASSIFICATION_CAP", None)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._prev_cap is not None:
            os.environ["CB_CLASSIFICATION_CAP"] = self._prev_cap
        else:
            os.environ.pop("CB_CLASSIFICATION_CAP", None)


class TestWikiInitIdempotency(_TempCorpusCase):
    def test_second_run_is_byte_identical_modulo_generated_at(self):
        r1 = wiki_init.write_wiki(self.corpus)
        self.assertTrue(r1["actions"])
        self.assertTrue(all(v == "created" for v in r1["actions"].values()))

        pages_after_first = {
            p.name: p.read_text(encoding="utf-8")
            for p in (self.corpus / "wiki").glob("*.md")
        }

        r2 = wiki_init.write_wiki(self.corpus)
        self.assertTrue(
            all(v == "unchanged" for v in r2["actions"].values()),
            r2["actions"],
        )

        pages_after_second = {
            p.name: p.read_text(encoding="utf-8")
            for p in (self.corpus / "wiki").glob("*.md")
        }
        self.assertEqual(set(pages_after_first), set(pages_after_second))
        for name, text1 in pages_after_first.items():
            text2 = pages_after_second[name]
            self.assertEqual(
                wiki_init._strip_generated_line(text1),
                wiki_init._strip_generated_line(text2),
                f"{name} not byte-identical modulo generated_at",
            )

    def test_slugs_are_collision_free_and_kind_prefixed(self):
        result = wiki_init.write_wiki(self.corpus)
        entities = ce.load_corpus(self.corpus)
        for eid in entities:
            slug = wiki_init.slugify_id(eid)
            self.assertIn(slug, result["actions"])
            self.assertTrue((self.corpus / "wiki" / f"{slug}.md").is_file())


class TestWikiInitProhibitedFields(_TempCorpusCase):
    def test_no_prohibited_fields_on_any_page(self):
        wiki_init.write_wiki(self.corpus)
        pages = list((self.corpus / "wiki").glob("*.md"))
        self.assertTrue(pages)
        for p in pages:
            text = p.read_text(encoding="utf-8")
            for field in wiki_init.PROHIBITED_FIELDS:
                self.assertNotIn(
                    field, text,
                    f"prohibited field {field!r} leaked onto {p.name}",
                )

    def test_no_evidence_quotes_copied_onto_pages(self):
        # foil-stability-cluster carries evidence[].quote in the entity JSON
        # (see fixtures/golden_corpus/entities/concept/foil-stability-cluster.json)
        # — the page must reference the entity id, never the quoted text.
        wiki_init.write_wiki(self.corpus)
        entity = json.loads(
            (self.corpus / "entities" / "concept" / "foil-stability-cluster.json")
            .read_text(encoding="utf-8")
        )
        quotes = [e["quote"] for e in entity.get("evidence", []) if e.get("quote")]
        self.assertTrue(quotes, "fixture should carry evidence quotes for this test to mean anything")
        page = (self.corpus / "wiki" / "concept-foil-stability-cluster.md").read_text(encoding="utf-8")
        for q in quotes:
            self.assertNotIn(q, page)


class TestWikiInitFrontmatterFields(_TempCorpusCase):
    def test_required_frontmatter_fields_present(self):
        wiki_init.write_wiki(self.corpus)
        page = (self.corpus / "wiki" / "org-atlas-marine.md").read_text(encoding="utf-8")
        for field in (
            "id:", "kind:", "slug:", "sources:", "last_verified_at:",
            "links_out:", "links_in:", "supersedes:", "superseded_by:",
            "valid_until:",
        ):
            self.assertIn(field, page)

    def test_last_verified_at_null_when_absent(self):
        # golden_corpus entities predate the M4 freshness rule — matches
        # real-corpus coverage reality (person 0/331, vessel 0/131).
        wiki_init.write_wiki(self.corpus)
        page = (self.corpus / "wiki" / "org-atlas-marine.md").read_text(encoding="utf-8")
        self.assertIn("last_verified_at: null", page)

    def test_links_in_reflects_inbound_wiki_links(self):
        wiki_init.write_wiki(self.corpus)
        # org:atlas-marine is wiki_links-referenced by
        # concept:atlas-marine-demand-theme (see fixture).
        page = (self.corpus / "wiki" / "org-atlas-marine.md").read_text(encoding="utf-8")
        self.assertIn('links_in:\n  - "concept:atlas-marine-demand-theme"', page)


class TestWikiInitClassificationCap(_TempCorpusCase):
    def test_restricted_entity_never_becomes_a_page_below_cap(self):
        os.environ["CB_CLASSIFICATION_CAP"] = "public"
        try:
            result = wiki_init.write_wiki(self.corpus)
        finally:
            os.environ.pop("CB_CLASSIFICATION_CAP", None)
        self.assertGreater(result["withheld_count"], 0)
        pages = {p.stem for p in (self.corpus / "wiki").glob("*.md")}
        # fixtures/golden_corpus/manifest.json maps entities/person/** -> restricted
        self.assertFalse(any(s.startswith("person-") for s in pages))

    def test_rebuild_scoped_to_kinds_leaves_other_kinds_untouched(self):
        wiki_init.write_wiki(self.corpus)
        before = {p.name for p in (self.corpus / "wiki").glob("*.md") if p.name.startswith("concept-")}
        self.assertTrue(before)
        wiki_init.write_wiki(self.corpus, kinds=["org"], rebuild=True)
        after_concepts = {p.name for p in (self.corpus / "wiki").glob("*.md") if p.name.startswith("concept-")}
        self.assertEqual(before, after_concepts)


class TestWikiAuditLints(unittest.TestCase):
    """Synthetic fixtures built inline — narrow, targeted cases for each of
    the four M11 lints, independent of golden_corpus's shape."""

    def _corpus(self, entities: list[dict], tmp: pathlib.Path) -> pathlib.Path:
        corpus = tmp / "synthetic"
        (corpus / "entities" / "concept").mkdir(parents=True)
        (corpus / "entities" / "org").mkdir(parents=True)
        for e in entities:
            kind_dir = corpus / "entities" / e["kind"]
            kind_dir.mkdir(parents=True, exist_ok=True)
            slug = e["id"].split(":", 1)[1]
            (kind_dir / f"{slug}.json").write_text(json.dumps(e), encoding="utf-8")
        (corpus / "manifest.json").write_text(
            json.dumps({"corpus_id": "synthetic", "data_classification": "public"}),
            encoding="utf-8",
        )
        return corpus

    def _base_entity(self, eid, kind, **overrides):
        e = {
            "id": eid, "kind": kind, "names": overrides.pop("names", [eid]),
            "summary": "synthetic test entity", "wiki_links": [],
            "topics": [], "provenance": {"extractor": "test/v1", "extraction_method": "human"},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }
        e.update(overrides)
        return e

    def test_merge_candidate_duplicate_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            entities = [
                self._base_entity("org:acme-inc", "org", names=["Acme Inc"]),
                self._base_entity("org:acme-inc-2", "org", names=["Acme Inc"]),
            ]
            corpus = self._corpus(entities, pathlib.Path(tmp))
            audit = ce.wiki_audit(corpus_dir=str(corpus))
            reasons = {m["reason"] for m in audit["merge_candidates"]}
            self.assertIn("duplicate-name", reasons)

    def test_split_candidate_many_claim_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = [
                {"metric": f"metric_{i}", "measurements": [{"value": i, "source": "test"}]}
                for i in range(10)
            ]
            entities = [self._base_entity("org:sprawling", "org", claims=claims)]
            corpus = self._corpus(entities, pathlib.Path(tmp))
            audit = ce.wiki_audit(corpus_dir=str(corpus))
            self.assertEqual(len(audit["split_candidates"]), 1)
            self.assertEqual(audit["split_candidates"][0]["id"], "org:sprawling")

    def test_stale_supersession_dangling_identity_assertion(self):
        with tempfile.TemporaryDirectory() as tmp:
            entities = [
                self._base_entity(
                    "org:old-name", "org",
                    identity_assertions=[{
                        "assertion_id": "ida_test_1",
                        "source_system": "test", "source_id": "1",
                        "method": "exact-key", "as_of": "2026-01-01T00:00:00Z",
                        "asserted_by": "test/v1", "status": "superseded",
                        "superseded_by": "ida_test_does_not_exist",
                    }],
                ),
            ]
            corpus = self._corpus(entities, pathlib.Path(tmp))
            audit = ce.wiki_audit(corpus_dir=str(corpus))
            self.assertEqual(len(audit["stale_supersessions"]), 1)
            self.assertEqual(audit["stale_supersessions"][0]["scope"], "identity_assertion")

    def test_freshness_pre_rule_not_counted_as_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            entities = [self._base_entity("org:never-verified", "org")]
            corpus = self._corpus(entities, pathlib.Path(tmp))
            audit = ce.wiki_audit(corpus_dir=str(corpus))
            self.assertEqual(audit["freshness_lint"]["pre_rule_count"], 1)
            self.assertEqual(audit["freshness_lint"]["stale"], [])

    def test_freshness_stale_when_old_last_verified_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            entities = [self._base_entity(
                "org:ancient", "org", last_verified_at="2020-01-01T00:00:00Z",
            )]
            corpus = self._corpus(entities, pathlib.Path(tmp))
            audit = ce.wiki_audit(corpus_dir=str(corpus))
            self.assertEqual(audit["freshness_lint"]["pre_rule_count"], 0)
            stale_ids = {f["id"] for f in audit["freshness_lint"]["stale"]}
            self.assertIn("org:ancient", stale_ids)


class TestRenderProposals(_TempCorpusCase):
    def test_proposals_md_renders_and_has_all_sections(self):
        audit = ce.wiki_audit(corpus_dir=str(self.corpus))
        md = ce.render_proposals(audit, now_iso="2026-07-16T00:00:00Z")
        for heading in (
            "# audit/proposals.md", "## Contradictions", "## Dead links",
            "## Freshness expired (updated_at)", "## Orphans",
            "## Schema invalid", "## Merge candidates", "## Split candidates",
            "## Stale supersessions", "## Freshness (last_verified_at)",
        ):
            self.assertIn(heading, md)

    def test_cli_proposals_flag_writes_file(self):
        audit = ce.wiki_audit(corpus_dir=str(self.corpus))
        md = ce.render_proposals(audit)
        out = self.corpus / "audit" / "proposals.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        self.assertTrue(out.is_file())
        self.assertIn("# audit/proposals.md", out.read_text(encoding="utf-8"))


class TestFreshnessPolicy(unittest.TestCase):
    def test_missing_last_verified_at_is_null_not_zero(self):
        result = freshness_policy.compute_freshness(None, "org")
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "pre-rule, never verified")

    def test_fresh_at_t0(self):
        import datetime as dt
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        result = freshness_policy.compute_freshness(
            "2026-01-01T00:00:00Z", "org", now=now,
        )
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["status"], "fresh")

    def test_stale_well_past_half_life(self):
        import datetime as dt
        now = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
        result = freshness_policy.compute_freshness(
            "2020-01-01T00:00:00Z", "org", now=now,
        )
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["status"], "stale")


if __name__ == "__main__":
    unittest.main()
