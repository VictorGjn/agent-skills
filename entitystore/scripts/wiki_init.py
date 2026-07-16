#!/usr/bin/env python3
"""wiki_init.py — one-shot entity-page seeder (M11).

Reads the JSON entity corpus (corpora/<id>/entities/**/*.json — company-
brain's actual store; NOT an events log, that's context-engineering's older
model) and writes one `corpora/<id>/wiki/<slug>.md` page per entity: a
REGENERABLE PROJECTION over the store, never a second source of truth. The
entity JSON stays authoritative; a wiki page is byte-reproducible from it
at any time via `--rebuild`. Nothing here writes an entity or a claim — see
company-brain/CLAUDE.md "THE WRITER RULE" (scribes write RAW only,
enrichers build ALL entities; this script only ever touches `wiki/*.md`).

Idempotent: re-running with unchanged entities reproduces byte-identical
output, modulo the `generated_at:` line (wall-clock timestamp, stripped
before the idempotency compare — mirrors context-engineering's
`_strip_updated_line`).

Cap-aware: reads through `cb_engine._filter_by_classification`, so an
entity above the caller's `CB_CLASSIFICATION_CAP` never becomes a page —
same fail-closed contract as every other entitystore read endpoint (see
SURFACE.md "Classification cap").

Pointer-based pages: a page links to entity ids and raw source refs. It
never copies claim `evidence[].quote` text — that keeps the M7 PII-purge
surface to the JSON entities alone, not duplicated across two file trees.

PROHIBITED on every page (company-brain: no stored scores/trust/tier/
reputation, freshness computed-on-read only — see freshness_policy.py):
`freshness_score`, `confidence`, `centroid_embedding`, and any
score/trust/tier/reputation-shaped field.

CLI:
    python wiki_init.py --corpus /path/to/corpus
    python wiki_init.py --corpus /path/to/corpus --kinds org person
    python wiki_init.py --corpus /path/to/corpus --rebuild
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import defaultdict

# cb_engine.py is a sibling script, not a package member (see
# tests/test_golden_queries.py for the same sys.path convention).
_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import cb_engine  # noqa: E402

# Wiki-page frontmatter schema version. Independent of the entity schema
# version (schemas/entity.schema.json) — this versions the PAGE shape, not
# the underlying entity.
PAGE_SCHEMA_VERSION = "1.0"

# Fields this module will NEVER write to a page, no matter what. Enforced
# by test_wiki_init.py so a future edit can't silently reintroduce a
# prohibited field. See module docstring.
PROHIBITED_FIELDS = (
    "freshness_score", "confidence", "centroid_embedding",
    "trust", "trust_score", "reputation", "tier",
)


def slugify_id(entity_id: str) -> str:
    """`kind:slug` -> `kind-slug`. Entity ids are already globally unique
    (schema-enforced `^[a-z][a-z0-9-]{0,63}:[a-z0-9][a-z0-9-]{0,127}$`), so
    this is collision-free by construction — no near-miss suffixing needed
    (unlike CE's hint-derived slugs, which weren't unique going in)."""
    return entity_id.replace(":", "-", 1)


def _inbound_links(entities: dict[str, dict]) -> dict[str, list[str]]:
    """Map target entity id -> list of referrer ids whose `wiki_links`
    include it.

    NOTE: as-linksto-export (feature/m11-links-to-export) is landing a
    same-named, same-shaped helper directly in cb_engine.py (for the
    `links_to` MCP endpoint) on this same stacked branch, concurrently with
    this work. That commit hadn't landed yet when this file was written, so
    this is a local copy scoped to wiki_init's own needs, not an import —
    reconcile/dedupe (keep either; they're behaviorally identical) once
    both PRs are in.

    Caller must pass an already classification-filtered entities dict so
    both sides of every edge come pre-scoped to the cap.
    """
    inbound: dict[str, list[str]] = defaultdict(list)
    for eid, e in entities.items():
        for ref in e.get("wiki_links", []) or []:
            inbound[ref].append(eid)
    return inbound


def _sources_for_entity(entity: dict) -> list[dict]:
    """Pointer-only source list: provenance + ACTIVE identity_assertions.

    Superseded/retracted assertions are deliberately excluded — they're
    history the entity JSON still carries, not a live pointer a page reader
    should follow today. Deduped + sorted for idempotency.
    """
    sources: list[dict] = []
    prov = entity.get("provenance") or {}
    if prov.get("extractor"):
        sources.append({
            "type": "provenance",
            "ref": prov.get("extractor"),
            "method": prov.get("extraction_method"),
        })
    for a in entity.get("identity_assertions") or []:
        if a.get("status") != "active":
            continue
        sources.append({
            "type": a.get("source_system"),
            "ref": a.get("source_id"),
            "method": a.get("method"),
            "as_of": a.get("as_of"),
        })

    seen: set[tuple] = set()
    uniq: list[dict] = []
    for s in sources:
        key = (s.get("type"), s.get("ref"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    uniq.sort(key=lambda s: (s.get("type") or "", s.get("ref") or ""))
    return uniq


def _yaml_scalar(v) -> str:
    """Render a Python scalar as a YAML-safe frontmatter value. Strings are
    always JSON-quoted (JSON is a YAML subset) so embedded `:`, `#`, quotes
    etc. never break a downstream line-oriented frontmatter parser — same
    hazard class CE's audit.py source-row parser hit with unquoted Notion
    URLs."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    return json.dumps(str(v), ensure_ascii=False)


def _render_frontmatter(entity: dict, *, links_in: list[str], generated_at: str) -> str:
    eid = entity["id"]
    kind = entity.get("kind", "")
    slug = slugify_id(eid)
    sources = _sources_for_entity(entity)
    links_out = sorted(set(entity.get("wiki_links") or []))
    links_in_sorted = sorted(set(links_in))

    lines = ["---"]
    lines.append(f"id: {_yaml_scalar(eid)}")
    lines.append(f"kind: {_yaml_scalar(kind)}")
    lines.append(f"slug: {_yaml_scalar(slug)}")
    lines.append(f"schema_version: {_yaml_scalar(PAGE_SCHEMA_VERSION)}")
    # M4 field, copied verbatim — null when the entity predates the
    # freshness rule (see freshness_policy.py; person 0/331, vessel 0/131
    # coverage as of M11). NEVER a computed freshness_score.
    lines.append(f"last_verified_at: {_yaml_scalar(entity.get('last_verified_at'))}")
    # Decision-continuity fields. Null-allowed: as of M11, no entity in the
    # real corpus has these set (the M4 dedup collapse populated
    # identity_assertions/echo_of, not a top-level superseded_by) — surfaced
    # here so the day an enricher DOES start writing them, pages pick them
    # up with zero wiki_init changes.
    lines.append(f"supersedes: {_yaml_scalar(entity.get('supersedes'))}")
    lines.append(f"superseded_by: {_yaml_scalar(entity.get('superseded_by'))}")
    lines.append(f"valid_until: {_yaml_scalar(entity.get('valid_until'))}")
    lines.append("sources:")
    for s in sources:
        lines.append("  -")
        for k in ("type", "ref", "method", "as_of"):
            if s.get(k) is not None:
                lines.append(f"    {k}: {_yaml_scalar(s[k])}")
    lines.append("links_out:")
    for ref in links_out:
        lines.append(f"  - {_yaml_scalar(ref)}")
    lines.append("links_in:")
    for ref in links_in_sorted:
        lines.append(f"  - {_yaml_scalar(ref)}")
    lines.append(f"generated_at: {_yaml_scalar(generated_at)}")
    lines.append("---")
    return "\n".join(lines)


def _render_body(entity: dict, *, links_in: list[str]) -> str:
    names = entity.get("names") or [entity.get("id", "")]
    title = names[0]
    lines = ["", f"# {title}", ""]

    summary = (entity.get("summary") or "").strip()
    if summary:
        lines += [summary, ""]

    topics = sorted(set(entity.get("topics") or []))
    if topics:
        lines += [f"**Topics:** {', '.join(topics)}", ""]

    # Claim metric NAMES only — never values, never evidence quotes. Follow
    # the pointer (the entity id above) for the actual measurements.
    claims = entity.get("claims") or []
    metrics = sorted({c.get("metric") for c in claims if c.get("metric")})
    if metrics:
        lines.append("## Claim metrics")
        lines.append("")
        lines.append(
            "_Pointer only — see the entity record for values and "
            "sources; evidence is never copied onto this page._"
        )
        lines.append("")
        for m in metrics:
            lines.append(f"- `{m}`")
        lines.append("")

    links_out = sorted(set(entity.get("wiki_links") or []))
    lines.append("## Links out")
    lines.append("")
    if links_out:
        for ref in links_out:
            lines.append(f"- [[{ref}]]")
    else:
        lines.append("_(none)_")
    lines.append("")

    lines.append("## Links in")
    lines.append("")
    links_in_sorted = sorted(set(links_in))
    if links_in_sorted:
        for ref in links_in_sorted:
            lines.append(f"- [[{ref}]]")
    else:
        lines.append("_(none)_")
    lines.append("")

    return "\n".join(lines)


def render_page(entity: dict, *, links_in: list[str], generated_at: str) -> str:
    fm = _render_frontmatter(entity, links_in=links_in, generated_at=generated_at)
    body = _render_body(entity, links_in=links_in)
    return fm + "\n" + body


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _strip_generated_line(text: str) -> str:
    """Drop the wall-clock `generated_at:` line before an idempotency
    compare — everything else must match byte-for-byte."""
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith("generated_at:")
    )


