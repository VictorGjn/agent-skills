# Context-Engineering Roadmap (v3 — scope-corrected)

> **Goal**: Make `context-engineering` the go-to skill for **token-efficient codebase context engineering**. Index a repo, pack the most relevant N files at the right depth into the LLM's window, compound the *code-derived* entity graph over time so the next query lands faster.
>
> **Scope (decided 2026-04-28)**: This skill is for **codebases**, not company-wide knowledge. Ingestion of meeting notes, opportunity briefs, product signals, CRM data, etc. → that's `syroco-product-ops` routines, not this skill. The Karpathy LLM-wiki and GAM patterns adopted here apply *within* the code domain — entity pages are concepts/components/decisions extracted FROM the repo (source code + repo docs + ADRs), not from external sources.
>
> **Value over proof**: Active phases focus on **delivering compounding value** for code understanding, not on **empirically proving** the system. All baselines, ablations, public benchmarks moved to a **Deferred — Proving Layer** appendix.

---

## 0. Karpathy's LLM Wiki vs. context-engineering today

| Dimension | Karpathy LLM Wiki (Apr 2026) | context-engineering today | Gap |
|---|---|---|---|
| Primary mode | Build & maintain a persistent KB | Pack files into a context window per query | Synthesis layer missing |
| Storage | `raw/` sources + `wiki/` compiled entity pages | `cache/*.json` indexes (3 incompatible schemas, no version) | No concept-level pages, no schema discipline |
| Unit of knowledge | One entity / concept per markdown page | One file (chunked at 5 depth levels for *render*, not retrieval) | File ≠ concept; chunking is render fidelity |
| Graph | Hand-densified `[[wiki-links]]` between concepts | 17 AST/import relations weighted, file→file (and 59% silently dropped in cached graph) | File-level, not idea-level, currently corrupted |
| Statefulness | Compounds — new source updates existing pages | Each `pack` resolves against indexes that aren't enriched, just rebuilt | No compounding |
| Contradiction handling | Flagged inline between sources | None | Missing |
| Self-healing | Periodic lint / audit | None | Missing |
| Human view | Obsidian graph view | Markdown packed output (read once, discard) | No browsable artefact |
| AI view | Same wiki pages used as dense context | Depth-packed file slices | Concept-page packing missing |
| Agent role | Active compiler ("machines synthesise; humans write") | Passive retriever | Agent doesn't write back |

**Bottom line**: today the skill is excellent at *finding and compressing* what already exists — when it actually runs. It is not yet *creating, linking, or auditing* a stable artefact that compounds.

---

## 0.5. Architecture refinement after GAM + Animesh Kumar (2026-04-28)

Two external inputs sharpened the wiki design materially. Captured here so Phase 1 lands on the *right* schema, not the easy one.

### GAM — Hierarchical Graph-based Agentic Memory (Wu et al, arXiv:2604.12285, April 2026)

Core idea: **decouple memory encoding from consolidation.** GAM keeps two graph layers:

- **Event Progression Graph** — sequential, append-only, captures dialogue / signals as they arrive. Cheap. Never rewritten.
- **Topic Associative Network** — consolidated entities, *only* updated when a **semantic shift** is detected (cosine distance, theme break, explicit trigger).

Result: long context is preserved without thrashing the entity graph on every new source.

**Mapped to context-engineering**: the original plan had `raw/` (sources) + `wiki/` (entities). GAM tells us we want *three* tiers:

```
raw/      — verbatim sources (PDFs, transcripts, code snapshots)
events/   — append-only JSONL log: extracted claims, observations, signals
wiki/     — consolidated entity pages, refreshed only on semantic shifts
```

The synthesizer doesn't run on every source addition. It runs when the events queue has accumulated enough drift against an entity page (cosine threshold) OR on `wiki audit`. This keeps Phase 2 cheap by default and avoids the "rewrite-on-every-write" failure mode I was about to bake in.

### Animesh Kumar — "Data changes with context" (LinkedIn, April 2026)

Core idea: **knowledge graphs as dynamic context engines.** Five shifts:
1. From assets to connections (entities + relationships + context, not isolated docs)
2. Reasoning over retrieval (multi-hop insights, not lookups)
3. Trust + governance (provenance, explainability, contradiction handling)
4. Break silos (unified source of truth)
5. AI-ready (decision intelligence, not just data)

**Mapped to context-engineering**: three concrete additions to Phase 1/3:

