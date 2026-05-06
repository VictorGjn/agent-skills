# Context Engineering MCP — Specification

**Version**: 1.0.0-rc4 (post-plan-corpus review)
**Status**: Release candidate. rc3 + 14 plan-corpus findings applied (multi-corpus contract, wiki contract, freshness, errors, telemetry, positioning). Final v1.0 freeze on production MCP ship.
**Editor**: Victor Grosjean
**Last updated**: 2026-05-05

**Changelog vs rc3** (review of all `plan/` content vs rc3, 2026-05-04):
- Multi-corpus contract: `corpus_ids[]` array on `ce_pack_context` + `ce_find_relevant_files`; `corpus_commit_shas` object output; `<corpus>:<path>` prefix in `files[].path` (§ 3.1, § 3.2)
- New error codes: `EMBEDDING_PROVIDER_MISMATCH` (multi-corpus dim drift), `CORPUS_PREFIX_COLLISION` (shared root basename) (§ 7.1)
- `ce_wiki_*` contract sketch: `scope?` parameter on `ce_wiki_ask`, decision-continuity fields (`supersedes`/`superseded_by`/`valid_until`), async-write invariant (§ 10.1, with cross-ref from § 4.2)
- Freshness policy contract: stored `last_verified_at`, computed `freshness_score`, per-source half-life table (§ 4.4 new)
- Schema evolution policy split: `wiki/` refusal-and-rebuild vs `events/` forward-migrate (§ 4.2)
- `wiki.*` / `lat.*` MUST return `corpus_commit_sha` for cache idempotency (§ 10b)
- `lat.*` normative bits: `lat.locate` upstream conformance, `lat_check` exit-code contract (§ 10b)
- Telemetry: `audit.flagged`, `freshness.expired`, `audit.broken_refs`, `corpus.consolidation_triggered` (§ 9)
- `next_tool_suggestions` semantic-routing rule for `ce_wiki_impact_of` (§ 3.0.1)
- `ce_wiki_impact_of` `relation_kinds` defaults per corpus type (§ 10.1)
- § 1.5.1 left-flank competitive table: codebase-memory-mcp / mache / GitNexus / Graphify

**Changelog vs rc2** (audit doc: `plan/audits/spec-mcp-vs-mcp-builder-2026-05-04.md`):
- `ce_*` service prefix on all 7 tools, with v1.0 aliases (§ 3.0.2)
- Per-tool annotations added (§ 3.0.3)
- Current deployment status table merged in from PR #36 (§ 3.0.4, informative)
- Transport renamed to "Streamable HTTP" (drops "+SSE", § 3.0)
- Error model split: tool errors in `result.isError` (§ 7.1), protocol errors as JSON-RPC (§ 7.2)
- OAuth 2.1 promoted to v1.0 optional with metadata path specified (§ 6.1)
- Local HTTP hardening: loopback bind, Origin validation (§ 6.4)
- `ce_list_corpora` pagination via `limit`/`offset` + `brain_head_sha` echo (§ 3.5)
- `response_format` on `ce_pack_context`: `markdown` / `structured` / `both` (§ 3.1)
- Package naming convention noted (§ 12)
- `lat.*` divergence rationale tightened (§ 10b)
- v2-reserved `wiki.closure` renamed to `ce_wiki_impact_of` (canonical, § 10) matching PR #37 implementation; local stdio keeps `wiki.impact_of` dot-notation per § 10b
- § 10b: clarified that `wiki.*` and `lat.*` dot-notation is local-stdio-only; deployed-v2 promotions adopt `ce_*` prefix

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

### 1.5.1 Adjacent open-source MCP-shaped peers

The right-flank framing above (Cody/Cursor/Glean/LlamaIndex) addresses commercial code-retrieval and enterprise search. The left flank is open-source MCP-shaped peers that contest individual CE pillars:

| Tool | Contests | Differentiator CE keeps |
|---|---|---|
| `codebase-memory-mcp` | Freshness invalidation and decision-layer (`manage_adr`) — the only peer hitting two pillars at once | Depth-aware packing + multi-corpus + agent-native composition. No `manage_adr` analog yet — closing this gap is § 10.1's `ce_wiki_*` family and decision-continuity fields. |
| `mache` | Corpus-agnostic on the code side (28-language tree-sitter graph + Louvain clustering) | Multi-corpus (code + docs + transcripts + Notion + Gmail) under one packer; cross-corpus `corpus_ids[]`. mache is code-only. |
| `GitNexus` | Cross-repo "contracts" (decision-layer for code) | Source-agnostic decision-layer (wiki schema is corpus-shaped, not code-shaped); freshness contract per source type. |
| `Graphify` | Multi-modal corpus building | Already integrated as an indexer; CE consumes Graphify's `graph.json` if present. Complementary, not competitive. |

CE's union: depth-aware packing, multi-corpus, decision-continuity, freshness — all source-agnostic, all version-controlled via git. Each listed peer covers one or two of those four pillars. Anabasis Skill ABC interop, the deployed Vercel host, and `next_tool_suggestions` routing are CE's secondary differentiators.

---

## 2. Conformance levels

A consumer is **CE-1.0-compliant** if it:

1. Authenticates with a Bearer token in the `Authorization` header (or OAuth 2.1 if the server advertises it; see § 6.1).
2. Calls tools by canonical name from § 3 (aliases per § 3.0.2 accepted but emit `X-CE-Deprecated` warnings).
3. Treats unknown response fields as forward-compatible (ignore-extra).
4. Honors `data_classification` gates (§ 6.3).

A server implementation is **CE-1.0-compliant** if it:

1. Implements all 7 v1 tools from § 3 with the exact schemas given, accepting aliases per § 3.0.2.
2. Persists state in a GitHub-repo-shaped backend matching `schemas/` in the
   brain repo.
3. Returns errors using the codes in §7.
4. Emits the telemetry events listed in §9.
5. Validates manifests against `manifest.schema.json` before every commit.

---

## 3. Tool catalog (v1)

**Seven tools** + standard MCP `initialize` handshake. Canonical tool names use the `ce_` service prefix (§ 3.0.2); v1.0 aliases without the prefix are accepted for migration. All tools accept a JSON object request body, return a JSON object response. Errors follow § 7 — tool errors return in `result.isError` (§ 7.1), protocol errors as JSON-RPC (§ 7.2).

### 3.0 MCP basics

