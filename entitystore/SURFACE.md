# entitystore — JSON-native MCP surface (v1, revised post-audit)

The contract the engine builds against. Locked 2026-05-28 (Option B pivot: JSON-native, no gbrain dependency). **Revised same day** after a /code-review pass that exposed three "wrong defers"; semantic search, depth-banded `wiki_pack`, and git commit-through landed in v1, not v1.1.

## Goal (v1)

A **local stdio MCP server** exposing the company-brain JSON entity corpus as **eight** typed endpoints. Single-user (Victor). No auth/RBAC. Schema-injection seam preserved: the engine reads `entity.schema.json` by path, never embeds.

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

## Classification cap (M7)

Every read endpoint (`wiki_ask`, `wiki_pack`, `wiki_audit`, `stats`, `resolve`) drops
entities above the caller's classification cap **before** scoring, neighbor
expansion, depth-banding, or budget accounting — a withheld entity never
influences ranking and never leaks through a `wiki_link` neighbor.

**Ordered enum** (matches `company-brain/schemas/manifest.schema.json`'s
`data_classification` enum and scribe-check CRITERIA C1):

```
public < internal < confidential < restricted
```

**Effective classification of an entity** (first match wins):
1. Longest/most-specific matching pattern in the corpus manifest's OPTIONAL
   `classification_map` — `{"<glob relative to corpus root>": "<level>"}`,
   e.g. `"entities/some-kind/**": "restricted"`. Glob matching is
   `fnmatch`-style (no distinction between `*` and `**` — both cross path
   separators); the longest pattern STRING wins ties, not path-segment depth.
2. Else the manifest's corpus-level `data_classification`.
3. Else `'restricted'` — **fail-closed** for a corpus that declares neither.

**Caller cap**: `CB_CLASSIFICATION_CAP` env var **only** — server-instance
scoped, **never a tool/function parameter**. A parameter would let a caller
self-elevate past the process's configured ceiling, which defeats the point
of a server-side gate.
- Unset → `'restricted'` (full read) — every pre-M7 local flow keeps working
  unmodified.
- Set to an unrecognized value → fails closed to `'public'` (most
  restrictive), not to full read, so a typo can't silently grant everything.

**Transparency block**: every read endpoint's response carries
`withheld_count` (entities dropped by the cap) and `effective_cap` (the cap
actually applied) — `wiki_ask`/`wiki_pack` nest these inside `stats`;
`stats`/`resolve`/`wiki_audit` carry them top-level. Consumers see coverage
loss instead of a silently truncated result.

**What M7 ships vs what M12 adds**: M7 is the env-cap read gate described
above — one process-wide ceiling, set by whoever launches the MCP server,
with no notion of caller identity. **M12 adds Bearer/role→cap binding**
(per-caller identity resolving to a cap, likely via an auth header the
server maps to a `CB_CLASSIFICATION_CAP`-equivalent at request time) — that
is explicitly NOT built here. M7 does not half-build auth: there is no
token, header, or role concept anywhere in this file's code, only the env
var and the enum it's compared against.

**MCP read surface**: the eight `@mcp.tool()` functions in `cb_mcp.py` are
the **entire** MCP read surface. No `corpora://` (or any other) MCP
*resource* exists, and none should be added — raw `entities/*.json` /
`manifest.json` are never served directly over MCP; every read goes
through an endpoint above, which means every read goes through the
classification gate. Adding a raw-file resource would bypass it.

## Endpoints

### 1. `wiki_ask(query, kind?, topics?, depth?, budget?, mode?, top?) → JSON`

Read entities matching the query; return matched entities + their wiki_link neighborhood (depth-bounded). **Refuses dump-all** when query + kind + topics are all empty.

**Request**
```json
{
  "query": "route optimization",
  "kind": "concept",                 // optional: concept|org|person|post|vessel|navigation|product
  "topics": ["commercial"],          // optional intersection filter
  "depth": 1,                         // wiki_link neighborhood expansion
  "budget": 8000,                     // soft char cap (~ tokens × 4); evicts LOWEST-scored first
  "mode": "hybrid",                   // "substring" | "semantic" | "hybrid" (default)
  "top": 30                           // max matched entities pre-truncation
}
```

