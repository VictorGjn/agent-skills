---
name: context-engineering
description: "Build and query a token-efficient, provenance-tracked company brain across code, docs, and connector streams. Five capabilities ship as one skill: multi-source indexer (14 languages via tree-sitter AST + markdown heading trees, schema-versioned, incremental); Source ABC (connector contract — connectors live elsewhere, never in this skill); EntityStore (three-tier raw/events/wiki layer with full provenance, semantic-shift consolidation, drift/split/merge auditor); depth-aware packer (5 levels, 95% budget utilization, multi-hop reasoning, query-as-lens reranking, anti-hallucination filters); MCP server. Anabasis `find-links` reference implementation. Use when: an agent needs broad file awareness within a token budget, building or querying a wiki/EntityStore over code + human knowledge, extracting features from a repo (code-to-knowledge), packing entity pages with provenance, multi-hop reasoning across linked concepts, indexing a workspace or GitHub repo. Do NOT use for: single-file reads, when every file needs full content, scheduling or orchestration (that's the Anabasis runtime), connector implementations (those live in syroco-product-ops or similar adapter library)."
version: 0.3.0
mcp_tools:
  - context-engineering.pack
  - context-engineering.index_workspace
  - context-engineering.index_github_repo
  - context-engineering.build_embeddings
  - context-engineering.resolve
  - context-engineering.stats
requiredApps: []
repository: https://github.com/victorgjn/agent-skills
license: MIT
transports: [stdio, http]
---

# Context Engineering

**The engine for building and querying a queryable, compounding company brain — across code, human-curated knowledge, and connector streams.**

Five tightly-coupled capabilities ship as one skill:

1. **Multi-source indexer** — AST (14 languages via tree-sitter) + markdown heading trees, schema-versioned cache, incremental re-indexing.
2. **Source ABC** — the contract connectors implement to feed events into the brain. This skill ships `WorkspaceSource` + `GithubRepoSource` only; Notion / HubSpot / Gmail / Granola adapters live elsewhere (Anabasis spec calls this `SignalSource`).
3. **EntityStore** — three-tier brain layer: `raw/` (verbatim sources) + `events/` (append-only JSONL of extracted claims) + `wiki/<slug>.md` (consolidated entity pages with full provenance — every cited claim resolves to file:line + content_hash + ts). Reference impl of Anabasis spec `EntityStore` ABC.
4. **Synthesizer + Auditor** — GAM-grade semantic-shift detector (consolidate only on cosine drift, never on every event); `wiki_init.py` one-shot seeder; Auditor proposes splits / merges / contradictions / dead links.
5. **Retrieval surface** — depth-aware packer (5 levels, 95% budget utilization) + multi-hop reasoning paths through `[[wiki-links]]` + query-as-lens reranking + RRF fusion + authority signals + anti-hallucination filters + knowledge-type priority.

Plus an MCP server exposing the whole stack as composable tools (`pack`, `index_workspace`, `index_github_repo`, `build_embeddings`, `resolve`, `stats`, and Phase 2 `wiki.{ask,add,audit,export}`).

The depth packer is one of these five capabilities, not the headline. The full brain — index + EntityStore + synthesis + retrieval — is what makes this skill the reference implementation of Anabasis's `find-links` (spec v0.2).

## At-a-glance: what CE replaces

- **Sourcegraph for an LLM consumer**: Sourcegraph excels at human-precise xref over millions of files; CE excels at *packing the right slice into the LLM's token budget*. Different jobs, complementary tools.
- **A bare RAG over a repo**: CE adds AST-grade symbol extraction, depth-variable packing (instead of fixed-size chunks), provenance-tracked entity pages, and multi-hop reasoning. RAG is one resolution mode (`--semantic`); CE is four (keyword, semantic, graph, wiki).
- **A static markdown wiki (Obsidian-style)**: CE *generates and maintains* the wiki from `events/` instead of requiring humans to author every entity page. Humans curate; the synthesizer compounds.
- **A scheduled "scrape Notion → vector DB" pipeline**: CE provides the engine the pipeline plugs into via Source ABC. The schedule itself is the Anabasis runtime's job, not CE's.

## Quick start (one verb)

```bash
pack_context.py "users getting 401 on refresh tokens"
```

