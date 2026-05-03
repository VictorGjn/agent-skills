"""audit.py — Auditor v1 (M4 in plan/prd-closed-loop.md, the cannonball complement).

Reads wiki/<slug>.md + freshness_policy + supersedes chains, writes
``audit/proposals.md`` with three rules:

1. Stale supersession — entities still linking to a decision whose
   ``superseded_by`` chain has moved on (structural graph walk; no NLI).
2. Freshness expired — entities whose computed freshness is below 0.3
   AND whose last_verified_at is older than the source-type's half-life
   (double-guarded per phase-1.md §1.7).
3. Slug-collision near-misses — entries previously recorded in
   wiki/_index.md by wiki_init.py; surfaced for operator review (merge
   vs. rename decision).

Per ``plan/phases/phase-1.md`` §1.7 + ``plan/prd-closed-loop.md`` M4.

CLI:
    python3 scripts/wiki/audit.py --brain /path/to/brain

The MCP wrapper (S3 ``wiki.audit``) reads the produced proposals.md and
returns its content; this script is the cron-driven runner.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from .validate_page import validate_page, ValidationError
    from .freshness_policy import (
        compute_freshness_multi_source, half_life_days, shortest_half_life,
    )
    from .wikiref import parse_wikirefs
except ImportError:  # script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from wiki.validate_page import validate_page, ValidationError
    from wiki.freshness_policy import (
        compute_freshness_multi_source, half_life_days, shortest_half_life,
    )
    from wiki.wikiref import parse_wikirefs


# Wiki-link extraction is now handled by wikiref.parse_wikirefs (Phase 1 of
# CE x lat.md interop). The legacy `[[slug]]` / `[[slug|display]]` form is
# returned as `WikiRef(kind="slug", ...)`; new forms `[[slug#section]]`
# (kind=section) and `[[src/file#symbol]]` (kind=code) are also recognized.
# Existing audit rules consume only kind="slug" refs to preserve flag
# counts. Phase 2's broken-ref rule consumes section/code refs.
# Frontmatter delimiter: must mirror validate_page._FRONTMATTER_RE so the
# body extraction is robust to `---` characters appearing inside frontmatter
# values (e.g., Notion URLs like `notion.so/--Page---abc123`, date ranges).
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Source-row parser for the block-style YAML wiki_init.py emits post-M4:
#   sources:
#     - type: code
#       ref: src/foo.ts
#       ts: 2026-04-01T00:00:00Z
# The legacy inline shape `- { type: ..., ref: ..., ts: ... }` is still
# accepted for backwards compat with pages written before M4 lands.
_SOURCE_BLOCK_DASH_RE = re.compile(r"^\s*-\s*$")
_SOURCE_BLOCK_KV_RE = re.compile(r"^\s+(type|ref|ts):\s*(.+?)\s*$")
_SOURCE_INLINE_RE = re.compile(
    r"^\s*-\s*\{\s*type:\s*([^,}\s]+)\s*,\s*ref:\s*([^,}]+?)\s*"
    r"(?:,\s*ts:\s*([^,}\s]+))?\s*\}",
)
# Freshness threshold from phase-1.md §1.7: flag below 0.3 AND elapsed > half_life.
_FRESHNESS_FLOOR = 0.3
# Path of the proposals output relative to the brain root.
_PROPOSALS_RELPATH = Path("audit") / "proposals.md"


def _load_pages(wiki_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """Parse all wiki/<slug>.md (excluding _-prefixed) frontmatter + body.

    Returns:
        (pages, warnings) where pages maps slug -> {fm: dict, body: str, path: Path}
        and warnings is a list of human-readable issues (e.g., stale schema)
        that the Auditor surfaces but doesn't crash on.
    """
    pages: dict[str, dict] = {}
    warnings: list[str] = []
    if not wiki_dir.exists():
        return pages, warnings
    for path in sorted(wiki_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        # C2 fix: read the file ONCE and pass the cached text to every
        # downstream extractor. Three independent reads (validate_page,
        # body split, _parse_source_rows) opened a TOCTOU window where a
        # concurrent wiki_init / --rebuild could leave fm, body, and
        # sources desynchronized. Read-once also halves I/O.
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            warnings.append(f"skipped {path.name}: read failed ({e})")
            continue
        try:
            fm = validate_page(path, text=text)
        except ValidationError as e:
            warnings.append(f"skipped {path.name}: {e}")
            continue
        slug = fm.get("slug") or path.stem
        # C1 fix: extract body via the same frontmatter regex validate_page
        # uses. Naive `text.split("---", 2)[-1]` orphans frontmatter
        # content into the body when a value (e.g., a Notion URL ref)
        # contains `---`, which silently drops `[[wikilinks]]` past the
        # bad split point and produces false negatives in the audit.
        fm_match = _FRONTMATTER_RE.match(text)
        body = text[fm_match.end():] if fm_match else ""
        sources = _parse_source_rows(text)
        pages[slug] = {
            "fm": fm,
            "body": body,
            "path": path,
            "sources": sources,
        }
    return pages, warnings


def _parse_source_rows(text: str) -> list[dict]:
    """Walk the frontmatter for `sources:` block rows and parse type/ref/ts.

    Accepts both the block-style YAML wiki_init emits post-M4 and the
    legacy inline ``- { type: ..., ref: ..., ts: ... }`` shape for any
    pages written before the M4 cutover. M6 fix: closing-`---` while
    inside the sources block ends parsing unconditionally — the prior
    `and out` guard left in_sources=True past frontmatter when the
    sources block was empty, leaking body bullets into source-types.
    """
    out: list[dict] = []
    in_sources = False
    current: dict[str, str] | None = None  # block-style: row in progress
    for line in text.splitlines():
        if line.startswith("---") and in_sources:
            # M6 fix: closing frontmatter delimiter ALWAYS ends the
            # sources block, even when zero rows were parsed.
            if current:
                out.append(current)
                current = None  # prevent post-loop tail append
            break
        if line.startswith("sources:"):
            in_sources = True
            continue
        if not in_sources:
            continue
        # Top-level (un-indented, non-empty) frontmatter key ends the block.
        if line.strip() and not line.startswith((" ", "\t")):
            if current:
                out.append(current)
                current = None
            in_sources = False
            continue
        # Try inline shape first — it's the legacy format and matches in
        # one line, no state machine needed.
        m_inline = _SOURCE_INLINE_RE.match(line)
        if m_inline:
            out.append({
                "type": m_inline.group(1),
                "ref": m_inline.group(2),
                "ts": m_inline.group(3) or "",
            })
            continue
        # Block style: `- ` opens a new row, then `<indent>key: value`
        # lines populate it until the next `- ` or block end.
        if _SOURCE_BLOCK_DASH_RE.match(line):
            if current:
                out.append(current)
            current = {"type": "", "ref": "", "ts": ""}
            continue
        m_kv = _SOURCE_BLOCK_KV_RE.match(line)
        if m_kv and current is not None:
            current[m_kv.group(1)] = m_kv.group(2)
    if current:
        out.append(current)
    return out


def find_stale_supersessions(pages: dict[str, dict]) -> list[dict]:
    """Rule 1: entities still linking to a decision whose superseded_by has moved on.

    For each `kind: decision` page D with `superseded_by` set non-null,
    find any page X (any kind) whose body contains `[[D.slug]]`. Each
    UNIQUE (X, D) pair is a stale-reference flag — repeated
    `[[same-decision]]` links in the same page produce one flag, not N
    (Codex P2 fix: dedupe per (source_slug, target_slug) pair).
    """
    flags: list[dict] = []
    # Pre-extract decisions with non-null superseded_by.
    superseded_decisions: dict[str, dict] = {}
    for slug, page in pages.items():
        fm = page["fm"]
        if fm.get("kind") != "decision":
            continue
        sb = fm.get("superseded_by")
        if sb is None or sb == "" or sb == "null":
            continue
        superseded_decisions[slug] = page

    if not superseded_decisions:
        return flags

    # M5 fix: build an id -> slug index so we can resolve `superseded_by`
    # values that operators have written in id form (e.g. ent_a4f3a4f3a4f3)
    # back to the slug an operator can actually open. The audit's matching
    # was already correct — we look for `[[<superseded-decision-slug>]]`
    # wikilinks — but the report printed the raw id, which the operator
    # could not follow. Resolve here so the report shows a clickable slug.
    id_to_slug: dict[str, str] = {}
    for slug, page in pages.items():
        page_id = page["fm"].get("id")
        if page_id:
            id_to_slug[page_id] = slug

    # Track seen (source, target) pairs to dedupe within a page.
    seen_pairs: set[tuple[str, str]] = set()
    for src_slug, src in pages.items():
        for ref in parse_wikirefs(src["body"]):
            # Phase 1 backward-compat: only kind="slug" refs feed this rule
            # (preserves the prior `[[slug]]` / `[[slug|display]]` regex
            # semantics). Section/code refs are captured by parse_wikirefs
            # but ignored here — Phase 2's broken-ref auditor consumes them.
            if ref.kind != "slug":
                continue
            target_slug = ref.target
            if target_slug not in superseded_decisions:
                continue
            pair = (src_slug, target_slug)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            target = superseded_decisions[target_slug]
            sb_raw = target["fm"].get("superseded_by")
            sb_slug = id_to_slug.get(sb_raw) if isinstance(sb_raw, str) else None
            flags.append({
                "rule": "stale-supersession",
                "source_slug": src_slug,
                "target_slug": target_slug,
                "superseded_by": sb_raw,
                "superseded_by_slug": sb_slug,  # None if id has no matching page
            })
    return flags


def find_freshness_expired(
    pages: dict[str, dict],
    *,
    now: datetime | None = None,
) -> tuple[list[dict], list[str]]:
    """Rule 2: entities whose freshness is below threshold AND elapsed > half_life.

    Both conditions required. Prevents false positives from fresh-but-fast-decay
    sources (high half-life types just past midpoint) AND from low-half-life
    types still within half-life.

    Returns (flags, warnings). Codex P1 fix: a single page with malformed
    `last_verified_at` (or any other freshness-computation crash) MUST NOT
    abort the audit run — the script's resilience goal is "skip bad pages
    and keep generating audit/proposals.md." Bad pages surface as
    warnings.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    flags: list[dict] = []
    warnings: list[str] = []
    for slug, page in pages.items():
        last_verified = page["fm"].get("last_verified_at")
        if not last_verified or last_verified == "null":
            continue
        sources = page.get("sources") or []
        source_types = [s["type"] for s in sources if s.get("type")]
        if not source_types:
            source_types = ["default"]

        try:
            score = compute_freshness_multi_source(last_verified, source_types, now=now)
        except (ValueError, TypeError) as e:
            warnings.append(
                f"freshness check skipped for {slug}: invalid "
                f"last_verified_at={last_verified!r} ({e})"
            )
            continue
        if score >= _FRESHNESS_FLOOR:
            continue

        # Second guard: elapsed must exceed shortest half-life. Prevents
        # flagging an email source (21d half-life) just past 10d that
        # decayed below 0.3 due to its short curve.
        try:
            verified_dt = _parse_iso(last_verified)
        except ValueError as e:
            warnings.append(
                f"freshness check skipped for {slug}: cannot parse "
                f"last_verified_at={last_verified!r} ({e})"
            )
            continue
        if verified_dt.tzinfo is None:
            verified_dt = verified_dt.replace(tzinfo=timezone.utc)
        elapsed_days = (now - verified_dt).total_seconds() / 86400.0
        threshold_days = shortest_half_life(source_types)
        if elapsed_days <= threshold_days:
            continue

        flags.append({
            "rule": "freshness-expired",
            "slug": slug,
            "score": round(score, 3),
            "last_verified_at": last_verified,
            "elapsed_days": round(elapsed_days, 1),
            "shortest_half_life_days": threshold_days,
            "source_types": source_types,
        })
    return flags, warnings


