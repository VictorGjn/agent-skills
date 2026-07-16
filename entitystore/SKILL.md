---
name: entitystore
description: The schema-agnostic EntityStore engine — JSON-native entity corpus, Source ABC, contradiction/merge/split/freshness auditor, depth-aware packer, a regenerable wiki/<slug>.md page layer (M11), and MCP. Carved from context-engineering. Reads the entity schema BY PATH (it ships no schema of its own); the canonical schema + entities live in company-brain, and domain Sources/connectors live in syroco-product-ops. Use when building or querying a provenance-tracked entity brain over any corpus, seeding/auditing wiki entity pages, auditing for contradictions/drift/staleness, or packing entity context within a token budget. Do NOT use for code-context packing or code-knowledge-graph visualisation (that's the context-engineering skill, which continues independently — entitystore does not replace it).
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

## Storage: JSON-native, not CE's three-tier raw/events/wiki

Superseded by the JSON-native pivot (2026-05-28, see SURFACE.md "Status"):
entitystore has no `raw/` or `events/` tier and no events-log consolidator.
`corpora/<id>/entities/**/*.json` (git-committed, enricher-written) is the
one source of truth every endpoint reads directly.

`corpora/<id>/wiki/*.md` DOES exist again as of M11 — but it's this repo's
own `scripts/wiki_init.py` deriving pages straight from the JSON entities
(a regenerable projection), not CE's events-log consolidator of the same
name. See SURFACE.md "Wiki pages (M11)" for the contract; don't confuse the
two `wiki_init.py`s if you're cross-referencing `context-engineering/scripts/wiki/`.

- **Auditor** (`cb_engine.py`'s `wiki_audit`) surfaces contradictions / dead
  links / freshness expiries / orphans / schema-invalid, plus (M11) merge /
  split / stale-supersession / `last_verified_at` freshness lints —
  `render_proposals` writes them to `audit/proposals.md`.
- **Packer** (`wiki_pack` in `cb_engine.py`) assembles a depth-banded,
  budget-bounded context bundle.

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

## Wiki/document-entity layer (M11)

`scripts/wiki_init.py` seeds `corpora/<id>/wiki/<slug>.md` — one page per
entity, derived straight from the JSON corpus (**not** an events log; that
was CE's dropped model). A page is a **regenerable projection**, never a
second source of truth: `--rebuild` reproduces it byte-for-byte from the
entities, nothing here writes an entity or a claim (THE WRITER RULE holds).
Cap-aware, pointer-based (links to ids/refs, never copies claim evidence
quotes), carries `last_verified_at` verbatim + decision-continuity fields
(`supersedes`/`superseded_by`/`valid_until`), and never stores a
`freshness_score`/`confidence`/`trust`/`tier` field.

`scripts/freshness_policy.py` computes freshness **on read only** from
`last_verified_at` with per-kind half-lives; a missing timestamp is
`score=None, status="pre-rule, never verified"` — never `0.0`, never an
error (most of the live corpus is pre-rule as of M11 — person 0/331,
vessel 0/131 `last_verified_at` coverage).

`cb_engine.py`'s `wiki_audit` gained four more report-only lints — merge
candidates, split candidates, stale supersessions, and the
`last_verified_at`-first freshness lint — and `cb_engine.py wiki-audit
--proposals` renders the full result to `<corpus>/audit/proposals.md`.
None of this is an MCP tool addition; it's a CLI/library layer SURFACE.md's
six MCP endpoints don't depend on. See SURFACE.md "Wiki pages (M11)" for
the full contract.

## Not in scope

Code-context packing, code-AST indexing, code-knowledge-graph viz, CSB
benchmarks — those stay in `context-engineering`, which depends on this engine.
