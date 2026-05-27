"""impact_of.py — entity-rooted impact closure over wiki/<slug>.md pages.

Implements the only surviving item from the abandoned job-shaped-mcp-surface
RFC: a verb that answers "given an entity, what's affected by it?" — with
recall guarantees and hub stop-list to keep the answer useful on real
brains. See ``plan/proposals/wiki-impact-of.md`` for design rationale.

Algorithm:
1. Resolve `entity` arg against slug / id / case-insensitive title.
2. Load all wiki pages via the same primitives audit.py uses (validate_page
   gating, single-pass body extraction).
3. Build inbound mention index from wikiref.parse_wikirefs filtered to
   kind="slug" — section/code refs are out of scope for v0.1.
4. Add supersession edges: each `kind: decision` page with non-null
   superseded_by adds an edge superseded_by_slug -> predecessor_slug.
5. BFS to max_hops with visited-set dedupe.
6. Hub stop-list: skip traversal through any node with > HUB_THRESHOLD
   inbound mentions (still report direct hits; just don't fan out).
7. Score each affected node as 1/(1+hops) * kind_mult * freshness_mult.

Used by:
- ``mcp_server.py`` — wiki.impact_of MCP tool.
- ``audit.py`` (future) — could surface "entity X has 0 affected, candidate
  for archive" via the same primitive.

Per ``plan/proposals/wiki-impact-of.md`` v0.1.
"""
from __future__ import annotations

import os
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from .audit import _load_pages
    from .freshness_policy import compute_freshness_multi_source
    from .wikiref import parse_wikirefs
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from wiki.audit import _load_pages  # type: ignore[no-redef]
    from wiki.freshness_policy import compute_freshness_multi_source  # type: ignore[no-redef]
    from wiki.wikiref import parse_wikirefs  # type: ignore[no-redef]


# Default hub threshold — entities with strictly more than this many inbound
# mentions are treated as hubs and not traversed *through*. They still surface
# as 1-hop affected when directly linked, but we don't fan out from them.
# Override via env CE_IMPACT_HUB_THRESHOLD (int >= 1).
DEFAULT_HUB_THRESHOLD = 10

# Per-kind base risk multiplier. Decisions affect more downstream than
# concepts because a superseded decision invalidates structural claims;
# code-backlinked entities are slightly less impactful than first-class
# concepts because they're typically narrower.
KIND_MULTIPLIER = {
    "decision": 1.0,
    "component": 0.9,
    "concept": 0.8,
    "code": 0.7,
    "actor": 0.6,
    "process": 0.6,
    "metric": 0.6,
}
DEFAULT_KIND_MULTIPLIER = 0.6

# Freshness clamp lower bound — even fully-decayed entities register as
# affected (just downweighted), since stale references in the brain are
# load-bearing data for the operator.
FRESHNESS_FLOOR = 0.3


@dataclass(frozen=True)
class AffectedEntity:
    slug: str
    kind: str
    title: str
    hops: int
    risk: float
    edge_kinds: tuple[str, ...]      # path of edge kinds taken to reach
    source_slug: str                  # immediate parent in BFS tree


@dataclass
class ImpactResult:
    entity_slug: str | None
    entity_fm: dict | None
    affected: list[AffectedEntity]
    skipped_hubs: list[tuple[str, int]]  # (slug, inbound_count)
    recall: str                          # "100%" or "best-effort"
    error: str | None = None             # ENTITY_NOT_FOUND, ENTITY_AMBIGUOUS, etc.
    error_detail: list[str] | None = None  # candidates for ambiguous/not-found


def _hub_threshold() -> int:
    raw = os.environ.get("CE_IMPACT_HUB_THRESHOLD")
    if raw:
        try:
            v = int(raw)
            if v >= 1:
                return v
        except ValueError:
            pass
    return DEFAULT_HUB_THRESHOLD


def _resolve_entity(
    entity_arg: str,
    pages: dict[str, dict],
) -> tuple[str | None, list[str], str]:
    """Resolve an entity arg to a slug.

    Returns ``(slug, candidates, error_kind)`` where ``error_kind`` is:
        - "" — slug resolved, candidates empty
        - "ambiguous" — multiple title hits, candidates lists them
        - "not_found" — no resolution, candidates lists closest slugs

    Lookup order:
    1. Exact slug match.
    2. Exact id match.
    3. Case-insensitive title match (single hit only; multiple = ambiguous).
    4. Closest-3 slug suggestions on miss (substring containment, not Levenshtein).
    """
    if entity_arg in pages:
        return entity_arg, [], ""

    for slug, page in pages.items():
        if page["fm"].get("id") == entity_arg:
            return slug, [], ""

    needle = entity_arg.lower().strip()
    title_hits = [
        slug for slug, page in pages.items()
        if (page["fm"].get("title") or "").lower().strip() == needle
    ]
    if len(title_hits) == 1:
        return title_hits[0], [], ""
    if len(title_hits) > 1:
        return None, sorted(title_hits), "ambiguous"

    suggestions = sorted(
        s for s in pages if needle in s.lower() or s.lower() in needle
    )[:3]
    return None, suggestions, "not_found"


