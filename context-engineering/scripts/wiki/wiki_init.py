"""wiki_init.py — events-log -> wiki/<slug>.md consolidator (M2 in PRD).

Reads the brain's append-only events log, clusters events by `entity_hint`,
and writes/updates one `wiki/<slug>.md` per entity. **Idempotent**: running
twice over the same events log produces byte-identical output the second
time (modulo `updated:` timestamp drift if the wall clock advances — but
the body, frontmatter excluding `updated:`, and slug-collision footnote
are stable).

V0.1 clustering strategy: deterministic by `entity_hint`. Events without
an `entity_hint` are skipped (an upstream extractor or future LLM-driven
synthesizer assigns hints). Real semantic clustering (cluster events with
no hint by cosine similarity of their embeddings) is Phase 2 work.

Per ``plan/phases/phase-1.md`` §1.5 + ``plan/prd-closed-loop.md`` M2.

CLI:
    python3 scripts/wiki/wiki_init.py --brain /path/to/brain
    python3 scripts/wiki/wiki_init.py --brain /path/to/brain --rebuild

`--rebuild` deletes existing `wiki/*.md` first, used when the schema
version bumps (per §1.2.1 refusal-and-rebuild policy).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

# Import siblings via relative import when run as a module, OR fall back to
# direct import when run as a script (PYTHONPATH includes scripts/).
try:
    from .events import read_events
    from .validate_page import SCHEMA_VERSION
    from .wikiref import format_wikiref
except ImportError:  # script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from wiki.events import read_events
    from wiki.validate_page import SCHEMA_VERSION
    from wiki.wikiref import format_wikiref


# Slug-collision discipline per phase-1.md §1.2 acceptance rule:
# lowercased keys for case-insensitive filesystems (Windows NTFS, macOS APFS).
_SLUG_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Lowercase kebab-case slug. Idempotent: slugify(slugify(x)) == slugify(x)."""
    s = _SLUG_NORMALIZE_RE.sub("-", title.lower()).strip("-")
    return s or "untitled"


def make_id(slug: str, sources_signature: str) -> str:
    """Stable id: short hash of slug + a sources signature.

    Idempotent: same inputs -> same id. Renaming (slug change) does
    NOT change id, so callers should pass a STABLE signature derived
    from the source identities, not the rendered content.

    Returns a 12-hex-char (48-bit) prefix. The earlier 8-hex (32-bit)
    version had ~1% birthday-paradox collision at 77k entities — fine
    for the demo, silent corruption for any Anabasis customer with a
    real corpus. 48 bits gives ~1% at ~16M entities; ~0.001% at 77k.
    Trivial to widen further if the brain ever crosses 10M entities.
    """
    import hashlib
    h = hashlib.sha256(f"{slug}:{sources_signature}".encode("utf-8")).hexdigest()
    return f"ent_{h[:12]}"


