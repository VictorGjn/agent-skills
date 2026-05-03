# Context Engineering vs lat.md

**Tested against [`1st1/lat.md`](https://github.com/1st1/lat.md) at the v0.X version current on 2026-05-03.** This page documents how CE relates to lat.md as of Phase 4 of the [CE × lat.md interop integration](../plan/PRD-latmd-integration.md). lat.md is a great tool with a sharp scope; CE is a different shape that interops with lat.md's conventions where they overlap.

## TL;DR

> **lat.md is "a knowledge graph for your codebase, written in markdown."** CE indexes lat.md repos out of the box, plus everything lat.md doesn't try to do (Granola transcripts, Notion DBs, Gmail labels, HubSpot notes), and adds depth-aware packing on top. Different scope, complementary tools.

## What's the same

The architectural pattern is essentially identical:

- Markdown wiki files (`lat.md/*.md` ≈ CE's `wiki/<slug>.md`)
- `[[wiki-links]]` for cross-references
- Source-to-doc backlinks via comments (`// @lat:` — CE adopted lat.md's syntax in Phase 3)
- MCP server exposing graph queries
- CI / pre-commit validation of broken refs

The thesis underneath both is the same one converging across the agent-context space — see `concept:agent-memory-as-graph` in our yc-sus26-brain corpus, currently at q=0.974 with 7 independent witnesses.

## Comparison matrix

| Axis | lat.md | Context Engineering |
|---|---|---|
| **Scope** | Codebases only | Multi-corpus: code + Granola + Notion + Gmail + HubSpot via Source ABC |
| **Link syntax** | `[[file#section]]`, `[[src/file.ts#symbol]]` | Same syntax, plus existing `[[slug]]` form (Phase 1 wikiref parser) |
| **Source backlinks** | `// @lat: [[ref]]` (TS/JS/Java/Go/Rust/...), `# @lat:` (Py/Rb) | Same syntax, same languages (Phase 3 SourceCommentBacklinkSource) |
| **Subgraph expansion** | `lat expand "fix [[OAuth Flow]]"` | `lat.expand` MCP tool (Phase 4) — BFS over wikirefs, depth-capped, budget-bounded |
| **Retrieval modes** | Semantic (with OpenAI key), fuzzy section match | Keyword + semantic + graph + RRF + lens re-ranking + 5-depth-level packing |
| **CI validation** | `lat check` | `lat_check.py --strict` + auditor 4th rule for broken refs (Phase 2) |
| **MCP tools** | locate, section, refs, search, expand | All 5 above (Phase 4) **plus** pack, index_workspace, index_github_repo, build_embeddings, resolve, stats, wiki.ask, wiki.add, wiki.audit (14 total) |
| **Depth-aware packing** | n/a (single-depth section returns) | 5 graded depths (Full / Detail / Summary / Mention) into a token budget |
| **Knowledge-type classification** | n/a | architecture / deep-dive / runbook / changelog / test, drives relevance ranking |
| **Concept-quality scoring** | n/a | Anabasis Entity ABC: specificity / falsifiability / convergence / witness dedup |
| **Distribution** | npm + GitHub release, Cursor + Claude Code adoption | MIT, `npx skills add`, MCP-ready |
| **License** | MIT | MIT |

## Where lat.md is the right call

- **Single-codebase docs.** lat.md is purpose-built for this and ships polished. If your only use case is "annotate a codebase with cross-referenced markdown notes," lat.md gives you that with less surface area.
- **Distribution.** lat.md hit 1k★ in days. Network effects are real.
- **Authoritative `// @lat:` annotations.** Developer-tagged backlinks are more reliable than CE's AST-extracted heuristics. CE supports both; lat.md is annotation-first by design.

## Where CE is the right call

- **Multi-corpus.** If you want the same tool to retrieve across your codebase AND your Granola transcripts AND your Notion DB AND your Gmail label, CE is the only option. lat.md doesn't try.
- **Token-budget retrieval.** When you need to pack 40+ files into an LLM context window at varying depths instead of returning one section, CE's packer is the difference.
- **Concept-quality layer.** When you need to know not just *what's in the graph* but *which concepts are true* (specificity, falsifiability, convergence), the Anabasis Entity ABC adds a layer lat.md doesn't have.
- **Long-running compounding brain.** lat.md is a static format; CE has a synthesizer + auditor that consolidates events into wiki entities on semantic shift, schedules consolidation runs, and detects drift / contradictions over time.

## Interop — drop CE on a lat.md repo

```bash
# Clone lat.md or your own lat.md-structured repo
git clone https://github.com/1st1/lat.md /tmp/lat.md

# Install CE (or `pip install -e .` from the agent-skills checkout)
pip install context-engineering

# Build a code index from the repo
python -m scripts.wiki.code_index /tmp/lat.md --cache /tmp/code-index.json

# Run lat-style queries via CE's MCP
python -m scripts.mcp_server  # starts stdio server
# In another shell, an MCP client calls:
#   lat.locate(ref="src/auth.ts#validateToken")  -> identical result to lat locate
#   lat.section(ref="auth-middleware#OAuth Flow") -> heading-bounded slice
#   lat.refs(target="auth-middleware")            -> reverse index
```

CE's `lat.locate` returns the same shape lat.md's CLI does for code refs; semantic differences land in the multi-corpus and depth-packer surfaces (`pack`, `wiki.ask`).

## Pinning

This document references lat.md as of commit-sha **TBD** (pin the commit you tested against once you run the integration check). Re-validate manually on lat.md major version bumps. CE does **not** auto-track lat.md spec changes — additive changes are absorbed when needed.

## Acknowledgements

[lat.md](https://github.com/1st1/lat.md) by **Yury Selivanov** (also of [uvloop](https://github.com/MagicStack/uvloop) and [EdgeDB](https://github.com/edgedb/edgedb)) ships under MIT. CE's wikiref parser, comment-backlink syntax, and `lat.*` MCP tools deliberately match lat.md's surface so users never have to choose between them — the two tools should compose, not compete. lat.md was announced on LinkedIn by [Andre Lindenberg](https://linkedin.com/in/alindnbrg) on 2026-05-03 and validated the agent-memory-as-graph thesis at the codebase-doc layer.
