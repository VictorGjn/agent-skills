# lat.md integration — CE proposal

**Date:** 2026-05-03
**Status:** Proposal / not approved
**Author:** Claude session (under Victor's direction)
**Trigger:** Yury Selivanov shipped [lat.md](https://github.com/1st1/lat.md) (1k★, MIT, MCP-ready). Andre Lindenberg endorsed it on LinkedIn the same day. Same architectural pattern as CE Phase 2 wiki, shipped first.

## Why this matters

CE Phase 2 wiki and lat.md converged on the same architecture independently:
graph-structured agent context, markdown wiki files, `[[wiki-links]]`,
source-doc backlinks, MCP-exposed retrieval. lat.md commoditized the
*pattern* for codebases. Three implications:

1. **The structural pattern is becoming consensus.** Six independent witnesses now articulate `agent-memory-as-graph` (Andre, Shubham, GAM paper, Syroco, Yury via lat.md, Andre's lat.md endorsement post). Score in our brain corpus: `q=0.974`. This is no longer differentiation; it's the floor.
2. **CE's positioning needs to shift.** Don't pitch CE as "the graph format." That war's over. Pitch CE as **multi-corpus + depth-aware packing + concept-quality layer** — the things lat.md doesn't have.
3. **Interop is the cheap win.** lat.md is becoming the convention for codebase docs. CE should *consume* lat.md-structured codebases as a corpus and *emit* lat.md-compatible output where useful. Free distribution wedge — every lat.md user becomes a CE candidate.

## What lat.md has that CE doesn't (and should adopt)

| Feature | lat.md | CE Phase 2 plan | Recommendation |
|---|---|---|---|
| Explicit source backlinks (`// @lat:`) | Yes — developer-tagged | No — AST extraction only | **Add.** Support both. AST stays as the default; explicit comments override and improve precision. Phase 2 schema update. |
| CI validation (`lat check`) | Yes — broken refs fail commit | Roadmap (audit/log.jsonl) | **Add.** Port `lat check` semantics to a CE skill. Pre-commit hook. Phase 2.5 deliverable. |
| Standardized backlink syntax (`[[file#section#sub]]`, `[[src/file.ts#symbol]]`) | Yes | Custom `[[wiki-link]]` | **Adopt their syntax** for codebase corpora. Same syntax = same tooling = free editor support. |
| Distribution + adoption | 1k★ in days, Cursor + Claude Code adoption | None yet | **Lean on convergence.** When CE Phase 2 ships, position as "CE indexes lat.md repos out of the box, plus everything lat.md doesn't (Granola, Notion, Gmail, …)." |

## What CE has that lat.md doesn't (lean into these)

1. **Multi-corpus.** lat.md is codebase-only. CE's Source ABC handles code, markdown, Granola, Notion DBs, Gmail, HubSpot. This is the universal-context generalization lat.md isn't trying to be.
2. **Depth-aware packing.** lat.md returns sections (one depth). CE fits 40+ files into a token budget at 5 graded depths (Full / Detail / Summary / Mini / Headline). Different problem.
3. **Knowledge-type classification.** CE's `knowledge_type` field (architecture / deep-dive / runbook / changelog / test) drives relevance ranking. lat.md doesn't classify.
4. **Concept-quality layer (Anabasis Entity ABC).** lat.md tells you *what's in the graph*. Anabasis tells you *which concepts are true* (specificity, falsifiability, convergence, witness dedup). This is the durable moat — see `concept-first-trust` memory.
5. **Embedding + RRF + lens re-ranking.** CE's retrieval stack is a layer above lat.md's "embedding search if you wire an OpenAI key."

## Proposed CE roadmap impact

### Phase 2 (already planned — wiki layer)

**Updates to existing plan:**

- **2.1 wiki schema** — adopt lat.md's `[[file#section#sub]]` and `[[src/file.ts#symbol]]` link syntax for codebase wikis. For non-code corpora (Granola, Notion), keep `[[concept-id]]` style.
- **2.2 source_adapter.py** — extend to recognize `// @lat:` / `# @lat:` comments in source files. Treat as explicit backlinks; merge with AST-extracted symbols (explicit wins on conflict).
- **2.3 Source ABC** — add `LatMdSource` concrete subclass that reads `lat.md/*.md` files directly as wiki entities (no extraction needed; they're already wiki-shaped). Living next to `WorkspaceSource` and `GithubRepoSource`.

**Critical files:**
- `agent-skills/context-engineering/scripts/wiki/source_adapter.py` (new — extend)
- `agent-skills/context-engineering/scripts/wiki/sources/lat_md_source.py` (new)
- `agent-skills/context-engineering/scripts/wiki/wiki_init.py` (update — emit lat.md-compatible link syntax for code corpora)

**Verify:**
- `pack --wiki "OAuth Flow"` over a lat.md-structured repo returns the expected wiki entity + linked symbols at full depth, source files at demoted depth.
- Round-trip: a lat.md repo indexed by CE produces wiki entities byte-identical to the lat.md/*.md source files.

### Phase 2.5 (new — CI validation)

**Goal:** port `lat check` semantics to CE so wiki-aware repos catch broken references at commit time.

- **2.5.1** — `ce check` CLI subcommand. Validates:
  - All `[[wiki-links]]` resolve to existing wiki entities (or external refs).
  - All `[[src/file#symbol]]` references resolve to actual AST symbols.
  - Bidirectional sync: `// @lat:` annotations in source point to existing wiki sections.
  - Optional `require-code-mention: true` per wiki section: flags if no source file annotates back.
- **2.5.2** — pre-commit hook template. `agent-skills/context-engineering/hooks/pre-commit.sample`.
- **2.5.3** — GitHub Action template. `.github/workflows/ce-check.yml.sample` for end users to drop in.

**Critical files:**
- `agent-skills/context-engineering/scripts/check.py` (new)
- `agent-skills/context-engineering/hooks/pre-commit.sample` (new)
- `agent-skills/context-engineering/SKILL.md` (update — document `ce check`)

### Phase 3 (already planned — `pack --wiki`)

**Updates:**
- **3.1 MCP tool naming** — align with lat.md where it doesn't conflict. lat.md exposes `locate`, `section`, `refs`, `search`, `expand`. CE exposes `pack`, `index_workspace`, `resolve`, `stats`. Consider adding `wiki.locate`, `wiki.section`, `wiki.refs` aliases to `wiki.ask` for migration ease.

### Phase 4 (new — repositioning)

**Goal:** when Phase 2 ships, public framing leans on what CE *uniquely* does.

- **README headline (proposed):** *"Multi-corpus context engineering. Indexes your codebase (lat.md-compatible), Granola transcripts, Notion DBs, Gmail labels, HubSpot notes. Depth-aware packing. Concept-quality scoring."*
- **Comparison page** in docs/: `docs/vs-lat-md.md` — honest matrix of overlap and divergence. Cite Yury's work; don't compete on the structural axis.
- **Anabasis spec amendment:** add `LatMdEntityStore` as a reference EntityStore implementation for codebase corpora. Like a backend driver. Codifies interop.

## Summary table — work units

| Phase | New work | Existing plan changes | Effort |
|---|---|---|---|
| 2 | `LatMdSource` adapter | Wiki link syntax, `// @lat:` recognition | 2-3 days |
| 2.5 | `ce check` + hooks | (new phase) | 3-4 days |
| 3 | MCP tool aliases | None functional | 1 day |
| 4 | Comparison docs, repositioning | README rewrite | 1 day |

Total: ~8 days of work. Doesn't shift any existing critical paths.

## Risks

- **Naming collision** — if lat.md decides to support multi-corpus later, our differentiation narrows. Mitigation: ship Phase 4 framing soon, build distribution before they pivot.
- **Yury Selivanov pivots lat.md to be runtime-y** — would invalidate the "lat.md is just the format, CE is the engine" framing. Mitigation: Anabasis spec is independent; runtime moat lives there.
- **The `// @lat:` comment style spreads as the standard** — locks our hand. Mitigation: adopt it now, contribute upstream if/when CE has improvements.

## Decision needed

Sign-off on:

1. Phase 2 schema update to adopt lat.md link syntax for code corpora.
2. New Phase 2.5 (`ce check` + hooks).
3. Phase 4 (repositioning + comparison docs).

If approved, draft these phases as proper PHASE.md files under `plan/phases/` and ultraplan each before implementation.