def write_wiki(
    corpus_dir: str | pathlib.Path,
    *,
    kinds: list[str] | None = None,
    rebuild: bool = False,
    now_iso: str | None = None,
) -> dict:
    """Seed/update `corpus_dir/wiki/<slug>.md` for every (cap-visible,
    optionally kind-filtered) entity in the corpus.

    Returns: {"actions": {slug: "created"|"updated"|"unchanged"},
              "withheld_count": int, "effective_cap": str}
    """
    cdir = pathlib.Path(corpus_dir)
    wiki_dir = cdir / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    kind_set = set(kinds) if kinds else None

    if rebuild:
        for old in wiki_dir.glob("*.md"):
            if old.name.startswith("_"):
                continue  # keep hand-maintained index/notes pages
            if kind_set is not None:
                old_kind = old.stem.split("-", 1)[0]
                if old_kind not in kind_set:
                    continue
            old.unlink()

    all_entities = cb_engine.load_corpus(cdir)
    kept, withheld_count, effective_cap = cb_engine._filter_by_classification(
        all_entities, cdir,
    )

    # Inbound links are computed over the FULL cap-visible set, not the
    # kind-narrowed one — a --kinds=org run must still show a person's
    # inbound link on the org pages it writes, even though no person page
    # gets written this run.
    inbound_all = _inbound_links(kept)

    entities = kept
    if kind_set is not None:
        entities = {eid: e for eid, e in kept.items() if e.get("kind") in kind_set}

    generated_at = now_iso or _now_iso()

    actions: dict[str, str] = {}
    for eid in sorted(entities):
        e = entities[eid]
        slug = slugify_id(eid)
        target = wiki_dir / f"{slug}.md"
        page_text = render_page(
            e, links_in=inbound_all.get(eid, []), generated_at=generated_at,
        )
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if _strip_generated_line(existing) == _strip_generated_line(page_text):
                actions[slug] = "unchanged"
                continue
            actions[slug] = "updated"
        else:
            actions[slug] = "created"
        target.write_text(page_text, encoding="utf-8")

    return {
        "actions": actions,
        "withheld_count": withheld_count,
        "effective_cap": effective_cap,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=None,
                     help="corpus dir; falls back to CB_CORPUS_DIR env")
    ap.add_argument("--kinds", nargs="*", default=None,
                     help="only seed pages for these entity kinds")
    ap.add_argument("--rebuild", action="store_true",
                     help="delete existing wiki/*.md (scoped to --kinds if given) first")
    args = ap.parse_args(argv)

    cdir = cb_engine._resolve_corpus(args.corpus)
    result = write_wiki(cdir, kinds=args.kinds, rebuild=args.rebuild)
    actions = result["actions"]
    created = sum(1 for v in actions.values() if v == "created")
    updated = sum(1 for v in actions.values() if v == "updated")
    unchanged = sum(1 for v in actions.values() if v == "unchanged")
    print(
        f"wiki_init: {created} created, {updated} updated, {unchanged} unchanged "
        f"(withheld_count={result['withheld_count']}, "
        f"effective_cap={result['effective_cap']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
