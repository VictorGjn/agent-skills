# Context Engineering MCP — Specification

**Version**: 1.0.0-rc2 (post-audit, 13 themes applied)
**Status**: Release candidate. All 13 audit themes addressed. Final v1.0 freeze on YC outcome.
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

Provide the canonical [Anabasis Skill ABC](https://github.com/VictorGjn/anabasis/blob/main/spec/skill.md) reference implementation for retrieval, exposed via MCP as agent-callable depth-packing primitives. CE is the **last-mile assembler** — it turns upstream specialists' output (PDF parsers, web scrapers, transcript tools, code indexers) into LLM-ready depth-packed context, addressable by `corpus_id`, sized to the model's appetite. Specialists do source extraction; CE assembles; Anabasis orchestrates the stack.

Standalone usefulness is the secondary value prop — a Claude Code or n8n caller can use CE directly without Anabasis. But the design north star is Anabasis-Skill-shaped: typed I/O, idempotent, emits events to a temporal log.

### Non-goals

- **Become the company-wide knowledge graph.** Out of scope; that's `syroco-product-ops` / Glean. CE indexes corpora and serves them; it does not reason across them.
- **Replace per-source specialists.** CE does not parse PDFs (PageIndex / LlamaParse do that), scrape web (Firecrawl), transcribe audio (Whisper), or rank code into IDE results (Sourcegraph Cody). It composes their output into a unified retrieval layer. Adapters per source type produce CE's universal index format.
- **Replace the skill mechanism.** The existing `context-engineering` skill becomes a thin client that proxies to this MCP when configured. See §11 migration path.
- **Provide chat or generation.** No LLM completions are exposed by this server. Consumers compose CE tools with their own LLM clients (Claude, GPT, etc.).
- **Multi-tenant SaaS.** v1 is single-tenant Syroco-internal with one shared bearer token. Per-tenant isolation is a v2 concern.

## 1.5 Build vs buy

A YC reviewer (or anyone reading) will ask: why not Sourcegraph Cody Context API, Cursor `@codebase`, Glean, or LlamaIndex? Answer:

**Cody / Cursor / Augment** — code-retrieval specialists. CE composes them when they expose appropriate APIs; CE is *upstream* of the model's context window where Cody is *upstream* of the IDE's keyword search. Different shape: Cody returns ranked snippets to a human, CE returns a depth-packed bundle to an LLM. Anabasis Skill workflows can wrap Cody as a Skill alongside CE.

**Glean** — cross-source enterprise search ($1B+, 100+ connectors). Different shape: human typing queries vs. agent runtime composing skills. Glean's moat (connectors + enterprise sales motion) is at the source-extraction layer; CE's moat is at the agent-orchestration layer above. Customers will run both.

**LlamaIndex** — Python framework for building RAG apps. CE is a *deployed service* with a stable wire contract; LlamaIndex is a library you embed. CE could be implemented atop LlamaIndex internally; the spec is provider-agnostic.

**The honest answer to "why build?"**: the depth-aware token-budget-fused packing primitive (Full / Detail / Summary / Structure / Mention bands within a budget) is novel and not exposed cleanly by any of the above. Combined with git-as-storage versioning (every refresh is a commit, `git log -p` is the temporal log) and the Anabasis Skill ABC contract, CE is the *agent-native, version-controlled, source-agnostic retrieval primitive* nothing else fills. If that thesis breaks, the fallback is wrapping Cody behind the same wire contract — the spec is shaped to allow that swap without client rewrites.

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

**Seven tools** + standard MCP `initialize` handshake. All tools accept a JSON object request body, return a JSON object response. Errors follow §7 (mapped to JSON-RPC numeric codes per MCP spec). Tool names are lowercase verb_object; no aliases.

### 3.0 MCP basics

This server is a Model Context Protocol server per [modelcontextprotocol.io/specification](https://modelcontextprotocol.io/specification). Conformance:

- **Transports**: `stdio` (Claude Code local) and `streamable-HTTP+SSE` (Anabasis remote, cloud routines, n8n). At least one MUST be supported; v1 reference implementation supports both.
- **Capabilities**: server returns the following capability descriptor in its `initialize` response:
  ```json
  { "tools": { "listChanged": false },
    "resources": { "listChanged": true, "subscribe": false },
    "prompts": { "listChanged": false },
    "logging": {} }
  ```
  `tools.listChanged: false` because the v1 catalog is fixed. `resources.listChanged: true` because corpora are added/removed via `register_corpus` / `delete_corpus`.
- **Resources**: in addition to the tools below, the server exposes corpora as MCP resources under the URI scheme `corpora://`. `corpora://` lists all visible corpora; `corpora://<id>/manifest` reads a single corpus manifest. Resource access is gated by the same `data_classification` rules as tool calls (§6.3). This is the canonical way for clients to enumerate corpora — `list_corpora` is preserved as a tool for clients without resource support.
- **Discovery**: clients use `tools/list`, `resources/list`, `resources/templates/list` to enumerate. Tool descriptions include the "use when / don't use when" framing required for high-quality LLM tool-selection.

### 3.0.1 Standard response field: `next_tool_suggestions`

Every tool response MAY include an optional `next_tool_suggestions` array nudging the caller toward the cheap-first call sequence:

```json
"next_tool_suggestions": [
  {
    "tool": "pack_context",
    "reason": "you called find_relevant_files; pack the top 3 hits",
    "args_hint": { "corpus_id": "gh-syrocolab-foo-main" }
  }
]
```

**Server-side**: include this field when the just-completed tool call has an obvious next step. Typical pairs:
- `find_relevant_files` → `pack_context` (pack the top hits)
- `index_github_repo(async=true)` → `get_job_status` (poll for completion)
- `list_corpora` → `pack_context` (chosen `corpus_id`)

Maximum 2 suggestions per response. Suggestions are heuristic; the server is not required to provide them.

**Client-side**: agents SHOULD prefer suggested tools when continuing a task — the server has more context about the cheap path than the agent does. Agents MUST NOT treat suggestions as required (they're hints, not contract).

This is a forward-compatible **MINOR** addition (§ 8 versioning) — clients that ignore the field continue to work unchanged.

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

### 3.3 `upload_indexed_corpus` (a.k.a. `register_corpus`)

> **Naming note**: tool name is `upload_indexed_corpus` for explicitness — sibling write tool `index_github_repo` makes the contrast obvious to an LLM scanning the catalog ("server fetches & indexes" vs "client supplies the index"). Legacy alias `register_corpus` remains for v1.0 → v1.1 migration only and is removed in v2.

**Use when**: you've already indexed a corpus locally (or via a custom adapter the server can't reach) and want to make it queryable through the MCP. Typical: local repos, private repos the server's GitHub App can't read, Granola transcripts processed by an adapter, etc.

**Don't use when**: the server has read access to the source repo. Use `index_github_repo` instead — it's faster (no upload bandwidth) and the server validates against source.

**Input**:

| Field | Type | Required | Description |
|---|---|---|---|
| `source` | object | yes | Manifest `source` field — see `manifest.schema.json` |
| `corpus_id` | string | no | If omitted, server derives from `source` (e.g. `gh-syrocolab-foo-main`). |
| `data_classification` | enum | yes | `public` / `internal` / `confidential` / `restricted` |
| `embedding` | object | yes | `{provider, model, dims}` |
| `files` | array | yes | Array of file-entry objects (see `file-entry.schema.json`). |
| `embeddings` | object | yes | See "Embeddings encoding" below. |
| `graph_edges` | array | no | Optional; reserved for v2. |
| `concept_clusters` | object | no | Optional; reserved for v2. |

**Embeddings encoding** (audit fix — JSON-mode-safe):

The `embeddings` object is one of:
- `{ "format": "json", "vectors": [[float, ...], ...], "paths": [string], "hashes": [string] }` — N×dims `number[][]`. Tool-using LLMs can emit this reliably. **Default.**
- `{ "format": "presigned", "vectors_url": "https://...", "paths_url": "...", "hashes_url": "...", "byte_format": "float32-le-row-major" }` — for payloads >8 MB. Client first calls `upload_indexed_corpus_init` (returns presigned URLs), uploads each blob, then calls `upload_indexed_corpus` with the URLs. Server validates byte length matches `N × dims × 4`.

`paths.length === hashes.length === N` regardless of format. Server validates.

**Constraints**:
- Total inline (`format: "json"`) request body ≤ **32 MB**. Above 32 MB, MUST use `format: "presigned"`.
- Per-corpus size cap: 1 GB committed (covers virtually all code repos; large doc corpora may need split-by-section).

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
  "status": "queued",
  "poll_with": "get_job_status"
}
```

Clients query progress via `get_job_status(job_id)` (§3.7). Do NOT poll `list_corpora` — async failure modes (Cron worker crash, OOM) may not surface in `last_refresh_error`.

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

### 3.6 `get_health`

> **Naming note**: was `health` in v1.0-rc1. Renamed to `get_health` for verb_object consistency. Legacy `health` accepted as alias in v1.0 only.

**Use when**: ops monitoring, smoke tests, version-pinning checks.

**Don't use when**: you want to know if a *specific corpus* is reachable — call `list_corpora` and check `lifecycle_state`.

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

### 3.7 `get_job_status`

**Use when**: checking on an async refresh started via `index_github_repo(async=true)`.

**Don't use when**: you have a sync result; just inspect the response.

**Purpose**: Surface async job progress + terminal state. Without this tool, async semantics are observation-blind — a Cron worker that OOMs leaves the client polling forever.

**Input**:

| Field | Type | Required | Description |
|---|---|---|---|
| `job_id` | string | yes | Returned by an async tool call. |

**Output**:

```json
{
  "job_id": "string",
  "corpus_id": "string",
  "status": "queued | running | complete | failed | timeout",
  "started_at": "string | null",
  "completed_at": "string | null",
  "progress": { "files_indexed": 0, "files_total": 0, "phase": "string" } | null,
  "error": { "code": "string", "message": "string" } | null,
  "result_commit_sha": "string | null"
}
```

**Errors**: `JOB_NOT_FOUND` (404, not retryable — job IDs expire after 7 days), `INVALID_ARGUMENT`.

**Idempotency**: trivially idempotent (read-only). Cacheable for 5 seconds.

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
- `upload_indexed_corpus` (client-supplied re-upload)

Refresh writes acquire a corpus-scoped lock by writing `manifest.lock = {holder, acquired_at, expires_at, intent}` *first*, then performing data writes, **then committing the data + lock-clear in a SINGLE tree-then-commit API call** (no separate "release commit" — that race window leaks locks). The single commit bumps `version` + `last_refresh_completed_at` + `last_refresh_commit_sha` AND sets `lock: null` atomically.

Lock TTL is **300 seconds** for sync refreshes (Vercel Pro 60s + buffer); **20 minutes** for async refreshes via Vercel Cron. Long-running refreshes MUST emit a heartbeat every 60 seconds that updates `lock.expires_at = now + TTL`. Without heartbeats, a 15-minute Cron job's lock would expire mid-work and another writer would race-take it over, producing torn writes.

Lock acquire is idempotent on `holder`: re-PUT-ing with the same `holder` and current `lock.expires_at` is a no-op success — covers retry-after-network-hiccup cases.

Stale locks (`expires_at < now`) MAY be taken over by another writer. Takeover emits a `lock.taken_over` telemetry event with `prior_holder` and `age_seconds` for SRE diagnosis.

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

Tokens are issued out-of-band. The server reads a hashed token map from Vercel KV (`tokens:` namespace), NOT a raw JSON env var. Each entry: `{token_id: string, sha256(token): string, role: enum, created_at: timestamp, last_used_at: timestamp | null}`. Token revocation = delete KV row; takes effect on next request (no redeploy). Telemetry logs `token_id` (not the token) on every `tool.call` event for auditability.

A bootstrap token for the first deploy can come from `CE_MCP_BOOTSTRAP_TOKEN` env (writes one row to KV at startup if KV is empty). v2 spec moves to OAuth 2.1 per Anthropic's MCP guidance.

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

CE servers expose errors via two channels — **MCP JSON-RPC** for `stdio` and `streamable-HTTP+SSE` transports (the canonical channel), and **HTTP-level errors** for non-MCP HTTP clients calling the same Vercel functions directly.

**MCP / JSON-RPC** (numeric codes per JSON-RPC 2.0 + MCP):

```json
{
  "jsonrpc": "2.0",
  "id": "<request id>",
  "error": {
    "code": -32602,
    "message": "human-readable",
    "data": {
      "code_name": "INVALID_ARGUMENT",
      "details": { "...": "..." },
      "retryable": false,
      "retry_after_seconds": null
    }
  }
}
```

**HTTP-direct** (legacy / non-MCP callers):

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

Both convey identical information in different shapes. HTTP responses MUST also emit the `Retry-After` header (in seconds) when `retryable: true` and `retry_after_seconds` is known. This duplication is intentional — both header and body fields agree, so clients can use either.

**JSON-RPC ↔ string code map**:

| String code | JSON-RPC code | HTTP | Retryable |
|---|--:|---|---|
| `INVALID_ARGUMENT` | -32602 | 400 | no |
| `UNAUTHENTICATED` | -32001 | 401 | no |
| `PERMISSION_DENIED` | -32002 | 403 | no |
| `CORPUS_NOT_FOUND` | -32004 | 404 | no |
| `JOB_NOT_FOUND` | -32004 | 404 | no |
| `CORPUS_LOCKED` | -32005 | 409 | yes |
| `WRITE_CONFLICT` | -32005 | 409 | yes |
| `EMBEDDING_MISMATCH` | -32602 | 400 | no |
| `PAYLOAD_TOO_LARGE` | -32602 | 413 | no |
| `RATE_LIMITED` | -32006 | 429 | yes |
| `BUDGET_EXCEEDED` | -32007 | 408 | no — use async |
| `EMBEDDING_PROVIDER_ERROR` | -32008 | 502 | yes |
| `EMBEDDING_PROVIDER_PARTIAL` | -32008 | 502 | yes (with details.success_count) |
| `BRAIN_UNAVAILABLE` | -32009 | 503 | yes |
| `BRAIN_RATE_LIMITED` | -32010 | 503 | yes (different from BRAIN_UNAVAILABLE: GitHub secondary rate limit; back off, don't page) |
| `WEBHOOK_SECRET_MISMATCH` | -32011 | 500 | no — operator action: reconfigure webhook |
| `SOURCE_FORBIDDEN` | -32012 | 403 | no |
| `SOURCE_NOT_FOUND` | -32013 | 404 | no |
| `SOURCE_MISMATCH` | -32014 | 409 | no — corpus_id collides with different source.branch; specify `corpus_id` explicitly |
| `INTERNAL` | -32603 | 500 | yes |

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
| `tool.call` | every tool invocation, after auth | `tool, token_id, role, corpus_id?, took_ms, status_code, error_code?, corpus_commit_sha?` |
| `corpus.refreshed` | successful refresh commit | `corpus_id, source.commit_sha, file_count, embedded_count, took_ms` |
| `corpus.refresh_failed` | refresh terminal failure | `corpus_id, error_code, attempts, last_error_message` |
| `corpus.archived` | lifecycle transition to archived | `corpus_id, last_active_commit_sha, age_days` |
| `lock.taken_over` | stale lock takeover | `corpus_id, prior_holder, age_seconds` |
| `lock.held_duration` | every lock release (success path) | `corpus_id, holder, duration_seconds` — needed to detect leaks before takeover |
| `cache.hit` / `cache.miss` | per-request, sampled 1:N | `layer (memory \| kv \| tarball), corpus_id, took_ms` — needed to compute hit ratio per layer for cold-storm diagnosis |
| `brain.size` | weekly sweep | `total_bytes, per_corpus_bytes` (for monitoring 5 GB pain threshold) |
| `embed_provider.call` | every upstream embedding API call | `provider, model, batch_size, took_ms, success, error_code?` — distinguishes provider-side outage from server-side bug |
| `github_app.token_age` | every Octokit auth refresh | `installation_id, token_age_seconds` — App tokens expire in 1h; without this, rotation breakage falls off a cliff silently |
| `auth.token_used` | derived from `tool.call` (sampled 1:1) | `token_id, role, ip_prefix, ua_hash` — leaked-token detection |

Event schemas freeze at the same MAJOR version as the tool API. Event sink is JSON-line stdout in v1 (caller pipes to OTel collector / Vercel Logs / wherever). v1.1 will commit to OpenTelemetry semantic conventions.

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
- `wiki_closure(entity_id, max_hops?, relation_kinds?, min_weight?, budget?)` — entity-rooted blast-radius closure with risk-score per affected entity. Currently only on the local stdio MCP (`scripts/mcp_server.py`) per Phase 2.4; reserved here for v2 promotion to the deployed MCP if customer demand surfaces.

---

## 11. Skill-author migration path (informative)

The existing `context-engineering` skill at `agent-skills/context-engineering/` is CLI-first. After v1 deploy, it becomes a thin MCP client. Migration semantics:

- **Trigger**: skill checks `CONTEXT_ENG_MCP_URL` env at startup. If set, it proxies tool calls to the MCP. If unset, it falls back to local CLI execution. **Local-only mode is preserved as a first-class path** — tests, dev loops, and air-gapped use rely on it.
- **Tool name aliasing during transition**: the skill's `mcp_tools` frontmatter pins the v1 names (`pack_context`, `find_relevant_files`, `upload_indexed_corpus`, `index_github_repo`, `list_corpora`, `get_health`, `get_job_status`). v1.0 server SHOULD accept legacy aliases (`pack`, `index_workspace`, `build_embeddings`, `resolve`, `stats`, `register_corpus`, `health`) and emit `X-CE-Deprecated` warnings. Aliases are removed in v2.
- **Failure UX**: when `CONTEXT_ENG_MCP_URL` is set but the MCP is unreachable, the skill MUST surface a `BRAIN_UNAVAILABLE`-shaped error to its caller. Skills MAY implement automatic local fallback; default behavior is to fail-closed so callers know they're getting stale data.
- **Test fixtures**: the reference implementation at `agent-skills/context-engineering/tests/fixtures/mock_mcp_server.py` provides deterministic responses for each tool. Skill authors building wrapper skills test against the mock first, then against staging deploy.
- **`upload_indexed_corpus` UX in the skill**: when the skill's CLI produces a fresh local index, it MAY auto-upload to the configured MCP via `upload_indexed_corpus`. This is opt-in via `--mcp-publish` flag; default is local-only.

A wrapper skill (e.g. "weekly architectural brief") can be built atop these tools without touching the MCP server — the catalog is stable across minor versions per §8.

## 12. Implementation notes (informative)

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

## 13. References

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

---

## Appendix D — Recommended per-task budget (informative)

For agents composing CE tools across a single user task (one Claude Code turn, one cron-routine iteration, one Anabasis Skill invocation):

- **Tool calls per task**: aim for **≤5**. Most tasks resolve in 2–3 (`list_corpora` → `pack_context`, or `find_relevant_files` → `pack_context`).
- **Total CE-served tokens per task**: aim for **≤30k for code corpora, ≤80k for doc corpora**. The `pack_context` budget envelope already enforces a per-call cap; this appendix is about *not making 4 redundant calls in a row*.
- **First-call discipline**: prefer `list_corpora` (small, cheap) over `pack_context` with a guessed `corpus_id`. A `CORPUS_NOT_FOUND` error wastes a full RTT.
- **Suggestion-following**: when a response includes `next_tool_suggestions` (§3.0.1), the suggested tool is usually the right next call. Agents that follow suggestions should resolve common workflows in ≤2 calls.

These are guidelines for agent and skill authors. The server does not enforce them; the goal is to make the cheap path obvious in tool catalogs and skill prompts.