**Response**
```json
{
  "matched": [ { "id": "concept:opportunity-route-optimization", ...full entity... } ],
  "neighbors": [ { "id": "org:kcc", "kind": "org", "names": [...], "summary": "..." } ],
  "stats": { "matched": N, "neighbors": M, "truncated": false,
             "mode": "hybrid", "semantic_used": true,
             "withheld_count": 0, "effective_cap": "restricted" }
}
```

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
  "entity_count_total": 248,
  "entity_count_audited": 248,
  "withheld_count": 0, "effective_cap": "restricted",
  "contradictions": [ { "key": [...], "values": [{ "value": 16, "source": "..." }, { "value": 12, "source": "..." }] } ],
  "dead_links": [ { "from": "concept:foo", "to": "org:nonexistent" } ],
  "freshness_expired": [ { "id": "post:bar", "updated_at": "...", "days_stale": 120 } ],
  "orphans": [ { "id": "concept:baz" } ],
  "schema_invalid": [ { "id": "...", "error": "..." } ],
  "summary": { "contradictions": N, "dead_links": M, ... }
}
```

`entity_count_total`/`entity_count_audited` and every check below them (`contradictions`, `dead_links`, `orphans`, ...) are already scoped to the classification cap — an entity above the cap is dropped before ANY check runs, so it can't appear in `orphans`, can't be the `to` of a live `dead_links` entry (a link to it now reads as dead — that's the correct, cap-consistent read: it doesn't exist for this caller), and can't contribute a claim to `contradictions`.

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
             "mode": "hybrid", "semantic_used": true,
             "withheld_count": 0, "effective_cap": "restricted" }
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
                  "cached_entities": 248, "entities_total": 248 },
  "withheld_count": 0, "effective_cap": "restricted"
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
  ],
  "withheld_count": 0, "effective_cap": "restricted"
}
```

Matches by `id` (exact), then `names[]` (case-insensitive substring), then slug-form. Top-K = 10.

### 7. `links_to(entity_id, corpus_dir?) → JSON`

Reverse-lookup: every entity whose `wiki_links` reference `entity_id` (inbound edges). Factored out of `wiki_audit`'s orphan check (`_inbound_links(entities)`) so the two never drift.

**Request**: `{ "entity_id": "org:atlas-marine" }`

**Response**
```json
{
  "id": "org:atlas-marine",
  "exists": true,
  "inbound": [
    { "id": "concept:atlas-marine-demand-theme", "kind": "concept",
      "names": ["Atlas Marine Stability-Aware Routing Demand"],
      "summary": "..." }
  ],
  "count": 1,
  "withheld_count": 0, "effective_cap": "restricted"
}
```

Cap-filters **both** the target entity and every inbound referrer, mirroring `wiki_ask`'s neighbor handling:
- The target is looked up in the already-capped entity set, so an entity withheld by the cap reports `exists: false` — indistinguishable from a genuinely nonexistent id. A caller can never use `links_to` as a cap oracle ("it returns something different for withheld vs. missing, therefore it exists").
- `_inbound_links` is built from that same capped set, so a withheld referrer never accumulates an inbound entry — it can't leak through the `{id, kind, names, summary}` neighbor summary, same guarantee `wiki_ask` gives its neighbors.

### 8. `export(corpus_dir?, format?, kind?, out_dir?) → JSON`

One-way boundary **export** shim — writes the (cap-filtered) corpus to files outside the store. Per prior scoping (CE `prd-closed-loop.md` S5/AC13), this is deliberately "the cheap half of the promised feature": a crossing OUT of the store, never a way back in. **No re-import, no round-trip merge system** — `wiki_add` remains the only write path into the store (THE WRITER RULE is unaffected).

