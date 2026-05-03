# Context Engineering vs Context Signals MCP

**Tested against [Context Signals MCP](https://github.com/dineshraghupatruni/context-signals-mcp) (npm) shipped 2026-05-01.** This page documents how CE relates to Dinesh Raghupatruni's "Context Signals MCP" tool. The thesis — *"agents are limited by navigation, not reasoning"* — is identical; the layer Dinesh ships is one slice of what CE does.

## TL;DR

> **Context Signals MCP packages "the symbol map" as a single-purpose tool with published 79–95% context reduction.** CE's `ast_extract.py` + `code_index.py` produce the same map (14 languages via tree-sitter), then add depth-aware packing, multi-corpus indexing, and a wiki/concept layer on top. Same underlying primitive; different scope.

## What Dinesh ships

Per the public LinkedIn post + npm package:

- Extracts a **reusable map** of functions, routes, classes, imports, and file paths with line numbers
- Serves the map to coding agents BEFORE they read source files
- Reports 79% (mixed projects) – 81% (Cal.com TRPC) – 95% (Trigger.dev Core) **context reduction**
- Distributed via npm; integrates with Cursor + Claude Code via MCP

**Quote from Dinesh's post**: *"Signals are a map, not the territory. They don't replace code — they help agents reach the right code faster."*

That's exactly what CE's `ast_extract.py` produces and what `pack` consumes.

## Comparison matrix

| Axis | Context Signals MCP | Context Engineering |
|---|---|---|
| **What it ships** | Symbol map (functions, routes, classes, imports, file:line refs) | Symbol map (same shape) **plus** depth-packer, wiki layer, multi-corpus Source ABC |
| **Languages** | (Per docs — confirm against current README) | 14 via tree-sitter: TS / JS / Py / Rust / Go / Java / Ruby / C / C++ / C# / Kotlin / Scala / PHP / Swift |
| **Integration** | npm + MCP-ready, Cursor + Claude Code | MCP server (14 tools), `npx skills add`, `pip install`, CLI |
| **Caching** | (Per docs) | `(path, mtime_ns, sha1[:8])` invalidation in `cache/code_index.json` |
| **Multi-corpus** | Code only | Code + Granola + Notion + Gmail + HubSpot via Source ABC |
| **Depth-aware packing** | n/a — symbol map is the output | 5 graded depths (Full / Detail / Summary / Mention) into a token budget |
| **Wiki / concept layer** | n/a | `wiki/<slug>.md` entity pages with provenance, semantic-shift consolidation, auditor |
| **Published benchmarks** | **79–95% context reduction** on Cal.com TRPC, Trigger.dev Core, mixed projects | Demo run on 2–3 reference repos in [`docs/benchmarks.md`](./benchmarks.md) (deferred from full eval per `value_over_proof` discipline) |

## Where Context Signals is the right call

- **You have a single-codebase agent and you only need the symbol map.** No multi-corpus, no wiki layer, no depth packing. Context Signals does this well, with published numbers and a simple npm install.
- **Drop-in MCP integration.** No setup beyond `npm install`.

## Where CE is the right call

- **Multi-corpus.** CE's whole point is uniform retrieval across code + transcripts + emails + CRM. Context Signals is code-only by design.
- **Token-budget retrieval.** When the agent's question doesn't map to a single symbol but spans 40 files at varying relevance, CE's `pack` returns each at the right depth instead of dumping the whole symbol table.
- **Wiki / concept layer.** When you need to compound knowledge over time — entities, decisions, runbooks with provenance — CE's wiki + auditor + semantic-shift consolidation has a real role to play.

## On the published numbers

Dinesh published 79–95% context reduction. This is **the right framing** for the market and we'll publish equivalent numbers in [`docs/benchmarks.md`](./benchmarks.md) on the same reference repos he cited. CE's `pack` operates at a different layer (depth-graded packing of multi-file context, not symbol-map serving) so the numbers will differ in shape; the methodology will be reproducible.

Per CE's `value_over_proof` discipline we don't ship a full benchmark suite yet — but we do publish a demonstration run on Dinesh's cited repos so anyone can verify.

## Interop

Context Signals and CE solve adjacent problems and can compose:

- Use Context Signals for fast symbol-table-as-context for coding agents
- Use CE when you need multi-corpus retrieval, depth packing, or a wiki layer

CE's `code_index.py` produces a similar artifact to Context Signals' "signal map" but is internal to CE's `pack` and `lat.locate / search` flows. We don't currently expose it as a standalone npm package; if there's demand, that's a small lift.

## Acknowledgements

[Context Signals MCP](https://github.com/dineshraghupatruni/context-signals-mcp) by **Dinesh Raghupatruni** ships under (license — confirm against repo) and validated the navigation-bounds-not-reasoning-bounds thesis with hard numbers. The public benchmarks shaped this PRD's Phase 5 framing requirement that CE ship comparable demonstration numbers.
