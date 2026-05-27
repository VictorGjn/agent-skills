---
name: entitystore
description: The schema-agnostic EntityStore engine — raw/events/wiki tiers, Source ABC, semantic-shift consolidator, contradiction auditor, depth-aware packer, and MCP. Carved from context-engineering. Reads the entity schema BY PATH (it ships no schema of its own); the canonical schema + entities live in company-brain, and domain Sources/connectors live in syroco-product-ops. Use when building or querying a provenance-tracked entity brain over any corpus, consolidating events into wiki entity pages, auditing for contradictions/drift, or packing entity context within a token budget. Do NOT use for code-context packing or code-knowledge-graph visualisation (that's the context-engineering skill, which now depends on this engine).
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

## Carve status (in progress)

- **Done:** engine core copied here (`scripts/wiki/*`, `pack_context*`,
  `mcp_server`, `embed_resolve`, `mmr`); schema-injection validator proven
  against company-brain v4.
- **Next:** point `context-engineering` at this engine and shrink it to a thin
  code-context wrapper (keep code_graph/viz/CSB-bench there); reconcile the wiki
  tier to emit company-brain's JSON entity schema; then delete CE's duplicated
  engine copies (only once CE's suite stays green).

## Not in scope

Code-context packing, code-AST indexing, code-knowledge-graph viz, CSB
benchmarks — those stay in `context-engineering`, which depends on this engine.
