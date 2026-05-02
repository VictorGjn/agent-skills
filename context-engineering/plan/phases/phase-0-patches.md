# Phase 0 - Patch Plan (file:line precise)

> Audience: Developer executing Phase 0 truth-up work, Mon May 4 onward.
> Companion to: plan/phases/phase-0.md (deliverables + acceptance) and ROADMAP.md v3.
> Reading note: This doc is patch-level only. Open the listed file alongside it; line refs valid as of commit at 2026-05-01.

State as of 2026-05-01: deliverables 0.7 and 0.8 are partially landed in code already (cap + RRF helpers exist) but neither is wired to acceptance. 0.11 and 0.12 are unstarted.

---

## Deliverable 0.7 - MAX_RELATIONS cap on graph construction

**Files to edit**:
- `scripts/code_graph.py` (cap exists; needs `audit/log.jsonl` sink)
- `scripts/graphify_adapter.py` (cap NOT applied - this is the actual 12,175 to 5,000 source)
- `scripts/index_workspace.py` (audit dir bootstrap - optional)

**Current state**:
- `scripts/code_graph.py:23-31` already defines `MAX_RELATIONS` (default 50_000, env override). `_add_edge` at `:263-278` enforces the cap and warns once on stderr. Good - leave it; only add the JSONL sink.
- `scripts/graphify_adapter.py:113-138` builds `edges` from `raw_edges` with **no cap**. Graphify graphs on `claude-code-index.json` produce >12k file-level relations; this path is the real silent-truncation source documented in ROADMAP v3 (the prior code path stored only the first 5k by truncating downstream - Graphify path now stores all, but should still cap and warn for symmetry with the import-only path).
- No `audit/` dir exists yet; `cache/` is the canonical writable location (will move to XDG in 0.2).

**Patch**:

1. `scripts/graphify_adapter.py` - share the cap with `code_graph`. Near top of module, add: `from code_graph import MAX_RELATIONS`. In `adapt_to_code_graph` at `:134`, replace the bare loop with a length-checked one that breaks once `len(edges) >= MAX_RELATIONS`, sets a `truncated=True` flag on `stats`, and logs once via the audit helper described below.

2. New helper `scripts/_audit.py` (~25 lines) exposing `log_warning(event: str, **fields)`: writes a JSONL line to `<cache_dir>/audit/log.jsonl` if writable (cache dir = same path resolution `index_workspace.py:312-313` uses today). Fails open: any IO error -> `print(json.dumps(...), file=sys.stderr)`. Single source for 0.7, 0.11, 0.12 to share.

3. `scripts/code_graph.py:268-272` - replace the inline `print(...)` warning with `_audit.log_warning("graph.truncated", source="import_only", cap=MAX_RELATIONS, total_attempted=...)`. Track attempted-but-dropped count by adding a counter alongside `_truncated`.

4. `stats` dict in both `code_graph.build_graph` (`:347-353`) and `adapt_to_code_graph` (`:146-152`) gets `truncated: bool, cap: MAX_RELATIONS` keys so `mcp_server.pack` and downstream tools can surface it.

**Tests to add** (`scripts/tests/test_max_relations.py`):
- Synthetic 100-file workspace with 60k forced edges (mock `_add_edge` calls). Assert `len(edges) == 50_000`, `stats[truncated] is True`, audit entry written.
- `CONTEXT_ENG_MAX_RELATIONS=10` env -> cap respected; invalid value (`50k`) -> falls back to default with stderr warning (already handled at `:24-31`, just assert).
- Graphify adapter: feed a 60k-link `graph.json` fixture, assert cap + warning.

**Risks / unknowns**:
- The 12,175 number was measured against `claude-code-index.json`. We have no fixture of that cache in the repo; build a synthetic one for tests rather than relying on a one-off file.
- `audit/log.jsonl` location depends on cache relocation in **0.2**; until 0.2 lands, write next to `cache/` and accept that path moves later. Do not block 0.7 on 0.2.
- `from code_graph import MAX_RELATIONS` creates a module dep edge - fine, both modules already share `code_graph` semantics, but make sure no circular import is introduced (it is not today: `graphify_adapter` is imported lazily inside `code_graph.build_graph_with_fallback`).

---

## Deliverable 0.8 - RRF fusion replaces 0.6/0.4 linear

**Files to edit**: `scripts/embed_resolve.py`

**Current state**:
- Constants `SEMANTIC_WEIGHT = 0.6` and `KEYWORD_WEIGHT = 0.4` declared at `:42-43` - **dead constants**, not used anywhere downstream (grep confirms zero refs in the file). Linear fusion was already removed at some prior point.
- `RRF_K = 60` defined at `:283`; `_rrf_score` helper at `:290-292`.
- `resolve_hybrid` at `:295-388` already computes RRF - **but** at `:360-372` it sorts by `max(kw_raw, sem_raw)` (raw score) with RRF as `_rrf_bonus` tiebreaker, and `confidence` is set to the raw max. The doc comment at `:351-359` explains this is intentional (downstream `relevance_to_depth` uses fixed thresholds). So pure RRF-as-confidence would break depth banding.

