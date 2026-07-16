# entitystore — JSON-native MCP surface (v1, revised post-audit)

The contract the engine builds against. Locked 2026-05-28 (Option B pivot: JSON-native, no gbrain dependency). **Revised same day** after a /code-review pass that exposed three "wrong defers"; semantic search, depth-banded `wiki_pack`, and git commit-through landed in v1, not v1.1.

## Goal (v1)

A **local stdio MCP server** exposing the company-brain JSON entity corpus as **six** typed endpoints. Single-user (Victor). No auth/RBAC. Schema-injection seam preserved: the engine reads `entity.schema.json` by path, never embeds.

## Non-goals (v1)

- Multi-user / multi-tenant / OAuth / RBAC.
- Vercel / HTTP deployment.
- Markdown wiki pages as a second source of truth — CE's events-log-consolidator
  model is still dropped. **M11 reinstated a wiki-DOC layer, but only as a
  REGENERABLE PROJECTION over the JSON entity corpus** (see "Wiki pages (M11)"
  below): `wiki_init.py` derives `corpora/<id>/wiki/<slug>.md` straight from the
  entities, byte-reproducible via `--rebuild`, never hand-edited, never a write
  path back into an entity or claim (THE WRITER RULE holds — enrichers remain
  the sole entity writer).
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
with no notion of caller identity. **M12 adds Bearer/role→cap binding**, in
two engine-only pieces (no HTTP transport yet — that's the served-mode
task):

1. `cb_engine._classification_cap()` now has a second input ahead of the env
   var: a `contextvars.ContextVar` (`_REQUEST_CAP`), set only through the
   `cb_engine.request_cap(level)` context manager. When no override is
   active, resolution is byte-identical to M7 (env var, then `'restricted'`
   fallback). The override is isolated per thread and per asyncio Task, so a
   threadpool- or Task-dispatching server can hold a different cap per
   concurrent request without one caller leaking into another's read. It is
   **still never a tool/function parameter** — `request_cap()` is for server
   middleware only, never for a tool implementation, and no MCP tool
   parameter can reach it.
2. `cb_auth.py` (stdlib-only: `hashlib`/`hmac`/`json`) verifies a presented
   Bearer token against a token-map JSON file (`CB_AUTH_TOKENS_PATH`):
   `{"sha256:<hex>": {"role": "<role>"}, "roles": {"<role>": "<cap>"}}`.
   `verify_token()` hashes the presented token and constant-time-compares
   (`hmac.compare_digest`) it against the map; plaintext tokens are never
   stored or logged. It returns `(role, cap)` or `None` — the cap comes from
   the file's own `"roles"` section if present, else a hardcoded default
   (`reader/internal/analyst/admin` → `public/internal/confidential/
   restricted`).

Binding the two together — extracting a Bearer header, calling
`verify_token()`, and holding `request_cap(cap)` for one request's
lifetime — is server middleware, which is the served-mode (HTTP) task, not
this one. M7/M12-as-shipped-here still has no token, header, or transport
concept in `cb_engine.py` itself; `cb_auth.py` has no HTTP/transport
dependency either.

**MCP read surface**: the six `@mcp.tool()` functions in `cb_mcp.py` are the
**entire** MCP read surface. No `corpora://` (or any other) MCP *resource*
exists, and none should be added — raw `entities/*.json` / `manifest.json`
are never served directly over MCP; every read goes through an endpoint
above, which means every read goes through the classification gate. Adding
a raw-file resource would bypass it.

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

## Wiki pages (M11) — regenerable projection, not a second store

