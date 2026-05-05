# CE v1.1 — bench-readiness plan

**Status:** drafted 2026-05-06 after Phase 5.5 (PR #49) lands. Inputs: flipt smoke results (H1 PASS Δ=+0.362, H2-IR PASS, n=4) + sharp edges discovered while smoking.

**Goal:** Get the full 70-task IR-only sweep (`run_ir_bench × {C1, C2, C3, C4}`) running unattended end-to-end on the production CE MCP, producing a real `docs/benchmarks-v1.md` with v1 verdicts. No ad-hoc per-repo babysitting.

**Non-goals for v1.1:** Phase 1 Haiku run (H4 verdict), full bench plan v2 (cross-repo, 264 tasks), real-time eval dashboard. Those are v1.2.

---

## What we know after Phase 5.5

**Wins (kept):**
- Semantic mode wired end-to-end. flipt smoke: keyword 0.000 → semantic 0.362 file_recall on n=4. The lift is real and big — not a measurement artifact.
- MMR demonstrably changes top-K composition (Jaccard < 0.7 on 4/4 tasks). Recall happens to match C2 in this smoke; with broader n we expect recall delta too.
- 70 IR-scorable tasks converted across ~40 unique repos. CSB → spec/ground_truth pipeline works.
- Production deploy stable. 137 server-prod tests + 54 csb-eval = 191 green.

**Bottlenecks holding back the full run:**
1. **Vercel /tmp is per-instance ephemeral.** Bench batch can't run unattended across cold starts — corpora vanish 5+ min after the indexing call. Today's workaround: run bench within a warm-instance window, accept partial coverage.
2. **60s→300s maxDuration helped, but big repos (django, kubernetes, aspnetcore, vscode) still exceed it.** Indexer's 300-file cap + content-fetch latency + embedding cost combine to push past 300s on dense repos. Today's workaround: ce_upload_corpus from a local clone (skipped tonight because no local MISTRAL_API_KEY).
3. **No async path.** `ce_index_github_repo` with `async=true` returns NOT_IMPLEMENTED. The bench plan's stated v1.1 work.
4. **Indexer re-fetches everything every call.** No incremental — if a repo's main moves by 1 file, we re-fetch all 300 + re-embed all 300. Cheap for the bench (one-shot) but wrong for ongoing use.

---

## Phase ordering

Front-load the cheapest unblocks. Don't build async until storage is durable; don't run the bench until storage is durable.

### Phase A — Vercel Blob persistence (~2-3 days)

**Why first:** storage is the prerequisite for everything else. Async needs a place to store job state; bench needs corpora to survive cold starts; ongoing CE use needs reads to be repeatable.

**Scope:**
- Add `_lib/storage/blob.py` — wraps `@vercel/blob` REST API (or Python equivalent). Functions: `put(key, body)`, `get(key) -> bytes | None`, `head(key) -> meta | None`, `delete(key)`, `list(prefix)`.
- Replace `corpus_store.cache_dir()` filesystem reads/writes with Blob. Keep the `LoadedCorpus` shape; only the I/O layer changes.
- Migration: on cold start, `corpus_store.load_corpus` falls back to Blob if `/tmp/<id>.index.json` is missing. Writes go to Blob always; `/tmp` becomes a per-instance read-cache.
- Vercel Blob env var: `BLOB_READ_WRITE_TOKEN` (auto-provisioned when you create a Blob store via `vercel blob create`).
- Tests: monkeypatch `_lib/storage/blob.py` with an in-memory dict so existing 137 tests don't change.

**Verification gate:** index `pallets/itsdangerous` → wait 10 min (cold start window) → query `mode=semantic` succeeds. Same for `flipt-io/flipt`.

**Risks:**
- Blob latency on read (200-500ms per call). Mitigation: per-instance `/tmp` warm cache.
- Blob storage cost. Free tier = 1 GB. 70 corpora × ~2 MB avg = 140 MB. Comfortable.
- `BLOB_READ_WRITE_TOKEN` not in current Vercel env — needs `vercel blob create` setup.

### Phase B — async indexing via Vercel Cron + KV (~3-4 days)

**Why second:** unblocks repos > 300s without forcing local upload.

**Scope:**
- Vercel KV (Redis) for job queue + status. Schema:
  ```
  KEY job:<id>          → {status, repo, branch, corpus_id, classification, embed,
                            file_count, files_indexed, embedded_count, error,
                            created_at, started_at, completed_at}
  KEY queue:pending     → list of job_ids (RPUSH on enqueue, LPOP by worker)
  KEY job:<id>:cursor   → {next_file_index, partial_files, partial_embeddings}
  ```
- Indexer refactor: `scripts/index_github_repo.py` becomes resumable. Accepts `start_index`, returns `(files, next_index)`. The cron tick processes a budget-bounded chunk (~50 files) and saves cursor.
- New endpoint: `api/cron/index-worker.py`. Vercel Cron fires every 1 min; worker pulls one job from `queue:pending`, advances cursor, saves to Blob. Job loops back into `queue:pending` if not done.
- `ce_index_github_repo` with `async=true` enqueues, returns `{job_id, status: "queued"}`. NOT_IMPLEMENTED branch removed.
- `ce_get_job_status` reads from KV (not the in-memory `job_store`). The in-memory store stays as a per-instance read cache.
- Vercel Cron schedule: 1-minute interval (Pro tier minimum). For 70 corpora at ~50 files/min, full bench setup takes ~10 hours unattended.

**Verification gate:** enqueue `kubernetes/kubernetes` indexing async; poll `ce_get_job_status` until complete; query `mode=semantic` returns ranked results from a real Kubernetes corpus.

**Risks:**
- Cron min interval 1 min; for huge repos (e.g., 5000 files / 50-per-min = 100 min) that's slow but acceptable for offline bench setup.
- KV cost: free tier 30K commands/month. 70 jobs × 5-50 cron ticks × few R/W per tick = ~5-10K commands. Comfortable.
- Worker idempotency: if a tick crashes mid-batch, the next tick should resume from saved cursor without re-fetching already-indexed files. Atomic write pattern: write `job:<id>:cursor` BEFORE writing the partial corpus to Blob; on resume, reconcile.

### Phase C — full bench run + writeup (~1 day)

**Why last:** depends on A+B. With Blob persistence + async indexing, the bench is mechanical.

**Scope:**
- Pre-stage all 70 corpora via async setup_corpus.py calls (overnight). Each enqueues a job; cron worker chews through.
- Once all 70 corpora are `complete` (poll via ce_get_job_status), run `run_ir_bench × {C1, C2, C3, C4}` → 280 task-runs total. Each task-run is `find` (cheap, no embeds beyond the query). Total cost: ~$3 of codestral query embeds amortized over 70 corpora × 4 configs.
- `diff_runs.py` → `docs/benchmarks-v1.md` with the H1 / H2-IR verdict tables, per-language slices (Go / Python / Rust / Java / TypeScript / C++ / C#), and per-task-category slices (debug / fix / feature / refactor / secure / test).
- Compare to flipt smoke: does H1's 36pp lift hold across 70 tasks, or was flipt anomalous? Does H2-IR Jaccard<0.7 frequency change?
- Brief write-up of caveats: still single-repo SDLC, still IR-only, still no Haiku reward signal — see bench-plan.md for what's next.

**Verification gate:** `docs/benchmarks-v1.md` exists, has signed verdicts on H1 + H2-IR, lists tasks where keyword UNEXPECTEDLY beat semantic (signals to investigate).

**Risks:**
- Some sg-evals snapshots may be private or the GITHUB_TOKEN scope may not cover them. Pre-check via `setup_corpus --repo <name> --classification public --dry-run` ideally; or just enumerate failures.
- 70 tasks × 4 configs = 280 calls, plus polling overhead. Should fit in a few hours of evening time once async is plumbed.

---

## Out of v1.1 scope (sequenced for later)

- **Phase 1 Haiku run** (~$18, validates H4 — does CE actually beat baseline grep on single-repo SDLC reward?). Bench plan owner.
- **Cross-repo bench** (264 tasks). Needs CE's cross-corpus pack to ship first (`plan/p4-cross-corpus-pack.md`).
- **Incremental indexing** (don't re-fetch unchanged files). Real product feature; bench-irrelevant.
- **`docs/vs-sourcegraph.md` head-to-head**. Needs Sourcegraph CSB run on same task set. v2 work.
- **MMR lambda auto-tuning by query type** (`scripts/mmr.classifyQuery`). Already in the local skill via PR #35; port to server-prod if H7 fails on the bench.

---

## Estimated total: ~6-8 working days for A+B+C

Conservative: A = 3, B = 4, C = 1. Plus inevitable debug + Codex review rounds.

After this lands, the bench can be re-run on demand (e.g., to validate a packer change) without per-repo babysitting. That's the value of the storage + async investment beyond just "tonight's bench."

---

## Sharp edges to remember from Phase 5.5 (carry forward into v1.1)

- **Vercel function bundle can't reach `..` paths.** Anything imported from `scripts/` must be vendored to `_lib/vendor/` with a sha-sync test. Phase 5 did this for `pack_context_lib.py`; Phase 5.5 did it for `index_github_repo.py`. Phase B will need the same for any worker dependencies.
- **vercel.json is gitignored** by the repo's `*.json` blanket rule. Bumps to maxDuration / regions persist in working tree only. Either force-add or amend `.gitignore`. (Worth doing in v1.1 since maxDuration matters more with cron + async.)
- **errors.py registry was incomplete** before this PR — `INTERNAL` was protocol-only; the unknown-code fallback for `tool_error` raised `KeyError`. Fixed in PR #49. Pattern: when adding new error codes, register in `TOOL_ERROR_CODES` AND verify the fallback path.
- **Mistral codestral-embed: 8192-token cap, code is ~3 chars/token.** MAX_INPUT_CHARS=20K is the safe truncate for code corpora.
- **Idempotency must check intent** (mode, embed flag), not just `commit_sha`. Stale keyword-only corpora otherwise pin every retry forever. PR #49 fixed for `embed`; if Phase B adds more dimensions (async vs sync, partial-coverage retry), extend the intent guard.
- **CSB task layout:** task.toml `repo` is a bare name; canonical `owner/name` + branch (`--branch v5.6.7`) live in `environment/Dockerfile`. Converter at `eval/csb/csb_to_spec.py` handles all 4 ground-truth shapes (`files` / `expected_files` / `buggy_files` / `required_findings` — last is verifier-style, not IR-scorable).

---

## Tracking

- This plan: `context-engineering/plan/ce-v1.1-bench-readiness.md`
- Bench plan: `context-engineering/plan/codescalebench-bench-plan.md` (parent)
- Cross-corpus pack: `context-engineering/plan/p4-cross-corpus-pack.md`
- v1 production MCP shipping: `context-engineering/plan/production-mcp-shipping-plan.md` (8 phases ✓)
- This session's PR: VictorGjn/agent-skills#49 (Phase 5.5 + smoke + self-review fixes)