def _build_mention_index(
    pages: dict[str, dict],
) -> dict[str, list[tuple[str, str]]]:
    """target_slug -> [(source_slug, edge_kind), ...]

    Edge kinds emitted today: "mentions", "supersedes". Section/code refs
    from parse_wikirefs are filtered out per v0.1 scope.
    """
    inbound: dict[str, list[tuple[str, str]]] = {}

    # Body wikirefs (kind="slug" only).
    for src_slug, page in pages.items():
        for ref in parse_wikirefs(page["body"]):
            if ref.kind != "slug":
                continue
            inbound.setdefault(ref.target, []).append((src_slug, "mentions"))

    # Supersession edges: a superseded decision still affects the page that
    # superseded it (operator may want to trace back from the new decision
    # to the predecessor's referencers). Edge points superseded_by -> predecessor.
    for src_slug, page in pages.items():
        fm = page["fm"]
        if fm.get("kind") != "decision":
            continue
        sb = fm.get("superseded_by")
        if not sb or sb in ("null", ""):
            continue
        # superseded_by may be an id or a slug; normalize to slug if possible.
        sb_slug = sb if sb in pages else None
        if sb_slug is None:
            for s, p in pages.items():
                if p["fm"].get("id") == sb:
                    sb_slug = s
                    break
        if sb_slug:
            inbound.setdefault(src_slug, []).append((sb_slug, "supersedes"))

    return inbound


def _kind_mult(kind: str) -> float:
    return KIND_MULTIPLIER.get(kind, DEFAULT_KIND_MULTIPLIER)


def _freshness_mult(fm: dict, sources: list[dict]) -> float:
    last_verified = fm.get("last_verified_at")
    if not last_verified or last_verified == "null":
        return 1.0
    source_types = [s["type"] for s in sources if s.get("type")] or ["default"]
    try:
        score = compute_freshness_multi_source(last_verified, source_types)
    except (ValueError, TypeError):
        return FRESHNESS_FLOOR
    return max(FRESHNESS_FLOOR, min(1.0, score))


def compute_impact(
    entity_arg: str,
    brain_dir: Path,
    *,
    max_hops: int = 3,
    relation_kinds: Optional[list[str]] = None,
    min_weight: float = 0.0,
    include_hubs: bool = False,
) -> ImpactResult:
    """Pure-Python core. The MCP wrapper handles markdown rendering + telemetry.

    Returns ImpactResult; never raises on missing brain or unknown entity
    (those become error fields in the result, mirroring wiki.ask behavior).
    """
    wiki_dir = brain_dir / "wiki"
    if not wiki_dir.exists():
        return ImpactResult(
            entity_slug=None, entity_fm=None, affected=[],
            skipped_hubs=[], recall="100%",
            error="BRAIN_NOT_FOUND",
            error_detail=[str(brain_dir)],
        )

    pages, _warnings = _load_pages(wiki_dir)
    if not pages:
        return ImpactResult(
            entity_slug=None, entity_fm=None, affected=[],
            skipped_hubs=[], recall="100%",
            error="EMPTY_CORPUS",
            error_detail=[str(wiki_dir)],
        )

    slug, candidates, error_kind = _resolve_entity(entity_arg, pages)
    if slug is None:
        err = "ENTITY_AMBIGUOUS" if error_kind == "ambiguous" else "ENTITY_NOT_FOUND"
        return ImpactResult(
            entity_slug=None, entity_fm=None, affected=[],
            skipped_hubs=[], recall="100%",
            error=err, error_detail=candidates,
        )

    inbound = _build_mention_index(pages)
    threshold = _hub_threshold()
    inbound_counts = {s: len(refs) for s, refs in inbound.items()}
    hubs = {
        s for s, c in inbound_counts.items()
        if c > threshold and s != slug  # never treat the queried entity as a hub
    }

    allowed_kinds = set(relation_kinds) if relation_kinds else None

    # BFS. Visited tracks first hop count seen for a slug; we keep the
    # shortest path (smallest hops). edges_to[slug] = (source_slug, edge_kind).
    visited: dict[str, int] = {slug: 0}
    edge_path: dict[str, tuple[str, str]] = {}  # target -> (source, edge_kind)
    queue: deque[str] = deque([slug])
    skipped_through_hub: list[tuple[str, int]] = []

    while queue:
        cur = queue.popleft()
        hops = visited[cur]
        if hops >= max_hops:
            continue

        # Find pages mentioning cur (forward direction = "what's affected by cur")
        # We use the inbound index inverted: a page X has cur in its body =
        # X mentions cur = X is affected by cur (when cur changes, X needs
        # re-reading). This is the inbound list keyed by cur.
        direct_affected = inbound.get(cur, [])
        for src_slug, edge_kind in direct_affected:
            if allowed_kinds is not None and edge_kind not in allowed_kinds:
                continue
            if src_slug in visited:
                continue  # shorter path already found
            visited[src_slug] = hops + 1
            edge_path[src_slug] = (cur, edge_kind)

            # Hub stop: we record the hop but don't fan out from it unless
            # include_hubs is True.
            if not include_hubs and src_slug in hubs:
                skipped_through_hub.append((src_slug, inbound_counts[src_slug]))
                continue
            queue.append(src_slug)

    # Build AffectedEntity records.
    affected: list[AffectedEntity] = []
    for s, hops in visited.items():
        if s == slug:
            continue
        page = pages.get(s)
        if not page:
            continue
        fm = page["fm"]
        srcs = page.get("sources") or []
        kind = fm.get("kind") or "concept"
        title = fm.get("title") or s
        risk = (1.0 / (1 + hops)) * _kind_mult(kind) * _freshness_mult(fm, srcs)
        if risk < min_weight:
            continue
        # Reconstruct edge_kinds path.
        path_edges: list[str] = []
        cursor = s
        while cursor in edge_path:
            parent, ek = edge_path[cursor]
            path_edges.append(ek)
            cursor = parent
            if cursor == slug:
                break
        path_edges.reverse()

        source_slug = edge_path[s][0] if s in edge_path else slug
        affected.append(AffectedEntity(
            slug=s, kind=kind, title=title, hops=hops, risk=round(risk, 3),
            edge_kinds=tuple(path_edges), source_slug=source_slug,
        ))

    # Sort: shortest hops first, then highest risk, then slug.
    affected.sort(key=lambda a: (a.hops, -a.risk, a.slug))

    recall = "100%" if not skipped_through_hub else "best-effort"
    return ImpactResult(
        entity_slug=slug,
        entity_fm=pages[slug]["fm"],
        affected=affected,
        skipped_hubs=skipped_through_hub,
        recall=recall,
        error=None,
        error_detail=None,
    )