`scripts/wiki_init.py` is a one-shot, idempotent seeder: it reads the JSON
entity corpus (the same tree every endpoint above reads — **not** an events
log; that was CE's model) and writes one `corpora/<id>/wiki/<slug>.md` page
per entity. A page is a **pointer-based, byte-reproducible projection**: the
entity JSON stays the only source of truth, `--rebuild` regenerates every
page from scratch, and nothing here ever writes an entity or a claim (THE
WRITER RULE: enrichers remain the sole entity writer — see
company-brain/CLAUDE.md).

**Slug**: `kind:slug` -> `kind-slug` — collision-free by construction, since
entity ids are already globally unique (no CE-style near-miss suffixing
needed).

**Frontmatter**: `id`, `kind`, `slug`, `schema_version` (page-schema,
independent of the entity schema), `last_verified_at` (copied verbatim from
the entity's M4 field, `null` if the entity predates the freshness rule),
`supersedes` / `superseded_by` / `valid_until` (null-allowed
decision-continuity fields), `sources[]` (pointer-only: `provenance` +
every ACTIVE `identity_assertions[]` entry — retracted/superseded
assertions are excluded as live pointers), `links_out` (= `wiki_links`),
`links_in` (reverse-computed over the SAME cap-filtered entity set every
other endpoint uses).

**PROHIBITED on every page, no exceptions**: `freshness_score`,
`confidence`, `centroid_embedding`, and any score/trust/tier/reputation
field. Freshness is **computed on read only** — see `freshness_policy.py`
and `wiki_audit`'s `freshness_lint` below; it is never written to a page,
an entity, or a claim. Claim `evidence[].quote` text is never copied onto a
page either (pointer-based only — keeps the M7 PII-purge surface to the
JSON entities, not duplicated across two trees).

**Cap-aware**: reads through the same `_filter_by_classification` as every
endpoint — an entity above `CB_CLASSIFICATION_CAP` never becomes a page.

**`--kinds`** filters which pages get (re)written; **`--rebuild`** deletes
existing pages first (scoped to `--kinds` if given, so a scoped rebuild
never clobbers other kinds' pages).

### `freshness_policy.py` — computed-on-read freshness

Per-kind half-life decay (`score = max(0, 1 - elapsed_days / (2 *
half_life_days))`), keyed by entity `kind` (mirrors `FRESHNESS_THRESHOLD_DAYS`
above). An entity with no `last_verified_at` returns `score=None`,
`status="pre-rule, never verified"` — **not** `0.0`, **not** an error. Per
the real M4 coverage reality (person 0/331, vessel 0/131 as of M11), most of
the live corpus is pre-rule; treating that as "stale" would flag nearly the
whole brain. Never persisted anywhere — every caller (wiki page frontmatter
is `last_verified_at` only, `wiki_audit`'s lint, future `wiki_ask` stats)
recomputes it fresh.

### `wiki_audit` lints (M11 additions)

Four more checks, additive to the five in "Endpoints" above, all
report-only (never write an entity/claim):

- **`merge_candidates`** — entities sharing a normalized name or a
  normalized id-slug near-miss. `reason`: `duplicate-name` |
  `slug-near-miss`.
- **`split_candidates`** — an entity whose `claims[].metric` set or
  `topics[]` set exceeds a conservative threshold (6 metrics / 8 topics by
  default). Simple counting heuristic, not semantic clustering — over-flags
  by design, human decides.
- **`stale_supersessions`** — (a) any entity's `wiki_links` still pointing
  at another entity whose top-level `superseded_by` is set, and (b) an
  `identity_assertions[]` entry with `status=superseded` whose
  `superseded_by` id isn't present among that same entity's own assertions
  (a dangling M4 identity chain).
- **`freshness_lint`** — `last_verified_at`-first (see `freshness_policy.py`
  above), falling back to `updated_at` only for the pre-rule bucket's own
  reporting. Returns `{pre_rule_count, pre_rule_sample (capped at 20),
  stale}` — pre-rule entities are a count + small sample, never the full
  list, so a 0%-coverage kind doesn't flood the report; they are never
  promoted into `stale`.

### `render_proposals` / `audit/proposals.md`

`cb_engine.py wiki-audit --proposals` (or `render_proposals(wiki_audit(...))`
directly) renders the full `wiki_audit` result — all nine checks — as
markdown to `<corpus>/audit/proposals.md`. A **report file**, never an
entity/claim write: no WRITER RULE contact.

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
- Classification cap (M7): `classify_entity()` precedence checked as a pure function; if the live corpus's `data_classification` is above `'public'`, `CB_CLASSIFICATION_CAP=public` is set temporarily and `stats`/`wiki_ask`/`resolve`/`wiki_audit` are asserted to withhold every entity.

`tests/test_classification_gate.py` is the dedicated, hermetic classification-gate suite (synthetic mixed-classification fixture — every enum level plus a longest-glob-wins override, on top of the same `fixtures/golden_corpus/` used by `tests/test_golden_queries.py`, now carrying `fixtures/golden_corpus/manifest.json`).

`tests/test_wiki_init.py` (M11) is the dedicated wiki-layer suite, run against a **tempdir copy** of `fixtures/golden_corpus/` (never mutates the committed fixture): idempotency (byte-compare modulo `generated_at`), prohibited-fields absence, no evidence-quote leakage, required frontmatter fields, `links_in` correctness, classification-cap enforcement on page generation, `--kinds`-scoped `--rebuild`, all four `wiki_audit` lints (synthetic inline fixtures), `render_proposals` section coverage, and `freshness_policy` boundary cases.

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
    ├── validate_corpus.py    # JSON Schema validator (schema-injection seam)
    ├── wiki_init.py          # M11: entity-page seeder -> corpora/<id>/wiki/<slug>.md
    └── freshness_policy.py   # M11: computed-on-read freshness (last_verified_at, per-kind half-life)
```

CE's markdown engine (`wiki/`, `mcp_server.py`, `pack_context*.py`, `embed_resolve.py`, `mmr.py` — ~3000 LOC) deleted in the /simplify pass. Git keeps the history.
