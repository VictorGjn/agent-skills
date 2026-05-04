# Production MCP Shipping Plan

**Goal**: Ship a production MCP server (replacing the YC-demo stub) that is testable through CodeScaleBench and competitive with Sourcegraph MCP.

**Status**: armed 2026-05-04 after PR #38 (rc3 spec) merged. 8 phases, each = one PR, each with audit + self-review + Codex monitoring before next phase.

---

## Phases

### Phase 1 — Spec rc4 (apply plan-corpus review findings)

14 findings from the parallel agent review of all `plan/` content. P1 = correctness gaps; P2 = missing normative content; P3 = positioning.

**Key edits**:
- Multi-corpus `corpus_commit_sha` shape (object when `corpus_ids[]`, single string otherwise)
- `corpus_ids[]` array on `ce_pack_context` + `ce_find_relevant_files`
- `wiki.*` / `lat.*` MUST return `corpus_commit_sha`
- `ce_wiki_ask` `scope?` parameter contract
- Freshness policy contract (stored `last_verified_at`, computed `freshness_score`, per-source half-life)
- Schema evolution split (wiki rebuild vs events forward-migrate)
- Async-write invariant on consolidation
- Telemetry events: `audit.flagged`, `freshness.expired`, `audit.broken_refs`
- Decision-continuity fields: `supersedes` / `superseded_by` / `valid_until`
- Error codes: `EMBEDDING_PROVIDER_MISMATCH`, `CORPUS_PREFIX_COLLISION`
- `lat.*` normative bits (lat.locate upstream conformance, exit-code contract)
- § 1.5 left-flank competitive table (codebase-memory-mcp, mache, GitNexus, Graphify)
- `next_tool_suggestions` semantic-routing for `ce_wiki_impact_of`
- `ce_wiki_impact_of` `relation_kinds` defaults

**Cost**: $0. Spec-only.

### Phase 2 — Server foundation

Stack choice: **Python + MCP SDK** (reuses existing CE engine; mcp-builder skill explicitly supports Python). FastMCP for stdio + Streamable HTTP.

**Deliverables**:
- `server-prod/` directory at repo root (parallel to `server-stub/`)
- `pyproject.toml` declaring `context_engineering_mcp` package
- Streamable HTTP transport + stdio transport switch via env
- Bearer auth middleware reading hashed token map per § 6.1
- Tool registration boilerplate with annotations (`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`) per § 3.0.3
- `ce_get_health` skeleton returning v1.0.0 (not stub)
- Local dev runs both transports
- Test fixtures (mock corpus)

**Cost**: $0. Build-only.

### Phase 3 — Read tools

Wire the 4 read tools against the existing CE pack engine (`scripts/pack_context_lib.py` etc.).

**Deliverables**:
- `ce_pack_context` with `response_format: markdown|structured|both`
- `ce_find_relevant_files` with `top_k`
- `ce_list_corpora` with `limit`/`offset`/`brain_head_sha` echo
- `ce_get_health` with `auth_methods_supported`
- ETag + conditional `If-None-Match` returning 304
- Error envelope: tool errors via `result.isError`, protocol errors via JSON-RPC

**Cost**: $0. Build-only.

### Phase 4 — State backend + write tools

Minimal state for v1: local filesystem (or Vercel KV blobs) — defer GitHub-as-storage (`syrocolab/company-brain`) to v1.1 since brain repo doesn't exist yet.

**Deliverables**:
- `ce_upload_corpus` (inline JSON; presigned deferred to v1.1)
- `ce_index_github_repo` (sync only; async via Cron deferred to v1.1)
- `ce_get_job_status` (returns `complete` immediately for sync ops)
- Manifest storage (per-corpus folder)
- Lock primitive (filesystem lock with TTL)

**Cost**: $0. Build-only.

### Phase 5 — Deploy to Vercel

Replace the stub at `ce-mcp-stub.vercel.app` (or stand up new `ce-mcp.vercel.app` and 301 the stub).

