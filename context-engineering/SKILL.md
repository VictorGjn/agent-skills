---
name: context-engineering
description: "Depth-packed context loading for codebases and document collections. Use when an LLM agent needs broad file awareness within a token budget, when extracting features from a repo (code-to-knowledge), or when building a codebase overview. Packs 40+ files at 5 depth levels with keyword, semantic, and graph resolution. 14 languages via tree-sitter AST."
requiredApps: []
---

# Context Engineering

Pack many files at varying depth into a token budget, instead of loading 2-3 fully.

## Architecture

```
Query → Resolution (keyword | semantic | graph) → Entry points
                                                       ↓
                                                 Graph traversal (optional)
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
```

### 4. Read

Use packed output for orientation. Read critical files fully with your file-read tool.

## Code-to-Knowledge Pattern

Extract a feature inventory from any codebase:

```bash
# 1. Index the repo
python3 scripts/index_github_repo.py acme/fleet-dashboard --branch develop

# 2. Broad scan: what features exist?
python3 scripts/pack_context.py "all features pages routes components" --semantic --budget 16000

# 3. Deep dive per domain
python3 scripts/pack_context.py "real-time position tracking PubNub" --semantic --graph --budget 8000
python3 scripts/pack_context.py "weather layers map visualization" --semantic --budget 8000
python3 scripts/pack_context.py "authentication authorization roles" --graph --budget 8000
```

Tested on 3 production repos (1200-3800 files each): extracted 18 undocumented features from Live, 14 from Fleet, 57 new backend modules. 4-7x more features than manual file-by-file reading.

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
|----------|------|---------|
| 1st | Ground Truth | Source code, schemas, API docs |
| 2nd | Framework | Architecture docs, guidelines |
| 3rd | Evidence | Research, benchmarks |
| 4th | Signal | Meeting notes, feedback |
| 5th | Hypothesis | Plans, proposals, RFCs |
| 6th | Artifact | READMEs, changelogs |

## MCP Server

```bash
pip install "mcp[cli]" requests
python3 scripts/mcp_server.py              # stdio (local)
python3 scripts/mcp_server.py --http 8000  # remote
```

Tools: `pack`, `index_workspace`, `index_github_repo`, `build_embeddings`, `resolve`, `stats`.

## Scripts

| Script | Purpose |
|--------|---------|
| `pack_context_lib.py` | Core: scoring, packing, knowledge types |
| `pack_context.py` | CLI: query → depth-packed output |
| `embed_resolve.py` | Embedding resolver: build, resolve, hybrid |
| `ast_extract.py` | tree-sitter AST symbol extraction (14 languages) |
| `code_graph.py` | Import/dependency graph + BFS traversal |
| `embeddingResolver.ts` | TypeScript port for Node.js agents |
| `mcp_server.py` | MCP server (stdio + HTTP) |
| `index_workspace.py` | Index local files → JSON |
| `index_github_repo.py` | Index GitHub repo via API → JSON |

## LLM Integration Pattern

1. **Expand** query with domain synonyms
2. **Scan** at 8K with `--semantic` for landscape
3. **Dive** into 2-3 critical files at full depth
4. **Re-pack** with `--semantic --graph` if structural context needed

Details: `references/eval-results.md` for benchmarks (100% recall at 8K tokens on 3 repos).
