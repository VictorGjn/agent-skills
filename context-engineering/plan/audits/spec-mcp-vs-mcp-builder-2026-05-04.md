# SPEC-mcp.md audit against `anthropics/skills@mcp-builder`

**Date**: 2026-05-04
**Auditor**: Claude (Opus 4.7) at Victor's request
**Skill version**: anthropics/skills@mcp-builder (47.7K installs, current head)
**Spec version under audit**: SPEC-mcp.md v1.0.0-rc2 (post-13-theme audit, dated 2026-05-01)
**Skill files consulted**: SKILL.md, reference/mcp_best_practices.md, reference/evaluation.md
**Audit basis**: skill prescriptions vs every normative section of SPEC-mcp.md

---

## TL;DR

SPEC-mcp.md is **substantially better-engineered than the skill prescribes** in error semantics, idempotency, lifecycle/locking, telemetry, and ETag/cache discipline — those reflect serious infra audit work and shouldn't be touched. But it **misses several skill best-practices that are cheap fixes today and load-bearing if CE ships alongside Sourcegraph + Augment + others** in CodeScaleBench-style multi-MCP harnesses.

**4 P1 changes recommended before v1.0 freeze, 4 P2, 3 P3.** None of the P1s are deep — they're naming/discipline fixes and a section reorganization in §7. Implementation already drafted in §"Action items" below.

---

## Conformance summary by SPEC section

| SPEC § | Topic | Skill verdict | Notes |
|---|---|---|---|
| 1, 1.5 | Goal / build vs buy | ✓ no-op | Out of skill scope |
| 2 | Conformance levels | ✓ aligned | Standard MCP conformance pattern |
| 3.0 | MCP basics | ⚠ outdated wording | "streamable-HTTP+SSE" → "Streamable HTTP" (skill: SSE deprecated) |
| 3.0.1 | next_tool_suggestions | ✓ exceeds skill | Useful UX, not in skill but doesn't violate |
| 3.1 `pack_context` | Headline tool | ⚠ 4 sub-issues | name prefix, format param, annotations, error layer |
| 3.2 `find_relevant_files` | Ranked paths | ⚠ same 3 issues | name prefix, annotations, error layer |
| 3.3 `upload_indexed_corpus` | Client-supplied index | ⚠ 3 issues | name prefix, annotations, error layer |
| 3.4 `index_github_repo` | Server-side indexing | ⚠ same | name prefix, annotations, error layer |
| 3.5 `list_corpora` | Discovery | ⚠ + missing pagination | also: name prefix, annotations |
| 3.6 `get_health` | Liveness | ⚠ name prefix | otherwise ✓ |
| 3.7 `get_job_status` | Async polling | ⚠ same | otherwise ✓ |
| 4 | State model | ✓ exceeds skill | Skill doesn't cover state-as-git |
| 5.1 | Refresh & locking | ✓ exceeds skill | Sophisticated lock TTL + heartbeat is beyond skill |
| 5.2 | Async ops | ✓ aligned | Standard pattern |
| 5.3 | Webhook & invalidation | ✓ exceeds skill | |
| 6.1 | Bearer tokens | ⚠ defer-OAuth | Skill says use OAuth 2.1 *now*, not v2 |
| 6.2 | GitHub App | ✓ aligned | Standard practice |
| 6.3 | Data classification gates | ✓ exceeds skill | |
| 7 | Error model | ⚠ conflated layers | Mixes protocol-level and tool-level errors |
| 7 | Error model — content | ✓ exceeds skill | Dual JSON-RPC+HTTP shapes with retryable+retry_after exceeds skill |
| 8 | Versioning | ✓ aligned | |
| 9 | Telemetry | ✓ exceeds skill | 11 event types vs skill's "log security errors" |
| 10 | Future tools | ✓ aligned | |
| 10b | lat.md interop | ⚠ naming | `lat.locate` uses dot-notation; skill prescribes underscore service-prefix |
| 11 | Migration path | ✓ aligned | |
| 12 | Implementation notes | ✓ aligned | |
| Appendix B | Common flows | ✓ exceeds skill | |
| Appendix D | Per-task budget | ✓ exceeds skill | Skill doesn't cover budget guidance |
| — | Eval suite | ❌ missing | Skill prescribes 10-question XML eval; SPEC has none |

---

## P1 findings (fix before v1.0 freeze)

### P1.1 Tool names need a service prefix

**Skill says** (`mcp_best_practices.md`):
> Include service prefix: Anticipate that your MCP server may be used alongside other MCP servers. Use `slack_send_message` instead of just `send_message`.

**SPEC has**: `pack_context`, `find_relevant_files`, `upload_indexed_corpus`, `index_github_repo`, `list_corpora`, `get_health`, `get_job_status`. **No service prefix.**

