"""wikiref.py — wiki-link grammar parser (Phase 1 of CE × lat.md interop).

Single source of truth for parsing `[[wiki-links]]` across CE. Replaces the
single-regex approach at ``audit.py:46`` with a structured parser that
returns typed ``WikiRef`` records covering three forms:

1. **Slug**: ``[[my-slug]]`` or ``[[my-slug|display]]`` — the existing
   CE form. Refers to a wiki entity by slug.
2. **Section**: ``[[my-slug#Section]]`` or ``[[my-slug#Section#Subsection]]``
   — entity slug plus an in-page heading anchor. Compatible with lat.md's
   ``[[file#section#sub]]`` syntax.
3. **Code**: ``[[src/foo.ts#symbol]]`` or ``[[src/foo.ts#Class.method]]``
   — source-file path plus a symbol anchor. Compatible with lat.md's
   ``[[src/file.ts#Symbol]]`` syntax.

The parser is **backward-compatible**: every legacy ``[[slug]]`` reference
still parses to ``WikiRef(kind="slug", target="slug")``. Existing audit
rules consume only ``kind == "slug"`` refs to preserve flag counts on the
demo brain. Phase 2 (broken-ref auditor) consumes ``kind == "section"``
and ``kind == "code"`` refs against ``code_index.json`` and the heading
walker.

Per ``plan/PRD-latmd-integration.md`` Phase 1 acceptance criteria.

Usage::

    from wiki.wikiref import parse_wikirefs, WikiRef

    refs = list(parse_wikirefs("see [[auth-middleware]] and [[src/auth.ts#validate]]"))
    # [WikiRef(kind="slug", target="auth-middleware", ...),
    #  WikiRef(kind="code", target="src/auth.ts", anchor="validate", ...)]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterator, Optional

# Code-file extensions that signal kind="code" even when the target lacks a `/`.
# Order doesn't matter — membership check only. Lowercase, dot-prefixed.
_CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".pyi", ".rb", ".rs", ".go", ".java",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp",
    ".cs", ".kt", ".kts", ".scala", ".php", ".swift",
    ".m", ".mm",
})

# Capture everything between [[ and the next ]]. The character class excludes
# `]` so `[[foo]]bar]]` matches only `foo`. Includes `|`, `#`, and `/` —
# those split the captured text inside parse_wikiref().
_WIKIREF_RE = re.compile(r"\[\[([^\]]+?)\]\]")


@dataclass(frozen=True)
class WikiRef:
    """One parsed wiki-link reference.

    Fields:
        kind: ``"slug"``, ``"section"``, or ``"code"``.
        target: the slug (kind=slug/section) or path (kind=code).
        anchor: section heading or symbol name; ``None`` for plain slug refs.
        sub_anchor: the second ``#`` segment (kind=section only); ``None`` otherwise.
        display: optional display text from ``[[ref|display]]``; ``None`` otherwise.
        raw: the original ``[[...]]`` text including brackets, for telemetry / error messages.
    """
    kind: str
    target: str
    anchor: Optional[str] = None
    sub_anchor: Optional[str] = None
    display: Optional[str] = None
    raw: str = ""

    @property
    def slug(self) -> str:
        """Best-effort slug for the ref.

        For ``kind == "slug"`` or ``kind == "section"``, this is the target
        verbatim. For ``kind == "code"``, this is the path's basename without
        extension — useful when an audit rule needs to look up a wiki entity
        derived from a code file (Phase 3 ``kind: code`` entities).
        """
        if self.kind == "code":
            return PurePosixPath(self.target).stem
        return self.target


def _looks_like_code_path(target: str) -> bool:
    """Heuristic: target is a code reference if it contains a path separator
    OR ends with a known code-file extension.

    Trade-off: a wiki page literally titled ``foo.py`` would parse as ``code``.
    Acceptable — wiki slugs don't carry file extensions in practice (slugify
    strips dots), so this is a vanishingly rare collision and the existing
    ``[[slug]]`` form already disambiguates by omitting the dot.
    """
    if "/" in target:
        return True
    lower = target.lower()
    dot = lower.rfind(".")
    if dot < 0:
        return False
    return lower[dot:] in _CODE_EXTENSIONS


def parse_wikiref(inside: str, *, raw: str = "") -> Optional[WikiRef]:
    """Parse a single wiki-link's interior (text between ``[[`` and ``]]``).

    Returns ``None`` if the syntax is malformed (empty target after
    stripping). Idempotent: ``parse_wikiref(parse_wikiref(x).raw[2:-2])``
    yields the same ref shape.
    """
    text = inside.strip()
    if not text:
        return None

    # Split off |display first — display text may contain `#` or `/` that
    # would otherwise confuse target/anchor splitting.
    if "|" in text:
        ref_part, _, display_part = text.partition("|")
        display: Optional[str] = display_part.strip() or None
    else:
        ref_part = text
        display = None

    parts = ref_part.split("#")
    target = parts[0].strip()
    if not target:
        return None
    anchors = [p.strip() for p in parts[1:] if p.strip()]

    if _looks_like_code_path(target):
        return WikiRef(
            kind="code",
            target=target,
            anchor=anchors[0] if anchors else None,
            sub_anchor=None,  # code refs don't use sub-anchors; multi-`#` collapses
            display=display,
            raw=raw or f"[[{inside}]]",
        )
    if anchors:
        return WikiRef(
            kind="section",
            target=target,
            anchor=anchors[0],
            sub_anchor=anchors[1] if len(anchors) > 1 else None,
            display=display,
            raw=raw or f"[[{inside}]]",
        )
    return WikiRef(
        kind="slug",
        target=target,
        anchor=None,
        sub_anchor=None,
        display=display,
        raw=raw or f"[[{inside}]]",
    )


def parse_wikirefs(text: str) -> Iterator[WikiRef]:
    """Yield every WikiRef found in ``text``, in order of appearance.

    Malformed refs (empty target) are silently skipped — the regex is
    permissive on purpose; downstream consumers (audit rules, MCP tools)
    treat unrecognized targets as broken refs in their own error path.
    """
    for m in _WIKIREF_RE.finditer(text):
        ref = parse_wikiref(m.group(1), raw=m.group(0))
        if ref is not None:
            yield ref


def format_wikiref(
    *,
    kind: str,
    target: str,
    anchor: Optional[str] = None,
    sub_anchor: Optional[str] = None,
    display: Optional[str] = None,
) -> str:
    """Render a WikiRef tuple back to ``[[...]]`` markdown text.

    Round-trip property: ``format_wikiref(**asdict(ref))`` returns text that
    parses back to the same ref (modulo whitespace normalization).
    """
    if kind == "code":
        # Code refs ignore sub_anchor by construction (see parse_wikiref).
        body = f"{target}#{anchor}" if anchor else target
    elif kind == "section":
        if not anchor:
            body = target
        elif sub_anchor:
            body = f"{target}#{anchor}#{sub_anchor}"
        else:
            body = f"{target}#{anchor}"
    else:  # slug
        body = target
    if display:
        return f"[[{body}|{display}]]"
    return f"[[{body}]]"