# Heading regex: matches `^# Title`, `## Title`, etc. Captures the title
# text without the leading `#`s or trailing whitespace. Used by
# find_broken_refs to validate `[[slug#Section]]` anchors against the
# target page's actual headings.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _extract_headings(body: str) -> set[str]:
    """Return the set of heading titles (verbatim + lowercased + slug-form)
    extracted from a markdown body. Used to resolve section anchors.

    Three normalized forms are returned per heading so anchor matching is
    forgiving:
        - Verbatim:    ``OAuth Flow``
        - Lowercased:  ``oauth flow``
        - Slug-form:   ``oauth-flow`` (lowercase, hyphens for spaces, alnum)
    """
    out: set[str] = set()
    for m in _HEADING_RE.finditer(body):
        title = m.group(2).strip()
        if not title:
            continue
        out.add(title)
        out.add(title.lower())
        # Slug normalization mirrors wiki_init.slugify.
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        if slug:
            out.add(slug)
    return out


def find_broken_refs(
    pages: dict[str, dict],
    *,
    code_index: dict | None = None,
) -> list[dict]:
    """Return broken-reference flags for every wikiref in every page body.

    Three failure modes (PRD AC2):

    - ``page_not_found`` — slug/section ref whose target slug isn't in
      ``pages`` (also fires for path-targeted section refs, e.g. ``docs/foo.md``).
    - ``section_not_found`` — section ref whose target page exists but the
      anchor doesn't match any heading.
    - ``file_not_found`` — code ref whose target path isn't in
      ``code_index`` (skipped entirely if ``code_index`` is None — callers
      with docs-only brains don't need codebase validation).
    - ``symbol_not_found`` — code ref whose target file is indexed but the
      symbol doesn't resolve.

    Per ``plan/PRD-latmd-integration.md`` Phase 2 acceptance criteria.
    """
    flags: list[dict] = []
    code_files: dict = (code_index or {}).get("files", {})
    seen: set[tuple[str, str, str]] = set()  # dedupe (source, target, anchor)

    for src_slug, src in pages.items():
        body = src["body"]
        for ref in parse_wikirefs(body):
            anchor = ref.anchor or ""
            key = (src_slug, ref.target, anchor)
            if key in seen:
                continue
            seen.add(key)

            if ref.kind == "slug":
                if ref.target not in pages:
                    flags.append({
                        "rule": "broken-ref",
                        "source_slug": src_slug,
                        "ref": ref.raw,
                        "kind": ref.kind,
                        "reason": "page_not_found",
                        "target": ref.target,
                        "anchor": None,
                    })

            elif ref.kind == "section":
                # Path-targeted section refs (e.g. `docs/auth.md#Foo`) aren't
                # CE-native pages today; flag as page_not_found. Phase 2.5
                # may add doc-file resolution.
                if ref.target not in pages:
                    flags.append({
                        "rule": "broken-ref",
                        "source_slug": src_slug,
                        "ref": ref.raw,
                        "kind": ref.kind,
                        "reason": "page_not_found",
                        "target": ref.target,
                        "anchor": ref.anchor,
                    })
                else:
                    target_page = pages[ref.target]
                    headings = _extract_headings(target_page["body"])

                    def _heading_matches(name: str) -> bool:
                        # Match verbatim, lowercased, or slug-normalized.
                        if not name:
                            return True
                        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                        return bool({name, name.lower(), slug} & headings)

                    anchor_ok = _heading_matches(ref.anchor or "")
                    # Codex P1 (PR #30 fix): also validate sub_anchor for
                    # `[[target#Section#Subsection]]` so deep-link rot is
                    # caught. Heading-tree nesting check is post-Phase 2; for
                    # now we require both names to exist as headings on the
                    # target page (a `section_not_found` if either is missing).
                    sub_ok = _heading_matches(ref.sub_anchor or "")
                    if not anchor_ok or not sub_ok:
                        # Surface the missing segment in the flag so report
                        # output points at the precise broken anchor.
                        missing = ref.anchor if not anchor_ok else ref.sub_anchor
                        flags.append({
                            "rule": "broken-ref",
                            "source_slug": src_slug,
                            "ref": ref.raw,
                            "kind": ref.kind,
                            "reason": "section_not_found",
                            "target": ref.target,
                            "anchor": ref.anchor,
                            "sub_anchor": ref.sub_anchor,
                            "missing_segment": missing,
                        })

            elif ref.kind == "code":
                if code_index is None:
                    continue  # Caller didn't supply code_index; nothing to check.
                file_entry = code_files.get(ref.target)
                if file_entry is None:
                    flags.append({
                        "rule": "broken-ref",
                        "source_slug": src_slug,
                        "ref": ref.raw,
                        "kind": ref.kind,
                        "reason": "file_not_found",
                        "target": ref.target,
                        "anchor": ref.anchor,
                    })
                elif ref.anchor:
                    matches = [s for s in file_entry["symbols"] if s["name"] == ref.anchor]
                    if not matches:
                        flags.append({
                            "rule": "broken-ref",
                            "source_slug": src_slug,
                            "ref": ref.raw,
                            "kind": ref.kind,
                            "reason": "symbol_not_found",
                            "target": ref.target,
                            "anchor": ref.anchor,
                        })
    return flags


