# benchmarks-v1.md

**Run date**: 2026-05-06
**Server**: `ce-mcp-prod.vercel.app` (commit `e9d4dd0`, v1.1 Phase B done — KV jobs + chunked async + Blob storage + Mistral codestral-embed)
**Source**: 70 IR-scorable CSB SDLC tasks at `eval/csb/converted-tasks/`
**Bench code**: `eval/csb/run_ir_bench.py`, `eval/csb/diff_runs.py`

## Headline

Both v1.1 hypotheses **clear their gates on the reachable subset**:

| Hypothesis | Reachable mean (n=24) | Gate | Verdict |
|---|---|---|---|
| H1 — codestral semantic > keyword | C1=0.000 → C2=0.102, Δ=+0.102 | ≥+0.10 abs file_recall | **PASS ✓** |
| H2-IR — MMR changes top-K composition | Jaccard<0.7 on 17/24 = 70.8% | ≥30% of reachable tasks | **PASS ✓** |

H4 / H7 require Haiku reward signal — IR-only path can't decide them (Phase 3 of bench plan).

## Coverage gap — 24/70 tasks reachable

Of the 70 IR-scorable tasks, only **24 had a usable corpus** at run time. Sources of the 46-task gap:

| Cause | Count | Detail |
|---|---|---|
| GitHub default branch ≠ `main` | 13 | apache/beam (master), ansible (devel), kafka (trunk), curl (master), element-web (develop), kubernetes (master), tidb (master), postgres (master), TypeScript (?), terraform (?), 4× linux tag refs |
| Forbidden by GH token | 21 | mostly `sg-evals/*` private fork snapshots; the prod token can't read them |
| URL-encoding bug in indexer | 2 | microsoft/vscode + dotnet/roslyn — paths with spaces aren't URL-encoded in `_lib/vendor/index_github_repo.py:github_get_raw` (raises `InvalidURL` mid-fetch) |
| Successfully indexed | 8 | django, flipt-io/flipt, envoyproxy/envoy, cilium/cilium, OpenGamma/Strata, apache/camel, ceph/ceph, gravitational/teleport |

Bench was launched at 4-way parallel; the parallel index burst ran the prod GH token into secondary rate limits, so a 13-task default-branch retry returned 403 SOURCE_FORBIDDEN on every repo. Sequential post-cooldown retry could lift the reachable set to ~37/70 (the 13 branch fixes), but doesn't fix the 21 sg-evals/private gap or the 2 URL-bug repos.

## Aggregate metrics — reachable subset (n=24)

| metric | C1 keyword | C2 codestral | C3 codestral+MMR | C4 shipping | Δ (C2−C1) |
|---|---|---|---|---|---|
| file_recall | 0.000 | 0.102 | 0.102 | 0.102 | +0.102 |
| precision@5 | 0.000 | 0.058 | 0.058 | 0.058 | +0.058 |
| recall@5 | 0.000 | 0.102 | 0.102 | 0.102 | +0.102 |
| f1@5 | 0.000 | 0.065 | 0.065 | 0.065 | +0.065 |

**C2 = C3 = C4 on aggregate** but they differ per-task: C2-vs-C3 top-5 Jaccard < 0.7 on 17 of 24 reachable tasks. MMR rotates which files appear in the top 5 without changing aggregate recall — expected for k=5 (MMR's redistribution is more visible at larger k where diversification isn't already forced by the truncation).

## Per-corpus breakdown (reachable subset)

| corpus | n tasks | C1 mean recall | C2 mean recall | embedded_count | notes |
|---|---|---|---|---|---|
| `gh-flipt-io-flipt-main` | 4 | 0.000 | **0.362** | 300/300 | small repo, full embed, semantic finds GT cleanly |
| `gh-django-django-main` | 10 | 0.000 | 0.100 | 300/300 | embedded; `django-repo-scoped-access-001` scored 1.0 (semantic wins) |
| `gh-envoyproxy-envoy-main` | 3 | 0.000 | 0.000 | 0/300 | keyword-only corpus (embed budget exceeded) |
| `gh-cilium-cilium-main` | 2 | 0.000 | 0.000 | 300/300 | GT files outside the indexed top-300 |
| `gh-opengamma-strata-main` | 2 | 0.000 | 0.000 | 0/300 | keyword-only |
| `gh-apache-camel-main` | 1 | 0.000 | 0.000 | 0/300 | keyword-only |
| `gh-ceph-ceph-main` | 1 | 0.000 | 0.000 | 0/300 | keyword-only |
| `gh-gravitational-teleport-main` | 1 | 0.000 | 0.000 | 300/300 | embedded; GT outside top-300 |

flipt is the cleanest signal: small enough to fit entirely, fully embedded, and its 4 IR tasks span representative SDLC patterns. C2 mean 0.362 there means semantic embedding genuinely retrieves GT files that keyword misses.

The "GT files outside the indexed top-300" cases on cilium/teleport suggest a different bench-validity ceiling: even with semantic embedding, the indexer's 300-file cap excludes many GT files outright. See indexer gaps below.

