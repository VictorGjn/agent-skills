---
name: entitystore
description: The schema-agnostic EntityStore engine — raw/events/wiki tiers, Source ABC, semantic-shift consolidator, contradiction auditor, depth-aware packer, and MCP. Carved from context-engineering. Reads the entity schema BY PATH (it ships no schema of its own); the canonical schema + entities live in company-brain, and domain Sources/connectors live in syroco-product-ops. Use when building or querying a provenance-tracked entity brain over any corpus, consolidating events into wiki entity pages, auditing for contradictions/drift, or packing entity context within a token budget. Do NOT use for code-context packing or code-knowledge-graph visualisation (that's the context-engineering skill, which continues independently — entitystore does not replace it).
---

# entitystore

The EntityStore **engine** — the machinery behind a provenance-tracked company
brain. Carved out of `context-engineering` so the engine is reusable and
**schema-agnostic**: it never embeds an entity schema, it reads one by path.

## Why it exists (the split)

| Layer | Owns | Lives in |
|---|---|---|
| **entitystore** (this skill) | engine: raw/events/wiki tiers, Source ABC, consolidator, auditor, packer, MCP | here (sibling of `context-engineering`) |
| **schema + canonical entities** | `entity.schema.json` + the git source-of-truth entities | `company-brain` |
| **domain Sources / connectors** | company-knowledge→events, HubSpot / Notion / Granola adapters | `syroco-product-ops` |

Because the schema lives with the data (not the engine), **adding a new entity
kind or property is a schema + data change with ZERO engine change.** The engine
validates and packs whatever schema company-brain hands it.

## The three tiers (unchanged from CE)

```
brain/
├── raw/      verbatim source bytes, content-addressed   (Sources via fetch())
├── events/   append-only JSONL, one event = one claim   (Sources via emit_events())
└── wiki/     consolidated entity pages, every claim → source   (wiki_init + audit)
```

- **Consolidate only on cosine drift** (`semantic_shift.py`) — the wiki doesn't
  churn on every event.
- **Auditor** (`audit.py`) surfaces splits / merges / contradictions (with
  per-side provenance) / dead links / freshness expiries.
- **Packer** (`pack_context*.py`) assembles a query-driven context bundle.

## Schema-injection contract

Every validation / emission entrypoint takes `--schema <path>`. Reference impl:
`scripts/validate_corpus.py --schema <entity.schema.json> --corpus <entities/>`.
The engine holds no opinion on kinds, claims, or overlays — those are the
schema's job.

## Status — JSON-native MCP SHIPPED + audit-pass landed (2026-05-28)

The engine pivoted to **JSON-native** after empirics overturned the "build on
gbrain as library" call. Same day, a /code-review pass reversed three "wrong
defers" (semantic, depth-banding, git commit-through) and a /simplify pass
deleted ~3000 LOC of CE markdown carcass. See `SURFACE.md` for the locked v1
contract and the memory files (`project_syroco_company_brain`,
`gbrain-vs-anabasis`) for the premortem and revised verdict.

- **Shipped (v1, post-audit):** `scripts/cb_engine.py` (six JSON-native
  endpoints) + `scripts/cb_mcp.py` (local stdio FastMCP server) +
  `scripts/cb_embed.py` (semantic resolver — Mistral default, OpenAI
  fallback) + `scripts/cb_mcp_smoke.py` (live wire-protocol smoke test) +
  `scripts/validate_corpus.py` (schema-injection seam). Engine self-test
  15/15 PASS (incl. negative test for contradiction detector + path-traversal
  guard, all running in a tempdir copy of the corpus). MCP smoke test 17/17
  PASS over real JSON-RPC against the live 248-entity `syroco-commercial`
  corpus. Charter-aware auditor logic ported from
  `company-brain/scratch/promote_gate.py`. Schema-injection seam preserved
  (`CB_SCHEMA_PATH` env). Registered in `~/.claude.json` as `companybrain` MCP.
- **Six endpoints:** `wiki_ask` (substring | semantic | hybrid + neighborhood) /
  `wiki_pack` (depth-banded budget-bounded bundle: Full/Detail/Summary/
  Headlines/Mention) / `wiki_audit` (charter-aware contradictions +
  dead_links + freshness + orphans + schema_invalid) / `wiki_add` (validate +
  write + git commit-through, path-traversal-safe) / `stats` (counts +
  freshness + embedding-provider status) / `resolve` (slug/alias/name).
- **Wrong-defer reversals (live-proven):** semantic via Mistral, depth-banded
  `wiki_pack` (43 items packed into 3997/4000 token budget), git commit-through
  (real commit sha returned via subprocess git invocation).
- **Honest deferrals to v1.1:** structured-query (filter on claim values),
  cross-corpus, Vercel/HTTP + OAuth, embedding-cache management UI.

## Not in scope

Code-context packing, code-AST indexing, code-knowledge-graph viz, CSB
benchmarks — those stay in `context-engineering`, which depends on this engine.
