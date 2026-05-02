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
except ImportError:  # script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from wiki.validate_page import validate_page, ValidationError
    from wiki.freshness_policy import (
        compute_freshness_multi_source, half_life_days, shortest_half_life,
    )


# Wiki-link extraction: `[[slug]]` or `[[slug|display]]` anywhere in body.
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")
# Source-row parser for the simple inline-yaml shape wiki_init.py emits:
# `  - { type: code, ref: src/foo.ts, ts: 2026-04-01T00:00:00Z }`
_SOURCE_ROW_RE = re.compile(
    r"^\s*-\s*\{\s*type:\s*([^,}\s]+)\s*,\s*ref:\s*([^,}]+?)\s*(?:,\s*ts:\s*([^,}\s]+))?\s*\}",
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
        try:
            fm = validate_page(path)
        except ValidationError as e:
            warnings.append(f"skipped {path.name}: {e}")
            continue
        slug = fm.get("slug") or path.stem
        body = path.read_text(encoding="utf-8").split("---", 2)[-1] if "---" in path.read_text(encoding="utf-8") else ""
        # Re-extract sources from the page text (the simple frontmatter parser
        # in validate_page collapses `sources:` to a string; we re-walk the
        # YAML rows directly to get type/ref pairs needed for freshness).
        sources = _parse_source_rows(path.read_text(encoding="utf-8"))
        pages[slug] = {
            "fm": fm,
            "body": body,
            "path": path,
            "sources": sources,
        }
    return pages, warnings


def _parse_source_rows(text: str) -> list[dict]:
    """Walk the frontmatter for `sources:` block rows and parse type/ref/ts."""
    out: list[dict] = []
    in_sources = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if line.startswith("---") and out and in_sources:
            # End of frontmatter while inside sources block — done.
            break
        if line.startswith("sources:"):
            in_sources = True
            continue
        if in_sources:
            if not line.startswith(" ") and not line.startswith("\t") and line.strip():
                # Top-level frontmatter key — sources block ended.
                in_sources = False
                continue
            m = _SOURCE_ROW_RE.match(line)
            if m:
                out.append({
                    "type": m.group(1),
                    "ref": m.group(2),
                    "ts": m.group(3) or "",
                })
    return out


def find_stale_supersessions(pages: dict[str, dict]) -> list[dict]:
    """Rule 1: entities still linking to a decision whose superseded_by has moved on.

    For each `kind: decision` page D with `superseded_by` set non-null,
    find any page X (any kind) whose body contains `[[D.slug]]`. Each
    such (X, D) pair is a stale-reference flag.
    """
    flags: list[dict] = []
    # Build a map slug -> page for fast wiki-link target lookup.
    by_slug = {slug: p for slug, p in pages.items()}
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

    # For each page, scan body for wiki-links to a superseded decision.
    for src_slug, src in pages.items():
        for m in _WIKILINK_RE.finditer(src["body"]):
            target_slug = m.group(1).strip()
            if target_slug in superseded_decisions:
                target = superseded_decisions[target_slug]
                flags.append({
                    "rule": "stale-supersession",
                    "source_slug": src_slug,
                    "target_slug": target_slug,
                    "superseded_by": target["fm"].get("superseded_by"),
                })
    return flags


def find_freshness_expired(
    pages: dict[str, dict],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Rule 2: entities whose freshness is below threshold AND elapsed > half_life.

    Both conditions required. Prevents false positives from fresh-but-fast-decay
    sources (high half-life types just past midpoint) AND from low-half-life
    types still within half-life.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    flags: list[dict] = []
    for slug, page in pages.items():
        last_verified = page["fm"].get("last_verified_at")
        if not last_verified or last_verified == "null":
            continue
        sources = page.get("sources") or []
        source_types = [s["type"] for s in sources if s.get("type")]
        if not source_types:
            source_types = ["default"]

        score = compute_freshness_multi_source(last_verified, source_types, now=now)
        if score >= _FRESHNESS_FLOOR:
            continue

        # Second guard: elapsed must exceed shortest half-life. Prevents
        # flagging an email source (21d half-life) just past 10d that
        # decayed below 0.3 due to its short curve.
        try:
            verified_dt = _parse_iso(last_verified)
        except ValueError:
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
            lines.append(
                f"- `{f['source_slug']}` references superseded "
                f"`{f['target_slug']}` (superseded by `{f['superseded_by']}`). "
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
              now_iso: str | None = None) -> dict:
    """Execute the three rules and write audit/proposals.md.

    Returns a dict {stale_supersessions, freshness_expired, slug_collisions,
    warnings, proposals_path} for callers (CLI prints; MCP wrapper returns).
    """
    brain_dir = Path(brain_dir)
    wiki_dir = brain_dir / "wiki"
    audit_dir = brain_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    pages, warnings = _load_pages(wiki_dir)
    stale_supersessions = find_stale_supersessions(pages)
    freshness_expired = find_freshness_expired(pages, now=now)
    slug_collisions = find_slug_collision_near_misses(wiki_dir)

    proposals = render_proposals(
        stale_supersessions=stale_supersessions,
        freshness_expired=freshness_expired,
        slug_collisions=slug_collisions,
        warnings=warnings,
        now_iso=now_iso,
    )
    proposals_path = brain_dir / _PROPOSALS_RELPATH
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    proposals_path.write_text(proposals, encoding="utf-8")

    return {
        "stale_supersessions": stale_supersessions,
        "freshness_expired": freshness_expired,
        "slug_collisions": slug_collisions,
        "warnings": warnings,
        "proposals_path": proposals_path,
    }


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auditor v1: scan brain/wiki/, write audit/proposals.md")
    parser.add_argument("--brain", required=True, type=Path, help="Brain root directory")
    args = parser.parse_args(argv)

    result = run_audit(args.brain)
    n_stale = len(result["stale_supersessions"])
    n_fresh = len(result["freshness_expired"])
    n_coll = len(result["slug_collisions"])
    n_warn = len(result["warnings"])
    print(
        f"audit: {n_stale} stale-references, {n_fresh} freshness-expired, "
        f"{n_coll} slug-collisions, {n_warn} validation-warnings -> "
        f"{result['proposals_path']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
