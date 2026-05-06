# Phase B — async indexing + scix-pattern spec additions

**Status:** drafted 2026-05-06 after Phase A (PR #50) lands. Two intertwined threads:

1. **Phase B (async indexing)** — Vercel KV + Cron worker to unblock repos that exceed `maxDuration=300s`. Mechanical work; bounded scope.
2. **Spec additions inspired by scix-agent's MCP design** — six load-bearing patterns CE today is missing. Some are 1-day landings on top of Phase A/B; others are v1.2 work but worth specifying now so we don't lock the wire shape.

This plan replaces the lean Phase B sketch in `ce-v1.1-bench-readiness.md` with a specific schema + the scix-derived API enrichments.

---

## Part 1: scix-agent patterns CE should adopt

Source: scix-agent has a stateful, typed, provenance-aware MCP server. Six patterns:

### Pattern 1 — Stateful MCP (session-scoped focused set)

scix keeps a `SessionState` keyed by `session_id`. Every `get_paper(bibcode)` call goes through `track_focused()` (FIFO, cap 500). Downstream tools (`find_gaps`, `temporal_evolution`, `synthesize_findings`, etc.) share a `_session_fallthrough_bibcodes()` resolver that defaults to "papers I just looked at" when explicit bibcodes aren't passed.

> So `find_gaps()` with no args asks "given everything I've inspected this session, what communities haven't I touched?" This is **server-side state across MCP calls** — not client-thread orchestration.

**CE today:** stateless. `pack(query)` doesn't know what was packed five minutes ago. Every call is a cold lookup.

**The CE shape:** `corpora_just_packed`, `paths_just_packed`, `queries_so_far`. After a `pack({query: "auth bug"})` returns 12 paths, an immediate `pack({query: "test setup", focus: "session"})` defaults to "expand around the same files" instead of starting from scratch. After `find_relevant_files({query})` an agent doing iterative debugging gets coherent context without re-paying retrieval cost.

**Storage:** Vercel KV (Phase B introduces it anyway). Key `session:<id>` → `{focused_paths: [...], queries: [...], last_seen_at}`. TTL 1 hour (sliding).

**Wire:** add optional `session_id` to all read-tool inputs. New `mode: "auto"` (or new flag) means "fall through to session." Server-side resolver picks the focused set.

**Effort:** ~1 day after KV is introduced. Bench impact: agents calling pack/find iteratively in a CSB task get coherent context — closer to how Sourcegraph MCP behaves naturally because it has session via stdio.

### Pattern 2 — Typed edges (trained, not imported)

scix runs SciBERT fine-tuned on SciCite (`background` / `method` / `result_comparison`) with an Anthropic LLM fallback. Citation contexts are regex-extracted from body text. Stored in `citation_contexts.intent` and consumed by every provenance tool.

**CE today:** `code_graph` produces import edges (untyped — just "A imports B"). The PR #4 Graphify integration adds richer types but they're imported from Graphify's classification (`call_graph`, `inheritance`, `cross_language`, `doc_to_code`), not learned.

**The CE shape:** edge types CE *could* learn from code:
- `imports` (current default)
- `calls` (function calls A → B)
- `extends` / `implements` (class hierarchy)
- `references` (string mentions, comments)
- `tests` (test file → tested file via naming convention + import)
- `documents` (doc file → code file)
- `configures` (config → consumer)

**Effort:** Learned edge typing is v1.2 — too expensive to train and validate now. v1.1 stops at *typed but not learned* — adopt Graphify's labels as authoritative when present, fall back to "imports" when absent. Deliver in cross-corpus pack PR (`p4-cross-corpus-pack.md`).

**Bench impact:** none in v1.1. Worth specifying now so the wire shape (Pattern 3) carries the type.

### Pattern 3 — Weight tables + chain-shaped returns

scix `claim_blame.py` defines `INTENT_WEIGHTS = {result_comparison: 1.0, method: 0.6, background: 0.3}` and ranks origin candidates by `0.5*intent + 0.3*chronology + 0.2*semantic`. Returns a `Hop[]` where each hop carries `intent`, `intent_weight`, `context_snippet`, `section_name`, plus `retraction_warnings` joined from `papers.correction_events`.

> CE returns "here's the chunk and a score" — scix returns a typed walk with edge labels at every step.

**CE today:** `pack` and `find_relevant_files` return flat lists `[{path, depth, relevance, tokens, ...}]`. No chain. No edge attribution. The agent can't trace "why is `auth.py` here? because it's imported by `login.test.ts` which matches the query."

**The CE shape:** add `trace` to find/pack responses (already partially there as `why`). Extend to a `Hop[]`:

```json
{
  "files": [
    {"path": "auth.py", "relevance": 0.83, ...},
    ...
  ],
  "trace": {
    "hops": [
      {"from": "<query>", "to": "login.test.ts", "edge": "semantic", "weight": 0.91},
      {"from": "login.test.ts", "to": "auth.py", "edge": "imports", "weight": 1.0},
      {"from": "auth.py", "to": "session.py", "edge": "calls", "weight": 0.6}
    ],
    "weights": {"intent": 0.4, "graph": 0.3, "semantic": 0.3}
  }
}
```

When `trace.show: true` (off by default for budget), the agent gets the typed walk. Otherwise the bare `files[]` shape is unchanged.

**Effort:** ~2 days. Engine already produces the data internally (the `_engine` field stripped before return); we'd thread it through and label edges per Pattern 2.

**Bench impact:** indirectly via better task success. Agent that knows why a file was retrieved makes better decisions. Harder to measure on file-recall metrics; will show in H4 reward.

### Pattern 4 — ZFC ("zero-frame-LLM-call") scaffolding

scix `synthesize_findings` bins working-set bibcodes into named sections using modal intent + community-share thresholds (`>=15% → background`, `>=5% → methods`). The server emits structure; the agent writes the prose. Inverse of standard RAG: instead of stuffing chunks into the prompt and hoping, the tool returns an outline the agent fills.

**CE today:** packs chunks into a budget and returns markdown. The agent has to figure out structure from depth bands.

**The CE shape:** new tool `ce_outline` (or `ce_pack_context` with `output_mode: "outline"`):

```json
{
  "outline": {
    "task_inferred": "fix",
    "sections": [
      {"name": "What's broken", "files": ["src/auth.py"], "rationale": "modified-recently + matches 'auth bug'"},
      {"name": "How it's tested", "files": ["test/test_auth.py"], "rationale": "test naming + imports auth.py"},
      {"name": "What it depends on", "files": ["src/session.py"], "rationale": "imported by auth.py, calls verified"},
      {"name": "Background", "files": ["docs/AUTH.md"], "rationale": "knowledge_type=ground_truth"}
    ],
    "skipped_for_budget": [...]
  }
}
```

Task presets (from `code_graph.py`'s existing task taxonomy): `fix` / `review` / `explain` / `build` / `document` / `research` — each maps to a different section template.

**Effort:** ~3 days. Has to land AFTER Pattern 3 (the trace) because section rationales come from edge labels.

**Bench impact:** scix's scaffolding pattern is the single highest-leverage change for agent task success. Agents using outline-mode get a coherent map of the corpus, not a 32K-token chunk dump. Worth running CSB H4 (Haiku reward) with outline-mode vs chunk-mode as a separate cell.

### Pattern 5 — Degrading-lane resolver (bulkhead + canary)

scix `src/scix/jit/router.py` routes per-document entity resolution across `live LLM (Haiku, 400ms JITBulkhead budget) / local SciBERT-NER / static-core sentinel`, with **5% deliberately routed to local to keep regression signal**. AST-lint enforces single-entry-point on the static-core writer.

**CE today:** soft fallback (semantic → keyword on failure) but no formal bulkhead, no canary. The fallback is silent in practice — caller has to inspect `reason` strings.

**The CE shape:** formal three-lane resolver for embeddings:
1. **Live**: Mistral codestral-embed (current). 30s timeout per batch.
2. **Local**: BGE-small (already shipped local-only via `MISTRAL_API_KEY` not set). For prod we'd need to bundle a smaller model — out of scope for v1.1.
3. **Static**: deterministic hash-based "embedding" that's basically a keyword bag-of-tokens projected to 1536-d. Serves as the sentinel — never throws, never hits the network.

5% canary: every 20th request (or N% configurable via `CE_CANARY_RATE`) goes through local even when live is healthy. Tracks regression (do live vs local recall@5 diverge?).

**Effort:** ~3 days for the resolver framework + canary tracking. Local lane is v1.2 (need bundled model). Static lane lands in v1.1 as the safety net under live.

**Bench impact:** none in steady state. When Mistral degrades, today CE silently keyword-falls-back. Tomorrow it static-falls-back with a `lane: "static"` field in the response so the agent knows. Real value is operational, not benchmark.

### Pattern 6 — Coverage transparency

scix every typed-edge tool returns a `coverage` field so callers can tell partial-precision results from full-corpus results.

> CE silently treats absence as absence.

**This is the highest-priority spec change.** Right now the bench harness can't tell whether a 0.3 recall came from "we found 30% of the right files" or "we only got embeddings for 30% of the corpus and found everything we could."

**The CE shape:** every read-tool response carries `coverage`:

```json
{
  "files": [...],
  "coverage": {
    "corpus_size_files": 300,
    "corpus_size_indexed": 300,
    "ranked_with": "semantic",
    "ranked_with_lane": "live",
    "files_eligible_for_mode": 300,    // had embeddings (semantic) or any tokens (keyword)
    "files_skipped_unembedded": 0,
    "fallback_to_keyword": false,
    "trace_id": "<request-id>"
  }
}
```

If `mode: semantic` was requested, `corpus.embedded_count = 200`, and `corpus.file_count = 300`, then the response shows:
- `files_eligible_for_mode: 200`
- `files_skipped_unembedded: 100`

Bench harness adjusts:
- `eligible_recall = files_found_correctly / min(top_k, files_eligible_for_mode)`
- Today's `file_recall` is comparable across configs only if `files_eligible_for_mode` is ~equal.

**Effort:** ~1 day. Mostly threading data the engine already has into the response shape.

**Bench impact:** **PRE-REQUISITE for v1 final numbers.** Without this, H1's +36pp lift on flipt could be exaggerated by partial-coverage configs. Land before the full 70-task sweep.

---

## Part 2: Phase B — async indexing infrastructure

### Why

Big bench repos (django, kubernetes, aspnetcore, vscode at full size) exceed `maxDuration=300s` in sync mode. Even with the indexer's `MAX_FILES_TO_FETCH=300` cap, content fetches at 100-200ms each + codestral embeds (~2s/batch) push past 300s on dense corpora. ~10 of the 70 IR-scorable bench tasks won't fit.

`ce_index_github_repo({async: true})` returns `NOT_IMPLEMENTED` today. Land it.

### Architecture

```
caller                                   server                              vercel infra
------                                   ------                              ------------
ce_index_github_repo                  ┌─ enqueue job                       ┌─ KV write
  args: {repo, branch, async: true}   │  job_id = uuid                      │  job:<id> = {…}
  →  {job_id, status: queued}         │  KV.SET job:<id> {queued, 0/?}      │  queue:pending RPUSH
                                       └─ KV.RPUSH queue:pending <id>

        every 1 minute:
        ┌─ vercel cron fires /api/cron/index-worker
        │
        │  ┌─ KV.LPOP queue:pending → job_id
        │  │
        │  │  ┌─ load job state from KV
        │  │  │   ↓
        │  │  │  fetch tree if cursor=0
        │  │  │   ↓
        │  │  │  fetch next ~50 files (budget-bounded inside the 60s cron tick)
        │  │  │   ↓
        │  │  │  embed those 50 (~3s)
        │  │  │   ↓
        │  │  │  merge into partial corpus body in KV
        │  │  │   ↓
        │  │  │  if done: write to Blob, KV.SET job:<id> {complete}
        │  │  │  else:    save cursor, KV.RPUSH queue:pending <id>  ← re-queue
        │  │  └─
        │  └─

ce_get_job_status({job_id})            ── KV.GET job:<id>
  → {status, files_indexed, files_total, ...}
```

### Components

**`_lib/storage/kv.py` — Vercel KV (Redis) client (stdlib HTTP)**

Same pattern as `blob.py`: REST API, Bearer auth via `KV_REST_API_TOKEN` + `KV_REST_API_URL` (auto-injected when you create a KV store on Vercel). Operations: `get`, `set` (with optional ex/nx), `del`, `lpush`/`rpush`/`lpop`/`brpop`, `expire`. Mock layer for tests.

**`_lib/jobs.py` — async job lifecycle**

```python
def enqueue(kind: "index_github_repo", args: dict, owner: str) -> str:
    """Returns job_id; pushes to queue:pending."""

def claim_next() -> Job | None:
    """Worker calls this. Atomic LPOP from queue:pending. Returns None if empty."""

def update_progress(job_id: str, *, cursor: int, files_indexed: int,
                    partial_body: bytes | None) -> None: ...

def complete(job_id: str, *, commit_sha: str, file_count: int, embedded_count: int) -> None: ...

def fail(job_id: str, *, code: str, message: str, retry: bool = False) -> None: ...

def status(job_id: str) -> JobStatus | None: ...
```

**`api/cron/index_worker.py` — Vercel Cron handler**

```python
def handler(request):
    """Vercel Cron fires every 1 min. Dequeue 1 job, advance, re-queue if needed."""
    job = jobs.claim_next()
    if not job:
        return {"status": "idle"}
    try:
        if job.kind == "index_github_repo":
            advance_index_github_repo(job)
    except Exception as e:
        jobs.fail(job.id, code="INTERNAL", message=str(e), retry=True)
    return {"status": "advanced", "job_id": job.id}
```

`advance_index_github_repo(job)` is a refactor of the canonical indexer that takes `job.cursor` (starting file index) and a wall-time budget (~50s; cron tick is bounded by maxDuration), returns `{next_cursor, partial_files, partial_embeddings, done: bool}`. If `done: true`, build the corpus body and write to Blob via `corpus_store.write_corpus()`. Else save partial state to KV (under `job:<id>:cursor`) and `RPUSH` job_id back to `queue:pending`.

**`scripts/index_github_repo.py` — chunkable signature**

Add `index_github_repo_chunk(owner, repo, branch, token, *, start_index, max_files, time_budget_s) -> ChunkResult`. The existing `index_github_repo()` keeps its current shape and becomes a thin wrapper that loops `index_github_repo_chunk` until done — for the local CLI + sync `async=false` path.

**`vercel.json` — Cron schedule**

```json
{
  "crons": [
    {"path": "/api/cron/index-worker", "schedule": "* * * * *"}
  ]
}
```

1-minute interval (Pro tier minimum). For 70 corpora at 30 files/min average, the full bench setup completes in ~10 hours unattended.

**Locks (cross-instance, KV-backed)**

The intra-instance filesystem lock from Phase A doesn't protect against two cron workers picking up the same job (LPOP is atomic, so this shouldn't happen — but if a worker times out and the queue replays, lock fights matter).

```
KV.SET lock:corpus:<id> <worker_id> NX EX 90   # acquire (90s TTL)
... do work ...
KV.DEL lock:corpus:<id>                          # release
```

Concurrent uploads/indexes for the same `corpus_id` get `CORPUS_LOCKED` (retryable) — same wire as today, just KV-backed instead of file-backed.

### Phasing

**Phase B.1 — KV client + jobs primitive (~2d)**
- `_lib/storage/kv.py` + tests with mock HTTP
- `_lib/jobs.py` API surface, in-memory mock for tests
- KV-backed locks replace filesystem locks in `corpus_store.write_corpus`

**Phase B.2 — Chunkable indexer + cron worker (~2d)**
- `scripts/index_github_repo.py` refactor to `index_github_repo_chunk` (+ wrapper)
- `api/cron/index_worker.py` handler
- `vercel.json` cron schedule

**Phase B.3 — async=true wire path (~1d)**
- `tools/index_github_repo.py`: when `async=true`, `jobs.enqueue` and return `{job_id, status: "queued"}`
- `tools/get_job_status.py`: read from KV (was in-memory)

**Phase B.4 — Coverage transparency (Pattern 6 from Part 1) (~1d)**
- Thread `coverage` block into find/pack response shape
- `corpus_store` already has `embedded_count`, `file_count`; add `_coverage()` helper

**Phase B.5 — Smoke (~0.5d)**
- Enqueue async indexing for kubernetes/kubernetes
- Poll `ce_get_job_status` until complete (~30 min worst case)
- Confirm corpus is queryable, `coverage` field reports correctly

Total: ~6.5 working days. Larger than the original Phase B sketch because Pattern 6 (coverage) lands here too — it depends on the same instrumentation paths.

### Out-of-scope, sequenced for v1.2

- **Pattern 1 (sessions)**: needs KV (Phase B.1 has it), but session resolver is its own concern. Land separately as v1.2.
- **Pattern 2 + 3 (typed edges + chain returns)**: depends on `p4-cross-corpus-pack.md`. v1.2.
- **Pattern 4 (ZFC outline)**: depends on Pattern 3. v1.2.
- **Pattern 5 (degrading lane)**: static lane lands in v1.1 (safety net), local lane is v1.2 (bundled model).

---

## Risk register

| Risk | Mitigation |
|---|---|
| Vercel KV cost | Free tier 30K commands/month. 70 jobs × ~5 ticks × ~3 R/W = ~1K. Comfortable. |
| Cron tick bound by maxDuration=300s | Each tick processes ~50 files. Big-repo total time = `total_files / 50` minutes. Kubernetes (~300 files capped, but we'd raise the cap with chunking) → 6+ ticks → 6+ minutes. |
| Worker crashes mid-batch | Atomic update: write partial state to KV BEFORE writing partial body to Blob. On resume, reconcile. |
| Two workers pick same job | LPOP is atomic; can't happen on a healthy Redis. KV-backed lock as belt + suspenders. |
| Embedding rate limits | Mistral codestral-embed has rate limits we haven't measured. Add per-batch retry with jitter (already in `embed.py`'s `_post`). |
| Coverage field changes break clients | New optional field; existing clients ignore it. Bench drivers add support. |

---

## Decisions to make explicit

1. **KV provider**: Vercel KV (Upstash Redis) is the obvious pick — same project, auto-injected env. Alternatives (Upstash directly, Cloudflare KV, custom Redis) add ops cost without a clear win at this scale.

2. **Coverage field shape**: I propose the structure above. Bench harness compatibility is more important than minimal — over-specifying is cheap to ignore later, under-specifying is expensive.

3. **Session TTL**: 1 hour sliding. Long enough for an agent's iterative debugging session; short enough that stale sessions don't accumulate.

4. **Outline tool name**: `ce_pack_context({output_mode: "outline"})` vs new `ce_outline_for_task` tool. The first is one fewer tool in `tools/list`; the second is more discoverable. Lean toward the first — keep the surface tight.

5. **Static embedding lane**: deterministic hash bag-of-tokens projection. NOT a real embedding — its sole job is to never throw + give a consistent result so the bench harness gets a baseline floor. Document loudly to prevent confusion.

---

## Tracking

- This plan: `context-engineering/plan/ce-v1.1-phase-b-async-and-scix-patterns.md`
- Parent: `plan/ce-v1.1-bench-readiness.md`
- Bench plan: `plan/codescalebench-bench-plan.md`
- Cross-corpus pack (where typed edges + chain returns will land): `plan/p4-cross-corpus-pack.md`
- Phase A PR: VictorGjn/agent-skills#50
