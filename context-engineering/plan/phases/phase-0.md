# Phase 0 — Truth-up + incremental + tree-sitter + authority + regression eval

> **Goal**: Make CE match its own README, run reliably under cron, cover the languages it claims, rank by structure not just keyword/type, and ship measurement infra so subsequent phases don't regress quietly.
>
> **Effort**: M (~1 week) | **Status**: pending | **Blocks**: Phase 0.5, Phase 1
>
> **Source**: ROADMAP.md v3 § Phase 0 + Sourcegraph gap analysis (incremental, authority, tree-sitter coverage) + regression eval.

## Why this phase first

CE's wiki layer (Phase 1) sits on top of the index, the graph, and the cache. Today all three have honesty problems:
- **Index**: 3 incompatible JSON schemas in one cache dir; bare `open(w)` writes; no `schema_version`
- **Graph**: 12,175 → 5,000 relations truncation in `claude-code-index.json` — every `--graph` query traverses a 59%-truncated graph
- **Cache**: writes into the user's global skills folder; collisions guaranteed

You cannot build the company brain on this. Phase 0 fixes it before Phase 1 stacks the wiki on top.

## Deliverables

### 0.1 — Verify advertised modules + add embed deps + scaffold wiki/ (S, ½ day) **[REVISED]**

**Status (verified 2026-05-01)**: All 14 advertised CE Python modules + `embeddingResolver.ts` are SHIPPED. ROADMAP v3's "5 missing modules" claim was stale.

**Real Phase 0.1 work**:
- Add `sentence-transformers>=2.7,<3` to `requirements.txt` and a new `embed` group in `pyproject.toml` optional-dependencies (gates Phase 0.5.4 BGE)
- Create `scripts/wiki/` subdirectory + `__init__.py` (scaffolds Phase 1.6 `wiki_init.py`)
- Verify `events/` + `semantic_shift.py` from PR #10: where do they live? Update findings if they're not at `scripts/wiki/`
- Decide console script exposure (currently `# Console scripts intentionally NOT exposed yet:` in pyproject) — recommend keep manual for v0.3, add console_scripts entry in Phase 0.5.8 when slash commands ship

**Acceptance**:
- `pip install -e .[embed]` succeeds (sentence-transformers installs cleanly)
- `scripts/wiki/` exists as empty package (placeholder for Phase 1)
- `python3 scripts/pack_context.py "test"` runs end-to-end on a fresh checkout without ImportError (smoke test)

### 0.2 — Pinned dependencies + cache relocation (S, ½ day)

**`requirements.txt` + `pyproject.toml`** with pinned versions:
- `tree-sitter-language-pack==0.4.0` (Python 3.12+) or `tree-sitter-languages==1.10.2` (3.10-3.11)
- `requests`
- `mcp[cli]`
- `sentence-transformers==2.7.0` (NEW — enables BGE in Phase 0.5)
- Optional: `openai` (semantic mode fallback)

**Cache relocation**:
- Move `cache/` from skill dir → `${XDG_CACHE_HOME:-~/.cache}/context-engineering/<repo-hash>/`
- `--cache-dir` flag to override
- `<repo-hash>` = SHA256 of `(workspace_path, source_url)` — namespacing prevents collisions across workspaces

**Acceptance**: indexing two different workspaces produces two separate cache dirs; user's skills folder no longer mutates.

### 0.3 — Schema versioning + atomic writes + file lock (M, 1 day)

Every JSON artefact gets `schema_version: "1.0"` in its top-level dict. The loader refuses to load on mismatch with a clear error.

Write paths converted to atomic `tmp + os.replace`:
- `embed_resolve.py:177`
- `index_github_repo.py:406`
- `index_workspace.py` (all writes)

File lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows) around cache writes — prevents concurrent runs corrupting JSON.

**Acceptance**: kill `index_workspace.py` mid-write 10 times; cache remains valid. Two parallel `index_workspace.py` runs against the same workspace serialize cleanly.

