# Step 0 — Pack one query

> **Atom: a single shot.** The smallest unit of context engineering is one query, one budget, one packed answer.

## What you'll do

Run `pack_context.py` against a workspace with no prior indexing and ask one question. The script auto-indexes on first run, picks a mode by query shape, and emits packed markdown.

## Command

```bash
python3 scripts/pack_context.py "authentication middleware" --budget 4000
```

The first run prints `Auto-indexing <cwd>...` to stderr, builds `cache/workspace-index.json`, then proceeds to pack.

## What you get back

Stdout is markdown organized by depth, looking roughly like:

```
<!-- depth-packed [keyword] query="authentication middleware" budget=4000 used=~3920 files=12 -->

## Full (1 files)
... full content of the most relevant file ...

## Detail (2 files)
... headings + first paragraphs ...

## Summary (3 files)
... headings + one-line summaries ...

## Headlines (4 files)
... heading-only listings ...

## Mention (2 files)
- `path/to/related-file.ts` (87 tok)
```

The packer aims for ~95% of the budget — the trailing comment shows the actual usage.

## Concept

A *pack* is the engine's atomic unit. Everything else in this ladder either feeds the pack with better candidates (indexing, graph, semantic) or recovers structure from packed events (EntityStore, wiki).

You are not retrieving "the top-k chunks" the way a vanilla RAG does. You are *renting* a token budget and the packer fills it at five resolutions — the most relevant file gets full content, the rest are progressively compressed down to a one-line mention.

## Why it works without any flags

Three auto-decisions happen behind the scenes:

| Decision | Rule | Example for this query |
|----------|------|-------------------------|
| Mode | proper-noun / `CamelCase` / `snake_case` → `graph`; `how/why/what` → `semantic`; else `keyword` | `keyword` (multi-word concept, no symbols) |
| Task preset | matches `fix / bug / 401 / traceback` → `fix`; `review / pr` → `review`; etc. | none (no trigger word) |
| Index | rebuild if missing at `cache/workspace-index.json` | rebuild |

Pass `--why` to see the trace inline.

```bash
python3 scripts/pack_context.py "authentication middleware" --budget 4000 --why
```

## Try it

Run the command above against any local code repo. Then re-run with `--budget 8000` and watch the depth distribution shift — more files at higher resolutions, same query.

## Next

[Step 1 — Budget and depth](01-budget-and-depth.md). The packer's five-level depth model is the molecule that makes the budget knob meaningful.