- **Multi-hop retrieval as a first-class mode** (`pack --multi-hop 2`): currently `--graph` does 1-hop. Reasoning queries need 2–3 hops with a path trace.
- **Provenance at every level**: every wiki entity page MUST list `sources[]` with file:line refs + commit SHAs (or content hash for non-git sources). Every contradicting claim flagged inline with the other source's URI.
- **Query-as-lens**: the active query re-ranks neighbours of the entity page, not just the entity itself. The same `auth-middleware.md` page surfaces different `[[See also]]` priorities depending on whether the query is "401 bug" vs "token rotation policy" vs "GDPR retention". Lens = query embedding + nearest top-K neighbours' relations.

### What changes in the implementation

| Was | Becomes |
|---|---|
| `raw/` + `wiki/` | `raw/` + `events/` (NEW) + `wiki/` |
| Synthesizer runs on every `wiki add` | Synthesizer runs on **semantic-shift trigger** (cosine drift threshold OR explicit) |
| `pack --graph` (1-hop) | `pack --graph` (1-hop) **+ `pack --multi-hop N`** (path-traced N-hop) |
| Wiki pages list sources by name | Wiki pages list `sources[]` with **file:line + content_hash + ts** |
| Static `[[wiki-links]]` | Static base + **query-aware lens re-ranking** at pack time |

These are not new phases — they are refinements to Phase 1, 2, and 3 that keep the same shipping order.

---

## 1. End-state vision (unchanged from v1)

```
sources (PDFs, repos, transcripts, signals)
        │
        ▼  ── orchestrated agents ──────────────────────────────────┐
   raw/                                                              │
   cache/  (versioned schema, atomic writes, incremental)            │
   wiki/   (entity pages, dense [[links]], contradictions)           │
   audit/  (lint reports, densification metrics, episodic log)       │
        │                                                            │
        ├──► HUMAN VIEW: Obsidian vault — graph, backlinks, search   │
        │                                                            │
        └──► AI VIEW: pack reads wiki/ first, falls back to          │
                      cache/ + raw/ ;  MCP exposes both.             │
                                                                     │
   compounding triggers (on-add, scheduled, cross-source) ◄──────────┘
```

The packer **stays**. The wiki **wraps** it. Same `pack` command, but the answer stitches from concept pages first, file slices second, raw sources last.

---

## 2. Phased roadmap (re-ordered after reviews)

### Phase 0 — Truth-Up *(NEW, blocking everything)*

**What**: Make the skill match its own README before adding anything.

| Deliverable | Why | Source finding |
|---|---|---|
| Vendor or write the 5 missing modules: `pack_context_lib.py`, `code_graph.py`, `index_workspace.py`, `mcp_server.py`, `embeddingResolver.ts` (or strip references) | `pack_context.py:32, :98` ImportError on first call. CLI is non-runnable today. | backend, AI eng, infra, data sci all flagged independently |
| `requirements.txt` + `pyproject.toml` with pinned versions (`tree-sitter-languages==1.10.2`, `requests`, `mcp[cli]`) | Install instructions are scattered `pip install` lines in SKILL.md; pinned versions live inside a docstring | infra |
| Move `cache/` out of skill dir — resolve to `${XDG_CACHE_HOME:-~/.cache}/context-engineering/<repo-hash>/`, accept `--cache-dir` | Today every workspace using the skill writes into the user's global skills folder; collisions guaranteed | infra, backend |
| Add `schema_version` to every JSON artefact + refuse-to-load on mismatch | 3 incompatible schemas in one cache dir; `embed_resolve.py:208` already silently papers over divergence | data eng |
| Atomic writes (`tmp + os.replace`) + file lock around cache writes | `embed_resolve.py:177`, `index_github_repo.py:406` do bare `open(w)` → concurrent runs corrupt JSON | data eng |
| Fix the 12,175 → 5,000 relations truncation in `claude-code-index.json` (raise cap or document + warn) | **Verified bug**: every `--graph` query on this cache silently traverses a 59%-truncated graph | data eng (verified) |
| Stable `file_id` (hash of `source_ref + path`), `content_hash`, `errors[]`/`skipped[]` per index | Bare-filename joins break in monorepos; silent garbage-in failures | data eng, backend |
| MCP HTTP defaults: bind `127.0.0.1`, `--bind 0.0.0.0` to expose, `--auth` reads `CONTEXT_ENG_TOKEN` env, reject >1MB requests, JSON stderr logs | `--http`/`--auth` are advertised with zero hardening detail | infra |
| Replace 0.6/0.4 linear fusion with RRF (Reciprocal Rank Fusion) | Parameter-free, removes a tuning chore — better default out of the box | AI eng |
| Soften the SKILL.md headline: replace "100% recall at 1% of repo" with concrete value language ("12 files at 8K vs 2 files at 8K") | Headline relies on a flattering metric definition; quietly stop leading with it | AI eng, data sci, AI PM |