### 0.4 — Stable `file_id` + `content_hash` + `errors[]` per index (S, ½ day)

Each indexed file entry gets:
- `file_id`: `sha256(source_ref + path)[:16]` — stable across re-indexes, unique in monorepos
- `content_hash`: `sha256(file_content)` — feeds incremental indexing (0.5)
- `errors[]`, `skipped[]`: per-file failure log surfaced to `audit/log.jsonl` later

**Acceptance**: re-running `index_workspace.py` on unchanged workspace produces byte-identical index (modulo timestamp).

### 0.5 — Incremental indexing (M, 2 days) **[Sourcegraph gap]**

`index-manifest.json` stores `{path: {mtime, content_hash, indexed_at}}` for every file in the index. On re-index:
1. Walk workspace, compute new manifest
2. Diff: added / changed / deleted paths
3. Re-parse only changed files; carry forward unchanged
4. Drop deleted from index + light-index + relations

`--full` flag forces complete re-index (escape hatch).

**Why this matters for the company brain**: Anabasis routines fire on cron, not on push hooks. Connectors stream events into `events/`; CE must re-index incrementally or every routine becomes a full-repo re-parse cycle.

**Acceptance**: editing 1 file in efficientship-backend (~500 files) re-indexes in <2 sec instead of full re-parse (~30 sec).

### 0.6 — Tree-sitter coverage to all 14 advertised languages (S-M, 1-2 days) **[corrects misleading SKILL.md claim]**

SKILL.md advertises: `.ts .tsx .js .jsx .py .go .rs .rb .java .c .cpp .cs .kt .scala .php`

Today's silent regex fallback: 11 of 14 (per ROADMAP.md Deferred section — verify exact count against `ast_extract.py`).

**Action**:
1. Audit `ast_extract.py` against advertised languages — list which actually use tree-sitter vs regex.
2. Add tree-sitter parsers for each missing language via `tree-sitter-language-pack` (covers all 14).
3. For each language, write 5 unit tests: function definition, class/struct, method, interface/trait, generic/template.
4. Update SKILL.md if any language genuinely can't ship tree-sitter coverage.

**Acceptance**: `pytest tests/test_ast_coverage.py` passes; `python3 -c "from scripts.ast_extract import LANGUAGE_PARSERS; print(LANGUAGE_PARSERS)"` shows all 14.

### 0.7 — Fix 12,175 → 5,000 relations truncation (S, ½ day)

`MAX_RELATIONS` constant introduced (default 50,000). When exceeded, log a `WARNING` to `audit/log.jsonl` (ensure audit/ exists or fall back to stderr).

**Acceptance**: re-indexing claude-code repo produces a graph with all relations OR a clear truncation warning. No silent 59% loss.

### 0.8 — RRF fusion replaces 0.6/0.4 linear (S, ½ day)

In `embed_resolve.py` (or wherever the fusion lives), replace:
```python
score = 0.6 * keyword_score + 0.4 * embedding_score  # OLD
```
with:
```python
# RRF — Reciprocal Rank Fusion, k=60 (Cormack 2009 default)
score = sum(1.0 / (60 + rank_in_list) for rank_in_list in (kw_rank, emb_rank))
```

**Why**: parameter-free, removes magic-number tuning. Default that beats the linear blend in most evaluations.

**Acceptance**: regression eval (0.10) shows RRF ≥ linear on the corpus.

### 0.9 — Authority signals from import/call graph (S, 1 day) **[Sourcegraph gap]**

Compute in-degree per file/symbol on `imports` + `calls` edges. At equal relevance, files/symbols with higher in-degree get better depth and a headline annotation.

**Output change at Headlines depth**:
```
## Headlines (3 files)
### src/auth/middleware.ts
  - `authenticate` (called from 14 files)
  - `requireRole` (called from 8 files)
```