Pack 40+ files at 5 depth levels into a token budget instead of loading 2-3 fully. The skill auto-decides:
- **Mode** — proper-noun / `CamelCase` / `snake_case` query → `graph`; `how/why/what` → `semantic` (if `OPENAI_API_KEY` set, else `keyword`); else `keyword`.
- **Task preset** — matches `fix / bug / 401 / traceback` → `--task fix`; `review / pr` → `review`; `explain / how does` → `explain`; etc.
- **Index** — auto-builds one for `cwd` if no index exists at the configured cache path.

Every call is logged to `cache/usage.jsonl` (metadata only — query length, mode, task, files packed, budget %, time_ms — no query content). Pass `--why` to see the trace inline.

Override anything with explicit flags (`--mode`, `--task`, `--graph`, `--semantic`, etc. — all still work).

## How it works

```
Query → Resolution → Scoring → Depth packing → Packed markdown
```

1. **Index** a workspace (local or GitHub). The indexer parses markdown heading trees and extracts AST symbols from 14 code languages via tree-sitter.
2. **Resolve** which files matter using keyword matching, embedding similarity, graph traversal, or any combination.
3. **Filter** off-topic results (topic overlap, section extraction, confidence scoring).
4. **Pack** files at variable depth — most relevant get full content, the rest get progressively compressed down to a one-line mention — until the token budget is ~95% used.

## User guide

### Scenario 1: Full code repository

Index an entire codebase — the indexer picks up both docs and source files automatically.

```bash
# Index
python3 scripts/index_workspace.py /path/to/my-app/

# Pack a query
python3 scripts/pack_context.py "authentication middleware" --budget 8000

# With graph traversal (follows imports/dependencies)
python3 scripts/pack_context.py "PaymentService" --graph --budget 8000

# Task-aware: "fix" mode follows tests, skips docs
python3 scripts/pack_context.py "fix login bug" --task fix --graph --budget 8000
```

The indexer walks the directory, skipping common build artifacts (`.next`, `dist`, `node_modules`, `target`, etc.). Code files get AST symbol extraction — every function, class, and interface becomes a searchable heading. Markdown files get heading-tree parsing.

### Scenario 2: Knowledge repository (mostly `.md` files)

Same workflow — the indexer auto-detects file types. For a pure-markdown repo (docs, RFCs, meeting notes, guidelines), heading-tree parsing gives you section-level granularity.

```bash
python3 scripts/index_workspace.py /path/to/company-knowledge/

# Conceptual query — semantic mode shines here
python3 scripts/pack_context.py "onboarding process new hires" --semantic --budget 12000

# With anti-hallucination filter for noisy knowledge bases
python3 scripts/pack_context.py "compliance requirements" --semantic --topic-filter --budget 8000
```

Knowledge-type classification kicks in: architecture docs rank higher than meeting notes, source-of-truth files beat changelogs. This matters when many files match and the packer has to choose who gets Full vs Mention depth.

### Scenario 3: Explore a specific feature in an existing graph

If you've already indexed and optionally run [Graphify](https://github.com/safishamsi/graphify), you can drill into a single feature without re-indexing.

```bash
# Graphify builds a rich knowledge graph (call graphs, inheritance, cross-language)
graphify /path/to/my-app/ --output graphify-out/

# Now query a specific feature — graph mode auto-detects graphify-out/graph.json
python3 scripts/pack_context.py "WebSocket reconnection logic" --graph --budget 8000

# Combine with semantic for maximum discovery
python3 scripts/pack_context.py "how does real-time sync work" --semantic --graph --budget 12000
```

Graph mode finds the entry points via keyword/semantic matching, then traverses the dependency graph outward (imports, callers, tests, docs). The result is a focused slice of the codebase around that feature — not a flat keyword search.

### What gets persisted

Indexing produces two JSON files in `cache/`:

| File | Size | Contents |
|------|------|----------|
| `workspace-index.json` | ~3-5 MB for a 500-file repo | Full index with heading trees, AST symbols, content previews, knowledge types |
| `workspace-index-light.json` | ~500 KB | Headings + metadata only (no tree content) — for quick lookups |

Both are human-readable JSON. A file entry in the light index looks like:

```json
{
  "path": "src/hooks/use-tab-history.ts",
  "tokens": 406,
  "nodeCount": 3,
  "headings": [
    { "depth": 0, "title": "src/hooks/use-tab-history.ts", "tokens": 406 },
    { "depth": 1, "title": "const popDirectionHints", "tokens": 5 },
    { "depth": 1, "title": "useTabHistory", "tokens": 36 }
  ]
}
```

The full index adds the complete heading tree with text content, first sentences, and first paragraphs — everything the packer needs to render at each depth level.

**Re-indexing:** Run `index_workspace.py` again whenever the codebase changes. It overwrites the cache. There's no incremental mode yet.

### Graph visualization

```bash
# 3D force-directed graph — opens in any browser
python3 scripts/visualize_graph.py --top 50

# With query overlay — shows how the packer "sees" the codebase
python3 scripts/visualize_graph.py --top 80 --query "authentication"

# Dual graph — two repos with cross-repo DTO linking
python3 scripts/visualize_graph.py --multi-index cache/fleet-index.json cache/backend-index.json --top 100

# File-level only (no symbols), custom output path
python3 scripts/visualize_graph.py --no-symbols -o my-graph.html

# With graphify edges for richer connections
python3 scripts/visualize_graph.py --graphify graphify-out/graph.json
```

Outputs a standalone `graph.html` — zero dependencies, interactive 3D visualization (Three.js). Functions are blue, classes purple, concepts teal, clusters amber, files gray. Click nodes to highlight connections and dim unrelated nodes. Search bar for live keyword filtering. Drag to orbit, scroll to zoom.

**Query overlay** (`--query`): Runs the pack_context scoring pipeline and color-codes nodes by relevance — ocean blue (high), teal (medium), sky blue (low), gray (unmatched). The in-browser search bar re-scores client-side for instant feedback.

**Dual graph** (`--multi-index`): Merges multiple workspace indexes with repo-prefixed paths. Automatically detects shared types across repos (e.g., matching DTOs) and draws amber cross-repo links. Concept clustering groups symbols by CamelCase prefix (Voyage*, Fleet*) to break up blob nodes.