**Patch**:

1. Remove dead constants `:42-43` (SEMANTIC_WEIGHT / KEYWORD_WEIGHT). They mislead readers into thinking linear blend is live.

2. Decision required: the spec says replace with `score = sum(1.0 / (60 + rank) for rank in (kw_rank, emb_rank))`. The current code does this RRF math but uses it only as a tiebreaker. **Recommend** keeping the current raw-max + RRF tiebreak because it preserves depth-band thresholds, BUT renaming and surfacing the RRF score:
   - At `:374-381` add `rrf_score: round(rrf_bonus / _RRF_MAX, 4)` to the result dict so callers can opt into pure RRF ranking by sorting on it.
   - Update docstring `:299-312` to drop the `RRF as primary fusion` framing (currently misleading) and document the actual hybrid: raw-max ranking + RRF tiebreak + RRF score exposed.

3. If the spec is non-negotiable on pure RRF as score, then at `:361, :366, :371` set `confidence = rrf_bonus / _RRF_MAX` and **remove** `pack_context_lib.relevance_to_depth` threshold dependency on confidence. That is a wider blast radius; flag for PM decision before coding.

4. Sweep `embed_resolve.py:443-455` and `mcp_server.py:140-150, :294-313` callers to confirm they treat `confidence` as opaque (most do; `mcp_server.py:147` reuses it as `relevance` directly, which IS a depth-banding input, confirming risk in option 3 above).

**Tests to add** (`scripts/tests/test_rrf_fusion.py`):
- File present in both lists at rank 1 -> highest confidence; confirms `_rrf_bonus = 2/(60+1) = 0.0328`.
- File only in keyword list -> raw kw score returned, lower than two-list winner of equivalent raw strength.
- Anti-noise gates (`SEM_MIN_COSINE`, `KW_MIN_RELEVANCE` at `:320-321`) drop weak hits.
- Regression: feed three queries from the eval golden set (Phase 0.10), assert top-3 results stable vs locked snapshot.

**Risks / unknowns**:
- Spec says replace linear with RRF. Linear was already removed silently at some point; the named constants are misleading. **Confirm with PM** whether deliverable is satisfied by current state + cleanup, or whether they want pure-RRF confidence (with depth-banding rework).
- Eval (0.10) does not exist yet, so RRF >= linear on the corpus acceptance is unverifiable until 0.10 lands. Consider sequencing: ship 0.8 cleanup now, gate the strict acceptance on 0.10.

---

## Deliverable 0.11 - MCP HTTP server hardening

**Files to edit**: `scripts/mcp_server.py`

**Current state** (`:369-375`):
```python
if __name__ == "__main__":
    if "--http" in sys.argv:
        idx = sys.argv.index("--http")
        port = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 8000
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        mcp.run()
```

Issues: hard-coded `0.0.0.0` (publicly bindable by default), no auth, no body-size limit, no structured logs, ad-hoc argv parsing. SKILL.md frontmatter advertises both transports (`:15`).

**Patch**:

1. Replace the argv block with `argparse`. New flags:
   - `--http [PORT]` (default port 8000 when flag present without value; flag absent -> stdio)
   - `--bind HOST` (default `127.0.0.1`)
   - `--auth` (boolean; reads `CONTEXT_ENG_TOKEN` from env at startup; refuse to start if flag set without env)
   - `--max-body BYTES` (default `1_048_576`)

2. Auth + size limit + JSON logging are **transport middleware**, not MCP-layer concerns. FastMCP `mcp.run(transport=http, ...)` wraps a Starlette app - the cleanest path is to grab the underlying ASGI app (FastMCP exposes `streamable_http_app()` or similar; verify in installed `mcp[cli]`) and wrap it with three middlewares before passing to uvicorn:
   - `BearerAuthMiddleware` - reads `Authorization: Bearer <token>`, 401 on miss/mismatch. Skip when `--auth` not set.
   - `BodySizeLimitMiddleware` - reads `content-length`, 413 if > max.
   - `JSONLoggingMiddleware` - writes `{ts, method, tool, status, duration_ms, request_id}` to stderr per request. No stdout writes (stdout reserved for stdio transport).

3. If FastMCP does not expose the ASGI app, fall back to running uvicorn directly with FastMCP request handler. Verify against installed `mcp[cli]` version (pinned in 0.2 requirements) before coding the middleware path.

4. Add startup log line to stderr: `{event: mcp.start, transport: http|stdio, bind: 127.0.0.1, auth: true|false, max_body: 1048576}`.

