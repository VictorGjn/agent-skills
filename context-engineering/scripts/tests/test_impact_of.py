"""Tests for wiki.impact_of.

Builds tiny synthetic brains in tmp dirs and asserts BFS / hub-stop /
supersession / error-path behaviors. No fixtures on disk; everything is
in-memory and deterministic.

Per ``plan/proposals/wiki-impact-of.md`` v0.1 acceptance criteria.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.impact_of import compute_impact, render_markdown  # noqa: E402


# Schema-1.2-compliant frontmatter template. Keeps tests focused on graph
# behavior rather than schema details — the validator is exercised in
# test_wiki_p3.py.
_TS = "2026-04-30T00:00:00Z"


def _page(slug: str, *, kind: str = "concept", body: str = "",
          superseded_by: str | None = None,
          title: str | None = None,
          scope: str = "default") -> str:
    title = title or slug
    fm = [
        "---",
        f"id: ent_{slug.replace('-', '_')[:12]:0<12}"[:20],
        f"kind: {kind}",
        f"title: {title}",
        f"slug: {slug}",
        f"scope: {scope}",
        'schema_version: "1.2"',
        "confidence: 0.7",
        f"updated: {_TS}",
        f"last_verified_at: {_TS}",
        "sources:",
        "  -",
        "    type: code",
        f"    ref: src/{slug}.ts",
        f"    ts: {_TS}",
    ]
    if kind == "decision":
        fm.append(f"supersedes: {'null'}")
        fm.append(f"superseded_by: {superseded_by or 'null'}")
        fm.append("valid_until: null")
    fm.append("---")
    fm.append("")
    fm.append(body)
    return "\n".join(fm)


def _write_brain(tmpdir: Path, pages: dict[str, str]) -> Path:
    wiki = tmpdir / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    for slug, content in pages.items():
        (wiki / f"{slug}.md").write_text(content, encoding="utf-8")
    return tmpdir


class DirectMentionsTests(unittest.TestCase):
    def test_one_hop_mention(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "auth": _page("auth"),
                "token-store": _page(
                    "token-store",
                    body="Used by [[auth]] for refresh tokens.",
                ),
            })
            result = compute_impact("auth", brain, max_hops=3)
            self.assertIsNone(result.error)
            self.assertEqual(result.entity_slug, "auth")
            slugs = [a.slug for a in result.affected]
            self.assertIn("token-store", slugs)
            ts = next(a for a in result.affected if a.slug == "token-store")
            self.assertEqual(ts.hops, 1)
            self.assertEqual(ts.edge_kinds, ("mentions",))

    def test_no_inbound(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "isolated": _page("isolated"),
            })
            result = compute_impact("isolated", brain)
            self.assertIsNone(result.error)
            self.assertEqual(len(result.affected), 0)


class MultiHopTests(unittest.TestCase):
    def test_two_hop_chain(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "x": _page("x"),
                "y": _page("y", body="See [[x]]."),
                "z": _page("z", body="Depends on [[y]]."),
            })
            result = compute_impact("x", brain, max_hops=2)
            self.assertIsNone(result.error)
            slugs_by_hop = {a.slug: a.hops for a in result.affected}
            self.assertEqual(slugs_by_hop.get("y"), 1)
            self.assertEqual(slugs_by_hop.get("z"), 2)

    def test_max_hops_caps_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "x": _page("x"),
                "y": _page("y", body="See [[x]]."),
                "z": _page("z", body="Depends on [[y]]."),
                "w": _page("w", body="Refs [[z]]."),
            })
            result = compute_impact("x", brain, max_hops=2)
            slugs = {a.slug for a in result.affected}
            self.assertIn("y", slugs)
            self.assertIn("z", slugs)
            self.assertNotIn("w", slugs)


class SupersessionTests(unittest.TestCase):
    def test_superseded_by_edge(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "decision-old": _page(
                    "decision-old", kind="decision",
                    superseded_by="decision-new",
                ),
                "decision-new": _page(
                    "decision-new", kind="decision",
                    body="Replaces [[decision-old]].",
                ),
            })
            # impact_of("decision-old") should pick up decision-new via
            # mentions and via the supersession edge.
            result = compute_impact("decision-old", brain)
            slugs = {a.slug for a in result.affected}
            self.assertIn("decision-new", slugs)


class HubStopTests(unittest.TestCase):
    def test_hub_excluded_from_traversal(self):
        # Build: x -> hub -> y
        # hub has 12 inbound mentions (above default threshold of 10),
        # so we should reach hub at hop=1 (direct mention) but NOT
        # traverse through it to y.
        with tempfile.TemporaryDirectory() as td:
            pages: dict[str, str] = {
                "x": _page("x"),
                "hub": _page("hub", body="Refs [[x]]."),
                "y": _page("y", body="Depends on [[hub]]."),
            }
            # 11 noise pages all mentioning hub to push it over threshold.
            for i in range(11):
                slug = f"noise-{i}"
                pages[slug] = _page(slug, body=f"Mentions [[hub]].")
            brain = _write_brain(Path(td), pages)

            result = compute_impact("x", brain, max_hops=3)
            slugs = {a.slug for a in result.affected}
            self.assertIn("hub", slugs)
            self.assertNotIn("y", slugs,
                "y should be unreachable: hub stop-list should block traversal through hub")
            self.assertEqual(result.recall, "best-effort")
            hub_slugs = {h[0] for h in result.skipped_hubs}
            self.assertIn("hub", hub_slugs)

    def test_include_hubs_bypasses(self):
        with tempfile.TemporaryDirectory() as td:
            pages = {
                "x": _page("x"),
                "hub": _page("hub", body="Refs [[x]]."),
                "y": _page("y", body="Depends on [[hub]]."),
            }
            for i in range(11):
                slug = f"noise-{i}"
                pages[slug] = _page(slug, body=f"Mentions [[hub]].")
            brain = _write_brain(Path(td), pages)

            result = compute_impact("x", brain, max_hops=3, include_hubs=True)
            slugs = {a.slug for a in result.affected}
            self.assertIn("y", slugs, "include_hubs should let traversal go through hub")
            self.assertEqual(result.recall, "100%")


class ErrorPathTests(unittest.TestCase):
    def test_unknown_entity(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "auth-middleware": _page("auth-middleware"),
            })
            result = compute_impact("auth", brain)  # partial match
            self.assertEqual(result.error, "ENTITY_NOT_FOUND")
            self.assertIn("auth-middleware", result.error_detail or [])

    def test_brain_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            result = compute_impact("anything", Path(td))
            self.assertEqual(result.error, "BRAIN_NOT_FOUND")

    def test_resolve_by_title(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "auth-mw": _page("auth-mw", title="Authentication Middleware"),
                "token": _page(
                    "token", body="Used by [[auth-mw]].",
                ),
            })
            # Title lookup with case-insensitive match should resolve.
            result = compute_impact("authentication middleware", brain)
            self.assertIsNone(result.error)
            self.assertEqual(result.entity_slug, "auth-mw")


class CycleTests(unittest.TestCase):
    def test_cycle_terminates(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "a": _page("a", body="Refs [[b]]."),
                "b": _page("b", body="Refs [[a]]."),
            })
            result = compute_impact("a", brain, max_hops=10)
            self.assertIsNone(result.error)
            slugs = [a.slug for a in result.affected]
            self.assertEqual(slugs.count("b"), 1, "cycle should not duplicate b")


class RenderMarkdownTests(unittest.TestCase):
    def test_renders_table(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _write_brain(Path(td), {
                "x": _page("x"),
                "y": _page("y", body="See [[x]]."),
            })
            result = compute_impact("x", brain)
            md = render_markdown(result, budget=8000)
            self.assertIn("## x", md)
            self.assertIn("y |", md)
            self.assertIn("Affected entities", md)
            self.assertIn("recall: 100%", md)


if __name__ == "__main__":
    unittest.main()
