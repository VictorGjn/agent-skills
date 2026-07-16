#!/usr/bin/env python3
"""
companybrain MCP server — local stdio.

Wraps cb_engine.py's six JSON-native endpoints as MCP tools so an MCP-aware
client (Claude Code) can read/write/pack company-brain entities through a
typed protocol instead of touching the JSON files directly.

Install (uv recommended; same dep as the CE / entitystore MCP):
    uv add "mcp[cli]" jsonschema requests

Run:
    python cb_mcp.py                    # stdio (default)
    python cb_mcp.py --self-test        # delegate to engine self-test

Configure:
    CB_CORPUS_DIR        — path to corpora/<id>/  (required)
    CB_SCHEMA_PATH       — path to entity.schema.json (required)
    MISTRAL_API_KEY      — enables semantic mode (or OPENAI_API_KEY)
    CB_EMBED_PROVIDER    — override auto-detection (mistral | openai)
    CB_EMBED_MODEL       — override default model

The MCP server returns JSON-encoded strings (FastMCP serializes tool returns
as text). Clients parse the JSON themselves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts dir to path so `import cb_engine` works regardless of cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from mcp.server.fastmcp import FastMCP

import cb_engine

mcp = FastMCP(
    "companybrain",
    instructions=(
        "JSON-native EntityStore over company-brain entities. "
        "Six endpoints — wiki_ask (substring | semantic | hybrid search + "
        "wiki_link neighborhood expansion), wiki_pack (depth-banded answer "
        "bundle within a token budget), wiki_audit (charter-aware "
        "contradictions + dead_links + freshness + orphans + schema), "
        "wiki_add (validate + write + optional git commit-through), stats "
        "(counts + freshness + embedding status), resolve (slug/alias -> "
        "canonical id). All responses are JSON-encoded strings."
    ),
)


def _response(payload) -> str:
    """One serialization convention for every tool reply."""
    return json.dumps(payload, default=str, ensure_ascii=False)


@mcp.tool()
def wiki_ask(
    query: str,
    kind: str | None = None,
    topics: list[str] | None = None,
    depth: int = 1,
    budget: int = 8000,
    mode: str = "hybrid",
    top: int = 30,
    freshness_floor: float | None = None,
    require_verified: bool = False,
    corpus_dir: str | None = None,
) -> str:
    """Search entities + expand wiki_link neighborhood to depth N.

    Args:
        query: free-text query. Refuses dump-all when query + kind + topics are all empty.
        kind:  optional filter — concept|org|person|post|vessel|navigation|product.
                Scope filtering; see freshness_floor for post-match filtering.
        topics: optional intersection filter. Scope filtering.
        depth: wiki_link hops to expand (0 = matched only, default 1).
        budget: soft cap on serialized output chars (~ tokens × 4). Eviction
                drops lowest-SCORED items first (not LIFO).
        mode: "substring" | "semantic" | "hybrid" (default — fast keyword OR
              embedding similarity, whichever scores higher per entity).
        top: max matched entities pre-truncation.
        freshness_floor: optional freshness score threshold [0.0, 1.0]. Matched entities
                         with freshness_policy.compute_freshness < floor are dropped
                         (post-cap, pre-budget). Pre-rule entities (no last_verified_at)
                         pass by default unless require_verified=True.
        require_verified: when True, drop pre-rule entities if freshness_floor is set.
        corpus_dir: override CB_CORPUS_DIR for this call.

    Returns:
        JSON: {matched[], neighbors[], stats (includes dropped_by_freshness if floor set)}.
    """
    return _response(cb_engine.wiki_ask(
        query=query, corpus_dir=corpus_dir, kind=kind, topics=topics,
        depth=depth, budget=budget, mode=mode, top=top,
        freshness_floor=freshness_floor, require_verified=require_verified,
    ))


@mcp.tool()
def wiki_pack(
    query: str,
    budget: int = 8000,
    kind: str | None = None,
    topics: list[str] | None = None,
    mode: str = "hybrid",
    top: int = 50,
    include_neighbors: bool = True,
    corpus_dir: str | None = None,
) -> str:
    """Pack a depth-banded answer bundle within a token budget.

    Unlike wiki_ask (which returns full entities + name-only neighbors and
    can truncate), wiki_pack DEMOTES the depth of lower-priority entities
    (Full -> Detail -> Summary -> Headlines -> Mention) until the bundle
    fits the budget. Top hits stay Full, the long tail collapses to
    one-line mentions.

    Args:
        query: free-text query.
        budget: soft token budget for the whole bundle.
        kind / topics: same filters as wiki_ask.
        mode: "substring" | "semantic" | "hybrid" (default).
        top: max entities to consider before depth-banding.
        include_neighbors: expand wiki_links once when packing (default True).
        corpus_dir: override CB_CORPUS_DIR.

    Returns:
        JSON: {query, budget, used_tokens, items: [{id, kind, depth, depth_name,
               tokens, payload, via}, ...], stats}.
    """
    return _response(cb_engine.wiki_pack(
        query=query, corpus_dir=corpus_dir, kind=kind, topics=topics,
        budget=budget, mode=mode, top=top, include_neighbors=include_neighbors,
    ))


@mcp.tool()
def wiki_audit(
    kinds: list[str] | None = None,
    corpus_dir: str | None = None,
) -> str:
    """Run the five audit checks on the corpus.

    1. contradictions    — charter-normalized claim key collisions with different values
    2. dead_links        — wiki_link targets that don't exist
    3. freshness_expired — entities past their kind-specific staleness threshold
    4. orphans           — no inbound refs + no claims + no evidence + no concept.statement
    5. schema_invalid    — entities failing entity.schema.json validation

    Args:
        kinds: optional filter to a subset of kinds (audit_count reflects the filter;
               entity_count_total still reports the corpus-wide count).
        corpus_dir: override CB_CORPUS_DIR.

    Returns:
        JSON with detailed findings + summary counts.
    """
    return _response(cb_engine.wiki_audit(corpus_dir=corpus_dir, kinds=kinds))


@mcp.tool()
def wiki_add(
    entity: dict,
    commit: bool = True,
    corpus_dir: str | None = None,
) -> str:
    """Validate (against entity.schema.json) + write + optionally git commit.

    Path-traversal: slug must match [a-z0-9][a-z0-9._-]* (no slashes, no '..').
    Schema path resolves: CB_SCHEMA_PATH env > <corpus>/../../schemas/entity.schema.json.

    Args:
        entity: the entity dict (must conform to entity.schema.json v5).
        commit: when True (default), git-add + git-commit the new/changed
                file after a successful write. Skips cleanly if the path
                isn't inside a git repo. Set False for batch flows where
                you commit N writes together.
        corpus_dir: override CB_CORPUS_DIR.

    Returns:
        JSON: {ok: true, id, path, validated_at, op, git?} on success,
              {ok: false, error_kind, message, details?} on validation failure.
    """
    return _response(cb_engine.wiki_add(entity, corpus_dir=corpus_dir,
                                        commit=commit))


@mcp.tool()
def stats(corpus_dir: str | None = None) -> str:
    """Corpus counts, by-kind / by-topic breakdowns, freshness percentiles,
    and embedding-cache status.

    Args:
        corpus_dir: override CB_CORPUS_DIR.

    Returns:
        JSON: {corpus, entity_count, by_kind, by_topic, wiki_links_total,
               claims_total, freshness, schema_version, embeddings}.
    """
    return _response(cb_engine.stats(corpus_dir=corpus_dir))


@mcp.tool()
def resolve(
    query: str,
    top_k: int = 10,
    corpus_dir: str | None = None,
) -> str:
    """Resolve a slug / alias / partial name to canonical entity ids.

    Tiers: exact id (1.0) > exact slug (0.95) > exact name (0.9) >
    substring on name (0.50-0.89, longer overlap higher) > summary substring (0.30).

    Args:
        query: the string to resolve.
        top_k: max matches to return (default 10).
        corpus_dir: override CB_CORPUS_DIR.

    Returns:
        JSON: {matches: [{id, kind, names, score}, ...]}.
    """
    return _response(cb_engine.resolve(query, corpus_dir=corpus_dir, top_k=top_k))


def _list_tools() -> list[str]:
    return ["wiki_ask", "wiki_pack", "wiki_audit", "wiki_add", "stats", "resolve"]


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(cb_engine._self_test_in_tempdir())
    if "--list-tools" in sys.argv:
        for n in _list_tools():
            print(n)
        sys.exit(0)
    mcp.run()
