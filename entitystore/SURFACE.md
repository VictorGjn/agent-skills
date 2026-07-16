# entitystore — JSON-native MCP surface (v1, revised post-audit)

The contract the engine builds against. Locked 2026-05-28 (Option B pivot: JSON-native, no gbrain dependency). **Revised same day** after a /code-review pass that exposed three "wrong defers"; semantic search, depth-banded `wiki_pack`, and git commit-through landed in v1, not v1.1.

## Goal (v1)

A **local stdio MCP server** exposing the company-brain JSON entity corpus as **six** typed endpoints. Single-user (Victor). No auth/RBAC. Schema-injection seam preserved: the engine reads `entity.schema.json` by path, never embeds.

## Non-goals (v1)

- Multi-user / multi-tenant / OAuth / RBAC.
- Vercel / HTTP deployment.
- Markdown wiki pages (CE's model — dropped).
- Code-context packing (CE's domain — stays in CE).
- Cross-corpus operations (single corpus per MCP instance).

## Storage model

```
company-brain/
├── schemas/entity.schema.json            # v5; schema-injection target
└── corpora/
    └── <corpus_id>/
        ├── manifest.json
        └── entities/
            ├── concept/<slug>.json
            ├── org/<slug>.json
            ├── person/<slug>.json
            ├── post/<slug>.json
            └── vessel/<slug>.json
```

- Git = source of truth (unchanged).
- Engine reads/writes JSON files directly.
- One `corpus_id` per MCP instance (env var `CB_CORPUS_DIR`, default `syroco-commercial`).

## Endpoints

### 1. `wiki_ask(query, kind?, topics?, depth?, budget?, mode?, top?, freshness_floor?, require_verified?) → JSON`

Read entities matching the query; return matched entities + their wiki_link neighborhood (depth-bounded). **Refuses dump-all** when query + kind + topics are all empty.

**Request**
```json
{
  "query": "route optimization",
  "kind": "concept",                 // optional: concept|org|person|post|vessel|navigation|product
  "topics": ["commercial"],          // optional intersection filter (scope)
  "depth": 1,                         // wiki_link neighborhood expansion
  "budget": 8000,                     // soft char cap (~ tokens × 4); evicts LOWEST-scored first
  "mode": "hybrid",                   // "substring" | "semantic" | "hybrid" (default)
  "top": 30,                          // max matched entities pre-truncation
  "freshness_floor": 0.5,             // optional: post-cap, pre-budget freshness filter [0.0, 1.0]
  "require_verified": false           // optional: when True, drop pre-rule entities if freshness_floor set
}
```

**Response**
```json
{
  "matched": [ { "id": "concept:opportunity-route-optimization", ...full entity... } ],
  "neighbors": [ { "id": "org:kcc", "kind": "org", "names": [...], "summary": "..." } ],
  "stats": { "matched": N, "neighbors": M, "truncated": false,
             "mode": "hybrid", "semantic_used": true, "dropped_by_freshness": K }
}
```

**Scope** (`kind` / `topics`): Pre-filter by entity kind or topic intersection. Filters happen before scoring.

**Freshness floor** (`freshness_floor` / `require_verified`): Post-cap, pre-budget scoring filter. When `freshness_floor` is set to a value in [0.0, 1.0]:
- Matched entities with `freshness_policy.compute_freshness(last_verified_at, source_types) >= freshness_floor` are kept.
- **Pre-rule entities** (those with no `last_verified_at` field) have `score=None` and **PASS the floor by default** (backward-compat) unless `require_verified=True` is set, which forces them to be dropped.
- Verified reality (M11 audits): 739/783 corpus entities are pre-rule, so a naive `freshness_floor` without `require_verified=False` (default) would drop most/all matched entities.
- `dropped_by_freshness` count is included in stats when the floor is set, showing how many entities were filtered out by freshness alone.

`mode="hybrid"` takes `max(substring_score, cosine_score)` per entity + a co-occurrence bonus. Falls back to substring if no embedding provider is configured. Neighbors return as `{id, kind, names, summary}` only.

### 2. `wiki_audit(corpus?, kinds?) → JSON`

Run the charter-aware auditor on the corpus. Five checks:

1. **`contradictions[]`** — claims with same charter-normalized key `(entity, metric, role, cp_type, tenor, status, as_of)` but different values. Ports `scratch/promote_gate.py` normalization. KCC[owner/owned/operating]=16 vs candidate=12 = real contradiction; KCC[owner/owned/on-order]=3 = coexists with operating=16.
2. **`dead_links[]`** — `wiki_links[]` referencing entity IDs that don't exist in the corpus.
3. **`freshness_expired[]`** — entities whose `updated_at` exceeds a kind-specific threshold (post: 90d, concept: 365d, org/person/vessel: 180d).
4. **`orphans[]`** — entities with zero inbound `wiki_links` AND zero `claims` AND zero `evidence`.
5. **`schema_invalid[]`** — entities failing `entity.schema.json` validation.

**Response**
```json
{
  "corpus": "syroco-commercial",
  "checked_at": "2026-05-28T...",
  "entity_count": 248,
  "contradictions": [ { "key": [...], "values": [{ "value": 16, "source": "..." }, { "value": 12, "source": "..." }] } ],
  "dead_links": [ { "from": "concept:foo", "to": "org:nonexistent" } ],
  "freshness_expired": [ { "id": "post:bar", "updated_at": "...", "days_stale": 120 } ],
  "orphans": [ { "id": "concept:baz" } ],
  "schema_invalid": [ { "id": "...", "error": "..." } ],
  "summary": { "contradictions": N, "dead_links": M, ... }
}
```

### 3. `wiki_add(entity, commit?) → JSON`

Validate against `entity.schema.json`, write to `corpora/<id>/entities/<kind>/<slug>.json`. **Slug must match `[a-z0-9][a-z0-9._-]*`** (no slashes, no `..`) — defends against path traversal. With `commit=True` (default), runs `git add + git commit -m "feat(brain): add|update <id>"` after a successful write. Skips cleanly when the target path isn't inside a git repo.

**Response**
```json
{ "ok": true, "id": "...", "path": "...", "validated_at": "...",
  "op": "add|update",
  "git": { "committed": true, "commit_sha": "...", "message": "...", "file": "..." } }
// or
{ "ok": false, "error_kind": "ValidationError|CorpusUnconfigured|SchemaUnconfigured",
  "message": "...", "details": {...} }
```

Set `commit=False` for batch flows where the caller commits N writes together.

### 4. `wiki_pack(query, budget, mode?, kind?, topics?, top?, include_neighbors?) → JSON`

Depth-banded answer bundle within a token budget. Top hits stay Full; the long tail demotes through Detail → Summary → Headlines → Mention until everything fits. This is what makes the engine useful for assembling answers — `wiki_ask` returns full entities + name-only neighbors and truncates at budget; `wiki_pack` *trades depth for breadth*.

**Request**
```json
{
  "query": "route optimization",
  "budget": 8000,
  "mode": "hybrid",
  "kind": "concept",                  // optional
  "include_neighbors": true,          // expand wiki_links once when packing
  "top": 50                            // max entities to consider pre-banding
}
```

**Response**
```json
{
  "query": "...", "budget": 8000, "used_tokens": 3997,
  "items": [
    { "id": "concept:opportunity-route-optimization", "kind": "concept",
      "depth": 0, "depth_name": "Full", "tokens": 1234,
      "payload": { ...full entity... }, "via": "matched" },
    { "id": "concept:eta-...", "depth": 2, "depth_name": "Summary",
      "tokens": 80, "payload": { "id": ..., "summary": ..., "concept_statement": ... } },
    { "id": "concept:weather-...", "depth": 4, "depth_name": "Mention",
      "tokens": 5, "payload": { "id": "concept:weather-...", "kind": "concept" } }
  ],
  "stats": { "items": 43, "dropped": 0,
             "depth_breakdown": { "Full": 5, "Summary": 2, "Mention": 36 },
             "mode": "hybrid", "semantic_used": true }
}
```

### 5. `stats(corpus?) → JSON`

Counts + breakdowns + freshness percentiles + **embedding-provider status**.

**Response**
```json
{
  "corpus": "syroco-commercial",
  "entity_count": 248,
  "by_kind": { "concept": 208, "org": 30, "person": 3, "post": 6, "vessel": 1 },
  "by_topic": { "commercial": 142, "competitive-intel": 39, ... },
  "wiki_links_total": 486,
  "freshness": { "p50_days": 1, "p90_days": 2, "p99_days": 3, "oldest_days": 4 },
  "claims_total": 31,
  "schema_version": 5,
  "embeddings": { "available": true, "provider": "mistral",
                  "model": "mistral-embed", "dims": 1024,
                  "cached_entities": 248, "entities_total": 248 }
}
```

### 6. `resolve(slug_or_alias) → JSON`

Resolve a slug / alias / partial name to a canonical entity URI.

**Request**: `{ "query": "Klaveness" }`

**Response**:
```json
{
  "matches": [
    { "id": "org:kcc", "kind": "org", "names": ["Klaveness Combination Carriers ASA", "KCC", "Klaveness"], "score": 1.0 }
  ]
}
```

Matches by `id` (exact), then `names[]` (case-insensitive substring), then slug-form. Top-K = 10.

## Validation contract

- Every `wiki_add` runs jsonschema validation against `schemas/entity.schema.json` (path from corpus parent dir).
- Schema path resolves: `CB_SCHEMA_PATH` env var > `<corpus>/../../schemas/entity.schema.json` > error.
- Engine carries no schema definitions internally.

## Error model

Typed errors as JSON:
```json
{ "ok": false, "error_kind": "ValidationError|NotFound|CorpusUnconfigured|SchemaUnconfigured", "message": "...", "details": {...} }
```

## Charter normalization (auditor)

Ported from `company-brain/scratch/promote_gate.py`:
```python
NORM = {
    "role":   {"ship-owner": "owner", "shipowner": "owner", "owner-operator": "operator"},
    "cp":     {"tc": "time", "vc": "voyage", "bb": "bareboat", "n/a": "owned"},
    "tenor":  {"period": "long", "short-period": "short", "long-period": "long", "": "n/a"},
    "status": {"total": "all", "in-service": "operating", "active": "operating",
               "newbuild": "on-order", "on order": "on-order"},
}
```
Two measurements collide only when every normalized key field matches and values differ. KCC-owner and LDC-charterer fleet counts never collide.

## Landed in v1 (audit-pass reversals of earlier "wrong defers")

- **`wiki_pack(query, budget, mode)`** — depth-banded context packer (Full / Detail / Summary / Headlines / Mention). Demotes lower-priority entities until the bundle fits the budget. Live test: 43 items fit in 3997/4000 tokens with 5 Full + 2 Summary + 36 Mention. The reason an entity engine exists vs `cat *.json`.
- **Semantic search** via `cb_embed.py` — Mistral default (`mistral-embed`, 1024d), OpenAI fallback. Per-corpus cache at `<corpus>/.cb_embed_cache.json` (gitignored). `wiki_ask(mode="hybrid")` takes the max of substring + cosine; falls back gracefully to substring when no API key is configured.
- **Git commit-through** on `wiki_add` — `commit=True` default runs `git add + git commit -m "feat(brain): add <id>"` after a successful write. Skips cleanly when not in a repo. Closes the silent-staleness failure mode at the engine ADD, where it belongs. Live-proven: commit sha `09d0f208da36` written + reverted as a sanity check.

## Deferred to v1.1 (honest defers)

- Structured-query interface (filter by claim measurement values, role, cp_type).
- Cross-corpus operations.
- Vercel / HTTP transport + OAuth.
- Embedding-cache management UI (rebuild, prune, diff).

## Test seam

`python cb_engine.py --self-test` runs all 5 endpoints against the live `syroco-commercial` corpus and prints pass/fail. Smoke-tests:
- `wiki_ask("route optimization")` returns ≥1 matched.
- `wiki_audit()` returns ≥0 contradictions, valid JSON, ≥1 dead_links flagged if any exist.
- `stats()` returns counts matching `find corpora/syroco-commercial/entities -name "*.json" | wc -l`.
- `resolve("Klaveness")` returns `org:kcc` as top match.
- `wiki_add(synthetic_entity)` round-trips through validator (synthetic gets cleaned up).

## File layout (final, post-/simplify)

```
agent-skills/entitystore/
├── SURFACE.md                # this file (v1 contract)
├── SKILL.md                  # skill description
└── scripts/
    ├── cb_engine.py          # engine — 6 endpoints, pure Python, no MCP dep
    ├── cb_mcp.py             # FastMCP local-stdio server wrapping engine
    ├── cb_embed.py           # semantic resolver (Mistral / OpenAI + cache)
    ├── cb_mcp_smoke.py       # live JSON-RPC smoke test (init + 6 tools)
    └── validate_corpus.py    # JSON Schema validator (schema-injection seam)
```

CE's markdown engine (`wiki/`, `mcp_server.py`, `pack_context*.py`, `embed_resolve.py`, `mmr.py` — ~3000 LOC) deleted in the /simplify pass. Git keeps the history.
