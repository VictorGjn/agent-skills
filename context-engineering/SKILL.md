---
name: context-engineering
description: "Pack 40+ files at 5 depth levels into any LLM context window. Use when an agent needs broad file awareness within a token budget, when extracting features from a repo (code-to-knowledge), or when building a codebase overview. Keyword, semantic, and graph resolution. 14 languages via tree-sitter AST. Anti-hallucination filters (topic, section, confidence). Task-type presets. Do NOT use for single-file reads or when every file needs full content."
requiredApps: []
---

# Context Engineering

Pack 40+ files at 5 depth levels into a token budget, instead of loading 2-3 fully.

## Prerequisites

- Python 3.10+
- `pip install tree-sitter-languages` for AST extraction (without it, falls back to regex)
- OpenAI API key (semantic mode only, via `OPENAI_API_KEY` env var)
- `pip install "mcp[cli]" requests` (MCP server only)

## Architecture

```
Query → Resolution (keyword | semantic | graph) → Entry points
                                                       ↓
                                                 Graph traversal (optional)
                                                       ↓
                                                 Anti-hallucination filters
                                                       ↓
                                                 Depth-aware packing (5 levels)
                                                       ↓
                                                 LLM reads packed context
```

## Resolution Modes (composable)

| Mode | Flag | Best for | Cost |
|------|------|----------|------|
| Keyword | *(default)* | Keyword-rich queries | Free |
| Semantic | `--semantic` | Conceptual queries ("how does auth work?") | ~$0.0001/query |
| Graph | `--graph` | Structural queries ("what depends on X?") | Free |
| Semantic+Graph | `--semantic --graph` | Full discovery | ~$0.0001/query |
| Graphify+Graph | `--graph` *(auto-detects `graphify-out/graph.json`)* | Rich call graphs, inheritance, cross-language | Free |

## Usage

### 1. Index

```bash
python3 scripts/index_workspace.py /path/to/files/
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

### Graphify integration (optional, richer graph)

If you've already run [Graphify](https://github.com/safishamsi/graphify) in the workspace, `--graph` auto-detects `graphify-out/graph.json` and uses it instead of the import-only graph. This surfaces files reachable via call graphs, inheritance, method relationships, and doc-to-code links — edges the import parser misses.

```bash
# Happy path: graphify graph.json auto-detected
python3 scripts/pack_context.py "query" --graph

# Explicit path
python3 scripts/pack_context.py "query" --graph --graphify-path /path/to/graph.json
```

Falls back silently to import-only if no `graph.json` exists. Zero cost beyond running Graphify once.

### 4. Read

Use packed output for orientation. Read critical files fully with your file-read tool.

## Output

Markdown grouped by depth level. Each section lists files at that depth:

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

Token utilization target: 95% of budget.

## Anti-Hallucination Filters (from chatbot)

Three filters run BEFORE packing to prevent off-topic context from reaching the LLM:

1. **Topic filter** (`--topic-filter`): removes results whose content doesn't overlap with query terms. Threshold: 25% term overlap OR high cosine score (>0.5).
2. **Section filter**: within long documents, extracts only sections matching query terms. Reduces noise from multi-topic docs.
3. **Confidence scoring**: when average cosine similarity is below threshold, injects uncertainty signal. Use `pack_context_lib.confidence_check()`.

## Task-Type Presets (from modular-patchbay)

Auto-detect or specify task type to adjust graph traversal behavior:

| Task | Flag | Behavior |
|------|------|----------|
| fix | `--task fix` | Deep imports, follow tests, skip docs |
| review | `--task review` | Wide scan, follow callers + tests + docs |
| explain | `--task explain` | Deep traversal, follow docs + links |
| build | `--task build` | Shallow, imports + docs only |
| document | `--task document` | Follow everything |
| research | `--task research` | Wide, docs + links + references only |

## Depth Levels

| Level | Content | Token cost |
|-------|---------|------------|
| Full | Complete file | 100% |
| Detail | Headings + first paragraphs | 40% |
| Summary | Headings + first sentences | 20% |
| Headlines | Heading tree only | 8% |
| Mention | Path + token count | 3% |

3-phase packer: assign depth from relevance → demote if over budget → promote if budget remains. 95% utilization.

## Knowledge Types

Files auto-classified, higher priority = better depth at equal relevance:

| Priority | Type | Examples |
|----------|------|----------|
| 1st | Ground Truth | Source code, schemas, API docs |
| 2nd | Framework | Architecture docs, guidelines |
| 3rd | Evidence | Research, benchmarks |
| 4th | Signal | Meeting notes, feedback |
| 5th | Hypothesis | Plans, proposals, RFCs |
| 6th | Artifact | READMEs, changelogs |

## Graph Relations (expanded)

17 relation kinds with weighted edges:

| Kind | Weight | Direction |
|------|--------|-----------|
| imports | 1.0 | A imports B |
| extends | 0.9 | A extends B |
| implements | 0.85 | A implements B |
| calls | 0.7 | A calls B |
| uses_type | 0.7 | A uses type from B |
| tested_by / tests | 0.6 | Test ↔ source |
| documents | 0.5 | Doc ↔ code |
| configured_by | 0.5 | A configured by B |
| links_to | 0.5 | Markdown link |
| references | 0.4 | Markdown reference |
| depends_on | 0.4 | Explicit dependency |
| defined_in | 0.4 | Symbol defined in |
| continues / supersedes | 0.3 | Doc versioning |
| related | 0.3 | Semantic relation |
| co_located | 0.3 | Same directory |

## MCP Server

```bash
pip install "mcp[cli]" requests
python3 scripts/mcp_server.py              # stdio (local)
python3 scripts/mcp_server.py --http 8000  # remote
python3 scripts/mcp_server.py --http 8000 --auth  # with API key auth
```

Tools: `pack`, `index_workspace`, `index_github_repo`, `build_embeddings`, `resolve`, `stats`.

## Scripts

| Script | Purpose |
|--------|---------|
| `pack_context_lib.py` | Core: scoring, packing, knowledge types, topic/section filters |
| `pack_context.py` | CLI: query → depth-packed output |
| `embed_resolve.py` | Embedding resolver: build, resolve, hybrid |
| `ast_extract.py` | tree-sitter AST symbol extraction (14 languages) |
| `code_graph.py` | Import/dependency graph + BFS traversal + task presets |
| `graphify_adapter.py` | Adapter: converts Graphify graph.json to code_graph format |
| `embeddingResolver.ts` | TypeScript port for Node.js agents |
| `mcp_server.py` | MCP server (stdio + HTTP + optional auth) |
| `index_workspace.py` | Index local files → JSON |
| `index_github_repo.py` | Index GitHub repo via API → JSON |

## LLM Integration Pattern

1. **Expand** query with domain synonyms
2. **Scan** at 8K with `--semantic` for landscape
3. **Dive** into 2-3 critical files at full depth
4. **Re-pack** with `--semantic --graph` if structural context needed

Details: `references/eval-results.md` for benchmarks (100% recall at 8K tokens on 3 repos).