**Why this matters for the company brain**: human-curated entity pages (Department Specs, ADRs) are referenced from many other pages — authority signals surface "this is load-bearing" without the user having to mark it.

**Acceptance**: efficientship-backend pack of "auth" surfaces `auth.module.ts` higher than a same-relevance utility file with 0 callers.

### 0.10 — Internal regression eval (M, 2-3 days) **[NOT public benchmark]**

Build a small golden-set harness:
- 30-50 `(query, must_include_files[], may_include_files[])` tuples per dogfood corpus
- Three corpora: CE itself + efficientship-backend + company-knowledge
- Metric: weighted recall on must_include + critical hit rate (top-1 must_include hit / total queries)

**Run on every PR via GitHub Action**: `python3 scripts/eval/run_regression.py --corpus all --output results.json`

**Acceptance**: baseline numbers captured for current main; PR check fails if regression eval drops > 5% below baseline.

**This is NOT the public benchmark** (deferred to Proving Layer). It's a regression suite — different purpose, different audience. Internal only.

### 0.11 — MCP HTTP hardening (S, 1 day)

- Default bind: `127.0.0.1` (localhost only)
- `--bind 0.0.0.0` to expose externally (explicit opt-in)
- `--auth` reads `CONTEXT_ENG_TOKEN` env var; reject requests without bearer match
- Reject requests > 1MB (prevent abuse)
- Logs: JSON to stderr (`{ts, method, tool, status, duration_ms}`), no stdout pollution

**Acceptance**: `nmap localhost -p 8000` shows port open only on localhost by default; `--auth` rejects unauthenticated requests with 401.

### 0.12 — Soften misleading SKILL.md headlines (S, 15 min)

Replace "100% recall at 1% of repo" framing (ROADMAP flagged) with concrete value language.

Recommended: "12 files at 8K tokens vs 2 files at 8K — same budget, 6× the context."

**Acceptance**: README + SKILL.md no longer lead with the 100%-recall metric. Soft on Phase 0.5 surface collapse work.

## Acceptance criteria (phase-level)

- [ ] All 12 deliverables shipped
- [ ] Regression eval baseline captured + GitHub Action wired
- [ ] No deliverable from Deferred — Proving Layer pulled in (preserve scope)
- [ ] CE Phase 0.5 unblocked (BGE local embeddings can land on top of clean index)
- [ ] CE Phase 1 unblocked (events/ writes are atomic, schema versioned, cache stable)

## Dependencies

- ✅ Memory updated (engine framing) — done 2026-05-01
- ⚠️ ROADMAP.md v3 verification — must `git diff HEAD~10 ROADMAP.md` before starting; some Phase 0 items may have already shipped post-roadmap
- ⚠️ Repo state check — `events/` + `semantic_shift.py` shipped per recovered ultraplan note; verify

## What this phase does NOT do

- No wiki schema work (Phase 1)
- No `pack --wiki` (Phase 2)
- No Source ABC for Notion/Gmail/HubSpot (out of scope; lives in syroco-product-ops)
- No public benchmark (Deferred — Proving Layer)
- No LSP precise xref (Deferred — see findings.md)

## YC RFS alignment (preview)

| Pillar | Phase 0 contribution |
|---|---|
| Executable | Incremental indexing makes cron-fired routines cheap; without it, every routine is full re-parse |
| Installs | Cache relocation + pinned deps remove silent collisions on install |
| Human knowledge | Tree-sitter coverage for all 14 langs ensures human-edited code is indexed precisely (less false positives) |
| Connections | MCP hardening makes the find skill safe to expose under Anabasis runtime |
| AI | RRF fusion + authority signals = better default ranking before any LLM call |
| Skills that automate | Regression eval ensures the skill stays usable as Phase 1+ stack on top |
| Company brain | Schema versioning + atomic writes = the brain doesn't corrupt under load |

7/7 served. Detailed audit in `plan/audits/phase-0.md`.
