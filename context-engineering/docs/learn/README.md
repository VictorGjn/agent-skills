# Learn the engine

A six-step stepladder from one query to a multi-corpus brain. Each step is one runnable command and one concept. No theory homework, no field-resonance shells. The whole walk takes under an hour on a real repo.

## Prerequisites

- Python 3.11+
- The skill installed and `pyproject.toml` deps resolved (`pip install -e .` from the skill root)
- A repo to point at — this guide uses `<workspace>` as a placeholder; substitute any local checkout

## The ladder

| # | Step | Concept | One-line take-away |
|---|------|---------|--------------------|
| 0 | [Pack one query](00-pack-one-query.md) | atoms — single shot | One file, one budget, one packed answer |
| 1 | [Budget and depth](01-budget-and-depth.md) | molecules — composition | The packer renders five depth levels, not one chunk size |
| 2 | [Index a workspace](02-index-a-workspace.md) | cells — persistent memory | An index is the brain's short-term memory; rebuild on demand |
| 3 | [Graph and multi-hop](03-graph-and-multi-hop.md) | organs — linked reasoning | Follow imports/calls/types, not just keywords |
| 4 | [Add a source](04-add-a-source.md) | neural systems — multi-corpus | Code is one Source; Granola/Notion/Slack are others |
| 5 | [EntityStore and wiki](05-entitystore-and-wiki.md) | compounding memory | Events accumulate into entity pages with full provenance |

## Metaphor mapping

Several pedagogical guides on context engineering (notably [davidkimai/Context-Engineering](https://github.com/davidkimai/Context-Engineering)) organize the discipline as a biological developmental hierarchy:

```
atoms → molecules → cells → organs → neural systems → fields
```

This ladder follows the same metaphor up to *neural systems* (multi-corpus). It deliberately stops short of the *fields* layer — quantum semantics, attractor dynamics, recursive emergence — because those are speculative-frontier framings without runnable artifacts in this engine. The whole skill is built around concrete primitives: indexer, Source ABC, EntityStore, depth-aware packer, MCP server. We compound by shipping more sources and tightening provenance, not by inventing new field operators.

## What this is not

- **Not a course.** No lectures, no quizzes, no certificates. Six runnable commands.
- **Not exhaustive.** The skill has 15 MCP tools; this ladder touches the six that matter most often. The others are reachable from the entry points each step lands on.
- **Not theoretical.** Every command produces output you can inspect.

When you finish step 5, the next read is [`SPEC-mcp.md`](../../SPEC-mcp.md) (full tool surface) and [`docs/vs-lat-md.md`](../vs-lat-md.md) / [`docs/vs-context-signals.md`](../vs-context-signals.md) (where this engine sits relative to adjacent tools).

Start with [step 0](00-pack-one-query.md).