**Request**
```json
{
  "format": "obsidian",             // "obsidian" | "jsonld" | "json" (default "obsidian")
  "kind": "concept",                // optional filter to one entity kind
  "out_dir": "/path/to/vault"       // default: <corpus_dir>/.cb_export/<format>/
}
```

**Response**
```json
{ "ok": true, "format": "obsidian", "out_dir": "...",
  "entity_count": 42, "files_written": 42,
  "withheld_count": 0, "effective_cap": "restricted" }
// or
{ "ok": false, "error_kind": "ValidationError", "message": "unknown format ..." }
```

Formats:
- **`obsidian`** — one Markdown note per entity at `<out_dir>/<kind>/<slug>.md`, JSON-encoded frontmatter (valid YAML, no extra dependency) plus `[[kind/slug|display name]]` wiki-links so the vault opens with links intact. A link to an id outside the exported set (withheld by the cap, or excluded by `kind`) renders as an unresolved Obsidian link — the same "reads as dead for this caller" outcome `wiki_audit`'s `dead_links` already documents, not a new leak (the raw id string was already present in the source entity's own `wiki_links`).
- **`jsonld`** — one file, `<out_dir>/export.jsonld`, a flat `@graph` of entities (`@id`, `@type`, `name`, `summary`, `topics`, `links`).
- **`json`** — one file, `<out_dir>/export.json`, a flat JSON array of full (cap-filtered) entity payloads.

Runs through the classification cap like every other read endpoint — an entity above the cap is never written to an export file, in any format.

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

`python cb_engine.py --self-test` runs all 8 endpoints against the live `syroco-commercial` corpus and prints pass/fail. Smoke-tests:
- `wiki_ask("route optimization")` returns ≥1 matched.
- `wiki_audit()` returns ≥0 contradictions, valid JSON, ≥1 dead_links flagged if any exist.
- `stats()` returns counts matching `find corpora/syroco-commercial/entities -name "*.json" | wc -l`.
- `resolve("Klaveness")` returns `org:kcc` as top match.
- `wiki_add(synthetic_entity)` round-trips through validator (synthetic gets cleaned up).
- Classification cap (M7): `classify_entity()` precedence checked as a pure function; if the live corpus's `data_classification` is above `'public'`, `CB_CLASSIFICATION_CAP=public` is set temporarily and `stats`/`wiki_ask`/`resolve`/`wiki_audit` are asserted to withhold every entity.
- `links_to` (M11): an existing id reports `exists: true` with `{id, kind, names, summary}`-shaped inbound entries; a nonexistent id reports `exists: false` with empty inbound.
- `export` (M11): all three formats (`obsidian`/`jsonld`/`json`) write into a tempdir and are asserted to actually land files on disk; an unknown format is rejected as a `ValidationError`.

`tests/test_classification_gate.py` is the dedicated, hermetic classification-gate suite (synthetic mixed-classification fixture — every enum level plus a longest-glob-wins override, on top of the same `fixtures/golden_corpus/` used by `tests/test_golden_queries.py`, now carrying `fixtures/golden_corpus/manifest.json`) — extended for M11 to also assert a restricted entity never appears in `links_to` inbound, or in an `export`, at a cap below `restricted`.

## File layout (final, post-/simplify)

```
agent-skills/entitystore/
├── SURFACE.md                # this file (v1 contract)
├── SKILL.md                  # skill description
└── scripts/
    ├── cb_engine.py          # engine — 8 endpoints, pure Python, no MCP dep
    ├── cb_mcp.py             # FastMCP local-stdio server wrapping engine
    ├── cb_embed.py           # semantic resolver (Mistral / OpenAI + cache)
    ├── cb_mcp_smoke.py       # live JSON-RPC smoke test (init + 8 tools)
    └── validate_corpus.py    # JSON Schema validator (schema-injection seam)
```

CE's markdown engine (`wiki/`, `mcp_server.py`, `pack_context*.py`, `embed_resolve.py`, `mmr.py` — ~3000 LOC) deleted in the /simplify pass. Git keeps the history.