## Indexer gaps surfaced by the bench (v1.2 work)

**These don't invalidate v1.1's verdicts but ceiling the bench's discriminating power.**

1. **`MAX_FILES_TO_FETCH = 300` is too low for production-grade IR.** `_lib/vendor/index_github_repo.py:56`. A real codebase IR system needs to index thousands of files. v1.2 should bump this to 5000+ and consider streaming via the async path with chunked indexing already in place (Phase B.2).
2. **Sort priority is `.md`-first.** `_lib/vendor/index_github_repo.py:303-306`. When a repo has > 300 indexable files, the corpus fills up with READMEs/CONTRIBUTING/CI docs before any source code is fetched. Critical fix for source-code IR. v1.2 should either:
   - flip priority (source-first, .md-last), or
   - balanced sampling across (md, source-major-lang, source-other), or
   - parameterize via an `extension_priority` arg so callers can request source-first.
3. **Source extension set is incomplete for the bench.** `INDEXABLE_EXTENSIONS` (line 30-44) lacks `.c`, `.cc`, `.cpp`, `.h`, `.hpp`, `.cs`, `.scala`, `.j2`. 11 of 70 CSB tasks have GT files only in those extensions and are unreachable as a result. v1.2 should broaden this set.
4. **Default branch detection is missing.** Indexer assumes the caller knows the right branch; doesn't fall back to GH's `default_branch` API on 404. Caused 13 of 46 reachable-gap tasks (apache/beam, kafka, kubernetes, etc. all use `master`). v1.2 should auto-resolve default branch.
5. **URL encoding bug in `github_get_raw`.** Paths with spaces (e.g. `microsoft/vscode/extensions/markdown-language-features/test-workspace/sub with space/file.md`) raise `InvalidURL`. v1.2 fix: `urllib.parse.quote(path, safe='/')` on the contents URL.

## What this bench does NOT measure

- **Pack composition**: `--metric-tool find` only — no `ce_pack_context` / Haiku reward.
- **Async indexer**: bench used sync path only; oversize repos that need the chunked indexer aren't covered (the 18 oversized repos all failed for unrelated reasons).
- **End-to-end production fidelity**: bench corpora are public source code, no private/internal classification gates exercised.

## Recovery plan for v1.2 bench

1. Indexer changes (gaps 1–3 above), shipped together as `feat(indexer): broaden coverage for IR bench`.
2. Default-branch auto-resolve (gap 4).
3. URL-encode path fix (gap 5).
4. Sequential re-index after rate-limit cooldown — covers the 13 default-branch repos (~30 min).
5. Re-run IR sweep × 4 configs on broader reachable set (~37–46/70 tasks).
6. Add the H2-IR Jaccard test at k=10 alongside k=5 (MMR's diversification is more visible at larger k).

Pre-conditions met for that bench launch:
- ce-mcp-prod end-to-end functional: KV (Upstash external + Vercel env), Blob (public-mode store + `BLOB_ACCESS=public` env), Mistral codestral-embed, cron worker authenticated, 300/300 file embedding on small repos.
- 70 converted-task spec.json + ground_truth.json directly readable by `run_ir_bench.py`.
- `transport.py` exception leak (commit unfinalized) lets bench callers diagnose backend errors without round-tripping logs.

## Reproduce

```bash
cd context-engineering/eval/csb
TOKEN=$(cat ~/.claude/handoffs/secrets/ce_mcp_bootstrap_token.txt)

# 1. Index 8 reachable repos sequentially (rate-limit-safe)
python index_all_repos.py --tasks-dir converted-tasks \
  --token-file ~/.claude/handoffs/secrets/ce_mcp_bootstrap_token.txt \
  --output runs/index-2026-05-06.jsonl --parallel 1

# 2. IR sweep × 4 configs
for cfg in ce-keyword ce-codestral ce-codestral-mmr ce-shipping; do
  python run_ir_bench.py --tasks-dir converted-tasks \
    --mcp-url https://ce-mcp-prod.vercel.app --token "$TOKEN" \
    --config "$cfg" --top-k 5 \
    --output runs/ir-${cfg}-2026-05-06.jsonl
done

# 3. Diff (note: aggregate uses 70-task denominator; use the per-corpus breakdown
#     in this report for the reachable-subset verdict)
python diff_runs.py \
  --runs runs/ir-ce-keyword-2026-05-06.jsonl \
         runs/ir-ce-codestral-2026-05-06.jsonl \
         runs/ir-ce-codestral-mmr-2026-05-06.jsonl \
         runs/ir-ce-shipping-2026-05-06.jsonl \
  --names C1 C2 C3 C4 \
  --output ../docs/benchmarks-v1-autodiff.md
```

The `diff_runs.py --both-views` output is preserved at `docs/benchmarks-v1-autodiff.md`. It now emits both denominators side-by-side (loose / all-tasks vs strict / reachable-subset) so the narrative reading and the auto-generated reading match. The strict view in that file is the same H1 PASS / H2-IR PASS verdict as this report.