**Deliverables**:
- Vercel project config
- Env vars: `CE_MCP_BOOTSTRAP_TOKEN`, `MISTRAL_API_KEY`, etc.
- Smoke test: `curl /api/health` returns `version: 1.0.0` (not stub)
- All 7 tools callable end-to-end
- DNS: confirm production URL pinned for CSB harness

**Cost**: $0. Deploy-only.

### Phase 6 — CodeScaleBench adapter

Per `plan/codescalebench-bench-plan.md`, add `BASELINE_MCP_TYPE=context-engineering` to CSB's existing config switch.

**Deliverables**:
- `BASELINE_MCP_TYPE=context-engineering` env handling in `agents/mcp_agents.py`
- `.mcp.json` template pointing at production CE MCP
- Prompt injection telling agent to use `ce_pack_context`
- Pre-task index build (cache by repo content hash)
- Local Docker run on 1 task to verify

**Cost**: $0. Build-only.

### Phase 7 — Bench run

**Phase 7a — smoke (~$18 Haiku)**: 20 single-repo SDLC tasks × {C0 baseline, C4 ce-shipping, C5 sourcegraph} at 100K budget. Pre-selected from published snapshot for reward variance ≥0.2.

**Phase 7b — IR-only sweep (~$3 codestral)**: 151 single-repo SDLC tasks × {C1 keyword, C2 codestral, C3 codestral+MMR, C4 +graph} × {32K, 100K, 180K} budgets. No Haiku.

**Deliverables**:
- Per-work-type Pareto plots (file recall vs token budget)
- Smoke reward delta (C4 vs C0, C4 vs C5)
- `docs/benchmarks-v1.md` writeup

**Cost**: ~$21 total.

### Phase 8 — Iterate

Decide based on results:
- **CE >= Sourcegraph on reward at lower token cost** → ship; rewrite SKILL.md "different jobs, complementary" to claim parity
- **CE = Sourcegraph reward** → ship; emphasize token efficiency
- **CE < Sourcegraph** → diagnose (retrieval? packing? ranking?); decide whether iterate or pivot

---

## Cross-phase guardrails

- **One PR per phase**. Self-review with three agents (consistency, editorial/code, implementer-burden) before push.
- **Codex review**: trigger `@codex review` on PR open; address findings before next phase.
- **Memory**: `Verify git push by remote ref` after every push; `Check open PRs first` before opening; `Branch off main, not feature branch`.
- **Force-push**: only with `--force-with-lease`; harness blocks may require manual user push.
- **Single Max20 token budget**: Phase 7 capped at ~$25; defer additional bench runs if budget exhausts.
- **Cross-repo deferred**: per memory `feedback_ce_cross_repo_status.md`, single-repo SDLC is the v1 bench target. Multi-repo Org tasks need Phase 4.5 (cross-corpus pack) first.

---

## Open architecture decisions (resolve in-phase)

- **Brain repo wiring**: defer to v1.1 or include in Phase 4? Recommend defer — local filesystem state is enough for v1 ship + bench.
- **OAuth 2.1**: spec rc3 says optional in v1.0. Defer implementation to v1.1 unless CSB harness requires it.
- **Embeddings provider in Vercel**: codestral via Mistral API only (BGE local-only, can't run in Vercel functions). Confirmed in `feedback_ce_cross_repo_status.md`.
- **Index storage during indexing**: Vercel KV (small) vs Vercel Blob (larger) vs filesystem (ephemeral). Decide in Phase 4.
- **Server hosting**: Vercel Functions (rc3 mandates) — keep. Alternative: Cloudflare Workers if Vercel's Python support is too restrictive. Decide in Phase 2.

---

## Audit doc trail

- `plan/audits/spec-mcp-vs-mcp-builder-2026-05-04.md` — Phase 1 source of P1/P2/P3 findings
- `plan/audits/<future>` — one per phase for self-review record
- `plan/codescalebench-bench-plan.md` — Phase 7 bench plan (already written)