If [Graphify](https://github.com/safishamsi/graphify) has been run in the workspace, `--graph` auto-detects `graphify-out/graph.json` and uses its richer call graphs, inheritance, and cross-language edges.

### Feature map (bird's-eye)

```bash
# Single repo — shows feature clusters and how they depend on each other
python3 scripts/feature_map.py --index cache/workspace-index.json

# Multi-repo
python3 scripts/feature_map.py --multi-index cache/fleet-index.json cache/backend-index.json

# Custom output
python3 scripts/feature_map.py --index cache/workspace-index.json -o my-features.html

# Tune cluster granularity on large repos (default 1, keeps singleton clusters)
python3 scripts/feature_map.py --index cache/backend-index.json --min-cluster 5

# v2: LLM-named concepts + sub-features per cluster, domain-tier coloring
python3 scripts/feature_map.py --index cache/fleet-index.json --concept-llm
```

Uses label propagation community detection on the import graph to discover natural feature clusters. Each cluster is labeled by its dominant directory or top symbol names. A second label-propagation pass folds clusters into **domains** (only edges with weight ≥ 2 count as structural coupling); the renderer colors each cluster by domain palette slot with lightness offsets for distinct members. Renders as interactive 2D SVG (D3 force-directed). Click a domain row in the legend to filter; click a cluster to see its concept, description, sub-features, and files.

On large repos (2000+ files) the default `--min-cluster 1` keeps every disconnected file visible; raise to `5` or `10` to focus on the backbone modules.

**Concept labeling (v2):** Add `--concept-llm` to use Claude Haiku to assign product-level concept names ("Navigation" instead of "SideNavbar, TopNavBar") plus a sub-feature list shown in the click-detail panel. Labels are cached per cluster content hash under `cache/concept-labels/` — the same cluster shape reuses the same label until files move between clusters. Defaults: model `claude-haiku-4-5-20251001`, 4 parallel workers, cache dir `cache/concept-labels/`. Budget: roughly $0.05 and ~1 min on a 100-cluster repo. Requires `ANTHROPIC_API_KEY`; fails fast if missing.

## Features

### Multi-language code indexing

The workspace indexer handles markdown (heading-tree) and code files:

`.ts` `.tsx` `.js` `.jsx` `.py` `.go` `.rs` `.rb` `.java` `.c` `.cpp` `.cs` `.kt` `.scala` `.php`

Code files get AST symbol extraction — functions, classes, interfaces, methods, and types become searchable headings, renderable at all 5 depth levels. Falls back to regex if tree-sitter is not installed.

### Five depth levels

| Level | What the LLM sees | Relative cost |
|-------|-------------------|---------------|
| **Full** | Complete file content | 100% |
| **Detail** | Headings + first paragraphs | ~40% |
| **Summary** | Headings + first sentences | ~20% |
| **Headlines** | Heading/symbol tree only | ~8% |
| **Mention** | File path + token count | ~3% |

The packer assigns depth from relevance, demotes to fit budget, then promotes if budget remains. Target: 95% utilization.

### Four resolution modes (composable)

| Mode | Flag | Use case |
|------|------|----------|
| **Keyword** | *(default)* | Name-based queries, free |
| **Semantic** | `--semantic` | Conceptual queries ("how does auth work?") |
| **Graph** | `--graph` | Structural queries ("what depends on X?") |
| **Semantic + Graph** | `--semantic --graph` | Full discovery |

Graph mode traverses imports/dependencies via BFS. If [Graphify](https://github.com/safishamsi/graphify) has been run in the workspace, `--graph` auto-detects `graphify-out/graph.json` and uses its richer call graphs, inheritance, and cross-language edges instead.

### Task-type presets

Adjust graph traversal behavior per intent:

| Task | Flag | Traversal strategy |
|------|------|--------------------|
| fix | `--task fix` | Deep imports, follow tests, skip docs |
| review | `--task review` | Wide scan, follow callers + tests + docs |
| explain | `--task explain` | Deep traversal, follow docs + links |
| build | `--task build` | Shallow, imports + docs only |
| document | `--task document` | Follow everything |
| research | `--task research` | Wide, docs + links + references only |

### Anti-hallucination filters

Three filters run before packing to prevent off-topic context from reaching the LLM:

- **Topic filter** (`--topic-filter`) — removes results with <25% query-term overlap (unless high cosine score)
- **Section filter** — extracts only matching sections from long multi-topic docs
- **Confidence scoring** (`--confidence`) — injects uncertainty signal when average similarity is weak

### Knowledge-type priority

Files are auto-classified. At equal relevance, higher-priority types get better depth:

| Priority | Type | Examples |
|----------|------|----------|
| 1st | Ground Truth | Source code, schemas, API docs |
| 2nd | Framework | Architecture docs, guidelines |
| 3rd | Evidence | Research, benchmarks |
| 4th | Signal | Meeting notes, feedback |
| 5th | Hypothesis | Plans, proposals, RFCs |
| 6th | Artifact | READMEs, changelogs |

### Graph relations

17 weighted relation kinds for import/dependency traversal:

`imports` (1.0) · `extends` (0.9) · `implements` (0.85) · `calls` (0.7) · `uses_type` (0.7) · `tested_by/tests` (0.6) · `documents` (0.5) · `configured_by` (0.5) · `links_to` (0.5) · `references` (0.4) · `depends_on` (0.4) · `defined_in` (0.4) · `continues/supersedes` (0.3) · `related` (0.3) · `co_located` (0.3)

## Usage

### 1. Index

```bash
python3 scripts/index_workspace.py /path/to/workspace/
python3 scripts/index_github_repo.py owner/repo --branch main
```

### 2. Build embeddings (semantic mode only, one-time)

```bash
python3 scripts/embed_resolve.py build cache/workspace-index.json
```

### 3. Pack

```bash
python3 scripts/pack_context.py "authentication middleware" --budget 8000
python3 scripts/pack_context.py "how does auth work" --semantic --budget 8000
python3 scripts/pack_context.py "PaymentService" --graph --budget 8000
python3 scripts/pack_context.py "explain payment flow" --semantic --graph --budget 16000
python3 scripts/pack_context.py "fix login bug" --task fix --graph --budget 8000
```

### 4. Read

Use packed output for orientation. Read critical files fully with your file-read tool.

## Output format

Markdown grouped by depth level:

```
<!-- depth-packed [keyword] query="auth middleware" budget=8000 used=~7600 files=12 -->

## Full (2 files)
### src/auth/middleware.ts
(complete file content)

## Detail (3 files)
### src/routes/api.ts
(headings + first paragraphs)

## Summary (3 files)
### src/config/database.ts
(headings + first sentences)

## Headlines (2 files)
### src/utils/logger.ts
  - Winston setup (24 tok)

## Mention (2 files)
- `src/utils/helpers.ts` (340 tok)
```

Also supports `--json` for structured output and `--quality` for fewer files at better depth.

## MCP Server

```bash
pip install "mcp[cli]" requests
python3 scripts/mcp_server.py              # stdio (local)
python3 scripts/mcp_server.py --http 8000  # remote
python3 scripts/mcp_server.py --http 8000 --auth  # with API key auth
```

Exposes tools: `pack`, `index_workspace`, `index_github_repo`, `build_embeddings`, `resolve`, `stats`.

## Prerequisites

- Python 3.10+
- `pip install tree-sitter-language-pack` (Python 3.12+) or `tree-sitter-languages` (Python 3.10-3.11) for AST symbol extraction. Falls back to regex if neither is installed.
- OpenAI API key (semantic mode only, via `OPENAI_API_KEY` env var)
- `pip install "mcp[cli]" requests` (MCP server only)

## Scripts

| Script | Purpose |
|--------|---------|
| `pack_context.py` | CLI entry point: query → depth-packed output |
| `pack_context_lib.py` | Core scoring, packing, knowledge types, filters |
| `index_workspace.py` | Index local workspace (markdown + code) → JSON |
| `index_github_repo.py` | Index GitHub repo via API → JSON |
| `ast_extract.py` | Tree-sitter AST symbol extraction (14 languages) |
| `code_graph.py` | Import/dependency graph + BFS traversal + task presets |
| `graphify_adapter.py` | Converts Graphify graph.json to code_graph format |
| `visualize_graph.py` | 3D force-directed graph visualization → HTML |
| `feature_map.py` | Bird's-eye feature map: community detection → D3 2D SVG |
| `community_detect.py` | Label propagation community detection (pure Python) |
| `embed_resolve.py` | Embedding resolver: build, resolve, hybrid |
| `embeddingResolver.ts` | TypeScript port for Node.js agents |
| `mcp_server.py` | MCP server (stdio + HTTP + optional auth) |

## Anabasis conformance

This skill is a useful adjacent skill in an Anabasis stack — it
interoperates with Anabasis runtimes via the [agentskills.io](https://agentskills.io/)
SKILL.md format (see the [Anabasis spec](https://github.com/VictorGjn/anabasis/blob/main/spec/skill.md)).
It is **not** Anabasis's reference skill #1 — that role belongs to
[`install-department`](https://github.com/victorgjn/agent-skills/tree/main/install-department),
which captures how each department actually works and writes a
Department Spec into the brain.

context-engineering is **independently useful** — `pip install -e .` and
run `python3 scripts/pack_context.py "your query"` against any indexed
repo without ever installing Anabasis. When Anabasis is present, the
runtime can route code-context queries to this skill via its declared
MCP surface (`mcp_tools` in the frontmatter above). There is no runtime
dependency on Anabasis in either direction.

**Trajectory**: when Anabasis spec `v0.2` freezes, this skill becomes
the reference implementation of [`find-links`](https://github.com/VictorGjn/anabasis/blob/main/spec/reference-skills/find-links.md) —
the canonical retrieval skill that operates over Department Specs (output
of [`install-department`](https://github.com/victorgjn/agent-skills/tree/main/install-department)),
repo content, and entity pages. install-department captures the brain;
find-links queries it. Two halves of the same operating loop.

**Licensing posture (option b, locked 2026-05-01)**: this engine is
**MIT public** and stays that way — the moat is operational, not
source-code-secret. The Anabasis runtime + hosted plane that schedule
this skill, manage state, resolve conflicts, and fan out to MCP-
authenticated connectors are commercial/closed. Cursor / Vercel /
Snowflake model: closed orchestration on open primitives. You can run
context-engineering today, and you can keep running it forever, with or
without Anabasis.
