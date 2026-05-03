# PRD — CE × lat.md interop (v0.1)

**Status:** In Prep · Bet · 9–13 day appetite (small batch)
**Owner:** Victor Grosjean · **Execution:** Claude
**Source plan:** [`C:\Users\victo\.claude\plans\audit-current-codebase-by-merry-treasure.md`](file:///C:/Users/victo/.claude/plans/audit-current-codebase-by-merry-treasure.md)
**Mode:** Submarine — local only, no Notion / upstream mirror per `feedback_submarine_mode` memory.

---

## Part 1 — Product

### Problem

Three independent provers of the agent-memory-as-graph thesis converged in <72 hours:

| Date | Who | What | Differentiator |
|---|---|---|---|
| 2026-05-01 | Dinesh Raghupatruni | Context Signals MCP (npm) | **Published 79–95% context reduction** on Cal.com TRPC, Trigger.dev, mixed projects |
| 2026-05-03 | Yury Selivanov | lat.md (1k★, MIT, MCP-ready) | **Standardized link grammar** + `// @lat:` source backlinks + `lat check` CI |
| 2026-05-03 | Andre Lindenberg | LinkedIn endorsement of lat.md | Public endorsement = market signal that the pattern is consensus |

CE has the architecture (Phase 1 wiki layer landed: events log, consolidator, validator, auditor, AST extraction across 14 languages, MCP server with 9 tools) but neither (a) interops with lat.md's emerging convention nor (b) ships competitive numbers.

**Persona breakdown:**

| Persona | What they need | Currently get | Gap |
|---|---|---|---|
| Codebase-doc users (lat.md adopters) | Drop CE into a `lat.md/`-structured repo and have it work | Nothing — CE doesn't recognize the grammar | Phase 1, 2, 3, 4 |
| Coding-agent operators (Cursor/Claude Code via MCP) | `lat.locate`/`section`/`refs`/`search`/`expand` tool surface | Only `wiki.ask`/`wiki.add`/`wiki.audit` | Phase 4 |
| Victor (founder) evaluating CE's market position | A 30-second narrative for "what does CE do that lat.md / Context Signals don't" | A 600-word memo I drafted earlier | Phase 5 |
| Future CE users in the codebase-doc segment | Published numbers comparable to Dinesh's 79–95% | Nothing | Phase 5 |
| Engineering team (Claude execution) | A gate-audit checklist that catches regressions before the next phase opens | Ad-hoc verification | Plan's gate audit protocol |

**Business stakes if we don't ship:**

- Codebase-doc segment captured by lat.md's syntax convention. CE looks like a generic depth-packer, not the engine.
- Dinesh's % numbers become the market's evaluation axis with no CE response.
- Anabasis's `find-links` reference impl loses one of its strongest distribution wedges.

**Competitive context** lives in the graph (yc-sus26-brain corpus): `concept:agent-memory-as-graph` at q=0.974 with 7 witnesses. The structural pattern is becoming consensus; differentiation has moved upstream to the concept-quality layer (Anabasis Entity ABC) and CE's multi-corpus + depth-aware packing.

### Context reminder

Existing CE layers this work touches:

| Layer | File | What it does today | What this PRD touches |
|---|---|---|---|
| Source ABC + EventStream | `scripts/wiki/source_adapter.py` | Push-shaped events log writer | Phase 3 adds pull-shaped `SourceCommentBacklinkSource` |
| Wiki consolidator | `scripts/wiki/wiki_init.py` | Groups events by `entity_hint` → `wiki/<slug>.md` | Phase 3 emits `kind: code` for code-backlink-only entities |
| Schema validator | `scripts/wiki/validate_page.py` | Validates frontmatter, `SCHEMA_VERSION = "1.1"` | Phase 3 bumps to `"1.2"`, adds `code` to `_VALID_KINDS` |
| Auditor (3 rules) | `scripts/wiki/audit.py` | Stale supersession / freshness expired / slug collisions | Phase 1 replaces wikilink regex; Phase 2 adds 4th rule (broken refs) |
| AST extractor | `scripts/ast_extract.py` | Symbols across 14 languages via tree-sitter | Phase 2 wraps it in `code_index.py`; Phase 3 reuses for comment parsing |
| MCP server | `scripts/mcp_server.py` | 9 tools (pack, index_*, build_embeddings, resolve, stats, wiki.{ask,add,audit}) | Phase 4 adds 5 lat.md-shaped tools (14 total) |

Prior decisions captured in plan:

| Decision | Choice | Rationale |
|---|---|---|
| Feature flag vs main | Ship in main | Simpler diff; user explicitly chose |
| lat.md spec tracking | Latest, static (commit-sha pin) | Manual revalidation on major bumps; no auto-tracking |
| Numbers | Demo run only | Honors `value_over_proof` memory; counters Dinesh's framing without committing to a benchmarking treadmill |
| CE skill install | Meta — use `pack` against CE itself for next plan | Process learning, not blocking |

### Chosen solution (MoSCoW)

**Must-have** (V1, all 5 phases):

- `scripts/wiki/wikiref.py` parser supporting 3-form grammar: `[[slug]]`, `[[slug#section]]`, `[[src/file#symbol]]`. Backward-compatible with existing `[[slug]]` form. (Phase 1)
- `scripts/wiki/code_index.py` symbol index built on `ast_extract`, persisted with `(path, mtime, sha1[:8])` cache invalidation. (Phase 2)
- `scripts/wiki/lat_check.py` CLI that exits 1 on broken refs; `audit.py` 4th rule `find_broken_refs`. (Phase 2)
- Pre-commit hook template + GitHub Action template, both running on changed files only. (Phase 2)
- `SourceCommentBacklinkSource` recognizing `// @lat: [[ref]]` (JS/TS/Java/Go/Rust/C/C++/C#/Kotlin/Scala/PHP) and `# @lat: [[ref]]` (Py/Ruby), emitting events. (Phase 3)
- Schema bump `1.1 → 1.2`, `code` added to `_VALID_KINDS`. (Phase 3)
- 5 new MCP tools (`lat.locate / section / refs / search / expand`) as wrappers over wikiref + code_index + existing wiki primitives. (Phase 4)
- `docs/vs-lat-md.md` + `docs/vs-context-signals.md` comparison docs (Phase 5)
- `docs/benchmarks.md` demo run on 2-3 reference repos with reproducible commands (Phase 5)
- Gate-audit checklist passes before each phase opens (full plan §"Execution protocol")

**Should-have** (V1.1, post-MVP):

- `Repos/anabasis/spec/entity.md` amendment: `LatMdEntityStore` as reference EntityStore impl for codebase corpora
- SKILL.md headline rewrite emphasizing multi-corpus + depth-aware + concept-quality
- SPEC-mcp.md update documenting the 5 new tools

**Could-have** (deferred):

- More than 3 reference repos in benchmarks
- npm-distributed CE CLI mirroring Context Signals' distribution model
- Cross-repo wiki link resolution (federation across multiple `lat.md/` corpora)

**Won't (this cycle):**

- Full eval harness with baselines/ablations (deferred per `value_over_proof` memory)
- Feature-flag gating for lat.md grammar (decision: ship in main)
- New CE corpora beyond what's already supported
- Multi-corpus depth-packing changes (Phase 2 of CE roadmap, not this PRD)
- Re-platforming the wiki schema beyond the additive 1.1 → 1.2 bump
- Marketing/launch beyond the docs (Anabasis pitch handles upstream framing)
- Browser extension for LinkedIn capture (separate Anabasis-runtime PRD)
- LatMdEntityStore runtime implementation in Anabasis (spec amendment only — runtime stays closed for 90 days)
- Auto-track lat.md spec updates

### User stories

1. **As a CE user with a lat.md repo,** I want to point CE at it and have all `[[file#section]]` and `[[src/file#symbol]]` refs resolve correctly, so that lat.md's graph traversal benefits CE's depth packing.
2. **As a developer using `// @lat: [[ref]]` comments,** I want CE to extract them as evidence for wiki entities, so that source-to-doc backlinks appear in entity provenance with zero manual work.
3. **As a maintainer running `lat check` in pre-commit,** I want CE to fail with a clear remediation message on broken refs, so that broken docs cannot reach main.
4. **As an Anabasis runtime invoking MCP tools,** I want `lat.locate / section / refs / search / expand` available alongside the existing `wiki.*` tools, so that lat.md client tooling (Cursor, Claude Code) interops with CE without code change.
5. **As Victor evaluating CE's market position vs lat.md and Context Signals,** I want a single docs/ page comparing each, so that anyone can see in 30 seconds what CE does that they don't.
6. **As an engineer on a phase,** I want a gate audit that runs all critical checks automatically, so that the previous phase is verified done before the next opens.
7. **As Yury Selivanov reading the CE comparison,** I want my work cited correctly and my license honored, so that this is interop, not adversarial.

### Acceptance criteria

**Phase 1 (wikiref parser):**

- *Given* a fixture set of 30+ wikiref strings spanning all 3 forms (slug, section, code) plus unicode and pipe-display variants, *when* `parse_wikirefs(text)` is called, *then* it returns the correct WikiRef list with no false positives or negatives.
- *Given* an existing `audit.py` run on `scripts/wiki/demo` brain, *when* audit.py is re-run after replacing `_WIKILINK_RE` with `wikiref.parse_wikirefs`, *then* the flag count is unchanged (no regressions).

**Phase 2 (code_index + lat_check):**

- *Given* a brain with 3 deliberately-broken refs (missing file, wrong symbol, wrong section), *when* `python scripts/wiki/lat_check.py --brain ./brain --strict` is invoked, *then* it exits 1 and `audit/proposals.md` lists all 3 broken refs by location + reason.
- *Given* the CE repo (~50 source files), *when* `code_index.py` rebuilds with a warm cache, *then* it completes in under 2 seconds.
- *Given* a pre-commit hook installed from `hooks/pre-commit.sample` and a wiki page edit introducing a broken ref, *when* the user runs `git commit`, *then* the commit is blocked and a clear stderr message points to the broken ref.

**Phase 3 (`@lat:` backlinks):**

- *Given* 5 source files with `// @lat: [[entity-slug]]` comments sprinkled across `scripts/`, *when* `wiki_init.py --rebuild` is run, *then* each affected entity's `sources:` block lists the 5 code refs with line numbers and `source_type: code-backlink`.
- *Given* a 1.1-schema page after the bump to 1.2, *when* `validate_page.py` is run, *then* it raises `ValidationError` pointing to `wiki_init.py --rebuild`.

**Phase 4 (5 MCP tools):**

- *Given* a CE-indexed clone of `1st1/lat.md`, *when* `lat.locate "OAuth Flow"` is invoked via MCP, *then* the result matches what upstream `lat locate "OAuth Flow"` returns.
- *Given* the MCP server starts after Phase 4 lands, *when* `mcp_server.py --list-tools` is run, *then* it lists 14 tools (9 existing + 5 new).
- *Given* each new MCP tool is invoked, *when* a request completes, *then* `cache/usage.jsonl` contains a `tool.call` and `tool.result` event with no errors.

**Phase 5 (docs + benchmarks):**

- *Given* the new `docs/vs-lat-md.md`, *when* read by an outside engineer, *then* they can name 3 things CE does that lat.md doesn't (multi-corpus, depth-aware packing, concept-quality scoring) within 60 seconds.
- *Given* the demo `docs/benchmarks.md`, *when* an outsider runs the documented commands on cited reference repos, *then* they reproduce the published reduction numbers within ±5%.

### Success metrics

**North Star:** CE indexes the `1st1/lat.md` repository with zero broken refs, end-to-end via `lat.locate` returning identical output to upstream `lat locate`, **by Day 14 from PRD approval**.

This single test proves: (a) wikiref grammar works on real lat.md syntax, (b) code_index resolves their symbols, (c) MCP surface is compatible, (d) interop is real.

**Supporting metrics (HEART-adapted for dev tools):**

| Pillar | Metric | Target | Measurement |
|---|---|---|---|
| Engagement | CE invocations on lat.md-structured repos | 0 → ≥3 within 30 days post-Phase 5 | opt-in telemetry in `cache/usage.jsonl` (counts repo paths matching `*/lat.md/*`) |
| Adoption | Stars/PRs on agent-skills/context-engineering after Phase 5 | +20% vs prior 30-day baseline | GitHub API |
| Retention | Same users re-running CE on the same repo over 7+ day windows | ≥40% retention | telemetry |
| Task success | Gate audits pass on first run for each phase | ≥80% (failures surface real issues, fixed before merge) | gate-audit logs |

**Qualitative gates** (named humans):

- Sign-off by **Victor** on each phase's gate-audit summary before the next opens.
- Stretch: **Andre Lindenberg** or **Yury Selivanov** publicly acknowledges CE's interop within 30 days post-Phase 5 (measured via LinkedIn → yc-sus26-brain corpus).

**Rollout plan:**

| Day | Phase | Ships to |
|---|---|---|
| 1–2 | Phase 1 (wikiref) | `main` |
| 3–6 | Phase 2 (code_index + lat_check) | `main` |
| 6–9 | Phase 3 (`@lat:` + schema bump) | `main` (release notes flag the rebuild requirement) |
| 9–12 | Phase 4 (5 MCP tools) | `main` |
| 12–13 | Phase 5 (docs + benchmarks + Anabasis spec) | `main` |

Phases 2/3/4 may parallelize after Phase 1 ships; final gate audit is sequential.

**Win in one sentence:** When someone clones `1st1/lat.md`, runs CE against it, and gets the same output Cursor would get from lat.md's MCP — *plus* everything CE adds (depth packing, multi-corpus, concept quality) — interop is real and CE is repositioned.

### Alternatives considered

| Option | Why rejected |
|---|---|
| Status quo (build CE wiki layer separately, ignore lat.md grammar) | Cedes the codebase-doc segment to lat.md; market evaluates CE on lat.md's terms with no response |
| Fork lat.md, add multi-corpus + depth packing on top | License-clean (MIT) but loses CE's existing user base; doubles maintenance burden |
| Wait for lat.md adoption to plateau before committing | Distribution velocity matters — lat.md gained 1k★ in 2-3 days; "wait and see" cedes the window |
| Compete head-on with lat.md on the codebase-doc axis | Loses on distribution. The wiki-layer pattern is becoming commodity per the convergence event; differentiation is upstream (concept-quality, multi-corpus) |
| Ship feature-flag-gated lat.md grammar | Adds complexity for no benefit; user explicitly rejected (Decision #1 in plan) |
| Match Dinesh's full benchmark suite end-to-end | `value_over_proof` memory says defer baselines until usage data exists; demo run is the right surface |

### Risks

(Mapped from the plan's top 3 + product-level additions.)

| Risk | Mitigation |
|---|---|
| Schema bump cost (Phase 3): forces every existing CE brain to `wiki_init.py --rebuild` | Refusal-and-rebuild already documented (`validate_page.py:36`); release notes explicit. Bump is additive (only adds `kind: code`); no existing pages break unless they had implicit `code` kind, which none do today |
| Code-index drift / CI cost (Phase 2): repo-wide AST walk too slow on monorepos | Incremental cache keyed by `(path, mtime, sha1[:8])`; pre-commit hook only walks changed files; matches existing `index_workspace.py` pattern |
| Ambiguous symbol resolution (Phase 2 & 4): `[[src/foo.ts#process]]` matches multiple symbols | `code_index` returns list, `lat_check` flags multi-match as warning (not error), resolution prefers exported symbols (already detected by `ast_extract`); operators disambiguate via dotted path `[[src/foo.ts#Class.method]]` |
| Distribution lag — lat.md keeps shipping during these 9–13 days | Pin to lat.md's commit-sha at PRD start; document upgrade path in `docs/vs-lat-md.md`; treat lat.md updates as separate follow-on PRDs |
| Yury sees this as competitive | Cite his work explicitly, honor MIT license; reach out via Andre's introduction post-Phase 5; framing is interop, not competition |
| Dinesh's published numbers improve mid-cycle | Phase 5 demo run tests on his cited repos (Cal.com TRPC, Trigger.dev) with the same prompts; document our methodology so numbers are reproducible |

---

## Part 2 — Technical sketch

Audience: Victor + Claude execution. Capabilities, not files (the plan file has the file-level breakdown).

### Back-end capabilities

- **Link-grammar parser** that returns typed wiki-references (slug / section / code) — replaces a fragile single regex with a structured parser. Used by the auditor, the wiki consolidator, and (by Phase 4) MCP tool routing.
- **Symbol index** that maps source-file paths → exported symbols → line ranges, persisted with mtime+sha cache invalidation. Built atop the existing AST extractor for 14 languages. Used to validate code-anchor references and to resolve them to source slices.
- **Broken-reference auditor rule** that joins parsed refs against the symbol index + heading walker. Adds to the existing 3-rule auditor; output folds into the existing `audit/proposals.md` surface — no new output channel.
- **CLI exit-code wrapper** for the auditor, suitable for pre-commit hooks and CI runners. Honors `--strict` semantics: warnings vs errors distinguishable.
- **Pull-shaped Source ABC subclass** that walks the repo, regexes per-language `// @lat:` or `# @lat:` comment annotations, emits them as events into the existing append-only log. The events flow through the existing consolidator with **zero changes** — backlinks land in entity `sources:` blocks via the existing dedup-by-`(source_type, source_ref)` logic.
- **Expanded valid-kinds set** including a new `code` kind for entities sourced primarily from code-backlinks. Triggers a schema version bump (additive only).
- **5-tool MCP overlay** mapping lat.md's verb surface (`locate / section / refs / search / expand`) onto existing CE primitives. Each tool is a thin wrapper. No new infrastructure; just routing.
- **Demonstration benchmark harness** that runs CE on 2–3 reference repos and emits a single markdown table comparing token counts vs naïve full-file inclusion.

### Conceptual domain model

```
   Source files (indexed)
        │
        ├─▶ AST extractor ─────▶ Symbol index (cached)
        │                            │
        └─▶ Comment parser ──┐       │
                             ▼       ▼
                  Events log (append-only)
                             │
                             ▼
                  Wiki consolidator
                             │
                             ▼
                  Entity pages (wiki/<slug>.md)
                       ▲           │
                       │           ▼
                       │       Auditor ◀──── Wikiref parser
                       │           │
                       │           ▼
                       │   audit/proposals.md
                       │   lat_check exit code
                       │
                  MCP tools (locate, section, refs, search, expand,
                             wiki.ask, wiki.add, wiki.audit, ...)
```

### Phases (engineering-facing)

| Phase | What | Effort | Critical files | Depends on |
|---|---|---|---|---|
| 1 | wikiref parser | S, ½–1 day | `scripts/wiki/{wikiref,audit,wiki_init}.py` | — |
| 2 | code_index + lat_check + hooks | M, 3–4 days | `scripts/wiki/{code_index,lat_check,audit}.py` + `hooks/` + `.github/workflows/` | Phase 1 |
| 3 | `@lat:` backlinks + schema bump 1.1→1.2 | M, 2–3 days | `scripts/wiki/{source_adapter,validate_page,wiki_init}.py` | Phase 1 |
| 4 | 5 new MCP tools | M, 2–3 days | `scripts/mcp_server.py` + `SPEC-mcp.md` | Phase 1 |
| 5 | docs + Anabasis spec amendment | M, 2–3 days | `SKILL.md` + `docs/{vs-lat-md,vs-context-signals,benchmarks}.md` + `Repos/anabasis/spec/entity.md` | Phases 1–4 |

Phases 2/3/4 may run in parallel after Phase 1.

### Technical risks

| Risk | Mitigation |
|---|---|
| WikiRef parser performance regression on huge wikis | Benchmark before/after on demo brain; if slower, fall back to compiled regex with structured parsing in a single pass |
| code_index cache invalidation correctness on Windows (ctime quirks) | Pin to `(path, mtime, sha1[:8])`; never trust ctime alone |
| MCP tool name collision with future lat.md additions | Document namespace strategy: `lat.*` for lat.md interop, `wiki.*` for CE-native; deprecation policy in SPEC-mcp.md |
| Test fixture brittleness across phases | `scripts/wiki/demo/` is the canonical test brain; refresh-and-rebuild as part of every gate audit |
| `// @lat:` regex false positives in string literals / comments-of-comments | Test fixtures cover edge cases per language; document known-false-positive patterns in source_adapter.py |

### Open questions

1. **Comment ref form**: should the `@lat:` parser also accept lat.md's `[[section-id]]` form within annotations, or only `[[ref]]`? *Recommend: accept both, normalize internally to wikiref kind.*
2. **`code` kind summary field**: when an entity has only code-backlink sources, what should its `summary` contain? *Recommend: nearest function/class docstring extracted by ast_extract.*
3. **Telemetry integration**: how does `lat_check` integrate with `cache/usage.jsonl`? *Recommend: emit a single `audit.broken_refs` event per run with count.*
4. **Benchmark scope**: should the demo benchmarks include Granola transcripts or stay code-only? *Recommend: code-only for direct comparison vs lat.md / Context Signals; multi-corpus benchmarks come later.*

### Gate-audit protocol

Per the source plan, every phase ends with a gate audit before the next opens:

| Check | Command |
|---|---|
| Existing tests still pass | `pytest scripts/wiki/tests scripts/tests` |
| New phase tests pass | `pytest scripts/wiki/tests/test_<phase>.py` |
| `audit.py` clean on demo brain | `python scripts/wiki/audit.py --brain scripts/wiki/demo` |
| `validate_page.py` clean across all wiki pages | `python scripts/wiki/validate_page.py wiki/*.md` |
| `lat check` clean (post-Phase 2) | `python scripts/wiki/lat_check.py --brain ./brain --strict` |
| MCP server starts with expected tool count | `python scripts/mcp_server.py --list-tools` |
| Telemetry sane | `tail cache/usage.jsonl \| grep error` |
| SKILL.md skill-check passes | `npx skill-check SKILL.md` |
| Branch state clean for next phase | `rtk git status` + `rtk git ls-remote origin <branch>` |

Failure = roll back or fix before proceeding.

**Branch discipline**: one branch per phase (`feature/latmd-phase-<N>-<slug>`), squash-merge to main after gate audit passes.

---

## Appendix

### Sources

- Approved plan: [`audit-current-codebase-by-merry-treasure.md`](file:///C:/Users/victo/.claude/plans/audit-current-codebase-by-merry-treasure.md)
- Earlier proposal (now superseded): [`plan/proposals/latmd-integration.md`](./proposals/latmd-integration.md)
- Convergence event evidence in graph (yc-sus26-brain corpus):
  - `concept:agent-memory-as-graph` (q=0.974, 7 witness orgs)
  - `concept:orchestration-is-the-moat` (q=0.812, 5 witness orgs)
- lat.md: https://github.com/1st1/lat.md
- Context Signals MCP: Dinesh Raghupatruni's post 2026-05-01 (LinkedIn activity 7455576329405784064)
- Andre Lindenberg's lat.md endorsement: LinkedIn activity 7456638959578865664
- CE current state: `scripts/wiki/`, `scripts/ast_extract.py`, `scripts/mcp_server.py`, `plan/COMPASS.md`, `plan/phases/phase-{1,2}.md`

### Memory references

- `feedback_concept_first_trust` — load-bearing for any future CE/Anabasis schema work
- `feedback_submarine_mode` — local-only PRD discipline applied here
- `value_over_proof` — defer baselines, demo run only for Phase 5
- `project_ce_eval_harness` — eval harness reused for Phase 5 demo benchmarks
- `reference_knowledge_graph_memory_mcp` — runtime EntityStore where the convergence evidence lives