def find_slug_collision_near_misses(wiki_dir: Path) -> list[dict]:
    """Rule 3: read collision footnotes from _index.md and surface them."""
    flags: list[dict] = []
    index = wiki_dir / "_index.md"
    if not index.exists():
        return flags
    text = index.read_text(encoding="utf-8")
    in_section = False
    for line in text.splitlines():
        if line.startswith("## Collision footnotes"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        m = re.match(
            r"^- `([^`]+)` collided with `([^`]+)` on (\d{4}-\d{2}-\d{2})\s*$",
            line,
        )
        if m:
            flags.append({
                "rule": "slug-collision",
                "final_slug": m.group(1),
                "original": m.group(2),
                "date": m.group(3),
            })
    return flags


def render_proposals(
    *,
    stale_supersessions: list[dict],
    freshness_expired: list[dict],
    slug_collisions: list[dict],
    warnings: list[str],
    broken_refs: list[dict] | None = None,
    now_iso: str | None = None,
) -> str:
    """Format the audit report as markdown for audit/proposals.md."""
    if now_iso is None:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    lines: list[str] = []
    lines.append("# audit/proposals.md")
    lines.append("")
    lines.append(f"_Generated: {now_iso}_")
    lines.append("")

    if warnings:
        lines.append("## Validation warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Stale references")
    lines.append("")
    if stale_supersessions:
        for f in stale_supersessions:
            # M5 fix: prefer the resolved slug for the report; fall back to
            # the raw `superseded_by` only when the id doesn't resolve to a
            # page in this brain (cross-brain reference, or stale id).
            sb_display = f.get("superseded_by_slug") or f["superseded_by"]
            lines.append(
                f"- `{f['source_slug']}` references superseded "
                f"`{f['target_slug']}` (superseded by `{sb_display}`). "
                f"Update the link or revoke the supersession."
            )
    else:
        lines.append("_(none)_")
    lines.append("")

    lines.append("## Freshness expired")
    lines.append("")
    if freshness_expired:
        for f in freshness_expired:
            types = ", ".join(f["source_types"])
            lines.append(
                f"- `{f['slug']}` freshness {f['score']:.2f} "
                f"(elapsed {f['elapsed_days']}d, shortest half-life "
                f"{f['shortest_half_life_days']}d, sources: {types}). "
                f"Last verified {f['last_verified_at']}."
            )
    else:
        lines.append("_(none)_")
    lines.append("")

    # Phase 2 (CE x lat.md): broken wikiref / code-symbol / section section.
    # Always rendered, even when broken_refs is None or empty, so consumers
    # can pin "Broken refs" as a stable section name in lat_check output.
    lines.append("## Broken refs")
    lines.append("")
    if broken_refs:
        for f in broken_refs:
            target = f.get("target", "")
            anchor_str = f"#{f['anchor']}" if f.get("anchor") else ""
            reason = f.get("reason", "unknown").replace("_", " ")
            lines.append(
                f"- `{f['source_slug']}` -> {f['ref']} "
                f"(target=`{target}{anchor_str}`, kind={f.get('kind')}, "
                f"reason: {reason})"
            )
    else:
        lines.append("_(none)_")
    lines.append("")

    lines.append("## Slug collisions (near-misses)")
    lines.append("")
    if slug_collisions:
        for f in slug_collisions:
            lines.append(
                f"- `{f['final_slug']}` collided with `{f['original']}` "
                f"on {f['date']}. Consider merging or renaming."
            )
    else:
        lines.append("_(none)_")
    lines.append("")

    return "\n".join(lines)


def run_audit(brain_dir: Path, *, now: datetime | None = None,
              now_iso: str | None = None,
              code_index: dict | None = None) -> dict:
    """Execute the four rules and write audit/proposals.md.

    Args:
        brain_dir: Brain root with `wiki/`, `audit/` etc.
        now: For freshness_expired's deterministic-test override.
        now_iso: Same, for the proposals.md timestamp.
        code_index: Optional code_index (from `wiki.code_index.load_code_index`).
            When provided, the broken-ref rule validates `[[src/file#symbol]]`
            references against the index. When None, only slug/section refs
            are validated; code refs are skipped.

    Returns a dict with all rule outputs for the CLI / MCP wrapper.
    """
    brain_dir = Path(brain_dir)
    wiki_dir = brain_dir / "wiki"
    audit_dir = brain_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    pages, warnings = _load_pages(wiki_dir)
    stale_supersessions = find_stale_supersessions(pages)
    freshness_expired, freshness_warnings = find_freshness_expired(pages, now=now)
    warnings = warnings + freshness_warnings
    slug_collisions = find_slug_collision_near_misses(wiki_dir)
    broken_refs = find_broken_refs(pages, code_index=code_index)

    proposals = render_proposals(
        stale_supersessions=stale_supersessions,
        freshness_expired=freshness_expired,
        slug_collisions=slug_collisions,
        warnings=warnings,
        broken_refs=broken_refs,
        now_iso=now_iso,
    )
    proposals_path = brain_dir / _PROPOSALS_RELPATH
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    proposals_path.write_text(proposals, encoding="utf-8")

    return {
        "stale_supersessions": stale_supersessions,
        "freshness_expired": freshness_expired,
        "slug_collisions": slug_collisions,
        "broken_refs": broken_refs,
        "warnings": warnings,
        "proposals_path": proposals_path,
    }


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _print_audit_summary(result: dict) -> None:
    """Shared CLI printer used by audit.main and lat_check.main."""
    print(
        f"audit: {len(result['stale_supersessions'])} stale-references, "
        f"{len(result['freshness_expired'])} freshness-expired, "
        f"{len(result['slug_collisions'])} slug-collisions, "
        f"{len(result.get('broken_refs', []))} broken-refs, "
        f"{len(result['warnings'])} validation-warnings -> {result['proposals_path']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auditor v1: scan brain/wiki/, write audit/proposals.md")
    parser.add_argument("--brain", required=True, type=Path, help="Brain root directory")
    args = parser.parse_args(argv)

    result = run_audit(args.brain)
    n_stale = len(result["stale_supersessions"])
    n_fresh = len(result["freshness_expired"])
    n_coll = len(result["slug_collisions"])
    n_broken = len(result.get("broken_refs", []))
    n_warn = len(result["warnings"])
    print(
        f"audit: {n_stale} stale-references, {n_fresh} freshness-expired, "
        f"{n_coll} slug-collisions, {n_broken} broken-refs, "
        f"{n_warn} validation-warnings -> "
        f"{result['proposals_path']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
