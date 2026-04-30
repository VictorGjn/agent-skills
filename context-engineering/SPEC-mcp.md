# Context Engineering MCP — Specification

**Version**: 1.0.0-rc1 (post-audit)
**Status**: Release candidate. Audit-applied fixes: Cache-Control security bug (§3.1), ETag canonicalization (§3.1). Pending v1.1: lock-TTL heartbeat, `get_job_status`, MCP-spec conformance (resources/initialize, JSON-RPC error codes), telemetry expansion, hashed bearer tokens, `register_corpus` multi-part for >8 MB payloads, base64-float32 → `number[][]`, build-vs-buy §1.5, Anabasis Skill ABC framing in §1.
**Editor**: Victor Grosjean
**Last updated**: 2026-05-01

This document is the contract between the CE MCP server and its consumers
(Claude Code, Anabasis agents, cloud routines, n8n, future internal tools).
The rationale and architecture decisions live in the plan handoff
(`~/.claude/handoffs/context_engineering_mcp_plan.md`); this spec defines
the *interface* — tool names, input/output schemas, error semantics,
idempotency contracts, lifecycle, auth, telemetry.

The spec is normative; the implementation in `victorgjn/agent-skills` and
the brain repo `syrocolab/company-brain` MUST conform.

---

## 1. Goal & non-goals

### Goal

Provide a stable, language-agnostic interface for indexing source corpora and
packing relevant files into LLM context windows. One server, many clients,
shared state in a versioned GitHub repository.

### Non-goals

- **Become the company-wide knowledge graph.** Out of scope; that's
  `syroco-product-ops`. CE indexes corpora and serves them; it does not
  reason across them.
- **Replace the skill mechanism.** The existing `context-engineering` skill
  becomes a thin client that proxies to this MCP when configured.
- **Provide chat or generation.** No LLM completions are exposed by this
  server. Consumers compose CE tools with their own LLM clients (Claude,
  GPT, etc.).
- **Multi-tenant SaaS.** v1 is single-tenant Syroco-internal with one
  shared bearer token. Per-tenant isolation is a v2 concern.

---

## 2. Conformance levels

A consumer is **CE-1.0-compliant** if it:

1. Authenticates with a Bearer token in the `Authorization` header.
2. Calls tools by exact name from §3 (no aliases).
3. Treats unknown response fields as forward-compatible (ignore-extra).
4. Honors `data_classification` gates (§6.3).

A server implementation is **CE-1.0-compliant** if it:

1. Implements all 6 v1 tools from §3 with the exact schemas given.
2. Persists state in a GitHub-repo-shaped backend matching `schemas/` in the
   brain repo.
3. Returns errors using the codes in §7.
4. Emits the telemetry events listed in §9.
5. Validates manifests against `manifest.schema.json` before every commit.

---

## 3. Tool catalog (v1)

Six tools. All accept a JSON object request body, return a JSON object
response. Errors follow §7. Tool names are lowercase verb_object; no
aliases.

### 3.1 `pack_context`

**Purpose**: Given a query and a corpus, return a depth-packed markdown bundle of relevant files sized to a token budget. The headline tool — 90% of consumer calls go here.

**Input**:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Natural-language query OR symbol name OR multi-word phrase. Max 4096 chars. |
| `corpus_id` | string | yes | — | Target corpus. See §4.1 for format. |
| `budget` | integer | no | 32000 | Token budget for packed output. Min 1000, max 200000. |
| `mode` | enum | no | `auto` | `auto`, `keyword`, `semantic`, `graph`, `deep`, `wide`. |
| `task` | enum \| null | no | `null` | `fix`, `review`, `explain`, `build`, `document`, `research`, or `null` for auto-detect. |
| `model_context` | integer | no | `null` | Hint: caller's model context window (e.g. `1000000`). When set with no explicit `budget`, server scales budget to ~12% of `model_context`, clamped to [4000, 64000]. |
| `why` | boolean | no | `false` | If true, include a trace of mode/task selection + entry points + budget rationale before the packed markdown. |

**Output**:

```json
{
  "markdown": "string (the packed context, ready to feed an LLM)",
  "tokens_used": "integer (actual)",
  "tokens_budget": "integer (the budget used)",
  "files": [
    { "path": "string", "depth": "Full|Detail|Summary|Structure|Mention", "tokens": "integer", "relevance": "number 0..1" }
  ],
  "trace": "string | null (present if why=true)",
  "corpus_commit_sha": "string (the brain-repo sha this answer was built from)",
  "took_ms": "integer"
}
```