def consolidate(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by entity_hint. Events without a hint are dropped (v0.1).

    Returns: {entity_hint: [event, ...]}, sorted within each group by ts.
    """
    grouped: dict[str, list[dict]] = {}
    for e in events:
        hint = e.get("entity_hint")
        if not hint:
            continue
        grouped.setdefault(hint, []).append(e)
    for hint in grouped:
        grouped[hint].sort(key=lambda x: x.get("ts", 0))
    return grouped


def render_page(
    *, slug: str, entity_id: str, scope: str, title: str,
    events: list[dict], schema_version: str = SCHEMA_VERSION,
    updated_iso: str | None = None,
) -> str:
    """Render one entity's wiki/<slug>.md content from its events.

    The frontmatter MUST satisfy validate_page.validate_page(). Body is
    a deterministic claim list — events sorted by ts, one bullet each.
    """
    if updated_iso is None:
        updated_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # last_verified_at is the most recent event ts (per phase-1.md §1.2):
    # "every emitter sets last_verified_at to the wall-clock time of touch."
    # Most-recent ts is exactly that.
    last_verified_ts = max((e.get("ts", 0) for e in events), default=0)
    last_verified_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_verified_ts),
    ) if last_verified_ts else updated_iso

    # Sources are derived from event source_refs, deduplicated, sorted for
    # idempotency. Each source carries its source_type for the multi-source
    # freshness rule in §1.2.2.
    #
    # Phase 1 (CE x lat.md): when events carry an optional `symbol` field
    # (Phase 3+ code-backlink events resolve // @lat: comments to AST
    # symbols), forward it so the body's Provenance section can render
    # lat.md-compatible [[src/path#symbol]] refs. Older events without
    # `symbol` keep the current `- src/path (type)` rendering verbatim.
    sources_seen: dict[tuple[str, str], dict] = {}
    for e in events:
        key = (e.get("source_type", "default"), e.get("source_ref", ""))
        if key not in sources_seen:
            sources_seen[key] = {
                "type": key[0],
                "ref": key[1],
                "ts": e.get("ts", 0),
                "symbol": e.get("symbol"),
            }
    sources = sorted(sources_seen.values(), key=lambda s: (s["type"], s["ref"]))

    # Confidence: number-of-sources heuristic for v0.1. Real synthesizer
    # confidence scoring is post-Phase-1.
    confidence = min(0.5 + 0.1 * len(sources), 0.95)

    fm_lines = [
        "---",
        f"id: {entity_id}",
        "kind: concept",
        f"title: {title}",
        f"slug: {slug}",
        f"scope: {scope}",
        f"schema_version: {schema_version!r}".replace("'", '"'),
        f"confidence: {confidence:.2f}",
        f"updated: {updated_iso}",
        f"last_verified_at: {last_verified_iso}",
        "sources:",
    ]
    # M4 fix: emit sources in block-style YAML, not inline `{...}`. Inline
    # form broke the audit's source-row parser whenever ref contained a
    # comma, brace, hash, or `]` (Notion URLs, GitHub paths with query
    # strings, anchored refs). Block style sidesteps the regex hazard
    # entirely — each value lives on its own line bounded by `\s*$`.
    for s in sources:
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s["ts"])) if s["ts"] else ""
        fm_lines.append("  -")
        fm_lines.append(f"    type: {s['type']}")
        fm_lines.append(f"    ref: {s['ref']}")
        fm_lines.append(f"    ts: {ts_iso}")
    fm_lines.append("---")

    body_lines = [
        "",
        f"# {title}",
        "",
        "## Claims",
        "",
    ]
    for e in events:
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e.get("ts", 0)))
        ref = e.get("source_ref", "")
        claim = e.get("claim", "").strip()
        body_lines.append(f"- {claim}  _(via {ref}, {ts_iso})_")
    body_lines.append("")
    body_lines.append("## Provenance")
    body_lines.append("")
    for s in sources:
        # Phase 1 (CE x lat.md): emit lat.md-compatible [[src/path#symbol]]
        # for code/code-backlink events that carry symbol info. Strip any
        # `:line` suffix from the ref since the symbol anchor already
        # locates the entity. Falls back to the legacy `- ref (type)` form
        # when no symbol is present.
        symbol = s.get("symbol")
        if symbol and s["type"] in ("code", "code-backlink"):
            path_only = s["ref"].split(":", 1)[0]
            ref_str = format_wikiref(kind="code", target=path_only, anchor=symbol)
            body_lines.append(f"- {ref_str} ({s['type']})")
        else:
            body_lines.append(f"- `{s['ref']}` ({s['type']})")
    body_lines.append("")

    return "\n".join(fm_lines + body_lines)


def write_wiki(
    brain_dir: Path,
    *,
    scope: str = "default",
    rebuild: bool = False,
    now_iso: str | None = None,
) -> dict[str, str]:
    """Read brain/events/, consolidate, write brain/wiki/<slug>.md per entity.

    Returns a dict {slug: action} where action is "created" / "updated" /
    "unchanged" — useful for telemetry / CI checks.

    Args:
        brain_dir: brain root (must have events/ subdir).
        scope: corpus scope tag for new pages. Existing pages keep their
            scope (read it from their frontmatter, not overwritten here).
        rebuild: if True, delete wiki/*.md before regeneration. Used when
            schema_version bumps (§1.2.1).
        now_iso: override `updated:` timestamp for deterministic tests.
    """
    brain_dir = Path(brain_dir)
    events_dir = brain_dir / "events"
    wiki_dir = brain_dir / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    # F2 fix: load scope-by-id BEFORE rebuild deletion (the prior delete →
    # load order silently reset every non-default scope on multi-scope
    # brains because the loader ran against an empty wiki/).
    #
    # Codex P2 fix: scope-by-slug fallback is RESTRICTED to rebuild mode.
    # A schema bump that widens make_id changes every entity_id, so during
    # rebuild the new id can't find the old scope and we must fall back to
    # slug. But in non-rebuild mode, applying the same fallback is a
    # correctness regression: when a NEW entity hint collides with an
    # existing slug (taking foo.md, pushing the existing entity to
    # foo-2.md), the NEW entity would inherit the old page's scope through
    # the slug map. Restricting the fallback to rebuild=True keeps the
    # migration path while preserving id-keyed scope discipline elsewhere.
    existing_scope_by_id = _load_existing_scope_by_id(wiki_dir)
    existing_scope_by_slug = (
        _load_existing_scope_by_slug(wiki_dir) if rebuild else {}
    )

    if rebuild:
        for old in wiki_dir.glob("*.md"):
            if old.name.startswith("_"):
                continue  # keep _index.md, _contradictions.md etc.
            old.unlink()

    if not events_dir.exists():
        # F2 fix follow-up: --rebuild on a brain with no events/ would
        # silently produce 0 pages. Surface the missing-events case as
        # a clear error so the operator can recover the events dir
        # (events.jsonl is primary truth; wiki/ is derived).
        raise FileNotFoundError(
            f"wiki_init: no events directory at {events_dir}. "
            f"events/ is primary truth (wiki/ is derived from it); "
            f"a missing events/ means we have nothing to consolidate. "
            f"Restore events/ from backup or git, then re-run."
        )

    events = read_events(events_dir)
    grouped = consolidate(events)

    # Slug collision discipline per phase-1.md §1.2 acceptance rule:
    # lowercased keys, numeric suffix on collision. Idempotent because we
    # always process entities in sorted hint-order.
    used_slugs_lower: set[str] = set()
    actions: dict[str, str] = {}
    collision_log: list[tuple[str, str]] = []  # (final_slug, original_slug)

    for hint in sorted(grouped):
        title = hint.replace("-", " ").replace("_", " ").title()
        base_slug = slugify(title)
        slug = base_slug
        n = 2
        while slug.lower() in used_slugs_lower:
            slug = f"{base_slug}-{n}"
            n += 1
        if slug != base_slug:
            collision_log.append((slug, base_slug))
        used_slugs_lower.add(slug.lower())

        events_for_entity = grouped[hint]
        sources_sig = ",".join(sorted({e.get("source_ref", "") for e in events_for_entity}))
        # Codex P1 fix: salt id with `hint` (which is unique per group),
        # not `base_slug` (which can collide across groups). Without this,
        # two distinct hints whose titles slugify identically AND that share
        # source_refs collapse to the same id — breaking supersedes /
        # superseded_by chains that key off id.
        entity_id = make_id(hint, sources_sig)

        # Scope preservation by stable entity_id (Codex P1 fix on PR #25),
        # with a slug fallback for the schema-bump case (F2): when --rebuild
        # widens every id, scope-by-id has no entry for the new entity_id,
        # so we look up the same slug in the pre-deletion snapshot. Final
        # fallback is the caller's `scope` arg (genuinely new entities).
        target = wiki_dir / f"{slug}.md"
        page_scope = (
            existing_scope_by_id.get(entity_id)
            or existing_scope_by_slug.get(slug)
            or scope
        )

        page_text = render_page(
            slug=slug, entity_id=entity_id, scope=page_scope, title=title,
            events=events_for_entity, updated_iso=now_iso,
        )

        if target.exists():
            # Codex P1 fix: refusal-and-rebuild model (§1.2.1) requires
            # stale-schema pages to error out, not get silently overwritten.
            # Validate before deciding "unchanged" / "updated"; if validation
            # fails (schema bump, missing keys), raise — caller runs --rebuild.
            from .validate_page import validate_page, ValidationError
            try:
                validate_page(target)
            except ValidationError as e:
                # F3 fix: spell out the migration cost so the operator can
                # plan the rebuild rather than running it blind. Scope
                # carry-over is via slug, which is robust for normal
                # collision-free rebuilds but can drift when a new entity
                # hint sorts ahead of an old one and shifts its slug.
                raise RuntimeError(
                    f"wiki_init: existing page {target.name} failed "
                    f"schema validation; refusal-and-rebuild policy requires "
                    f"`wiki_init.py --rebuild` to regenerate from the "
                    f"(unchanged) events log. Scope assignments are carried "
                    f"forward by slug across the rebuild; review wiki/ after "
                    f"rebuild for any pages whose scope unexpectedly reset to "
                    f"the default — that signals a slug shift caused by a "
                    f"new colliding entity hint. Original error: {e}"
                ) from e
            existing = target.read_text(encoding="utf-8")
            # Idempotent compare: ignore the `updated:` line which is wall-clock-
            # driven; everything else must match for "unchanged".
            if _strip_updated_line(existing) == _strip_updated_line(page_text):
                actions[slug] = "unchanged"
                continue
            actions[slug] = "updated"
        else:
            actions[slug] = "created"
        target.write_text(page_text, encoding="utf-8")

    if collision_log:
        _write_index_collisions(wiki_dir, collision_log)

    return actions


def _iter_scope_records(wiki_dir: Path):
    """Yield (entity_id, slug, scope) tuples for each well-formed page.

    Single source of truth for the two scope maps below. First-occurrence
    semantics for every key (L3 fix) so a hand-edited page with two
    `scope:` lines yields the same scope from both maps.
    """
    if not wiki_dir.exists():
        return
    for path in wiki_dir.glob("*.md"):
        if path.name.startswith("_"):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        in_fm = False
        closed = False
        page_id: str | None = None
        slug: str | None = None
        scope: str | None = None
        for line in content.splitlines():
            if line.startswith("---"):
                if in_fm:
                    closed = True
                    break
                in_fm = True
                continue
            if not in_fm:
                continue
            if line.startswith("id:") and page_id is None:
                raw = line.split(":", 1)[1].strip().strip('"\'')
                page_id = raw if raw else None
            elif line.startswith("slug:") and slug is None:
                raw = line.split(":", 1)[1].strip().strip('"\'')
                slug = raw if raw else None
            elif line.startswith("scope:") and scope is None:
                raw = line.split(":", 1)[1].strip().strip('"\'')
                scope = raw if raw else None
        if closed and scope is not None:
            yield (page_id, slug, scope)


def _load_existing_scope_by_id(wiki_dir: Path) -> dict[str, str]:
    """Build {entity_id: scope} — the canonical scope map for run-time
    write_wiki calls. id is stable across collision-order changes
    (Codex P1 fix on PR #25). Pages without an id are silently dropped.
    """
    return {pid: scope for (pid, _slug, scope) in _iter_scope_records(wiki_dir) if pid}


def _load_existing_scope_by_slug(wiki_dir: Path) -> dict[str, str]:
    """Build {slug: scope} — fallback for the `--rebuild` migration path.

    A schema bump that widens make_id changes every entity_id, so
    scope-by-id can't find the old scope. Slug is invariant for the
    same title so it survives the 1.0 → 1.1 schema bump cleanly.
    Pages without a slug are silently dropped.
    """
    return {slug: scope for (_pid, slug, scope) in _iter_scope_records(wiki_dir) if slug}


def _strip_updated_line(text: str) -> str:
    """Remove the `updated: <iso>` line for idempotency comparison."""
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith("updated:")
    )


_COLLISION_HEADER = "## Collision footnotes"
_COLLISION_RE = re.compile(r"^- `([^`]+)` collided with `([^`]+)` on \d{4}-\d{2}-\d{2}\s*$")


def _write_index_collisions(wiki_dir: Path, collisions: list[tuple[str, str]]) -> None:
    """Maintain a collision footnote section in wiki/_index.md per
    phase-1.md §1.2 rule 6.

    Codex P2 fix: idempotent — only ADD entries for collisions not already
    recorded (keyed by `final_slug`). Re-running with unchanged inputs
    produces an unchanged _index.md.
    """
    index = wiki_dir / "_index.md"
    today = time.strftime("%Y-%m-%d", time.gmtime())

    head_lines: list[str] = []
    existing_entries: list[tuple[str, str, str]] = []  # (final_slug, original, date)
    in_section = False

    if index.exists():
        existing = index.read_text(encoding="utf-8")
        for line in existing.splitlines():
            if line.startswith(_COLLISION_HEADER):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                # New section after the collision block — collision section
                # ends here. (We don't currently emit other ## sections after
                # collisions, but be defensive.)
                in_section = False
                head_lines.append(line)
                continue
            if in_section:
                m = _COLLISION_RE.match(line)
                if m:
                    existing_entries.append((m.group(1), m.group(2), line))
                # Skip blank lines / non-matching lines inside the section.
                continue
            head_lines.append(line)

    recorded_finals = {entry[0] for entry in existing_entries}
    new_entries: list[tuple[str, str, str]] = []
    for final_slug, original in collisions:
        if final_slug in recorded_finals:
            continue
        new_entries.append((
            final_slug, original,
            f"- `{final_slug}` collided with `{original}` on {today}",
        ))

    # If we have nothing to write AND no existing collisions, skip the index
    # rewrite entirely — preserves idempotency for non-colliding runs.
    if not existing_entries and not new_entries:
        return

    out_lines = list(head_lines)
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    if out_lines:
        out_lines.append("")
    out_lines.append(_COLLISION_HEADER)
    out_lines.append("")
    for entry in existing_entries + new_entries:
        out_lines.append(entry[2])
    out_lines.append("")
    index.write_text("\n".join(out_lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consolidate events log into wiki/<slug>.md pages")
    parser.add_argument("--brain", required=True, type=Path, help="Brain root directory")
    parser.add_argument("--scope", default="default", help="Corpus scope tag for new pages")
    parser.add_argument("--rebuild", action="store_true",
                        help="Delete existing wiki/*.md first (schema-bump remediation)")
    args = parser.parse_args(argv)

    actions = write_wiki(args.brain, scope=args.scope, rebuild=args.rebuild)
    created = sum(1 for v in actions.values() if v == "created")
    updated = sum(1 for v in actions.values() if v == "updated")
    unchanged = sum(1 for v in actions.values() if v == "unchanged")
    print(f"wiki_init: {created} created, {updated} updated, {unchanged} unchanged",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