**Cut from Phase 0** (moved to Deferred — Proving Layer): BM25/naive-embedding baselines, ablation matrix, Weighted-Recall-as-headline framing, magic-number calibration. None of these unblock value delivery; all of them are useful when usage data exists to measure against.

**Effort**: M (~1 week, lighter than v2). **Ships**: a skill that runs, doesn't silently corrupt or mis-answer, and has a defensible MCP server.

### Phase 0.5 — Surface Collapse + Telemetry *(NEW)*

**What**: Make the skill usable in 10 seconds and instrument from day 1.

| Deliverable | Notes | Source |
|---|---|---|
| One verb: `pack "query"` — auto-indexes on first run, auto-picks mode by query shape (proper-noun → graph; question → semantic; keyword → keyword), degrades silently without `OPENAI_API_KEY` | TTV today is 4 commands + a config decision | AI PM |
| Three user-facing flags only: `--budget`, `--mode auto\|deep\|wide`, `--task`. Hide depths/relations/knowledge-types behind defaults. | 4×6×5×17×6 config matrix is unnavigable | AI PM |
| `--why` flag showing query → mode → entry files → traversed → 95% budget filled | The trace is the demo | AI PM |
| Per-call telemetry to `cache/usage.jsonl`: `(query, mode, files_packed, budget_used%, time_ms, ts)` | Without this, "compounding" is a story not a measurement | CPO, AI PM |
| Activation metric instrumented: % new users who run `pack` ≥3× in 7 days AND ≥80% budget once | Defines what "good" looks like | AI PM |
| Promote anti-hallucination filters to headline ("Off-topic guard"); make depth-aware packing the lead ("12 files at 8K vs 2 files at 8K") | Real differentiators buried mid-SKILL.md | AI PM |
| Slash commands: `/pack`, `/pack-why` shipped with this phase (not Phase 5) | Habit-driver inside Claude Code | AI PM |

**Effort**: S (3–5 days). **Ships**: a skill anyone can try in one command and where you can measure whether they came back.

### Phase 1 — Three-tier wiki layer (storage + schema, GAM-aligned)

**What**: Add `events/` + `wiki/` + `audit/` next to `cache/`. Three-tier per GAM. Define entity-page schema with full provenance per Animesh. Reuse cleaned-up index from Phase 0.

| Deliverable | Notes |
|---|---|
| `events/<YYYY-MM-DD>.jsonl` | Append-only event log: `{ts, source_type, source_ref, file_id, claim, embedding, entity_hint?}` — every extracted claim from a source becomes one line. Cheap, never rewritten. (GAM: event progression graph) |
| `wiki/<slug>.md` schema | YAML frontmatter (`id` immutable, `kind`, `sources[{type, ref, line, hash, ts}]`, `confidence`, `updated`, `links_in/out`, `centroid_embedding`) + body + `## See also` with `[[wiki-links]]` + `## Provenance` listing all source citations. Slug is readable; `id` is stable. |
| `scripts/wiki/semantic_shift.py` | Detector: returns `True` when avg cosine distance of recent unconsolidated events for an entity exceeds threshold (default 0.35) OR when ≥N events accumulated (default 8). Trigger for the synthesizer. |
| `scripts/wiki/wiki_init.py` | One-shot: cluster current cache index → seed entity pages with events + initial citations |
| `scripts/wiki/source_adapter.py` | `Source` ABC: `list_artifacts() → [ref]`, `fetch(ref) → bytes`, `metadata(ref) → dict`. Concrete: `WorkspaceSource`, `GithubRepoSource` only. Notion/Granola/Gmail/PDF adapters are explicitly out of scope (live in `syroco-product-ops` routines instead). |
| `audit/log.jsonl` | Append-only episodic log (who/what/when changed); separate from events/ |
| `audit/proposals.md` | Auditor's split/merge/contradiction proposals — human accepts or rejects via plain markdown edit |
| `wiki/_index.md` | Auto-maintained list of entity pages, one-line each, sortable by `updated` desc |
| `wiki/_contradictions.md` | Cross-page contradictions; each entry is `[[entity-A]] vs [[entity-B]]: <claim diff>` |

