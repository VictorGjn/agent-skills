# Step 3 — Graph and multi-hop

> **Organ: linked reasoning.** Up to here, candidates were chosen by keyword or embedding similarity. The graph layer chooses by structural connection.

## What you'll do

Pack a query in `--graph` mode against an indexed workspace, optionally combine with `--semantic`, and visualize what the packer is "seeing".

## Commands

```bash
# Graph traversal: find entry points by keyword, then follow imports/calls/types outward
python3 scripts/pack_context.py "PaymentService" --graph --budget 8000

# Deep mode: graph + semantic together, MMR diversity rerank on
python3 scripts/pack_context.py "how does real-time sync work" --mode deep --budget 12000

# Task-aware: "fix" mode follows tests + types, skips docs
python3 scripts/pack_context.py "fix login bug" --task fix --graph --budget 8000
```

## What graph mode does

Without `--graph`, candidates come from keyword matching against the index (heading text, content, paths). With `--graph`:

1. The same keyword/semantic pass picks **entry points** — usually the top-N highest-scoring files.
2. The packer then traverses the dependency graph outward from those entry points: imports, callers, callees, types referenced, tests that exercise them.
3. The expanded set is re-scored, then handed to the depth-packing pipeline from steps 1-2.

Result: a focused slice of the codebase *around* the matched concept, not a flat keyword fan-out.

## Task presets

`--task` shapes the traversal. Each preset weights the graph differently:

| Preset | Prioritizes | Demotes |
|--------|-------------|---------|
| `fix` | Tests, types, error paths | Docs, READMEs |
| `review` | Diff hot-spots, callers | Generated code |
| `explain` | Docs, top-level entry points | Test scaffolding |
| `build` | Configuration, integration points | Vendored deps |
| `document` | Public API surface, comments | Internal helpers |
| `research` | Wide breadth, low depth | None — keep everything |

If you don't pass `--task`, the script auto-detects from query keywords (see step 0).

## Multi-hop reasoning

`--mode deep` combines graph traversal with semantic similarity and runs MMR (maximal marginal relevance) on the merged candidate set. This is multi-hop in two senses:

- **Structural**: follow the call/import graph from entry points
- **Semantic**: discover files that share concepts but no direct edge

The two passes converge — files that show up in both rank highest, files that show up in only one survive at lower depth.

## Visualize what the packer sees

```bash
python3 scripts/visualize_graph.py --top 80 --query "authentication"
```

Outputs a standalone `graph.html` — three.js, zero external deps. Nodes are colour-coded by relevance to the query: ocean blue (high), teal (medium), sky blue (low), gray (unmatched). Search bar re-scores client-side.

For comparing two repos:

```bash
python3 scripts/visualize_graph.py \
  --multi-index cache/fleet-index.json cache/backend-index.json \
  --top 100
```

Shared types across repos (matching DTOs by name) get drawn as amber cross-repo links.

## Optional: graphify

If you've installed [Graphify](https://github.com/safishamsi/graphify), it produces richer call graphs (cross-language edges, inheritance). The packer auto-detects `graphify-out/graph.json` when `--graph` is set.

Caveat: graphify v0.4 doesn't resolve TypeScript `paths` aliases. For TS monorepos with `@/foo`-style imports, fall back to the engine's native graph (it does resolve them via `scripts/tsconfig_resolver.py`).

## Concept

The graph is the engine's first multi-hop primitive. It changes the unit of recall from "files that mention the query" to "the local neighbourhood of the answer". Steps 4 and 5 generalize this beyond code: the EntityStore is a graph over claims, not files.

## Next

[Step 4 — Add a source](04-add-a-source.md). The neural-systems layer: code is one corpus; Granola transcripts, Notion DBs, Slack messages are others. Plug them in via the same Source ABC.