This server is a Model Context Protocol server per [modelcontextprotocol.io/specification](https://modelcontextprotocol.io/specification). Conformance:

- **Transports**: `stdio` (Claude Code local) and **Streamable HTTP** (Anabasis remote, cloud routines, n8n) — Streamable HTTP supersedes the older "HTTP+SSE" name; same wire transport. At least one MUST be supported; v1 reference implementation supports both.
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
- `ce_find_relevant_files` → `ce_pack_context` (pack the top hits)
- `ce_index_github_repo(async=true)` → `ce_get_job_status` (poll for completion)
- `ce_list_corpora` → `ce_pack_context` (chosen `corpus_id`)
- `ce_upload_corpus` (or v2 `ce_wiki_add`) → `ce_get_job_status` — consolidation is async per the wiki async-write invariant (§ 10); read-after-write within ~60s may not reflect the write.
- Query smells like an entity-impact question (matches `impact of`, `what does X touch`, `consequences of`, `who depends on`) → suggest `ce_wiki_impact_of` (v2) instead of `ce_pack_context`. The wiki tool is sharper for these queries; routing avoids the "agent over-calls the catch-all" failure mode named in the abandoned job-shaped-MCP-surface RFC.

Maximum 2 suggestions per response. Suggestions are heuristic; the server is not required to provide them.

**Client-side**: agents SHOULD prefer suggested tools when continuing a task — the server has more context about the cheap path than the agent does. Agents MUST NOT treat suggestions as required (they're hints, not contract).

This is a forward-compatible **MINOR** addition (§ 8 versioning) — clients that ignore the field continue to work unchanged.

### 3.0.2 Naming & v1.0 aliases

Tools use the `ce_*` snake_case prefix per the [MCP best-practices convention](https://modelcontextprotocol.io/specification) for disambiguation alongside other MCP servers. Bare names from earlier drafts are preserved as **v1.0 aliases**; aliases are removed in v2 with a 90-day deprecation window per § 8.

| Canonical (v1.0+) | v1.0 alias (deprecated v2) | Legacy pre-v1.0 alias |
|---|---|---|
| `ce_pack_context` | `pack_context` | `pack` |
| `ce_find_relevant_files` | `find_relevant_files` | `resolve` |
| `ce_upload_corpus` | `upload_indexed_corpus`, `register_corpus` | — |
| `ce_index_github_repo` | `index_github_repo` | `index_workspace` |
| `ce_list_corpora` | `list_corpora` | — |
| `ce_get_health` | `get_health` | `health` |
| `ce_get_job_status` | `get_job_status` | — |

Legacy stdio-MCP tool names that do **not** map 1:1 to a v1.0 canonical (`build_embeddings`, `stats`) are not aliased; clients calling those receive `INVALID_ARGUMENT` with a `details.canonical_replacement` hint pointing to the closest v1.0 tool.

Servers MUST emit `X-CE-Deprecated: <canonical-name>` on alias calls. Clients SHOULD pin to canonical names; alias support is for migration only.

### 3.0.3 Tool annotations

Per MCP 2025-06-18 spec, every tool declares behavioral hints clients use for parallelization decisions, safety prompts, and read-only-mode filtering:

| Tool | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` |
|---|:---:|:---:|:---:|:---:|
| `ce_pack_context` | true | false | true | false |
| `ce_find_relevant_files` | true | false | true | false |
| `ce_upload_corpus` | false | false | true | true |
| `ce_index_github_repo` | false | false | true | true |
| `ce_list_corpora` | true | false | true | false |
| `ce_get_health` | true | false | true | false |
| `ce_get_job_status` | true | false | true | false |

`idempotentHint: true` reflects same-input behavior; the equality predicate is per-tool (e.g. `ce_pack_context` keys on `(corpus_commit_sha, all input fields)`; `ce_upload_corpus` on `(corpus_id, files[].contentHash)` — see each tool's "Idempotency" subsection). `destructiveHint` is **false** for the write tools because their per-tool contracts make re-calls with identical inputs a no-op; the v2 `delete_corpus` will be the first tool to set this true.

Clients MUST NOT make security-critical decisions based on annotations — they are hints, not guarantees.

### 3.0.4 Current deployment status (informative)

The §3 catalog describes the **production v1 target** for the deployed MCP. Three surfaces exist today, at three completion stages — clients should pick the one that matches their need:

| Surface | Where | Tools live today | Notes |
|---|---|---|---|
| **Deployed MCP stub** | `https://ce-mcp-stub.vercel.app` (Vercel project `ce-mcp-stub`, region `cdg1`) | `pack_context`, `list_corpora`, `health` (3/7 specced) | YC-demo deployment shipped 2026-05-02 (PR #14). Wire-shape pinned to §3.1 / §3.5 / §3.6 — a v1 client can hit this URL and the request/response shapes match the spec. Single hard-coded corpus (this repo, indexed via `server-stub/build_demo_index.py`); keyword scoring only; 3 depth bands; no embeddings; no brain-repo writes. |
| **Local stdio MCP** | `scripts/mcp_server.py` (run via `mcp.run()` over stdio) | 15: `pack`, `resolve`, `index_workspace`, `index_github_repo`, `build_embeddings`, `stats`, `wiki.{ask,add,audit,impact_of}`, `lat.{locate,section,refs,search,expand}` | The full local surface. Naming convention per §10b — dotted namespaces (`wiki.*`, `lat.*`) for sub-corpus families; bare snake_case for top-level. Names intentionally diverge from the deployed-MCP target: `pack` (local) ↔ `ce_pack_context` (deployed); `resolve` (local) ↔ `ce_find_relevant_files` (deployed). The deployed target uses verbose snake_case for tool-list browsability; the local stdio uses shorter names because consumers already know the namespace. |
| **Production v1** | future Vercel deploy, replaces stub | 7 (the full §3 catalog) | Gap = `ce_find_relevant_files`, `ce_upload_corpus`, `ce_index_github_repo`, `ce_get_job_status`. Blocked on brain-repo writes (§5.1 lock-in-manifest), embedding-provider abstraction in MCP-server-mode (PR #35 landed BGE/codestral 2026-05-04), and §6 auth surface (hashed token map + `data_classification` gates + optional OAuth 2.1). |

**For agent authors**: bind to the deployed stub URL when YC-style demos suffice; bind to the local stdio MCP when you need the full surface (wiki, lat.md interop, write paths). The two surfaces coexist — same project, different deployments, intentionally different tool names per §10b.

**For SPEC readers**: §3.1–§3.7 describe the production v1 target. The stub implements §3.1 / §3.5 / §3.6 only. The local stdio MCP implements a parallel surface (different names, larger scope) called out in §10b.

### 3.0.5 Coverage transparency (cross-cutting response field)

**Why this exists**: a recall@5 of 0.30 means very different things when the
corpus had embeddings for 30% vs 100% of its files. Consumers building
benchmarks, dashboards, or quality alerts can't distinguish "we found 30%
of the right files" from "we only had retrieval signal for 30% of the
corpus and found everything we could." Pattern adopted from scix-agent's
typed-edge tools, where every response carries a `coverage` block.

**Contract**: read tools (`ce_pack_context`, `ce_find_relevant_files`,
and any future read tool) MUST include a top-level `coverage` object on
the response when ranking actually ran (i.e. on success, not on errors
that short-circuit before the engine).

```json
"coverage": {
  "corpus_size_files": 300,           // _meta.file_count for the corpus
  "ranked_with": "semantic",          // mode actually used by the engine
  "ranked_with_lane": "live",         // 'live' | 'static' | 'fallback' (see § 11.5)
  "files_eligible_for_mode": 300,     // had embeddings (semantic) or any tokens (keyword)
  "files_skipped_unembedded": 0,      // for semantic on a partial-embedding corpus
  "fallback_to_keyword": false,       // true when mode=semantic but engine ran keyword
  "trace_id": "<request-uuid>"        // server-assigned; useful for log lookup
}
```

In multi-corpus mode, `coverage` aggregates: `corpus_size_files` is the
total across all corpora; `files_eligible_for_mode` is the union; per-
corpus breakdown is available in the `_per_corpus` sub-object when
`why: true`.

**Wire compatibility**: `coverage` is a NEW optional top-level field;
clients that don't read it are unaffected. v1 servers SHOULD emit it as
soon as they have the data (Phase A / B of v1.1 plan); v1.0-rc4 servers
that skip it remain spec-conformant for the read shape but consumers
won't be able to compute "eligible recall" against them.

**Bench harness implication**: `eligible_recall = correct_files_found /
min(top_k, files_eligible_for_mode)` is the comparable cross-config
metric. Today's `file_recall` over-estimates configs with full coverage
relative to partial-coverage configs (or vice-versa); coverage closes
the gap.

### 3.1 `ce_pack_context` (alias: `pack_context`)

**Purpose**: Given a query and a corpus, return a depth-packed markdown bundle of relevant files sized to a token budget. The headline tool — 90% of consumer calls go here.

**Input**:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Natural-language query OR symbol name OR multi-word phrase. Max 4096 chars. |
| `corpus_id` | string | one of | — | Single target corpus. See § 4.1 for format. Mutually exclusive with `corpus_ids`. |
| `corpus_ids` | string[] | one of | — | Multiple target corpora (multi-corpus pack). Min 1, max 10. Mutually exclusive with `corpus_id`. Output paths get a `<corpus_id>:<path>` prefix to disambiguate. |
| `corpus_quota` | boolean | no | `true` (when `corpus_ids.length ≥ 2`) | Per-corpus quota allocation: round-robin top-K then global rerank. When false, fat-corpus may dominate. |
| `budget` | integer | no | 32000 | Token budget for packed output. Min 1000, max 200000. |
| `mode` | enum | no | `auto` | `auto`, `keyword`, `semantic`, `graph`, `deep`, `wide`. Note: in multi-corpus mode (`corpus_ids`), `semantic` requires all corpora share the same embedding `(provider, model, dims)` — otherwise returns `EMBEDDING_PROVIDER_MISMATCH`. |
| `task` | enum \| null | no | `null` | `fix`, `review`, `explain`, `build`, `document`, `research`, or `null` for auto-detect. |
| `model_context` | integer | no | `null` | Hint: caller's model context window (e.g. `1000000`). When set with no explicit `budget`, server scales budget to ~12% of `model_context`, clamped to [4000, 64000]. |
| `why` | boolean | no | `false` | If true, include a trace of mode/task selection + entry points + budget rationale before the packed markdown. |
| `response_format` | enum | no | `markdown` | `markdown` (single packed-markdown blob), `structured` (per-file content objects, no markdown blob), or `both`. Programmatic consumers SHOULD use `structured` to avoid markdown re-parsing. |

**Output** (`response_format: "markdown"`, default):

```json
{
  "markdown": "string (the packed context, ready to feed an LLM)",
  "tokens_used": "integer (actual)",
  "tokens_budget": "integer (the budget used)",
  "files": [
    { "path": "string (with `<corpus_id>:` prefix in multi-corpus mode)", "depth": "Full|Detail|Summary|Structure|Mention", "tokens": "integer", "relevance": "number 0..1", "corpus_id": "string (only in multi-corpus mode)" }
  ],
  "trace": "string | null (present if why=true)",
  "corpus_commit_sha": "string (single-corpus mode) | null (multi-corpus — see corpus_commit_shas)",
  "corpus_commit_shas": "object { [corpus_id]: sha } (multi-corpus mode) | null (single-corpus)",
  "coverage": "object — see § 3.0.5",
  "took_ms": "integer"
}
```

**Output** (`response_format: "structured"`):

```json
{
  "files": [
    {
      "path": "string (with `<corpus_id>:` prefix in multi-corpus mode)",
      "depth": "Full|Detail|Summary|Structure|Mention",
      "tokens": "integer",
      "relevance": "number 0..1",
      "content": "string (the rendered content at the assigned depth)",
      "corpus_id": "string (only in multi-corpus mode)"
    }
  ],
  "tokens_used": "integer",
  "tokens_budget": "integer",
  "trace": "string | null",
  "corpus_commit_sha": "string | null",
  "corpus_commit_shas": "object | null",
  "coverage": "object — see § 3.0.5",
  "took_ms": "integer"
}
```

`response_format: "both"` returns the union of both shapes from a single computation (servers MAY serve `markdown` from `files[].content` rather than rendering twice). ETag canonicalization includes `response_format`, so each shape caches independently.

**Multi-corpus invariants**:
- Exactly one of `corpus_id` / `corpus_ids` MUST be set (else `INVALID_ARGUMENT`).
- In multi-corpus mode, `corpus_commit_sha` is `null`; `corpus_commit_shas` is the authoritative reproducibility key. ETag derives from `(lexicographic sort of "<corpus_id>:<sha>" pairs over corpus_ids, sha256(canonical_inputs))`.
- All corpora MUST be readable by the caller's role / `data_classification_max`; classification denials return `INVALID_ARGUMENT` with `details.exceeded_classification` per § 6.3 (consistent with single-corpus). Authentication denials (no valid token at all) return `UNAUTHENTICATED` per § 7.2.
- **All-or-nothing semantics**: any per-corpus failure (`CORPUS_NOT_FOUND`, `CORPUS_LOCKED`, `CORPUS_PREFIX_COLLISION`, `EMBEDDING_PROVIDER_MISMATCH`) fails the whole call — multi-corpus calls never return partial results. Failure details list which corpus / corpora caused the failure.
- Path-prefix collisions (two corpora share the same root basename in `<corpus_id>:<path>`) trigger `CORPUS_PREFIX_COLLISION` with `details.colliding_corpora`.
- `corpus_ids.length` is capped at **10** for v1 to bound latency and prefix-collision-detection cost. v2 may raise this; in latency-sensitive paths, prefer ≤5.

**Errors**:

| Code | Meaning |
|---|---|
| `INVALID_ARGUMENT` | `query` empty, `budget` out of range, `mode`/`task` unknown, both/neither of `corpus_id`/`corpus_ids` set |
| `CORPUS_NOT_FOUND` | `corpus_id` (or any of `corpus_ids`) not in brain repo. `details.missing_corpora` lists the unknowns. |
| `CORPUS_ARCHIVED` | corpus is `archived` or `frozen` and serving disabled (rare; usually still served — see §4.3) |
| `CORPUS_LOCKED` | corpus is mid-refresh; retryable after `Retry-After` seconds |
| `RATE_LIMITED` | per-token call rate exceeded |
| `BUDGET_TOO_SMALL` | `budget` < min file's structural overhead (~500 tokens). Distinct from INVALID_ARGUMENT for clarity. |
| `EMBEDDING_PROVIDER_MISMATCH` | multi-corpus `mode: semantic` but corpora have different `(provider, model, dims)`. `details.providers` lists per-corpus tuples. Distinct from `EMBEDDING_MISMATCH` (shape error within one corpus). |
| `CORPUS_PREFIX_COLLISION` | multi-corpus paths would collide under the `<corpus_id>:<path>` convention. `details.colliding_corpora` lists the offenders. Caller can disambiguate by passing `corpus_ids` in a different order or renaming a corpus. |

**Idempotency**: idempotent given `(corpus_commit_sha | sorted(corpus_commit_shas), all input fields including `response_format`)`. Two calls with identical inputs against unchanged corpora return byte-identical `markdown` (or `files[].content` strings, for `structured`). In multi-corpus mode, the equality predicate uses the lexicographic-sorted concatenation of `corpus_id:sha` pairs.

**Cacheability**: `Cache-Control: private, max-age=60` plus an `ETag` derived from `(corpus_commit_sha + sha256(canonical_inputs))`. **NEVER `public`** — packed responses include source content from corpora that may be `confidential` or `restricted`; CDN/intermediary caching of these would leak content. Servers MUST additionally emit `Cache-Control: no-store` when the responding corpus's `data_classification` is `confidential` or `restricted`. Conditional `If-None-Match` returns 304.

ETag canonicalization: input fields serialized via RFC 8785 (JSON Canonicalization Scheme) before hashing. Two clients sending fields in different orders produce identical ETags.

### 3.2 `ce_find_relevant_files` (alias: `find_relevant_files`)

**Purpose**: Like `pack_context` but returns ranked paths only — no content. For consumers doing their own assembly (e.g. a tool that wants to pass paths to a different reader, or compose its own context format).

**Input**:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Same as `pack_context`. |
| `corpus_id` | string | one of | — | Single target corpus. Mutually exclusive with `corpus_ids`. |
| `corpus_ids` | string[] | one of | — | Multi-corpus mode. Min 1, max 10. Mutually exclusive with `corpus_id`. |
| `top_k` | integer | no | 20 | Min 1, max 200. |
| `mode` | enum | no | `auto` | Same set as `pack_context`. Same multi-corpus `EMBEDDING_PROVIDER_MISMATCH` rule for `semantic`. |
| `task` | enum \| null | no | `null` | Same. |

**Output**:

```json
{
  "files": [
    {
      "path": "string (with `<corpus_id>:` prefix in multi-corpus mode)",
      "relevance": "number 0..1",
      "keyword_score": "number (0 if mode skipped keyword)",
      "semantic_score": "number (0 if mode skipped semantic)",
      "graph_score": "number (0 if mode skipped graph)",
      "reason": "string (human-readable: 'matched X via semantic similarity, confirmed via graph hop from Y')",
      "corpus_id": "string (only in multi-corpus mode)"
    }
  ],
  "corpus_commit_sha": "string | null",
  "corpus_commit_shas": "object | null",
  "coverage": "object — see § 3.0.5",
  "took_ms": "integer"
}
```

**Errors**: `INVALID_ARGUMENT`, `CORPUS_NOT_FOUND`, `CORPUS_LOCKED`, `RATE_LIMITED`, `EMBEDDING_PROVIDER_MISMATCH`, `CORPUS_PREFIX_COLLISION`.

**Idempotency**: same as `pack_context`, including the multi-corpus rule (sorted concatenation of `corpus_id:sha` pairs).

### 3.3 `ce_upload_corpus` (aliases: `upload_indexed_corpus`, `register_corpus`)

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
- `{ "format": "presigned", "vectors_url": "https://...", "paths_url": "...", "hashes_url": "...", "byte_format": "float32-le-row-major" }` — for payloads >8 MB. Client first calls `ce_upload_corpus_init` (returns presigned URLs), uploads each blob, then calls `ce_upload_corpus` with the URLs. Server validates byte length matches `N × dims × 4`.

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
| `PAYLOAD_TOO_LARGE` | inline request body > 32 MB; use `format: "presigned"` per § 3.3 |
| `EMBEDDING_MISMATCH` | `paths.length !== hashes.length`, or `vectors` shape doesn't match `(N, dims)` |
| `WRITE_CONFLICT` | git push 409 after 3 retries |
| `BRAIN_UNAVAILABLE` | GitHub API down/throttled beyond retry budget |

**Idempotency**: idempotent on `(corpus_id, files[].contentHash for all files)`. A second call with identical content is a no-op (returns the existing `commit_sha` without writing).

### 3.4 `ce_index_github_repo` (alias: `index_github_repo`)

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
| `SOURCE_FORBIDDEN` | server's GitHub App can't read `repo`. Caller should use `ce_upload_corpus` instead. |
| `SOURCE_NOT_FOUND` | repo doesn't exist |
| `CORPUS_LOCKED` | retryable |
| `BUDGET_EXCEEDED` | sync indexing would exceed function timeout (~50s). Caller should retry with `async=true`. |
| `EMBEDDING_PROVIDER_ERROR` | upstream embedding API failed beyond retry budget |
| `WRITE_CONFLICT` | git push 409 after 3 retries |

**Idempotency**: idempotent on `(repo, branch, commit_sha)`. Re-indexing the same source commit is a no-op modulo embedding-provider drift (different model versions yield different vectors).

### 3.5 `ce_list_corpora` (alias: `list_corpora`)

**Purpose**: Discoverability. Returns all corpora visible to the caller, with metadata.

**Input**:

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `lifecycle_state` | enum[] | no | `["active", "idle"]` | Filter. Pass `["archived", "frozen"]` to include those. |
| `data_classification_max` | enum | no | `internal` | Maximum classification visible to caller. Default `internal`; explicit pass needed for higher. |
| `source_type` | enum | no | `null` | Filter by source type. |
| `limit` | integer | no | 50 | Max corpora per response. Min 1, max 200. |
| `offset` | integer | no | 0 | Pagination offset. |

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
  ],
  "total_count": "integer (matching corpora after data_classification_max + lifecycle_state + source_type filtering, before pagination)",
  "has_more": "boolean (true if total_count > offset + corpora.length)",
  "next_offset": "integer | null (offset for next page, or null if has_more is false)",
  "brain_head_sha": "string (the brain-repo sha this page was computed from)"
}
```

Pagination is best-effort against live data: each call recomputes against the current `brain_head_sha`, which the response echoes. Clients SHOULD compare `brain_head_sha` across pages and re-page from offset 0 if it changes.

**Errors**: `RATE_LIMITED` only.

**Idempotency**: trivially idempotent (read-only).

### 3.6 `ce_get_health` (aliases: `get_health`, `health`)

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
  "auth_methods_supported": ["bearer"],
  "took_ms": "integer"
}
```

**Errors**: never errors when reachable. If unreachable, no response.

### 3.7 `ce_get_job_status` (alias: `get_job_status`)

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

**Schema evolution policy (split by storage tier)**:

- `wiki/<slug>.md` pages: **refusal-and-rebuild** while corpus has < 10k entities. A `schema_version` bump triggers full regeneration via `wiki_init.py --rebuild` from the canonical `events/` log. Old wiki pages are archived to `wiki-archive/<schema_version>/` for diff/rollback. Above the 10k threshold, switch to forward-migrators (deferred to v2).
- `events/<YYYY-MM-DD>.jsonl`: **forward-migrate from day one**. Each line carries its own `schema_version`. Migrators are pure functions chained `v0 → v1 → v2 → ...` and applied at read time; events are never rewritten in place. Events are the primary truth; wiki is a materializable view.

Rationale: events are append-only and small per-line, so per-record migration scales. Wiki pages are larger, denormalized, and cheap to regenerate from events while the corpus is small — refusing-and-rebuilding is simpler than per-page migrators until the rebuild cost crosses ~minutes.

**Decision-continuity fields**: wiki entity pages with `kind: decision` carry the decision-continuity fields `supersedes`, `superseded_by`, `valid_until` defined in § 10.1. These fields are part of the wiki page schema and bump `schema_version` if changed.

### 4.3 Lifecycle

States: `active`, `idle`, `archived`, `frozen`. Transitions and serving
behavior defined in
[`LIFECYCLE.md`](https://github.com/syrocolab/company-brain/blob/main/LIFECYCLE.md).

Default serving policy:

- `active`, `idle`, `frozen` corpora are served by all read tools (`ce_pack_context`, `ce_find_relevant_files`, `ce_list_corpora`).
- `archived` corpora return tombstone metadata in `ce_list_corpora` and `CORPUS_ARCHIVED` from read tools, with `archive_location` in the error details.

A successful refresh resets `lifecycle_state` to `active`.

### 4.4 Freshness contract (entity pages)

Wiki entity pages carry a freshness signal that read tools surface to consumers. **Stored vs computed split**:

- **Stored**: `last_verified_at: <ISO 8601 timestamp>` per entity page (set by writer). Optionally `sources: [{type, ref, last_seen_at}, …]` listing source references with their own last-seen timestamps.
- **Computed-on-read**: `freshness_score: number 0..1`. Server computes at read time using a per-source-type half-life table, never stores it.

**Half-life table** (default; servers MAY tune via configuration):

| Source type | Half-life |
|---|---|
| `code` | 90 days |
| `web` | 30 days |
| `transcript` | 60 days |
| `email` | 21 days |
| `notion` | 60 days |
| `rfc` | 180 days |
| `department-spec` | 180 days |
| `default` (unknown source type) | 60 days |

**Decay formula**: `freshness_score = max(0, 1 - elapsed_seconds / (2 * half_life_seconds))` — linear, hits 0.5 at half-life and 0 at twice-half-life. Multi-source entities (`sources[]`) use the **shortest** half-life among the listed types (the most-stale-source rule prevents masking decay with one fresh signal).

**Read-tool surfacing**: `ce_pack_context` and `ce_find_relevant_files` SHOULD include `freshness_score` per file in their `response_format: "structured"` output for entities that have a `last_verified_at` field. Entities without `last_verified_at` MUST omit `freshness_score` (do not return `0` or `null` — the field is absent). `freshness_score < 0.3` triggers the `freshness.expired` telemetry event (§ 9). When `ce_pack_context` packs a decision page past `valid_until`, it MUST mark the file with `expired: true` in structured output and downgrade depth to `Mention` unless the caller passes (v2) `include_expired: true`. v2 may add `freshness_floor` filter inputs to read tools.

**Time semantics**: all "days" in this section refer to **86400-second days** (no DST/leap-second adjustment). `elapsed_seconds` and `half_life_seconds` use UTC-based `Math.floor((now - last_verified_at) / 1000)` and `half_life_days * 86400` respectively.

This is a **wire-contract decision**: clients see the computed score, never the stored field directly. Implementers may rebuild the half-life table or formula in v2 (MAJOR bump per § 8); v1 must conform to the table above.

---

## 5. Operational semantics

### 5.1 Refresh & locking

A refresh is one of:
- `ce_index_github_repo` (server-side full re-index)
- `ce_upload_corpus` (client-supplied re-upload)

Refresh writes acquire a corpus-scoped lock by writing `manifest.lock = {holder, acquired_at, expires_at, intent}` *first*, then performing data writes, **then committing the data + lock-clear in a SINGLE tree-then-commit API call** (no separate "release commit" — that race window leaks locks). The single commit bumps `version` + `last_refresh_completed_at` + `last_refresh_commit_sha` AND sets `lock: null` atomically.

Lock TTL is **300 seconds** for sync refreshes (Vercel Pro 60s + buffer); **20 minutes** for async refreshes via Vercel Cron. Long-running refreshes MUST emit a heartbeat every 60 seconds that updates `lock.expires_at = now + TTL`. Without heartbeats, a 15-minute Cron job's lock would expire mid-work and another writer would race-take it over, producing torn writes.

Lock acquire is idempotent on `holder`: re-PUT-ing with the same `holder` and current `lock.expires_at` is a no-op success — covers retry-after-network-hiccup cases.

Stale locks (`expires_at < now`) MAY be taken over by another writer. Takeover emits a `lock.taken_over` telemetry event with `prior_holder` and `age_seconds` for SRE diagnosis.

### 5.2 Async operations

Tools accepting `async: true` (currently `ce_index_github_repo` and reserved
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

A bootstrap token for the first deploy can come from `CE_MCP_BOOTSTRAP_TOKEN` env (writes one row to KV at startup if KV is empty).

**OAuth 2.1 (optional in v1.0, default in v2)**: Per [Anthropic's MCP auth guidance](https://modelcontextprotocol.io/specification), CE MAY expose OAuth 2.1 alongside Bearer. Bearer is the single-tenant default; OAuth 2.1 is required for multi-tenant or partner deployments. When enabled, servers MUST expose RFC 8414 metadata at `/.well-known/oauth-authorization-server` and add an `auth_methods_supported: ["bearer", "oauth2.1"]` field to `ce_get_health` output (this is distinct from the embeddings-provider list in `providers_available`). Token format and grant types are deferred to v2; v1.0 implementers SHOULD align with the MCP spec's published OAuth profile when it stabilizes. v2 makes OAuth 2.1 the default and deprecates raw Bearer for new deployments.

Roles in v1:
- `reader` — may call all read tools. Implicit `data_classification_max: internal`.
- `writer` — `reader` + `ce_upload_corpus`, `ce_index_github_repo`. Implicit `data_classification_max: confidential`.
- `admin` — all of the above + `data_classification_max: restricted`. Reserved for human ops.

A v2 spec will replace this with per-corpus ACL.

### 6.2 GitHub App

Server-side state mutations use a GitHub App installation token, NOT a PAT.
The App is scoped to:
- `Contents: Read & Write` on `syrocolab/company-brain`
- `Contents: Read & Write` on `syrocolab/company-brain-archive` (archive sibling)
- `Contents: Read` on Syroco source repos (for `ce_index_github_repo`)
- `Metadata: Read`

Source-repo reads outside the Syroco org use a separate read-only GitHub
App installation per org, OR fall back to unauthenticated public access
(rate-limited).

App credentials live in Vercel encrypted env. Rotation cadence: 90 days.

### 6.3 Data classification gates

Every corpus carries `data_classification ∈ {public, internal, confidential, restricted}`.

Read tools (`ce_pack_context`, `ce_find_relevant_files`) MUST refuse to return
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

### 6.4 Local HTTP server hardening

When CE runs as a Streamable HTTP server on a developer machine (rather than the canonical Vercel deploy):

- The server MUST **bind to `127.0.0.1` by default**, not `0.0.0.0`. Opt-in to `0.0.0.0` only via `CE_MCP_BIND_PUBLIC=1`, with a startup warning.
- When bound to loopback, the server MUST **reject requests whose `Origin` resolves to a non-loopback host** (`400 INVALID_ARGUMENT`) — this blocks DNS rebinding from malicious browser tabs. `Origin: null` (file://, sandboxed iframes) MUST also be rejected unless `CE_MCP_ALLOW_NULL_ORIGIN=1`. Servers MAY accept additional origins via `CE_MCP_ALLOWED_ORIGINS` (CSV).
- The server MUST **disable `CE_MCP_BOOTSTRAP_TOKEN`** when bound to a non-loopback address.

The Vercel deploy (fixed origin, managed TLS) is not subject to these rules; they apply to local-HTTP-mode only. stdio mode has no transport-layer attack surface.

---

## 7. Error model

CE distinguishes **tool errors** (returned in the tool's result so the agent can self-correct) from **protocol errors** (JSON-RPC errors that abort the call). HTTP-direct clients get a parallel HTTP shape carrying the same information. All three layers agree on the canonical string code.

**Boundary rule for implementers**: `auth + transport failures → § 7.2; everything else → § 7.1`. Concretely, only `UNAUTHENTICATED`, `PERMISSION_DENIED`, `WEBHOOK_SECRET_MISMATCH`, `BRAIN_UNAVAILABLE`, and `INTERNAL` are protocol errors. Request-validation, rate-limiting, payload-size, and upstream-provider failures are tool errors — the agent has the context to self-correct or retry.

### 7.1 Tool errors (`result.isError: true`)

Per MCP 2025-06-18 spec, errors that arise from a *valid* tool call — bad inputs, missing corpora, retryable conflicts — return as `result.isError: true` with a structured content block. The agent sees this as a normal tool response and can adjust its next call without thinking the protocol broke.

```json
{
  "isError": true,
  "content": [
    {
      "type": "text",
      "text": "INVALID_ARGUMENT: budget 500 below min 1000. Try budget=4000 or higher."
    }
  ],
  "structuredContent": {
    "code": "INVALID_ARGUMENT",
    "details": { "field": "budget", "min": 1000 },
    "retryable": false,
    "retry_after_seconds": null
  }
}
```

Tool error codes:

| Code | When | HTTP-direct | Retryable |
|---|---|---|---|
| `INVALID_ARGUMENT` | request shape / value violation (budget out of range, mode unknown, corpus_id malformed, etc.) | 400 | no |
| `BUDGET_TOO_SMALL` | `budget` < min file overhead (~500 tok) | 400 | no |
| `EMBEDDING_MISMATCH` | `paths.length !== hashes.length`, or vectors shape ≠ (N, dims) | 400 | no |
| `CORPUS_NOT_FOUND` | `corpus_id` not in brain repo | 404 | no |
| `CORPUS_ARCHIVED` | corpus archived; `archive_location` in details | 410 | no |
| `CORPUS_LOCKED` | another writer holds the lock | 409 | yes |
| `JOB_NOT_FOUND` | unknown `job_id` (or expired after 7 days) | 404 | no |
| `BUDGET_EXCEEDED` | sync indexing would breach Vercel timeout — retry with `async=true` | 408 | no — use async |
| `EMBEDDING_PROVIDER_ERROR` | upstream embedding API failed beyond retry budget | 502 | yes |
| `EMBEDDING_PROVIDER_PARTIAL` | partial success; `details.success_count` populated | 502 | yes |
| `SOURCE_FORBIDDEN` | server's GitHub App can't read repo — use `ce_upload_corpus` | 403 | no |
| `SOURCE_NOT_FOUND` | repo doesn't exist | 404 | no |
| `SOURCE_MISMATCH` | `corpus_id` collides with different source.branch | 409 | no — pass `corpus_id` explicitly |
| `WRITE_CONFLICT` | git push 409 after 3 retries | 409 | yes |
| `BRAIN_RATE_LIMITED` | GitHub secondary rate limit; back off, don't page | 503 | yes |
| `EMBEDDING_PROVIDER_MISMATCH` | multi-corpus `mode: semantic` but corpora differ on `(provider, model, dims)` | 400 | no |
| `CORPUS_PREFIX_COLLISION` | multi-corpus paths collide under `<corpus_id>:<path>` convention | 400 | no |
| `PAYLOAD_TOO_LARGE` | inline request body > 32 MB; use `format: "presigned"` | 413 | no |
| `RATE_LIMITED` | per-token call rate exceeded | 429 | yes |

### 7.2 Protocol errors (JSON-RPC)

Errors that prevent the tool from running at all — auth, oversized payloads, server bugs, infrastructure outages — surface as JSON-RPC `error` per MCP spec:

```json
{
  "jsonrpc": "2.0",
  "id": "<request id>",
  "error": {
    "code": -32001,
    "message": "human-readable",
    "data": {
      "code_name": "UNAUTHENTICATED",
      "details": { "...": "..." },
      "retryable": false,
      "retry_after_seconds": null
    }
  }
}
```

Protocol error codes:

| Code | JSON-RPC | HTTP-direct | Retryable | Meaning |
|---|--:|---|---|---|
| `UNAUTHENTICATED` | -32001 | 401 | no | missing/invalid bearer token |
| `PERMISSION_DENIED` | -32002 | 403 | no | token role insufficient for this tool / data classification |
| `BRAIN_UNAVAILABLE` | -32009 | 503 | yes | GitHub upstream down |
| `WEBHOOK_SECRET_MISMATCH` | -32011 | 500 | no — operator action |
| `INTERNAL` | -32603 | 500 | yes | server bug; reported to telemetry |

### 7.3 HTTP-direct shape

Non-MCP HTTP clients calling the same Vercel functions directly receive a flat error envelope (regardless of layer):

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

HTTP responses MUST also emit the `Retry-After` header (in seconds) when `retryable: true` and `retry_after_seconds` is known. Header and body agree; clients may use either.

`retryable: true` errors include `retry_after_seconds` when the server can estimate a useful backoff (from upstream rate-limit headers). Clients SHOULD honor it.

---

## 8. Versioning

Spec semver: `MAJOR.MINOR.PATCH`.

- `MAJOR` bump: breaking change to any tool's input/output schema or error
  semantics. Compatibility window: 90 days minimum.
- `MINOR` bump: backwards-compatible additions (new tools, new optional
  fields, new error codes that map to existing categories).
- `PATCH` bump: clarifications, doc fixes, no behavior change.

`ce_get_health` returns the active spec version. Clients SHOULD log it on startup.

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
| `audit.flagged` | Auditor surfaces a proposal | `corpus_id, rule (stale-supersession \| freshness-expired \| slug-collision \| contradiction), entity_id, reason` |
| `audit.broken_refs` | `lat_check` finds broken refs (CI integration) | `corpus_id, ref_count_total, ref_count_broken, exit_strict (boolean)` |
| `freshness.expired` | computed `freshness_score < 0.3` AND `last_verified_at` older than half-life | `corpus_id, entity_id, source_type, half_life_days, elapsed_days, freshness_score` |
| `corpus.consolidation_triggered` | semantic-shift detector triggers async consolidation after `ce_upload_corpus` / `wiki.add` events | `corpus_id, trigger (cosine_drift \| event_count_threshold \| manual), affected_entities, took_ms` |
| `multi_corpus.pack` | `ce_pack_context` or `ce_find_relevant_files` called with `corpus_ids[]` | `corpus_count, corpora (lexicographically sorted by corpus_id), corpus_quota (boolean), prefix_collisions (count)` — sized for capacity planning |

Event schemas freeze at the same MAJOR version as the tool API. Event sink is JSON-line stdout in v1 (caller pipes to OTel collector / Vercel Logs / wherever). v1.1 will commit to OpenTelemetry semantic conventions.

No event includes the `query` string or any indexed file content. Event
schemas freeze at the same MAJOR version as the tool API.

---

## 10. Future tools (v2 contract preview, non-normative)

Reserved names — implementations MUST NOT use these for unrelated tools. v2 will adopt the `ce_` prefix per § 3.0.2; the bare names listed below are reserved in both forms.

### 10.1 `ce_wiki_*` family (v2 deployed promotion)

The `wiki.*` family is shipped on the local stdio MCP today (`wiki.{ask,add,audit,impact_of}`). v2 promotes them to the deployed MCP under the `ce_wiki_*` canonical prefix per § 3.0.2. Sketches below pin v1.0 spec-grade decisions so callers can rely on them once promoted.

#### `ce_wiki_ask(query, corpus_id?, scope?, freshness_floor?, max_entities?)`

Entity-aware semantic ask over the wiki layer; returns markdown with cited entity pages.

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Required. |
| `corpus_id` | string | — | Target corpus (selects which corpus to query). Orthogonal to `scope` (which filters entities *within* the corpus by their frontmatter `scope:` field). |
| `scope` | string | `default` | Filters to entities whose frontmatter `scope:` matches. **Absent `scope` parameter MUST default to `scope: "default"` only** — entities tagged with other scopes (e.g. `competitive-intel`, `code-context`, `leads`) MUST NOT leak in. Pass `scope: "*"` to match all scopes within `corpus_id`. **The scope filter is applied AFTER the `data_classification_max` gate** — `scope: "*"` cannot bypass classification (see § 6.3). This is the entity-isolation contract that makes the source-agnostic positioning enforceable. |
| `freshness_floor` | number 0..1 | `0.0` | Filter out entities with computed `freshness_score < floor` per § 4.4. |
| `max_entities` | integer | 20 | Min 1, max 100. |

#### `ce_wiki_add(corpus_id, entity_id, body, sources, scope?, supersedes?)`

Append an entity event. **Async-write invariant**: the call is append-to-events; consolidation into `wiki/<slug>.md` is async via the semantic-shift detector. Read-after-write of `ce_wiki_ask` within ~60s (or the configured `corpus.consolidation_triggered` debounce) MAY not reflect the write. Servers MUST emit a `next_tool_suggestions` entry pointing to `ce_get_job_status` (or, in v1.0, document the timeout).

#### `ce_wiki_audit(corpus_id, rules?)`

Run Auditor rules. Returns proposals (splits / merges / contradictions / dead links / stale supersession / freshness expiry). Each proposal triggers `audit.flagged` telemetry per § 9.

#### `ce_wiki_impact_of(entity, max_hops?, relation_kinds?, min_weight?, budget?, include_hubs?)`

Entity-rooted impact closure with risk-score per affected entity. Local stdio name: `wiki.impact_of` (shipped via PR #37 in `scripts/mcp_server.py` + `scripts/wiki/impact_of.py`); deployed-v2 canonical name follows § 3.0.2's `ce_*` prefix.

**`relation_kinds` defaults**: `null` = "use the per-corpus-type default below" — narrows automatically. Pass an explicit array to override.

| Corpus type | Default `relation_kinds` |
|---|---|
| Code corpus (source type `gh` or `local` with code language detected) | `["imports", "calls", "extends", "implements", "uses_type", "tested_by", "references"]` |
| Wiki corpus (entity-page corpus) | `["mentions", "supersedes", "decided_by", "applies_to", "links_to"]` |
| Mixed / unknown | All edge kinds (no narrowing) |

This pins the design from the abandoned "job-shaped MCP surface" RFC; future drift requires a MAJOR bump per § 8.

#### Decision-continuity fields (entity-page schema)

Wiki entity pages with `kind: decision` carry three normative fields used by `ce_wiki_ask`, `ce_wiki_audit`, and `ce_wiki_impact_of`:

- `supersedes: <entity_id>` — this decision replaces the listed predecessor. The Auditor flags a successor that contradicts a still-valid predecessor (`stale-supersession` rule).
- `superseded_by: <entity_id>` — the inverse pointer (computed-or-stored, server's choice; if stored, MUST be kept consistent with `supersedes` chains).
- `valid_until: <ISO 8601 timestamp | null>` — explicit expiry. Server emits `freshness.expired` (§ 9) when crossed even if `last_verified_at` is recent.

These fields are CE's answer to `codebase-memory-mcp`'s `manage_adr` (see § 1.5.1).

### 10.2 Other reserved names

Reserved tool names — implementations MUST NOT use these for unrelated tools. v2 will adopt the `ce_` prefix per § 3.0.2 (e.g. `ce_find_related_symbols`, `ce_delete_corpus`); the bare names below are reserved in both forms.

- `find_related_symbols(symbol, corpus_id, hops?, relations?)` — graph traversal. Blocked on persistent `graph.jsonl`.
- `list_concept_clusters(corpus_id)` — birds-eye LLM-labelled clusters (PR #8).
- `detect_concept_drift(corpus_id, since)` — diff between commits (PR #10).
- `refresh_corpus(corpus_id)` — explicit refresh trigger.
- `delete_corpus(corpus_id)` — admin-only.
- `verify_corpus(corpus_id)` — schema + integrity check.
- `get_corpus_stats(corpus_id?)` — aggregate stats.
- `compute_embeddings(corpus_id, provider?, model?)` — re-embed without re-indexing.
- `get_pending_embeddings(corpus_id)` / `submit_embeddings(corpus_id, vectors)` — external handoff.

---

## 10b. lat.md interop tools (Phase 4 of CE × lat.md, local stdio MCP)

Five thin wrappers exposing lat.md's verb surface (`locate / section / refs / search / expand`) on top of CE primitives. Currently local stdio MCP only; v2 may promote to the deployed MCP if customer demand surfaces. All five emit `tool.call` + `tool.result` telemetry per §9. Implemented in `scripts/mcp_server.py` (Phase 4 commit landed 2026-05-03).

- `lat.locate(ref, brain?)` — resolve a wiki/code ref (`[[slug]]`, `[[slug#section]]`, or `[[src/file.ts#symbol]]`) to its location. Returns markdown with file path + line range (code refs) or section anchor (section refs). Slug refs glob `wiki/<slug>.md`; code refs load `cache/code_index.json` via `wiki.code_index.resolve_symbol`.
- `lat.section(ref, brain?, budget?)` — return the body slice for a ref. Slug → entire page body (frontmatter stripped). Section → heading-bounded slice (case-insensitive + slug-normalized match). Code → symbol's line range as a fenced code block. Output capped at `budget * 4` characters.
- `lat.refs(target, brain?)` — list every wiki page that references `target`. Walks `brain/wiki/*.md`, parses every wikiref via `wiki.wikiref.parse_wikirefs`, dedupes by `(source_slug, raw_ref)`, returns one bullet per inbound ref with `kind` annotation.
- `lat.search(query, brain?, budget?)` — case-insensitive substring search across wiki page bodies AND `cache/code_index.json` symbol names. Returns a markdown blob with two sections (wiki pages, code symbols). Capped at `budget * 4` chars; first 50 results per section.
- `lat.expand(ref, brain?, depth?, budget?)` — BFS recursive expansion. Fetches `lat.section(ref)`, parses every wikiref it contains, follows up to `depth` hops, dedupes visited refs, concatenates into one markdown blob. Stops once `budget * 4` chars reached.

The five tools accept either bare refs (`auth-middleware`, `auth-middleware#OAuth Flow`, `src/foo.ts#bar`) or fully-bracketed refs (`[[auth-middleware]]`). Servers MUST normalize bare refs to bracketed form internally (single canonical wikiref kind); both inputs MUST yield byte-identical outputs.

**Normative invariants** (lifted from `PRD-latmd-integration.md` for spec-grade enforcement):

- **`lat.locate` upstream conformance**: when invoked against the `1st1/lat.md` upstream corpus, output MUST match `lat locate` output byte-for-byte (modulo line-ending normalization). This is the conformance test for any future `ce_lat_locate` v2 promotion.
- **Ambiguous symbol resolution**: when a bare symbol resolves to multiple definitions, `lat.locate` MUST return a list and prefer **exported / public** symbols; ties broken by file-path lexicographic order. Callers MAY disambiguate via dotted path `[[src/foo.ts#Class.method]]`.
- **`corpus_commit_sha` in every return**: all `lat.*` (and `wiki.*`) responses MUST include `corpus_commit_sha` (single-corpus mode) or `corpus_commit_shas` (multi-corpus, when v2 promotion adds multi-corpus support) for cache idempotency. Same shape and ETag rules as § 3.1.
- **`lat_check` exit-code contract** (CI integration): the auxiliary `lat_check.py` CLI exits `0` when no broken refs, `1` when broken refs found and `--strict` was passed, `0` (with stderr warning) when broken refs found and `--strict` was not passed. Servers MUST NOT change this without a MAJOR bump per § 8 — third-party CI integrations depend on it.
- **`audit.broken_refs` telemetry**: every `lat_check` run emits the event in § 9 with `ref_count_total`, `ref_count_broken`, and `exit_strict (boolean)`.

Naming convention: `lat.*` and `wiki.*` dot-notation is **local-stdio-only** namespace shorthand. The deployed MCP uses the `ce_*` snake_case prefix per § 3.0.2; when these families promote to deployed (v2 candidates), they adopt `ce_lat_*` and `ce_wiki_*`. The `lat.*` divergence is preserved on the local stdio for parity with lat.md upstream verb names; `wiki.*` is purely a CE-native namespace convenience. New `lat.*` tools require a backing CE primitive.

---

## 10c. Future cross-cutting patterns (non-normative, v1.2+)

CE today is stateless, returns flat lists with bare relevance scores, and treats provider degradation as silent fallback. Five patterns from peer scientific-MCP servers (notably **scix-agent**, which ships stateful + typed-edge + provenance-aware MCP for academic literature) belong on the v1.2+ roadmap. They are non-normative until v1.2 lands; servers MAY emit them earlier as long as the wire shape doesn't conflict with v1 fields.

### 10c.1 Stateful sessions — server-side focused-set fall-through

Today every read call is a cold lookup. An agent debugging iteratively does `pack({query: "auth bug"})` → reads → `pack({query: "test setup"})` and the server has no idea the second call relates to the first.

**Contract sketch**:
- Server keeps `SessionState` keyed by `session_id` in KV (sliding 1-hour TTL).
- Each successful read updates a focused-set (FIFO, capped 500): paths returned, queries asked, mode used, corpus_id touched.
- Read tools accept optional `session_id` + new mode `"continuation"`. With `mode: continuation`, server defaults to "expand around the recent focused set" when `query` is sparse.
- Inspired by scix-agent's `_session_fallthrough_bibcodes` resolver (`mcp_server.py:3759`).

**v1 forward-compatibility**: clients that pass no `session_id` get the existing stateless behavior. v1.0 servers MUST ignore `session_id`/`continuation` rather than erroring on them — clients can opt in incrementally.

### 10c.2 Typed graph edges (over current `imports`-only)

The local stdio MCP exposes graph traversal through the import graph (PR #4 + Graphify). Edges today are either "imports" (default) or whatever Graphify classified them as (call_graph, inheritance, cross_language, doc_to_code). The wire shape doesn't expose this typing.

**Contract sketch**: when `mode: graph` (or `mode: semantic+graph`), each entry in `files[]` MAY include `via: { edge_type: "imports"|"calls"|"extends"|"references"|"tests"|"documents"|"configures", weight: number }`. Aggregate edge type set is enumerated; servers MAY emit a sub-set (`imports` is the floor).

**v1 forward-compat**: `via` is an optional new field; clients that don't read it are unaffected. v2.0 will likely PROMOTE this to required when graph mode actually ships server-side.

### 10c.3 Chain-shaped trace (typed walks, not flat ranks)

Today `pack`/`find` return `[{path, relevance, score}]`. scix-agent returns a `Hop[]` chain — every step labeled with intent, weight, context snippet, section. The agent gets a typed walk, not a soup of paths.

**Contract sketch**: when `why: true`, `trace` becomes a structured object instead of a string:
```json
"trace": {
  "hops": [
    {"from": "<query>", "to": "login.test.ts", "edge": "semantic", "weight": 0.91},
    {"from": "login.test.ts", "to": "auth.py", "edge": "imports", "weight": 1.0},
    {"from": "auth.py", "to": "session.py", "edge": "calls", "weight": 0.6}
  ],
  "weights": {"intent": 0.4, "graph": 0.3, "semantic": 0.3},
  "rationale_text": "<the v1 string-form trace, kept for back-compat>"
}
```

**v1 forward-compat**: existing clients reading `trace` as a string keep working — they read `trace.rationale_text` instead, OR servers MAY return a string when an `accept_trace_format: "string"` hint is set. Default: structured object once v1.2 ships; v1.0/v1.1 keep string.

### 10c.4 ZFC scaffolding mode — outline-shaped output

scix's `synthesize_findings` bins working-set bibcodes into named sections by intent + community-share thresholds, then returns a labeled outline the agent fills with prose. Inverse of standard RAG: server emits *structure*, agent emits content.

**Contract sketch**: new `output_mode` on `ce_pack_context`:
```
output_mode: "chunks" (default; current behavior — depth-banded chunks)
output_mode: "outline" (new; section-shaped scaffold for the agent to fill)
```

`outline` response shape:
```json
{
  "outline": {
    "task_inferred": "fix",
    "sections": [
      {"name": "What's broken", "files": [...], "rationale": "..."},
      {"name": "How it's tested", "files": [...], "rationale": "..."},
      {"name": "What it depends on", "files": [...], "rationale": "..."}
    ],
    "skipped_for_budget": [...]
  },
  "coverage": {...},
  "took_ms": ...
}
```

Section templates derive from the existing `task` enum (fix / review / explain / build / document / research). `code_graph.py`'s task taxonomy already exists locally — the server work is exposing it via this mode.

**Bench rationale**: agents on CSB H4 (Haiku reward) given `output_mode: outline` should produce more coherent answers than agents given equivalent-token chunk dumps. v1.2 introduces a separate bench cell to measure this.

### 10c.5 Degrading-lane resolver with canary

Today CE soft-falls-back when semantic embedding is unavailable (no key, Mistral 5xx, etc.) — silently. scix routes per-document calls across lanes (`live LLM` / `local model` / `static sentinel`) with a JIT bulkhead (400ms budget) and a 5% canary deliberately routed to the local lane to keep regression signal alive.

**Contract sketch**:
- Three lanes for embedding: `live` (Mistral codestral-embed), `local` (BGE-small bundled, v1.2), `static` (deterministic hash projection — never throws, never hits the network).
- Lane choice surfaces in `coverage.ranked_with_lane` (already in § 3.0.5).
- Canary rate configurable via `CE_CANARY_RATE` env (default 0.05). Canary requests carry `coverage.canary: true` so observability can split metrics.

**v1.1 lane partial**: the `static` lane lands in v1.1 as the safety net under `live`. The `local` lane (bundled model) is v1.2.

### Why these are deferred (load-bearing context)

CE's v1 wire surface MUST stabilize before v1.2 changes layer onto it. Pattern 6 (coverage transparency, § 3.0.5) is the only one of the six promoted to v1 because:
- It's purely additive (new field, ignorable by old clients).
- It's bench-blocking — the v1 final numbers depend on `eligible_recall` to compare configs honestly.
- The data already exists server-side; threading it into the response is a 1-day landing.

The other four patterns (sessions, typed edges, chain trace, outline mode, degrading lanes) all interact with each other and with `p4-cross-corpus-pack.md`'s new graph primitives. Locking them into v1 would force premature decisions. Specifying them here as v1.2 contracts keeps the door open without bending v1's wire.

---

## 11. Skill-author migration path (informative)

The existing `context-engineering` skill at `agent-skills/context-engineering/` is CLI-first. After v1 deploy, it becomes a thin MCP client. Migration semantics:

- **Trigger**: skill checks `CONTEXT_ENG_MCP_URL` env at startup. If set, it proxies tool calls to the MCP. If unset, it falls back to local CLI execution. **Local-only mode is preserved as a first-class path** — tests, dev loops, and air-gapped use rely on it.
- **Tool name aliasing during transition**: the skill's `mcp_tools` frontmatter SHOULD pin to canonical `ce_*` names per § 3.0.2. v1.0 server MUST accept every alias listed in § 3.0.2 and emit `X-CE-Deprecated: <canonical-name>` warnings on each. All aliases are removed in v2.
- **Failure UX**: when `CONTEXT_ENG_MCP_URL` is set but the MCP is unreachable, the skill MUST surface a `BRAIN_UNAVAILABLE`-shaped error to its caller. Skills MAY implement automatic local fallback; default behavior is to fail-closed so callers know they're getting stale data.
- **Test fixtures**: the reference implementation at `agent-skills/context-engineering/tests/fixtures/mock_mcp_server.py` provides deterministic responses for each tool. Skill authors building wrapper skills test against the mock first, then against staging deploy.
- **`ce_upload_corpus` UX in the skill**: when the skill's CLI produces a fresh local index, it MAY auto-upload to the configured MCP via `ce_upload_corpus`. This is opt-in via `--mcp-publish` flag; default is local-only.

A wrapper skill (e.g. "weekly architectural brief") can be built atop these tools without touching the MCP server — the catalog is stable across minor versions per §8.

## 12. Implementation notes (informative)

These are not part of the contract; they describe the v1 implementation.

- **Package naming**: npm `context-engineering-mcp-server` (TS/Node), PyPI `context_engineering_mcp` (Python). Repo path stays `agent-skills/context-engineering/`.
- **Hosting**: Vercel Functions (Node.js for the MCP server, Python scripts as build-time artifacts).
- **State**: `syrocolab/company-brain` GitHub repo. Per-corpus folder layout per `LIFECYCLE.md` and the schemas.
- **Read cache**: in-memory module-level cache (warm function reuse) → Vercel KV (corpus_id → commit_sha pointers, NOT content) → lazy per-corpus tarball fetch via GitHub archive API.
- **Write path**: Octokit, GitHub App installation token, single tree-then-commit per refresh, parallel blob creation capped at concurrency 10.
- **Locking**: lock-in-manifest, TTLs per § 5.1 (300s sync / 20 min async with 60s heartbeat), stale takeover on `expires_at < now`.
- **Embeddings format on disk**: NumPy `.npy` (vectors) + `paths.json` + `hashes.json` per corpus.
- **Long ops**: `ce_index_github_repo` >50s → punted to Vercel Cron via `async=true`.

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

Canonical names; v1.0 aliases per § 3.0.2.

| Tool | Use when |
|---|---|
| `ce_pack_context` | You want a markdown (or structured JSON) bundle of relevant files for a query, ready to feed an LLM. |
| `ce_find_relevant_files` | You want ranked file paths only (you'll fetch content yourself). |
| `ce_upload_corpus` | You've indexed something locally (or via a custom adapter) and want to make it queryable. |
| `ce_index_github_repo` | You want the server to clone + index a GitHub repo it can reach. |
| `ce_list_corpora` | You want to know what's available. Supports `limit`/`offset` pagination. |
| `ce_get_health` | You want to confirm the server is reachable and on a known version. |
| `ce_get_job_status` | You're polling an async job started via `ce_index_github_repo(async=true)`. |

## Appendix B — Common consumer flows

### Flow 1: First-time setup of a new code corpus

```
1. ce_index_github_repo(repo="owner/foo", branch="main", data_classification="internal")
   → returns corpus_id, commit_sha
2. ce_pack_context(query="...", corpus_id=...)
   → returns markdown
```

### Flow 2: Daily query against an existing corpus

```
1. ce_list_corpora({lifecycle_state: ["active"]})
   → choose corpus_id
2. ce_pack_context(query="...", corpus_id=..., budget=32000)
   → markdown
```

### Flow 3: Anabasis Skill workflow (weekly architectural brief)

```
[Monday 08:00 cron]
  → ce_index_github_repo(repo="syrocolab/efficientship-backend", branch="develop", async=false)
  → ce_list_corpora({source_type: "github_repo"}) — find corpus_id
  → ce_pack_context(query="recent architectural changes", corpus_id, budget=32000)
  → (different MCP) anthropic.summarize(packed_markdown)
  → (different MCP) slack.post(channel="#eng-weekly", content=summary)
```

### Flow 4: Local code with a custom adapter

```
1. (locally) run a custom adapter producing universal-format file entries + embeddings
2. ce_upload_corpus(source={...}, files=[...], embeddings=[...], data_classification="internal")
   → returns corpus_id
3. ce_pack_context(...) as usual
```

---

## Appendix C — Open questions deferred to v1.1

- Multi-part upload for `ce_upload_corpus` payloads >32MB (presigned URL flow already speced in § 3.3; v1.1 adds resume/abort semantics).
- Per-corpus ACL (replacing role-based default caps).
- Webhook-driven realtime client invalidation push (vs polling).
- Cost attribution: which tokens (embedding API + LLM context) belong to which caller? Telemetry covers it; billing is out of scope for v1.
- Cross-region replication of the brain repo (via GitHub mirror) for low-latency multi-region MCP.

---

## Appendix D — Recommended per-task budget (informative)

For agents composing CE tools across a single user task (one Claude Code turn, one cron-routine iteration, one Anabasis Skill invocation):

- **Tool calls per task**: aim for **≤5**. Most tasks resolve in 2–3 (`ce_list_corpora` → `ce_pack_context`, or `ce_find_relevant_files` → `ce_pack_context`).
- **Total CE-served tokens per task**: keep the cumulative cost in line with one `ce_pack_context` call's `budget` (default 32k per § 3.1) — avoid stacking multiple full-budget packs in a row. Code corpora typically settle below the default; doc corpora may legitimately need larger budgets for narrative continuity (raise `budget` rather than chain calls). Calibrate per corpus from telemetry once Appendix C cost-attribution lands.
- **First-call discipline**: prefer `ce_list_corpora` (small, cheap) over `ce_pack_context` with a guessed `corpus_id`. A `CORPUS_NOT_FOUND` error wastes a full RTT.
- **Suggestion-following**: when a response includes `next_tool_suggestions` (§3.0.1), the suggested tool is usually the right next call. Agents that follow suggestions should resolve common workflows in ≤2 calls.

These are guidelines for agent and skill authors, not enforced limits. The numeric budgets land once telemetry surfaces real per-corpus distributions; until then, treat the per-call `budget` as the unit of accounting and don't stack.
