"""Unit tests for scripts/wiki/wikiref.py.

Phase 1 of CE x lat.md interop. Covers PRD acceptance criteria:

- Given 30+ fixture strings spanning all 3 forms (slug, section, code) plus
  unicode and pipe-display variants, parse_wikirefs returns the correct
  WikiRef list with no false positives or negatives.
- Format helper round-trips: format_wikiref(**asdict(ref)) parses back to
  the same ref shape.
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import asdict
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.wikiref import (  # noqa: E402
    WikiRef,
    parse_wikiref,
    parse_wikirefs,
    format_wikiref,
)


class ParseWikirefSlugTests(unittest.TestCase):
    """Plain slug refs — the legacy CE form. Backward compat invariant."""

    def test_simple_slug(self):
        r = parse_wikiref("auth-middleware")
        self.assertEqual(r.kind, "slug")
        self.assertEqual(r.target, "auth-middleware")
        self.assertIsNone(r.anchor)
        self.assertIsNone(r.sub_anchor)
        self.assertIsNone(r.display)

    def test_slug_with_display(self):
        r = parse_wikiref("auth-middleware|the auth layer")
        self.assertEqual(r.kind, "slug")
        self.assertEqual(r.target, "auth-middleware")
        self.assertEqual(r.display, "the auth layer")

    def test_slug_with_unicode(self):
        r = parse_wikiref("café-notes")
        self.assertEqual(r.kind, "slug")
        self.assertEqual(r.target, "café-notes")

    def test_slug_with_unicode_display(self):
        r = parse_wikiref("résumé|Curriculum Vitæ")
        self.assertEqual(r.kind, "slug")
        self.assertEqual(r.target, "résumé")
        self.assertEqual(r.display, "Curriculum Vitæ")

    def test_slug_strips_whitespace(self):
        r = parse_wikiref("  auth-middleware  ")
        self.assertEqual(r.target, "auth-middleware")

    def test_empty_returns_none(self):
        self.assertIsNone(parse_wikiref(""))
        self.assertIsNone(parse_wikiref("   "))
        self.assertIsNone(parse_wikiref("|display-only"))


class ParseWikirefSectionTests(unittest.TestCase):
    """Section refs — entity slug + heading anchor (lat.md form 1)."""

    def test_section_single_anchor(self):
        r = parse_wikiref("auth-middleware#OAuth Flow")
        self.assertEqual(r.kind, "section")
        self.assertEqual(r.target, "auth-middleware")
        self.assertEqual(r.anchor, "OAuth Flow")
        self.assertIsNone(r.sub_anchor)

    def test_section_double_anchor(self):
        r = parse_wikiref("auth-middleware#OAuth Flow#Refresh Tokens")
        self.assertEqual(r.kind, "section")
        self.assertEqual(r.target, "auth-middleware")
        self.assertEqual(r.anchor, "OAuth Flow")
        self.assertEqual(r.sub_anchor, "Refresh Tokens")

    def test_section_with_display(self):
        r = parse_wikiref("auth-middleware#OAuth Flow|the flow")
        self.assertEqual(r.kind, "section")
        self.assertEqual(r.target, "auth-middleware")
        self.assertEqual(r.anchor, "OAuth Flow")
        self.assertEqual(r.display, "the flow")

    def test_section_with_pipe_in_display(self):
        # |display has higher precedence than #anchor splitting (we partition
        # on `|` first). Ensures display containing `#` survives.
        r = parse_wikiref("auth-middleware#flow|see #1234")
        self.assertEqual(r.kind, "section")
        self.assertEqual(r.target, "auth-middleware")
        self.assertEqual(r.anchor, "flow")
        self.assertEqual(r.display, "see #1234")

    def test_section_extra_anchors_dropped(self):
        # Three or more `#` segments: only first two land in anchor/sub_anchor.
        r = parse_wikiref("a#b#c#d")
        self.assertEqual(r.kind, "section")
        self.assertEqual(r.target, "a")
        self.assertEqual(r.anchor, "b")
        self.assertEqual(r.sub_anchor, "c")
        # `d` deliberately dropped — section refs are at most 2-deep.

    def test_section_unicode_anchor(self):
        r = parse_wikiref("notes#Café résumé")
        self.assertEqual(r.kind, "section")
        self.assertEqual(r.anchor, "Café résumé")


class ParseWikirefCodeTests(unittest.TestCase):
    """Code refs — source-file path + symbol anchor (lat.md form 2)."""

    def test_code_typescript(self):
        r = parse_wikiref("src/auth.ts#validateToken")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.target, "src/auth.ts")
        self.assertEqual(r.anchor, "validateToken")

    def test_code_python(self):
        r = parse_wikiref("scripts/wiki/wiki_init.py#consolidate")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.target, "scripts/wiki/wiki_init.py")
        self.assertEqual(r.anchor, "consolidate")

    def test_code_dotted_symbol(self):
        # Class.method form — anchor preserves the dot.
        r = parse_wikiref("src/foo.ts#AuthGuard.validate")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.anchor, "AuthGuard.validate")

    def test_code_no_symbol(self):
        # Path with no anchor — kind=code (path heuristic) with anchor=None.
        r = parse_wikiref("src/foo.ts")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.target, "src/foo.ts")
        self.assertIsNone(r.anchor)

    def test_code_extension_only_no_slash(self):
        # `Component.tsx` (no `/`) still parses as code via extension heuristic.
        r = parse_wikiref("Component.tsx#render")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.target, "Component.tsx")
        self.assertEqual(r.anchor, "render")

    def test_code_nested_path(self):
        r = parse_wikiref("apps/web/src/app/page.tsx#Page")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.target, "apps/web/src/app/page.tsx")
        self.assertEqual(r.anchor, "Page")

    def test_code_extra_anchors_collapse(self):
        # Code refs ignore sub-anchors by construction.
        r = parse_wikiref("src/foo.ts#Class#method")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.anchor, "Class")
        self.assertIsNone(r.sub_anchor)

    def test_code_with_display(self):
        r = parse_wikiref("src/auth.ts#validateToken|the validator")
        self.assertEqual(r.kind, "code")
        self.assertEqual(r.target, "src/auth.ts")
        self.assertEqual(r.anchor, "validateToken")
        self.assertEqual(r.display, "the validator")

    def test_slug_property_on_code(self):
        # `slug` is the basename without extension — useful when an audit
        # rule needs a slug-shaped key from a code ref.
        r = parse_wikiref("src/auth.ts#validateToken")
        self.assertEqual(r.slug, "auth")


class ParseWikirefsBatchTests(unittest.TestCase):
    """parse_wikirefs(text) — yields all refs in document order."""

    def test_finds_all_three_kinds_in_one_doc(self):
        text = """
        See [[auth-middleware]] for the design.
        Specifically [[auth-middleware#OAuth Flow]].
        Implementation in [[src/auth.ts#validateToken]].
        """
        refs = list(parse_wikirefs(text))
        self.assertEqual(len(refs), 3)
        self.assertEqual(refs[0].kind, "slug")
        self.assertEqual(refs[1].kind, "section")
        self.assertEqual(refs[2].kind, "code")

    def test_skips_malformed_refs(self):
        text = "[[]] [[ ]] [[|display-only]] [[valid]]"
        refs = list(parse_wikirefs(text))
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].target, "valid")

    def test_no_match_in_plain_text(self):
        self.assertEqual(list(parse_wikirefs("just plain markdown")), [])

    def test_handles_adjacent_refs(self):
        text = "[[a]][[b]][[c]]"
        targets = [r.target for r in parse_wikirefs(text)]
        self.assertEqual(targets, ["a", "b", "c"])

    def test_handles_nested_brackets_safely(self):
        # `]` inside the inner content terminates the match — first balanced
        # pair wins.
        text = "[[foo]] then [[bar]]baz"
        refs = list(parse_wikirefs(text))
        self.assertEqual([r.target for r in refs], ["foo", "bar"])

    def test_raw_field_preserved(self):
        text = "see [[auth-middleware|the layer]]"
        refs = list(parse_wikirefs(text))
        self.assertEqual(refs[0].raw, "[[auth-middleware|the layer]]")


class FormatWikirefRoundTripTests(unittest.TestCase):
    """format_wikiref(...) round-trips through parse_wikiref."""

    def _round_trip(self, original: WikiRef) -> WikiRef:
        rendered = format_wikiref(
            kind=original.kind,
            target=original.target,
            anchor=original.anchor,
            sub_anchor=original.sub_anchor,
            display=original.display,
        )
        # Strip [[ ]] for the inside parser
        inside = rendered[2:-2]
        return parse_wikiref(inside, raw=rendered)

    def test_slug_round_trip(self):
        original = WikiRef(kind="slug", target="auth-middleware", raw="[[auth-middleware]]")
        rt = self._round_trip(original)
        self.assertEqual(rt.kind, "slug")
        self.assertEqual(rt.target, "auth-middleware")

    def test_slug_with_display_round_trip(self):
        original = WikiRef(kind="slug", target="auth", display="auth layer", raw="")
        rt = self._round_trip(original)
        self.assertEqual(rt.target, "auth")
        self.assertEqual(rt.display, "auth layer")

    def test_section_round_trip(self):
        original = WikiRef(kind="section", target="auth", anchor="OAuth Flow", raw="")
        rt = self._round_trip(original)
        self.assertEqual(rt.kind, "section")
        self.assertEqual(rt.anchor, "OAuth Flow")

    def test_section_double_anchor_round_trip(self):
        original = WikiRef(
            kind="section", target="auth", anchor="OAuth", sub_anchor="Refresh", raw=""
        )
        rt = self._round_trip(original)
        self.assertEqual(rt.kind, "section")
        self.assertEqual(rt.sub_anchor, "Refresh")

    def test_code_round_trip(self):
        original = WikiRef(kind="code", target="src/auth.ts", anchor="validate", raw="")
        rt = self._round_trip(original)
        self.assertEqual(rt.kind, "code")
        self.assertEqual(rt.target, "src/auth.ts")
        self.assertEqual(rt.anchor, "validate")


class BackwardCompatibilityTests(unittest.TestCase):
    """The old ``_WIKILINK_RE = re.compile(r"\\[\\[([^\\]|]+?)(?:\\|[^\\]]+)?\\]\\]")``
    consumed `[[slug]]` and `[[slug|display]]`. Every input that matched the
    old regex must still parse to a slug-kind WikiRef with the same target.
    """

    def test_legacy_simple(self):
        self.assertEqual(parse_wikiref("auth-middleware").target, "auth-middleware")

    def test_legacy_with_display(self):
        r = parse_wikiref("auth-middleware|display text")
        self.assertEqual(r.target, "auth-middleware")
        self.assertEqual(r.display, "display text")

    def test_legacy_dash_underscore_digits(self):
        for slug in ("ent-a4f3", "user_profile", "abc123", "v2-design"):
            with self.subTest(slug=slug):
                r = parse_wikiref(slug)
                self.assertEqual(r.kind, "slug")
                self.assertEqual(r.target, slug)


if __name__ == "__main__":
    unittest.main()