**Errors**:

| Code | Meaning |
|---|---|
| `INVALID_ARGUMENT` | `query` empty, `budget` out of range, `mode`/`task` unknown |
| `CORPUS_NOT_FOUND` | `corpus_id` not in brain repo |
| `CORPUS_ARCHIVED` | corpus is `archived` or `frozen` and serving disabled (rare; usually still served — see §4.3) |
| `CORPUS_LOCKED` | corpus is mid-refresh; retryable after `Retry-After` seconds |
| `RATE_LIMITED` | per-token call rate exceeded |
| `BUDGET_TOO_SMALL` | `budget` < min file's structural overhead (~500 tokens). Distinct from INVALID_ARGUMENT for clarity. |

**Idempotency**: idempotent given `(corpus_commit_sha, all input fields)`. Two calls with identical inputs against an unchanged corpus return the same `markdown` byte-for-byte.

**Cacheability**: `Cache-Control: private, max-age=60` plus an `ETag` derived from `(corpus_commit_sha + sha256(canonical_inputs))`. **NEVER `public`** — packed responses include source content from corpora that may be `confidential` or `restricted`; CDN/intermediary caching of these would leak content. Servers MUST additionally emit `Cache-Control: no-store` when the responding corpus's `data_classification` is `confidential` or `restricted`. Conditional `If-None-Match` returns 304.

ETag canonicalization: input fields serialized via RFC 8785 (JSON Canonicalization Scheme) before hashing. Two clients sending fields in different orders produce identical ETags.

### 3.2 `find_relevant_files`

**Purpose**: Like `pack_context` but returns ranked paths only — no content. For consumers doing their own assembly (e.g. a tool that wants to pass paths to a different reader, or compose its own context format).

**Input**:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Same as `pack_context`. |
| `corpus_id` | string | yes | — | |
| `top_k` | integer | no | 20 | Min 1, max 200. |
| `mode` | enum | no | `auto` | Same set as `pack_context`. |
| `task` | enum \| null | no | `null` | Same. |

**Output**:

```json
{
  "files": [
    {
      "path": "string",
      "relevance": "number 0..1",
      "keyword_score": "number (0 if mode skipped keyword)",
      "semantic_score": "number (0 if mode skipped semantic)",
      "graph_score": "number (0 if mode skipped graph)",
      "reason": "string (human-readable: 'matched X via semantic similarity, confirmed via graph hop from Y')"
    }
  ],
  "corpus_commit_sha": "string",
  "took_ms": "integer"
}
```

**Errors**: `INVALID_ARGUMENT`, `CORPUS_NOT_FOUND`, `CORPUS_LOCKED`, `RATE_LIMITED`.

**Idempotency**: same as `pack_context`.

### 3.3 `register_corpus`

**Purpose**: Client uploads a pre-built index + embeddings for a corpus the server can't reach (local paths, private repos the server's GitHub App lacks access to, or specialized corpora processed by a custom adapter). Returns the `corpus_id` and the commit sha that landed.

**Input**:

| Field | Type | Required | Description |
|---|---|---|---|
| `source` | object | yes | Manifest `source` field — see `manifest.schema.json` |
| `corpus_id` | string | no | If omitted, server derives from `source` (e.g. `gh-syrocolab-foo-main` for github_repo). |
| `data_classification` | enum | yes | `public` / `internal` / `confidential` / `restricted` |
| `embedding` | object | yes | `{provider, model, dims}` |
| `files` | array | yes | Array of file-entry objects (see `file-entry.schema.json`). |
| `embeddings` | object | yes | `{vectors: base64-encoded float32 (N×dims), paths: [string], hashes: [string]}` |
| `graph_edges` | array | no | Optional; reserved for v2. |
| `concept_clusters` | object | no | Optional; reserved for v2. |

**Constraints**:
- Total request body ≤ **8 MB compressed**. Larger uploads use multi-part: client calls `register_corpus_init` → gets a presigned URL → uploads chunked → calls `register_corpus_finalize`. (Multi-part is v1.1; for v1 the 8 MB cap is hard.)
- `embeddings.paths.length === embeddings.hashes.length === N`. Server validates.

**Output**:

```json
{
  "corpus_id": "string",
  "commit_sha": "string",
  "version": "integer",
  "stats": { "file_count": "integer", "embedded_count": "integer", "size_bytes": "integer" }
}
```

**Errors**:

| Code | Meaning |
|---|---|
| `INVALID_ARGUMENT` | manifest validation failed (see §7 for details payload) |
| `CORPUS_LOCKED` | another writer holds the lock; retryable |
| `PAYLOAD_TOO_LARGE` | request body > 8 MB |
| `EMBEDDING_MISMATCH` | `paths.length !== hashes.length`, or `vectors` shape doesn't match `(N, dims)` |
| `WRITE_CONFLICT` | git push 409 after 3 retries |
| `BRAIN_UNAVAILABLE` | GitHub API down/throttled beyond retry budget |

**Idempotency**: idempotent on `(corpus_id, files[].contentHash for all files)`. A second call with identical content is a no-op (returns the existing `commit_sha` without writing).

### 3.4 `index_github_repo`

**Purpose**: Server-side indexing — clones a GitHub repo, runs the indexer, computes embeddings via the configured provider, commits the result to the brain. For repos the server's GitHub App can read.

**Input**:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `repo` | string | yes | — | `owner/name` format, e.g. `syrocolab/efficientship-backend`. |
| `branch` | string | no | repo default | Branch or commit sha. |
| `corpus_id` | string | no | derived | If omitted: `gh-{owner}-{name}-{branch}`. |
| `data_classification` | enum | yes | — | Required; no default. |
| `indexed_paths` | string[] | no | `[]` | Restrict to these paths (relative to repo root). Empty = whole repo. |
| `async` | boolean | no | `false` | If true, returns immediately with `job_id`; client polls `list_corpora` for `lifecycle_state: active` and `last_refresh_completed_at` change. |

**Output (sync, `async=false`)**:

```json
{
  "corpus_id": "string",
  "commit_sha": "string",
  "version": "integer",
  "stats": { "file_count": "integer", "embedded_count": "integer", "took_ms": "integer" }
}
```

**Output (async, `async=true`)**:

```json
{
  "corpus_id": "string",
  "job_id": "string",
  "status": "queued"
}
```

**Errors**:

| Code | Meaning |
|---|---|
| `INVALID_ARGUMENT` | malformed `repo`, unknown branch |
| `SOURCE_FORBIDDEN` | server's GitHub App can't read `repo`. Caller should use `register_corpus` instead. |
| `SOURCE_NOT_FOUND` | repo doesn't exist |
| `CORPUS_LOCKED` | retryable |
| `BUDGET_EXCEEDED` | sync indexing would exceed function timeout (~50s). Caller should retry with `async=true`. |
| `EMBEDDING_PROVIDER_ERROR` | upstream embedding API failed beyond retry budget |
| `WRITE_CONFLICT` | git push 409 after 3 retries |

**Idempotency**: idempotent on `(repo, branch, commit_sha)`. Re-indexing the same source commit is a no-op modulo embedding-provider drift (different model versions yield different vectors).

### 3.5 `list_corpora`

**Purpose**: Discoverability. Returns all corpora visible to the caller, with metadata.

**Input**:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `lifecycle_state` | enum[] | no | `["active", "idle"]` | Filter. Pass `["archived", "frozen"]` to include those. |
| `data_classification_max` | enum | no | `internal` | Maximum classification visible to caller. Default `internal`; explicit pass needed for higher. |
| `source_type` | enum | no | `null` | Filter by source type. |

**Output**:

```json
{
  "corpora": [
    {
      "corpus_id": "string",
      "source": { "type": "string", "uri": "string", "branch": "string|null" },
      "lifecycle_state": "active|idle|archived|frozen",
      "data_classification": "public|internal|confidential|restricted",
      "embedding": { "provider": "string", "model": "string", "dims": "integer" },
      "stats": { "file_count": "integer", "embedded_count": "integer", "size_bytes": "integer" },
      "version": "integer",
      "last_refresh_completed_at": "string|null",
      "archive_location": "string|null"
    }
  ]
}
```

**Errors**: `RATE_LIMITED` only.

**Idempotency**: trivially idempotent (read-only).

### 3.6 `health`

**Purpose**: Liveness + version + commit sha for ops/monitoring.

**Input**: empty object `{}`.

**Output**:

```json
{
  "ok": true,
  "version": "1.0.0",
  "commit_sha": "string (server git sha)",
  "brain_head_sha": "string (latest brain repo sha known to server)",
  "providers_available": ["openai", "mistral", "voyage"],
  "took_ms": "integer"
}
```

**Errors**: never errors when reachable. If unreachable, no response.

---

## 4. State model

### 4.1 Corpus identity

A `corpus_id` is a stable string that names exactly one corpus. Format:

```
^[a-z0-9][a-z0-9-]{0,127}$
```

Convention: `<source-type>-<slugified-source>-<scope>`.

| Source type | Example |
|---|---|
| `gh` | `gh-syrocolab-efficientship-backend-develop` |
| `local` | `local-victorgjn-anabasis-spec` |
| `granola` | `granola-product-onboarding-2026q2` |
| `notion` | `notion-roadmap-db` |

Server-derived `corpus_id` for `index_github_repo` is `gh-{owner}-{repo}-{branch}` with non-`a-z0-9-` chars replaced by `-`.

### 4.2 Manifest

Each corpus folder under `corpora/<corpus_id>/` contains a `manifest.json`
validated against
[`schemas/manifest.schema.json`](https://github.com/syrocolab/company-brain/blob/main/schemas/manifest.schema.json).

Manifest fields are normative; server implementations MUST NOT extend the
schema without bumping `schema_version`. Forward compatibility: clients
ignore unknown manifest fields.

### 4.3 Lifecycle

States: `active`, `idle`, `archived`, `frozen`. Transitions and serving
behavior defined in
[`LIFECYCLE.md`](https://github.com/syrocolab/company-brain/blob/main/LIFECYCLE.md).

Default serving policy:

- `active`, `idle`, `frozen` corpora are served by all read tools (`pack_context`, `find_relevant_files`, `list_corpora`).
- `archived` corpora return tombstone metadata in `list_corpora` and `CORPUS_ARCHIVED` from read tools, with `archive_location` in the error details.

A successful refresh resets `lifecycle_state` to `active`.

---

## 5. Operational semantics

### 5.1 Refresh & locking

A refresh is one of:
- `index_github_repo` (server-side full re-index)
- `register_corpus` (client-supplied re-upload)

Refresh writes acquire a corpus-scoped lock by writing
`manifest.lock = {holder, acquired_at, expires_at, intent}` *first*, then
performing data writes, then releasing the lock by writing
`manifest.lock = null` and bumping `version` + `last_refresh_completed_at`
+ `last_refresh_commit_sha`.

Lock TTL is **120 seconds**. Stale locks (where `expires_at < now`) MAY be
taken over by another writer.

### 5.2 Async operations

Tools accepting `async: true` (currently `index_github_repo` and reserved
slots in v2) MUST:

1. Validate inputs synchronously and return `{job_id, status: "queued"}` within 1s.
2. Enqueue the job via Vercel Cron or Vercel Queues (whichever is available).
3. Reflect progress in the manifest's `lock.intent` and `last_refresh_*` fields.

Clients poll `list_corpora` (filtered by `corpus_id`) and watch for
`last_refresh_completed_at` change *or* `last_refresh_error` set.

### 5.3 Webhook & invalidation

The brain repo SHOULD have a webhook configured that fires the server's
invalidation endpoint on every push. The server uses the webhook to update
its KV store of `corpus_id → commit_sha` pointers. Webhook delivery is
best-effort; a fallback Cron polls every 5 minutes for missed events.

In-memory module-level caches in serverless functions are invalidated by
comparing local `commit_sha` to KV `commit_sha` at the start of every
invocation.

---

## 6. Auth & ACL

### 6.1 Bearer tokens

All tool calls require:

```
Authorization: Bearer <token>
```

Tokens are issued out-of-band (manual provisioning for v1). The server
maintains an in-memory token → role mapping seeded from a Vercel env var
(`CONTEXT_ENG_TOKENS_JSON`).

Roles in v1:
- `reader` — may call all read tools. Implicit `data_classification_max: internal`.
- `writer` — `reader` + `register_corpus`, `index_github_repo`. Implicit `data_classification_max: confidential`.
- `admin` — all of the above + `data_classification_max: restricted`. Reserved for human ops.

A v2 spec will replace this with per-corpus ACL.

### 6.2 GitHub App

Server-side state mutations use a GitHub App installation token, NOT a PAT.
The App is scoped to:
- `Contents: Read & Write` on `syrocolab/company-brain`
- `Contents: Read & Write` on `syrocolab/company-brain-archive` (archive sibling)
- `Contents: Read` on Syroco source repos (for `index_github_repo`)
- `Metadata: Read`

Source-repo reads outside the Syroco org use a separate read-only GitHub
App installation per org, OR fall back to unauthenticated public access
(rate-limited).

App credentials live in Vercel encrypted env. Rotation cadence: 90 days.

### 6.3 Data classification gates

Every corpus carries `data_classification ∈ {public, internal, confidential, restricted}`.

Read tools (`pack_context`, `find_relevant_files`) MUST refuse to return
content from a corpus whose `data_classification` exceeds the caller's
`data_classification_max`. Default cap by role:

| Role | Default `data_classification_max` |
|---|---|
| `reader` | `internal` |
| `writer` | `confidential` |
| `admin` | `restricted` |

A caller MAY pass an explicit `data_classification_max` ≤ their role's
cap to further narrow.

Returns `INVALID_ARGUMENT` with details `{exceeded_classification: "..."}`
if exceeded.

---

## 7. Error model

All errors return HTTP 4xx or 5xx with a JSON body:

```json
{
  "error": {
    "code": "STRING_CODE",
    "message": "human-readable",
    "details": { "...optional structured context..." },
    "retryable": true,
    "retry_after_seconds": 30
  }
}
```

Standard codes (in addition to per-tool codes above):

| Code | HTTP | Retryable | Meaning |
|---|---|---|---|
| `UNAUTHENTICATED` | 401 | no | missing/invalid bearer token |
| `PERMISSION_DENIED` | 403 | no | token role insufficient |
| `INVALID_ARGUMENT` | 400 | no | request shape violation |
| `RATE_LIMITED` | 429 | yes | per-token rate exceeded |
| `INTERNAL` | 500 | yes | server bug; reported to telemetry |
| `BRAIN_UNAVAILABLE` | 503 | yes | GitHub upstream down |
| `EMBEDDING_PROVIDER_ERROR` | 502 | yes | upstream embedding API |
| `BUDGET_EXCEEDED` | 408 | use async | sync timeout would breach |

`retryable: true` errors include `retry_after_seconds` when the server can
estimate a useful backoff (e.g. from upstream rate-limit headers). Clients
SHOULD honor it.

---

## 8. Versioning

Spec semver: `MAJOR.MINOR.PATCH`.

- `MAJOR` bump: breaking change to any tool's input/output schema or error
  semantics. Compatibility window: 90 days minimum.
- `MINOR` bump: backwards-compatible additions (new tools, new optional
  fields, new error codes that map to existing categories).
- `PATCH` bump: clarifications, doc fixes, no behavior change.

`health` returns the active spec version. Clients SHOULD log it on startup.

Breaking changes require:
1. New tool name (`pack_context_v2`) OR new server endpoint version (`/v2/...`)
2. Both old and new shipped in parallel for 90 days
3. Deprecation warning header (`X-CE-Deprecated: ...`) on the old path
4. Migration guide in the spec PR description

---

## 9. Telemetry

The server emits structured events to a telemetry sink (Vercel Logs in v1,
configurable). Events are JSON, one per line.

Required events:

| Event | When | Fields |
|---|---|---|
| `tool.call` | every tool invocation, after auth | `tool, role, corpus_id?, took_ms, status_code, error_code?` |
| `corpus.refreshed` | successful refresh commit | `corpus_id, source.commit_sha, file_count, embedded_count, took_ms` |
| `corpus.refresh_failed` | refresh terminal failure | `corpus_id, error_code, attempts, last_error_message` |
| `corpus.archived` | lifecycle transition to archived | `corpus_id, last_active_commit_sha, age_days` |
| `lock.taken_over` | stale lock takeover | `corpus_id, prior_holder, age_seconds` |
| `cache.miss` | KV/in-memory cache miss requiring tarball fetch | `corpus_id, layer, took_ms` |

No event includes the `query` string or any indexed file content. Event
schemas freeze at the same MAJOR version as the tool API.

---

## 10. Future tools (v2 contract preview, non-normative)

Reserved names — implementations MUST NOT use these for unrelated tools:

- `find_related_symbols(symbol, corpus_id, hops?, relations?)` — graph traversal. Blocked on persistent `graph.jsonl`.
- `list_concept_clusters(corpus_id)` — birds-eye LLM-labelled clusters (PR #8).
- `detect_concept_drift(corpus_id, since)` — diff between commits (PR #10).
- `refresh_corpus(corpus_id)` — explicit refresh trigger (currently inferred from re-running `index_github_repo` with same id).
- `delete_corpus(corpus_id)` — admin-only.
- `verify_corpus(corpus_id)` — schema + integrity check.
- `get_corpus_stats(corpus_id?)` — aggregate stats.
- `compute_embeddings(corpus_id, provider?, model?)` — re-embed without re-indexing.
- `get_pending_embeddings(corpus_id)` / `submit_embeddings(corpus_id, vectors)` — external handoff (currently a CLI feature in `embed_resolve.py`).

---

## 11. Implementation notes (informative)

These are not part of the contract; they describe the v1 implementation.

- **Hosting**: Vercel Functions (Node.js for the MCP server, Python scripts as build-time artifacts).
- **State**: `syrocolab/company-brain` GitHub repo. Per-corpus folder layout per `LIFECYCLE.md` and the schemas.
- **Read cache**: in-memory module-level cache (warm function reuse) → Vercel KV (corpus_id → commit_sha pointers, NOT content) → lazy per-corpus tarball fetch via GitHub archive API.
- **Write path**: Octokit, GitHub App installation token, single tree-then-commit per refresh, parallel blob creation capped at concurrency 10.
- **Locking**: lock-in-manifest, 120s TTL, stale takeover on `expires_at < now`.
- **Embeddings format on disk**: NumPy `.npy` (vectors) + `paths.json` + `hashes.json` per corpus.
- **Long ops**: `index_github_repo` >50s → punted to Vercel Cron via `async=true`.

Full rationale + alternatives considered in
`~/.claude/handoffs/context_engineering_mcp_plan.md` (v0.3) and the
2026-05-01 infra audit (CE MCP infra audit).

---

## 12. References

- **Brain repo**: https://github.com/syrocolab/company-brain — schemas, layout, lifecycle policy
- **Skill**: https://github.com/victorgjn/agent-skills/tree/main/context-engineering — thin client + indexer + embedder
- **PR #12** (provider abstraction): https://github.com/victorgjn/agent-skills/pull/12
- **PR #10** (events log): https://github.com/victorgjn/agent-skills/pull/10
- **PR #9** (one-verb pack + RRF): https://github.com/victorgjn/agent-skills/pull/9
- **MCP spec**: https://modelcontextprotocol.io/specification (Anthropic's MCP standard this server implements)
- **JSON Schema Draft-07**: https://json-schema.org/draft-07
- **Anabasis Skill ABC**: PR #1 on `victorgjn/anabasis` — the contract this MCP fulfills as a Skill catalog provider

---

## Appendix A — Tool quick reference (for AI agents reading the catalog)

| Tool | Use when |
|---|---|
| `pack_context` | You want a markdown bundle of relevant files for a query, ready to feed an LLM. |
| `find_relevant_files` | You want ranked file paths only (you'll fetch content yourself). |
| `register_corpus` | You've indexed something locally (or via a custom adapter) and want to make it queryable. |
| `index_github_repo` | You want the server to clone + index a GitHub repo it can reach. |
| `list_corpora` | You want to know what's available. |
| `health` | You want to confirm the server is reachable and on a known version. |

## Appendix B — Common consumer flows

### Flow 1: First-time setup of a new code corpus

```
1. index_github_repo(repo="owner/foo", branch="main", data_classification="internal")
   → returns corpus_id, commit_sha
2. pack_context(query="...", corpus_id=...)
   → returns markdown
```

### Flow 2: Daily query against an existing corpus

```
1. list_corpora({lifecycle_state: ["active"]})
   → choose corpus_id
2. pack_context(query="...", corpus_id=..., budget=32000)
   → markdown
```

### Flow 3: Anabasis Skill workflow (weekly architectural brief)

```
[Monday 08:00 cron]
  → index_github_repo(repo="syrocolab/efficientship-backend", branch="develop", async=false)
  → list_corpora({source_type: "github_repo"}) — find corpus_id
  → pack_context(query="recent architectural changes", corpus_id, budget=32000)
  → (different MCP) anthropic.summarize(packed_markdown)
  → (different MCP) slack.post(channel="#eng-weekly", content=summary)
```

### Flow 4: Local code with a custom adapter

```
1. (locally) run a custom adapter producing universal-format file entries + embeddings
2. register_corpus(source={...}, files=[...], embeddings=[...], data_classification="internal")
   → returns corpus_id
3. pack_context(...) as usual
```

---

## Appendix C — Open questions deferred to v1.1

- Multi-part upload for `register_corpus` payloads >8MB (presigned URL flow).
- Per-corpus ACL (replacing role-based default caps).
- Webhook-driven realtime client invalidation push (vs polling).
- Cost attribution: which tokens (embedding API + LLM context) belong to which caller? Telemetry covers it; billing is out of scope for v1.
- Cross-region replication of the brain repo (via GitHub mirror) for low-latency multi-region MCP.
