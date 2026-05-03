"""End-to-end tests for scripts/wiki/lat_check.py + broken-ref auditor.

Phase 2 of CE x lat.md interop. Validates PRD AC2:

> Given a brain with 3 deliberately-broken refs (missing file, wrong
> symbol, wrong section), when ``lat_check.py --brain ./brain --strict``
> is invoked, then it exits 1 and ``audit/proposals.md`` lists all 3
> broken refs by location + reason.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.audit import find_broken_refs  # noqa: E402
from wiki.code_index import build_code_index  # noqa: E402
from wiki.lat_check import main as lat_check_main  # noqa: E402


# Canonical 1.1-schema page header used in fixtures.
_HEADER = (
    "---\n"
    "id: ent_test1234\n"
    "kind: concept\n"
    "title: {title}\n"
    "slug: {slug}\n"
    "scope: default\n"
    "schema_version: \"1.1\"\n"
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
    """Seed a minimal brain with three pages and three deliberately broken refs."""
    wiki = brain / "wiki"
    wiki.mkdir(parents=True)

    # Page 1: target page that will be referenced (exists).
    (wiki / "auth-middleware.md").write_text(
        _HEADER.format(title="Auth Middleware", slug="auth-middleware")
        + "# Auth Middleware\n\n## Claims\n\n## OAuth Flow\n\n"
        "Some content about OAuth.\n",
        encoding="utf-8",
    )

    # Page 2: source page with three deliberately broken refs.
    body = (
        "# Has Broken Refs\n\n"
        "## Claims\n\n"
        "- Healthy ref to [[auth-middleware]].\n"
        "- Healthy section ref [[auth-middleware#OAuth Flow]].\n"
        "- BROKEN missing-page slug: [[ghost-page]].\n"
        "- BROKEN missing-section: [[auth-middleware#Phantom Section]].\n"
        "- BROKEN missing-symbol: [[src/auth.ts#nonexistent_symbol]].\n"
    )
    (wiki / "has-broken-refs.md").write_text(
        _HEADER.format(title="Has Broken Refs", slug="has-broken-refs") + body,
        encoding="utf-8",
    )


def _seed_codebase(repo: Path) -> None:
    """Seed a tiny codebase the broken-symbol ref will fail against."""
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "auth.ts").write_text(
        "export function validateToken(t: string) { return true; }\n"
        "function helper() {}\n",
        encoding="utf-8",
    )


class FindBrokenRefsTests(unittest.TestCase):
    """Direct unit tests on ``find_broken_refs``."""

    def test_three_broken_refs_each_reason(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            repo = Path(td) / "repo"
            _seed_brain(brain)
            _seed_codebase(repo)
            code_index = build_code_index(repo)

            # Load pages via the audit's own helper to mirror runtime path.
            from wiki.audit import _load_pages
            pages, _warnings = _load_pages(brain / "wiki")
            flags = find_broken_refs(pages, code_index=code_index)

            reasons = sorted(f["reason"] for f in flags)
            self.assertEqual(
                reasons,
                ["page_not_found", "section_not_found", "symbol_not_found"],
                f"expected 3 distinct broken-ref reasons, got: {reasons}",
            )

    def test_no_code_index_skips_code_refs(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            _seed_brain(brain)

            from wiki.audit import _load_pages
            pages, _ = _load_pages(brain / "wiki")
            flags = find_broken_refs(pages, code_index=None)

            # Without a code_index we only catch the slug + section breaks.
            kinds = sorted({f["kind"] for f in flags})
            self.assertEqual(kinds, ["section", "slug"])

    def test_clean_brain_no_flags(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            wiki = brain / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "a.md").write_text(
                _HEADER.format(title="A", slug="a") + "# A\n\nLink to [[b]].",
                encoding="utf-8",
            )
            (wiki / "b.md").write_text(
                _HEADER.format(title="B", slug="b") + "# B\n\nNo refs.",
                encoding="utf-8",
            )

            from wiki.audit import _load_pages
            pages, _ = _load_pages(wiki)
            self.assertEqual(find_broken_refs(pages), [])


class LatCheckCLITests(unittest.TestCase):
    """End-to-end CLI invocations."""

    def test_strict_exits_1_when_broken(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            repo = Path(td) / "repo"
            _seed_brain(brain)
            _seed_codebase(repo)

            buf_err = io.StringIO()
            buf_out = io.StringIO()
            with redirect_stderr(buf_err), redirect_stdout(buf_out):
                rc = lat_check_main([
                    "--brain", str(brain),
                    "--code-root", str(repo),
                    "--strict",
                ])
            self.assertEqual(rc, 1)

            stderr = buf_err.getvalue()
            self.assertIn("3 broken reference", stderr)
            self.assertIn("page_not_found".replace("_", " "), stderr)
            self.assertIn("section_not_found".replace("_", " "), stderr)
            self.assertIn("symbol_not_found".replace("_", " "), stderr)

            # AC2: proposals.md must list all 3 broken refs.
            proposals = (brain / "audit" / "proposals.md").read_text(encoding="utf-8")
            self.assertIn("Broken refs", proposals)
            self.assertIn("ghost-page", proposals)
            self.assertIn("Phantom Section", proposals)
            self.assertIn("nonexistent_symbol", proposals)

    def test_non_strict_exits_0_with_findings(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            repo = Path(td) / "repo"
            _seed_brain(brain)
            _seed_codebase(repo)

            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = lat_check_main([
                    "--brain", str(brain),
                    "--code-root", str(repo),
                ])
            self.assertEqual(rc, 0)

    def test_strict_exits_0_when_clean(self):
        with tempfile.TemporaryDirectory() as td:
            brain = Path(td) / "brain"
            wiki = brain / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "a.md").write_text(
                _HEADER.format(title="A", slug="a") + "# A\n\nNo refs.",
                encoding="utf-8",
            )
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = lat_check_main(["--brain", str(brain), "--strict"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