**Tests to add** (`scripts/tests/test_mcp_http_hardening.py`):
- Spawn server on `:0` (random port) in a thread; assert default bind = 127.0.0.1 (connect via `127.0.0.1` succeeds; `0.0.0.0` connect from external interface fails - skip this assertion if CI lacks multi-interface).
- `--auth` set, no `Authorization` header -> 401.
- `--auth` set, wrong token -> 401. Correct token -> 200.
- POST 2MB body -> 413.
- Capture stderr, assert each request emits one valid JSON log line.
- Stdout silent during HTTP mode (assert `captured.stdout == ""`).

**Risks / unknowns**:
- FastMCP middleware injection point depends on the installed `mcp[cli]` version - recently changed. Verify on the pinned version (0.2 work) before writing the middleware. If FastMCP does not allow middleware injection, write a thin uvicorn wrapper script; do not fight the framework.
- Bearer-token auth via env var means rotating the token requires server restart. Acceptable for v0.3 (CE-internal). Document in SPEC-mcp.md.
- The 1MB cap will break large `pack` results returning >1MB markdown. Verify `pack_context` outputs typical sizes; if any exceed 1MB, raise default to 4MB or only apply limit to **request** bodies (correct interpretation - body is the inbound JSON-RPC envelope, not the response).

---

## Deliverable 0.12 - Soften SKILL.md headline

**Files to edit**: `SKILL.md`, `ROADMAP.md`, `ROADMAP-v4.md` (no top-level `README.md` in the CE skill dir; only `.pytest_cache/README.md` and `server-stub/README.md`).

**Current state**:
- Searched SKILL.md for `100%|recall at 1%|6x|same budget|12 files`: **no matches** for the exact `100% recall at 1% of repo` phrase. The `100%` at `SKILL.md:216` is the depth-cost table (legitimate, leave alone).
- `pack_context.py:8` docstring already uses concrete framing: `~12 files at mixed depth, ~95% of an 8K budget`.
- ROADMAP.md `:122` references the headline that needs softening; ROADMAP-v4.md does not contain the recall claim itself but cross-refs it.
- The closest `headline` in SKILL.md today is `:20`: `The engine for building and querying a queryable, compounding company brain...` - which is the new framing post-Phase-A, not the old recall claim.

**Patch**:

1. **Defensive sweep** - run `rg -n "100% recall|recall at 1%|1% of repo" SKILL.md ROADMAP.md ROADMAP-v4.md plan/`. If any hit, replace with the concrete value line. Likely zero hits in SKILL.md - the framing was on a prior README that has already been rewritten. ROADMAP.md `:122` is the only definite hit.

2. **Positive add** to SKILL.md, immediately after `:20` (the engine framing line), insert a new paragraph before `:22` (the `Five tightly-coupled capabilities` enumeration):
   ```markdown
   **The unit of value**: 12 files at 8K tokens vs 2 files at 8K - same budget, 6x the context.
   ```

3. ROADMAP.md `:122` - strike through or remove the row from the Phase 0 table once the deliverable lands; add a `[done]` annotation. Same sweep on ROADMAP-v4.md.

4. Optional: update SKILL.md frontmatter `description` field at `:3` - currently 1.7KB, no recall claim. Leave alone unless PM requests.

**Tests to add**: None (doc-only change). Acceptance is grep-based:
```bash
rg -n "100% recall|recall at 1%|1% of repo" SKILL.md   # must return 0 lines
rg -n "6x the context|same budget" SKILL.md            # must return >=1 line
```

**Risks / unknowns**:
- The `12 files at 8K vs 2 files at 8K` framing is only honest if measured. We have `pack_context.py:8` claiming `~12 files at mixed depth`, but no fresh measurement on the current corpus. **Recommend**: before committing the headline, run `pack_context.py auth` on three workspaces (CE, efficientship-backend, company-knowledge) at 8K budget; record actual file count in `plan/audits/headline-measurement.md`. If any corpus packs <8 files, soften further to `8-12 files vs 2 - 4-6x the context`.
- The `100%` at `SKILL.md:216` is the depth-cost relative metric, **not** a recall claim. Do not replace it.

---

## Sequencing recommendation

| # | Deliverable | Order | Reason |
|---|---|---|---|
| 0.12 | Headline soften | First (15 min) | Doc-only, no risk, immediate signal |
| 0.7 | MAX_RELATIONS audit-log sink | Second (1/2 day) | Code already 80% there; finish + test |
| 0.8 | RRF cleanup + decision | Third (1/2 day) | Needs PM decision (option 2 vs 3 above) before coding |
| 0.11 | MCP hardening | Fourth (1 day) | Largest blast radius; depends on 0.2 pinned `mcp[cli]` to verify middleware injection point |

Total: ~2.5 dev-days for an experienced Python engineer with the spec + files open.