def render_markdown(result: ImpactResult, *, budget: int = 8000) -> str:
    """Render an ImpactResult as markdown, capped at budget*4 chars.

    Layout: header comment + entity card + affected table + sources +
    hub-exclusion footer. Truncates the affected table if budget runs out.
    """
    char_cap = budget * 4
    if result.error:
        if result.error == "ENTITY_NOT_FOUND":
            hint = (f". Did you mean: {', '.join(result.error_detail)}"
                    if result.error_detail else "")
            return f"<!-- wiki.impact_of: ENTITY_NOT_FOUND{hint} -->"
        if result.error == "ENTITY_AMBIGUOUS":
            return (f"<!-- wiki.impact_of: ENTITY_AMBIGUOUS — multiple titles match. "
                    f"Pass slug instead: {', '.join(result.error_detail or [])} -->")
        if result.error == "BRAIN_NOT_FOUND":
            path = (result.error_detail or ["?"])[0]
            return f"<!-- wiki.impact_of: no wiki/ directory at {path} -->"
        if result.error == "EMPTY_CORPUS":
            path = (result.error_detail or ["?"])[0]
            return f"<!-- wiki.impact_of: empty corpus at {path} -->"

    fm = result.entity_fm or {}
    out: list[str] = [
        f"<!-- wiki.impact_of entity={result.entity_slug} "
        f"affected={len(result.affected)} recall={result.recall} -->",
        "",
        f"## {fm.get('title') or result.entity_slug}",
        f"kind: {fm.get('kind', 'concept')} · scope: {fm.get('scope', 'default')} "
        f"· last_verified: {fm.get('last_verified_at', 'unknown')}",
        "",
        f"## Affected entities — {len(result.affected)} found, recall: {result.recall}",
        "",
    ]
    if not result.affected:
        out.append("_No affected entities found at hops≤max._")
    else:
        out.append("| slug | kind | hops | risk | edge |")
        out.append("|---|---|---|---|---|")
        used = sum(len(line) + 1 for line in out)
        truncated_at = None
        for i, ae in enumerate(result.affected):
            edge = " → ".join(ae.edge_kinds) or "—"
            row = f"| {ae.slug} | {ae.kind} | {ae.hops} | {ae.risk} | {edge} |"
            if used + len(row) + 1 > char_cap:
                truncated_at = i
                break
            out.append(row)
            used += len(row) + 1
        if truncated_at is not None:
            remaining = len(result.affected) - truncated_at
            out.append(f"<!-- wiki.impact_of: truncated at budget; "
                       f"{remaining} more not shown -->")

    if result.skipped_hubs:
        out.append("")
        out.append("## Recall: best-effort")
        out.append("")
        out.append(f"Skipped traversal through {len(result.skipped_hubs)} hub "
                   "entities (still reported as 1-hop affected, just not "
                   "fanned through):")
        for hub_slug, count in sorted(result.skipped_hubs):
            out.append(f"- `{hub_slug}` — {count} inbound mentions")
        out.append("")
        out.append("Pass `include_hubs=true` to bypass.")

    return "\n".join(out)