**Why first**: GAM says storage shape *plus the consolidation trigger* decide everything. Get this right and synthesis (Phase 2) becomes mechanical.

### Phase 2 — Code-knowledge synthesis (Linker + Auditor)

**What**: Make the code-knowledge wiki self-building. *In scope*: the synthesizer reads `events/` (extracted from re-indexed source files), updates entity pages on semantic shift; the Linker densifies `[[wiki-links]]` between entity pages based on import/test/doc relations; the Auditor lints and proposes splits/merges.

**Out of scope** (intentionally — handled by `syroco-product-ops` routines): cross-skill producers writing to this wiki, Notion/Granola/Gmail ingestion, opportunity/PRD/signal pages.

```
   indexer (existing) → events/ → semantic-shift detector → synthesizer
                                                                  ↓
                              wiki/ entity pages ← linker (densify [[links]])
                                                                  ↓
                                                              auditor (lint)
```

| Component | Job |
|---|---|
| Indexer | AST + embed re-indexed files (already shipped) |
| Event extractor | For each indexed file: emit one event per significant heading / exported symbol / class to `events/<date>.jsonl` |
| Synthesizer | When semantic_shift fires: rewrite the affected entity page (concept/component/decision extracted from the code) |
| Linker | Densify `[[wiki-links]]` based on import/test/doc edges in code_graph |
| Auditor | Lint pages, propose splits when entity drifts into multi-concept territory, prune dead links |

Triggers: on `index_workspace` re-run, scheduled (nightly), or explicit `wiki audit`.

**Effort**: M (~1 week). Lighter than the original "multi-agent + cross-skill" plan because we only consume one source-type (the code repo).

### Phase 3 — Dual surfaces + bug-fix demo + Cursor distribution

**What**: Ship `--wiki` mode AND meet users where they are.

**Human view (Obsidian-compatible)**

- `wiki/` is plain markdown with `[[wiki-links]]` and YAML frontmatter — open in Obsidian, get graph view, backlinks, search for free
- `wiki/_dataview.md` (optional) — Dataview queries for "recently updated", "low-confidence", "orphan pages", "contradiction queue"

**AI view (packed retrieval)**