**Why this matters now**: CodeScaleBench mounts CE alongside Sourcegraph MCP and Augment Context Engine. If both servers expose `find_relevant_files` (likely), the agent can't disambiguate. The skill convention exists precisely for this case.

**Recommendation**: rename to `ce_pack_context`, `ce_find_relevant_files`, `ce_upload_corpus`, `ce_index_github_repo`, `ce_list_corpora`, `ce_get_health`, `ce_get_job_status`. The "Naming note" pattern already in §3.3 (`upload_indexed_corpus` / legacy `register_corpus`) sets precedent for graceful aliasing — keep current names as v1.0 aliases with `X-CE-Deprecated`.

**Effort**: rename + alias map. ~30 min in spec, ~2 hours in implementation.

---

### P1.2 Drop "+SSE" from transport language

**Skill says**:
> Avoid SSE (deprecated in favor of streamable HTTP).

**SPEC §3.0 says**: "stdio (Claude Code local) and streamable-HTTP+SSE (Anabasis remote, cloud routines, n8n)".

The MCP spec renamed the older "HTTP+SSE" transport to "Streamable HTTP" in 2025. Calling it "+SSE" today either looks dated to readers familiar with the spec, or implies a separate SSE channel that doesn't exist.

**Recommendation**: replace "streamable-HTTP+SSE" with "Streamable HTTP" everywhere in §3.0 and downstream references. Add one-line clarifier: "(formerly HTTP+SSE; renamed in MCP spec 2025)" if backwards-compat is a concern for older readers.

**Effort**: 5 min spec edit. Implementation should already be using the renamed transport.

---

### P1.3 Add per-tool annotations

**Skill says** (`mcp_best_practices.md`):
> Tool Annotations: provide annotations to help clients understand tool behavior:
> - readOnlyHint: tool does not modify its environment
> - destructiveHint: tool may perform destructive updates
> - idempotentHint: repeated calls with same args have no additional effect
> - openWorldHint: tool interacts with external entities

**SPEC has**: per-tool "Idempotency" prose blocks (§3.1 "idempotent given (corpus_commit_sha, all input fields)"; §3.3 "idempotent on (corpus_id, files[].contentHash for all files)") but **no protocol-level annotations**. Clients (including Claude Code) use these annotations for safety prompts, parallelization decisions, and read-only-mode filtering.

**Recommendation**: add an "Annotations" sub-block to each tool in §3:

| Tool | readOnly | destructive | idempotent | openWorld |
|---|---|---|---|---|
| `ce_pack_context` | true | false | true | false |
| `ce_find_relevant_files` | true | false | true | false |
| `ce_upload_corpus` | false | true | true | true |
| `ce_index_github_repo` | false | true | true | true |
| `ce_list_corpora` | true | false | true | false |
| `ce_get_health` | true | false | true | false |
| `ce_get_job_status` | true | false | true | false |

**Effort**: 10 min spec edit. Implementation: 1 line per tool (TS) or 1 decorator arg (Python).

---

### P1.4 Distinguish protocol-level vs tool-level errors in §7

**Skill says** (`mcp_best_practices.md`):
> Report tool errors within result objects (not protocol-level errors)

**SPEC §7 currently** maps every error code to a JSON-RPC numeric code, including tool-level validation errors (`INVALID_ARGUMENT` → -32602, `BUDGET_TOO_SMALL`, etc.). Per the MCP spec, these should be returned as `result.isError: true` so the agent can self-correct without the LLM thinking the protocol broke.

**Affected codes** that should move to `result.isError`:

| Currently | Should be |
|---|---|
| `INVALID_ARGUMENT` | `result.isError: true`, content explains constraint |
| `BUDGET_TOO_SMALL` | `result.isError: true` |
| `EMBEDDING_MISMATCH` | `result.isError: true` |
| `CORPUS_NOT_FOUND` | `result.isError: true` (it's a valid request, just empty hit) |
| `JOB_NOT_FOUND` | `result.isError: true` |
| `SOURCE_FORBIDDEN`, `SOURCE_NOT_FOUND`, `SOURCE_MISMATCH` | `result.isError: true` |

**Stays as JSON-RPC error** (genuine protocol failures):

| Code | Reason |
|---|---|
| `UNAUTHENTICATED` | client didn't authenticate at all |
| `PERMISSION_DENIED` | role-level denial, before tool dispatch |
| `RATE_LIMITED` | server-level cap |
| `BRAIN_UNAVAILABLE`, `BRAIN_RATE_LIMITED` | upstream infra |
| `INTERNAL` | server bug |
| `WEBHOOK_SECRET_MISMATCH` | operator misconfig |

**Recommendation**: rework §7 into two sub-sections — "7.1 Tool errors (in result)" and "7.2 Protocol errors (JSON-RPC)". HTTP-direct shape stays as-is.

**Effort**: 30 min spec edit, ~2 hours implementation if currently emits JSON-RPC for everything.

---

## P2 findings (should fix; not blocking)

### P2.1 OAuth 2.1 timeline

**Skill says**:
> Use secure OAuth 2.1 with certificates from recognized authorities. Validate access tokens before processing requests.

**SPEC §6.1**:
> v2 spec moves to OAuth 2.1 per Anthropic's MCP guidance.

**Tension**: Anthropic's *current* MCP guidance is OAuth 2.1 today. Punting it to v2 means CE ships with a less-secure auth path on day one. Bearer-with-hashed-KV is fine for single-tenant Syroco-internal use, but external integrations (Anabasis customers, n8n marketplace) will look at this and downgrade trust.

**Recommendation**: add OAuth 2.1 as a v1.0 optional auth method (alongside Bearer for backwards-compat), gate via env. Current Bearer stays as the easy on-ramp.

**Effort**: ~1 day implementation. Spec change minimal: change §6.1 to "Bearer + optional OAuth 2.1 per Anthropic's MCP guidance".

---

### P2.2 DNS rebinding protection

**Skill says** (`mcp_best_practices.md`):
> For streamable HTTP servers running locally:
> - Enable DNS rebinding protection
> - Validate the Origin header on all incoming connections
> - Bind to 127.0.0.1 rather than 0.0.0.0

**SPEC has**: nothing. §6 covers token auth but not transport-layer security for local deployments.

**Why it matters**: any developer running CE MCP locally over HTTP for testing exposes themselves to DNS rebinding attacks from any browser tab. The Vercel deploy doesn't have this issue (origin is fixed) but local dev does.

**Recommendation**: add §6.4 "Local HTTP server hardening": Origin header validation, 127.0.0.1 bind by default, opt-in 0.0.0.0 with explicit env flag.

**Effort**: 15 min spec edit, ~1 hour implementation.

---

### P2.3 Pagination on `list_corpora`

**Skill says** (`mcp_best_practices.md`):
> For tools that list resources: always respect the `limit` parameter. Implement pagination. Return pagination metadata (`has_more`, `next_offset`/`next_cursor`, `total_count`). Default to 20-50 items.

**SPEC §3.5**: `list_corpora` has filters (`lifecycle_state`, `data_classification_max`, `source_type`) but no `limit`/`offset`/`cursor`. Returns all matching corpora.

**Why it matters**: Syroco has ~30 corpora today; not a problem yet. But the brain repo design supports thousands (every Granola transcript, every Notion page, every Gmail thread becomes a corpus in the §1 "last-mile assembler" framing). Without pagination, `list_corpora` over a year of accumulated state would push 5+MB JSON.

**Recommendation**: add `limit` (default 50, max 200) and `offset` (default 0) inputs. Add `total_count`, `has_more`, `next_offset` to output. Keep filter behavior unchanged.

**Effort**: 20 min spec edit, ~1 hour implementation.

---

### P2.4 `pack_context` output should support a JSON format

**Skill says**:
> All tools that return data should support multiple formats:
> - JSON (machine-readable structured data) — `response_format="json"`
> - Markdown (human-readable formatted text) — `response_format="markdown"`, typically default

**SPEC §3.1**: returns `{markdown, tokens_used, ..., files: [...]}` — markdown is the only content shape. The `files` array is structured but the actual content is markdown-blob.

**Why it matters**: programmatic consumers (CodeScaleBench harness, Anabasis runtime synthesizer) re-parse the markdown to extract per-file content. A JSON output mode (`response_format: "json"` returning per-file `{path, depth, content, tokens}` objects) would skip the parse-roundtrip and reduce token cost for chained-tool flows.

**Recommendation**: add optional `response_format: enum["markdown", "json", "structured"]` (default markdown). `structured` mode returns the full file array with content, no markdown.

**Effort**: 30 min spec edit, ~3 hours implementation (existing internal data structure already has this; just need an output formatter).

---

## P3 findings (nice-to-have)

### P3.1 Server name doesn't follow skill convention

**Skill says**:
> Python: {service}_mcp (e.g., slack_mcp). Node/TS: {service}-mcp-server (e.g., slack-mcp-server).

**SPEC**: refers to "CE MCP server" generically. The Python implementation is `server-stub/` and `scripts/mcp_server.py`. Neither follows the convention.

**Recommendation**: name the deployed package `context-engineering-mcp-server` (TS preferred per skill) or `context_engineering_mcp` (Python). Doc-only — implementation can keep current paths.

---

### P3.2 No 10-question eval suite

**Skill says** (Phase 4):
> Create 10 evaluation questions. Each must be: independent, read-only, complex, realistic, verifiable (single answer), stable.
> The measure of quality of an MCP server is NOT how well or comprehensively the server implements tools, but how well these implementations enable LLMs with no other context to answer realistic and difficult questions.

**SPEC has**: zero CE-native evals. The CodeScaleBench bench plan (`plan/codescalebench-bench-plan.md`) covers external benchmarking but doesn't produce a portable XML eval that ships with the spec.

**Recommendation**: add §14 "Evaluation suite" — 10 questions answerable only via CE tools, in `evals/ce-mcp-v1.xml`. Stable answers from a frozen test corpus (e.g. a pinned commit of `agent-skills` itself). Run via the skill's `evaluation.py`.

**Effort**: ~half a day to write 10 good questions on a frozen corpus + verify answers. Worth it for distribution: this is what other agent harnesses load to evaluate "should I include this MCP?"

---

### P3.3 lat.md tools use `lat.foo` dot-notation

**Skill convention**: snake_case with service prefix → `lat_locate`, `lat_section`, etc.

**SPEC §10b**: `lat.locate`, `lat.section`, `lat.refs`, `lat.search`, `lat.expand` — explicitly justified as "lat.* for lat.md interop; wiki.* for CE-native; deployed-MCP non-wiki tools stay snake_case. ... convention reconciliation deferred to v2."

**Recommendation**: keep dot-notation **only if** the lat.md upstream uses it; otherwise, normalize to `ce_lat_locate` etc. for skill conformance. Worth a one-line check against lat.md's own convention before changing.

---

## What SPEC-mcp.md does BETTER than the skill prescribes

Credit where due — these are deliberate over-engineering and should NOT be cut to "simplify":

1. **Error model is deeper than skill** — dual JSON-RPC + HTTP shape, `retryable` + `retry_after_seconds` + `Retry-After` header, 18 mapped codes. Skill says "use JSON-RPC errors and helpful messages."
2. **Per-tool idempotency contracts** — SPEC explicitly says what makes a request a no-op vs a write. Skill mentions `idempotentHint` annotation only.
3. **Lifecycle states + locking with TTL + heartbeats + stale takeover** — sophisticated multi-writer story not covered by skill.
4. **ETag + Cache-Control discipline** including "NEVER `public`" for confidential corpora — skill says nothing about cache-leak risks.
5. **11 telemetry event types** (tool.call, corpus.refreshed, lock.held_duration, brain.size, embed_provider.call, github_app.token_age, etc.) — skill says "log security-relevant errors."
6. **Data classification gates** with role-based caps — skill mentions auth but not classification.
7. **Per-task budget guidance (Appendix D)** — skill has nothing equivalent for agent-side composition.
8. **`next_tool_suggestions`** (§3.0.1) — useful agent UX nudge, not in skill but worth keeping.

---

## Action items (ordered by leverage)

| # | P | Item | Spec edit | Implementation |
|---|---|------|-----------|----------------|
| 1 | P1 | Service-prefix all tools (`ce_*`); v1.0 alias the bare names | 30 min | 2 hours |
| 2 | P1 | Drop "+SSE", use "Streamable HTTP" | 5 min | — |
| 3 | P1 | Add `readOnly`/`destructive`/`idempotent`/`openWorld` annotations table | 10 min | 7 lines |
| 4 | P1 | Split §7 into protocol vs tool errors | 30 min | ~2 hours |
| 5 | P2 | OAuth 2.1 as v1.0 optional auth | 5 min | 1 day |
| 6 | P2 | DNS rebinding protection §6.4 | 15 min | 1 hour |
| 7 | P2 | Pagination on `ce_list_corpora` | 20 min | 1 hour |
| 8 | P2 | `response_format: "structured"` on `ce_pack_context` | 30 min | 3 hours |
| 9 | P3 | Server name convention | 5 min | — |
| 10 | P3 | 10-question XML eval suite | — | half day |
| 11 | P3 | Reconcile `lat.*` convention | 10 min | — |

**Cluster 1 — spec-only (1.5 hours)**: items 1-4 + 5+6+7+9+11 spec edits. Closes all P1 + P2 + most P3 in spec without writing code.

**Cluster 2 — implementation (~2 dev days)**: items 1, 4, 5, 6, 7, 8 implementation. Closes P1 + P2 in code. Skip if a v1.0 freeze ships before the production MCP rewrite.

**Cluster 3 — eval suite (half day)**: item 10. Independent of cluster 2; can ship at any time.

---

## Recommendation

If v1.0 freezes soon (post-YC), do **Cluster 1 now** as a single spec PR — it's an hour of editing and closes 9 of 11 findings at the contract layer. Implementation can lag.

If the production MCP rewrite happens before v1.0 freeze, do **Cluster 1 + Cluster 2** together — the rewrite is the natural time to add annotations, restructure errors, and rename tools.

**Cluster 3 is independent** and worth doing whenever — it's the artifact that makes CE MCP credible to other agent harnesses without them running our CodeScaleBench bench. 10 questions + frozen corpus = a 1-page distribution doc.