- `pack` gets `--wiki`: resolves against entity pages first (semantic + graph over `[[links]]`), pulls those pages full-depth, expands to source `cache/` for evidence at lower depth. 5-depth packing still applies — concept page is "Full", source slices demote.
- `pack --multi-hop N` (Animesh): traces N-hop reasoning paths through `[[wiki-links]]`, returns each step with the relation kind. Output includes a `## Reasoning path` block: `auth-middleware → token-store → session-policy → 2026-Q1-compliance-ADR`.
- **Query-as-lens** re-ranking: top-K entity neighbours are re-scored against the live query embedding before being pulled, so the same entity page surfaces different evidence under different lenses ("401 bug" lens vs "GDPR retention" lens).
- MCP tools: `wiki.ask`, `wiki.add`, `wiki.audit`, `wiki.export` (extends Phase 0's MCP server)

**Distribution**

- **Bug-fix demo** as hero use case (per AI PM): stack-trace → packed-context → fix sequence in 30 seconds. Lead README, demo, tweet thread with this.
- **Cursor `.cursorrules` snippet** + **hosted MCP endpoint** within 2 weeks of v1 — Cursor is the bigger TAM and they have the same JTBD

**Cut from Phase 3** (moved to Deferred — Proving Layer): public benchmark vs Cursor `@codebase` and Cody. Useful when there's a story to defend; today the value demo (bug-fix in 30 sec) carries more weight than a benchmark table.

**Effort**: M (1–2 weeks).

### Phase 4 — Compounding metrics & self-healing *(DEFERRED)*

**What**: Densification metrics, contradiction NLI, Auditor lint loop with human-in-the-loop accept/reject.

**Why deferred**: per AI PM, measuring densification on a wiki of 8 pages is theater. Wait until ≥3 active wikis with real usage. Per-call usage telemetry (Phase 0.5) and Auditor lint warnings (Phase 2) are enough until then.

Trigger to pull this back in: ≥3 wikis with ≥100 pages each AND telemetry showing actual repeat-query patterns to measure densification against.

### Phase 5 — Distribution (scope-corrected)

**What**: Get adoption among AI-native devs who are tired of `@file`-ing their way around an unfamiliar repo.

| Deliverable | Notes |
|---|---|
| Public essay: **"10 patterns Claude Code uses to manage context"** (off `claw-code-context-engineering-analysis.md`) | Cheapest distribution lever; funnels to the code-context use case |
| 60-second screen-rec: **stack trace → `pack` → fixed bug** in an unfamiliar repo | The bug-fix demo (per AI PM review) — concrete, repeatable, hard to fake |
| MCP server listed in Anthropic + Smithery directories | Cross-harness distribution (Cursor, Cline, Zed) |
| Hosted MCP endpoint + `.cursorrules` snippet | Cursor users have the same JTBD; meet them where they are |

**Effort**: S (3–5 days for essay + demo, days-to-weeks for directory acceptance).

---

## 3. Killer demo (Phase 3, locked in)

```
$ cd ~/some-unfamiliar-repo
$ pack "users are getting 401 on refresh tokens" --why
> mode=semantic+graph (auto)
> 14 files, 7,612/8,000 tok
> entry: auth/refresh.ts → traversed: jwt.ts, session.ts, middleware.ts, ...
> 6 files at Full, 5 at Detail, 3 at Mention
> off-topic guard: 2 files filtered (cosine 0.31, 0.29)
> [packed markdown follows]

→ paste into Claude → one-shot fix
```

Tagline: **"Stop letting your agent read 2 files when it could read 14."**

---

## 4. Why this beats the original roadmap

| Original roadmap (v1) | Revised (v2 post-review) |
|---|---|
| Started with new wiki layer | Starts with **truth-up** — the wiki cannot sit on top of fiction |
| Treated benchmarks as proven | Treats benchmarks as **suspect until baselines + ablations exist** |
| Cross-skill producers in Phase 5 | **Phase 2 — the moat ships with the engine** (CPO) |
| Densification metrics in Phase 4 | Densification metrics **deferred until ≥3 wikis** (AI PM); per-call **usage** telemetry day 1 |
| Distribution as final polish | Distribution essay + 60-sec demo as **P0 marketing during the land-grab window** (CPO) |
| 17 relations / 6 task presets / 4 modes visible to users | Collapsed to **`pack` + 3 flags**; complexity behind smart defaults (AI PM) |
| Headline: "depth packing" | Headline: **"OSS reference impl of Karpathy's LLM wiki"** (CPO) |

---

## 5. Sequencing & effort

| Step | Phase | Effort | Ships |
|---|---|---|---|
| 1 | **0 — Truth-Up** | M | Skill that actually runs + reproducible benchmarks + defensible MCP |
| 2 | **0.5 — Surface Collapse + Telemetry** | S | One-verb UX, usage data starts flowing |
| 3 | **1 — Wiki schema + `Source` ABC** | S | `wiki/` populated from cleaned cache |
| 4 | **3a (with Phase 1) — `pack --wiki` mode** | S | Immediate retrieval win on the new layer |
| 5 | **2 — Coordinator + Indexer + Synthesizer** | M | `wiki add` works end-to-end on one source |
| 6 | **2 — Linker + Auditor + cross-skill producers** | M | Densification + cross-skill feedstock |
| 7 | **5 — Essay + 60-sec demo + MCP directories** | S | Land-grab content lever active |
| 8 | **3b — Cursor `.cursorrules` + public benchmark** | M | Cross-harness distribution |
| 9 | **4 — Densification metrics, contradiction NLI** | M | Only when ≥3 active wikis exist |

S = ~½ day, M = ~1–2 weeks. Phases 0 + 0.5 + 1 + 3a together = the v1.0 ship.

---

## 6. Open questions to settle before Phase 0 starts

1. **Bootstrap corpus**: code repo (Syroco) vs product knowledge (`company-knowledge`) vs personal research? Each implies different first 1000 users. CPO picks AI engineers + Obsidian users → favours `company-knowledge`-style content over a private code repo. **Recommend**: company-knowledge as the showcase wiki, with a code-repo wiki as the "dogfood" example in the README.
2. **Naming**: keep `context-engineering` (precise, accurate) or rename to `repo-context` (concrete, googleable, names the artifact, per AI PM)? Tradeoff: link equity vs adoption. **Recommend**: defer to user. If chosen now, do it before any public essay (Phase 5).
3. **Slug strategy**: human-readable (`auth-middleware.md`) or stable IDs (`ent_a4f3.md`)? Hybrid resolved: readable filename, immutable `id` in frontmatter.
4. **Conflict policy**: keep both contradicting sources with `<!-- contradicts: src-X -->` markers, or auto-resolve by Knowledge Type priority + flag? **Recommend**: keep both with markers (Karpathy's own approach); auto-resolve looks confident but is brittle.
5. **Wiki location**: per-project (`<project>/wiki/`), global (`~/.claude/wiki/`), or its own versioned repo? **Recommend**: per-project default with optional global merge view; versioned repos for showcase wikis.
6. **Relations cap (the 12k→5k bug)**: raise the cap, paginate the file, or compress? **Recommend**: raise to a configurable `MAX_RELATIONS` (default 50k), emit warning when truncated, log to `audit/`.

---

## 7. Non-goals

- Not building a new graph DB. Markdown + frontmatter + `[[links]]` is the graph. Obsidian / a tiny Python parser reads it.
- Not replacing the packer. The packer is the retrieval engine; the wiki is its long-term memory.
- Not a chat product. Wiki is the artefact; agents and humans both read it.
- Not chasing enterprise. AI-native solo builders + staff engs who already use Obsidian are the audience. Enterprise wants Sourcegraph + SOC2 — different game.
- Not contorting the skill for maritime/Syroco use cases. Treat Syroco angle as recruiting + credibility halo (CPO).

---

## 8. Deferred — Proving Layer

These items are real and the personas were right to flag them. They are deferred because they prove the system works, rather than make it work for users. Pull them back when *any* of the following triggers fire:

- A public claim is challenged (someone tweets "your benchmark is misleading")
- Usage telemetry shows ≥100 weekly active users — at that scale, defensible numbers matter for retention/PR
- A second contributor needs to tune the system and asks "what's good?" — they need ablation data to make calls
- A target user (Karpathy-adjacent / AI engineer audience) explicitly asks for the comparison

| Deferred item | Source | Cost when needed |
|---|---|---|
| Add BM25 (`rank_bm25`) + naive-embedding top-K + full-repo-truncated **baselines** to the eval | AI eng, data sci | S (1–2 days) |
| **Ablation matrix** (keyword only / +semantic / +graph / +KT-bonus) per query×budget | data sci | S (1 day to instrument, slow to run) |
| **Lead docs with Weighted Recall + Critical Hit Rate**, demote bare Recall to a footnote | data sci | S (doc edit) |
| Hold-out **test set on a Syroco repo** (efficientship-backend or modular-patchbay HEAD), authored blind, single-run reporting with bootstrap CIs | data sci | M (build queries + ground truth) |
| **Public benchmark** vs Cursor `@codebase` and Cody on a fixed repo+question set | CPO, AI PM | M (build harness + write-up) |
| **Calibrate magic numbers**: 0.6/0.4 fusion (already swapped for RRF in P0), 0.5 cosine + 25% term overlap topic filter, 17 graph weights, 6 task presets, depth ratios | AI eng, data sci | M (instrument, sweep, report) |
| **Embed AST chunks instead of file identities** (function/class level via existing `ast_extract.py`) + cross-encoder rerank (`bge-reranker-base`) | AI eng | M (real retrieval-quality lift, but invisible to users until usage exposes failure modes) |
| Approximate-NN (FAISS / HNSW) for semantic mode | AI eng | S, only relevant past ~10k files indexed |
| `_get_docstring` off-by-one fix + unit tests | backend | S |
| GitHub indexer concurrency + ETag/If-None-Match | backend | S |
| Path-traversal / symlink / control-char sanitisation | backend | S |
| Tree-sitter fallback expanded to Go/Java/Ruby (currently silent for 11 of 14 advertised languages) | backend | S |
| Eval-script duplication consolidated into shared `parsing.py` | backend | S |

**The principle**: Phase 0 is "stop wrong answers / make it run." Phase 0.5 is "make people *want* to run it." Phases 1–3 are "make it compound." Proving comes after value lands, not before.

## 9. Kill criteria (from CPO review)

Shut this bet down at 6-month review if any of:

- **Anthropic ships native Claude Code persistent project memory** with markdown + graph export and adoption is below 500 weekly active users
- After 6 months, **average outlinks per page is flat or declining** on the showcase wiki — the compounding thesis is empirically false
- **No external installs** from Karpathy-adjacent or AI-engineering audience by month 4 despite the essay + demo — the narrative wedge didn't land
