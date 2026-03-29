# Context Engineering for AI Agents

**Give your LLM agent awareness of 40+ files instead of 3, in the same token budget.**

RAG retrieves chunks. Repo-map lists symbols. This packs entire file hierarchies at 5 depth levels with elastic budget allocation, so the model sees the full picture without blowing the context window.

```
                     Traditional RAG                              Context Engineering
               ┌─────────────────────────┐                ┌─────────────────────────┐
               │ Query → top-k chunks    │                │ Query → score all files  │
               │                         │                │         ↓                │
               │  ████  chunk 1 (100%)   │                │  ████  file A   (Full)   │
               │  ████  chunk 2 (100%)   │                │  ███   file B (Detail)   │
               │  ████  chunk 3 (100%)   │                │  ██    file C (Summary)  │
               │  ████  chunk 4 (100%)   │                │  ██    file D (Summary)  │
               │  ████  chunk 5 (100%)   │                │  █     file E (Headlines)│
               │  ████  chunk 6 (100%)   │                │  █     file F (Headlines)│
               │  ████  chunk 7 (100%)   │                │  █     file G (Headlines)│
               │  ████  chunk 8 (100%)   │                │  ░     file H  (Mention) │
               │                         │                │  ░     file I  (Mention) │
               │  8 chunks, 42 invisible │                │  ░     file J  (Mention) │
               │  No structure preserved │                │  ...15 more at Mention   │
               │  No relations between   │                │                          │
               │  chunks                 │                │  25 files, graded depth  │
               └─────────────────────────┘                │  Structure preserved     │
                                                          │  Relations visible       │
                                                          └─────────────────────────┘
```

## Benchmarks

Tested on 3 repos (30 queries, 5 budget levels each). Full data in [references/eval-results.md](context-engineering/references/eval-results.md).

```
Budget    Recall    Critical files at readable depth    % of repo consumed
──────    ──────    ────────────────────────────────    ──────────────────
  2K       84%                  0%                          0.3%
  4K       98%                  0%                          0.5%
  8K      100%                42-65%                        1.0%
 16K      100%                 80%                          2.0%
 32K      100%                85-90%                        4.0%
```

**100% recall at 8K tokens (1% of repo).** Every relevant file found. At 16K, 80% of critical files are at readable depth (Summary or better).

| Repo | Language | Files | Tokens | Recall@8K |
|------|----------|-------|--------|-----------|
| modular-patchbay | TypeScript | 350 | 809K | **100%** |
| fastify | JavaScript | 250 | 506K | **100%** |
| flask | Python+RST | 181 | 280K | **95%** |

## Quick Start

```bash
pip install requests  # only needed for semantic mode

# 1. Index
python3 context-engineering/scripts/index_workspace.py /path/to/project/

# 2. Pack (keyword mode, zero API cost)
python3 context-engineering/scripts/pack_context.py "auth middleware session" --budget 8000

# 3. Pack (semantic mode, bridges vocabulary gap)
python3 context-engineering/scripts/embed_resolve.py build          # one-time, ~$0.01/500 files
python3 context-engineering/scripts/pack_context.py "how does authentication work?" --semantic --budget 8000

# 4. Pack (graph mode, follows imports/deps)
python3 context-engineering/scripts/pack_context.py "PaymentService" --graph --budget 8000

# 5. Full pipeline: semantic + graph
python3 context-engineering/scripts/pack_context.py "how does auth work" --semantic --graph --budget 8000
```

Output is markdown. Feed it to any LLM. No framework lock-in.

## Code-to-Knowledge: Extract Features from Any Codebase

The killer use case. Index a repo, run targeted queries, get a complete feature inventory.

**Real example:** We ran this against 3 production repos (React + NestJS, 1200-3800 files each). Results:

| Repo | Files | Method | Features found |
|------|-------|--------|---------------|
| voyage-optimization-app | 1,217 | Manual reading (10 key files) | ~9 capabilities documented |
| voyage-optimization-app | 1,217 | **Context graph (35 files at graded depth)** | **27 capabilities (18 new)** |
| fleet | 391 | Manual reading (8 key files) | ~10 capabilities documented |
| fleet | 391 | **Context graph (55 files at graded depth)** | **24 capabilities (14 new)** |
| maritime-platform-backend | 3,872 | Known modules | 12 modules |
| maritime-platform-backend | 3,872 | **Context graph (20+ key files)** | **69 modules (57 new)** |

**4-7x more features** than manual file-by-file reading. The depth packing forces systematic exploration: every file gets scored, the top ones read in depth, peripheral ones acknowledged at Headlines/Mention level.

### How to do it

```bash
# 1. Index the repo
python3 context-engineering/scripts/index_github_repo.py acme/fleet-dashboard --branch develop

# 2. Broad scan: what features exist?
python3 context-engineering/scripts/pack_context.py "all features pages routes components" --semantic --budget 16000

# 3. Deep dive per domain
python3 context-engineering/scripts/pack_context.py "real-time tracking PubNub websocket" --semantic --graph --budget 8000
python3 context-engineering/scripts/pack_context.py "weather layers map visualization" --semantic --budget 8000
python3 context-engineering/scripts/pack_context.py "authentication authorization roles" --graph --budget 8000
python3 context-engineering/scripts/pack_context.py "background jobs queues cron" --semantic --budget 8000
```

### What it found that manual reading missed

**In the frontend (Live app, 1217 files):**
- 7 undocumented ECDIS export formats (Wartsila NACOS, Sperry Marine, email delivery)
- 20 undocumented navigation area types (JWLA, Whale Protection, MARPOL)
- Shore-to-ship Route Proposal workflow (entire feature, never documented)
- 3 distinct Power widget variants (only "Power" was documented)
- Network/Position status indicators, brightness control, expert/basic timeline modes

**In the backend (NestJS, 3872 files):**
- 57 modules beyond the 12 known (charter-party metrics, DTN weather, Kpler AIS, CANEdge IoT)
- 20 BullMQ queues, 15 cron jobs (none documented)
- 31 fleet monitoring alert types (none documented)
- Multi-provider weather architecture (Theyr + DTN + Spire)

The context graph doesn't just find more files. It finds **entire capabilities** that were invisible to spot-checking.

## Three Resolution Modes

| Mode | Best for | How it works | API cost |
|------|----------|-------------|----------|
| **Keyword** (default) | Keyword-rich queries ("PaymentService error") | Stem/path matching on filenames, headings, exports | Free |
| **Semantic** (`--semantic`) | Conceptual queries ("how does auth work?") | Hybrid keyword + embedding similarity (text-embedding-3-small) | ~$0.0001/query |
| **Graph** (`--graph`) | Structural queries ("what depends on X?") | BFS from entry points, follows imports/deps/tests | Free |

Modes compose: `--semantic --graph` uses embeddings to find entry points, then graph traversal to discover structurally related files.

### Why semantic matters

Keyword-only resolution has a vocabulary gap. Query "how does authentication work?", but files are named `session-manager.ts`, `jwt-middleware.ts`, `login-handler.ts`. No literal match. Zero results.

Semantic mode embeds each file's identity (path + exports + headings + first sentence, ~100 tokens) and finds them by cosine similarity. The vocabulary gap closes.

## Five Depth Levels

The core idea. Instead of include/exclude, every file gets a depth level proportional to its relevance:

| Level | What the LLM sees | Token cost |
|-------|-------------------|------------|
| **Full** | Complete file content | 100% |
| **Detail** | Headings + first paragraphs | 40% |
| **Summary** | Headings + first sentences | 20% |
| **Headlines** | Heading tree only | 8% |
| **Mention** | Path + token count | 3% |

The packer runs 3 phases:
1. **Assign** initial depth from relevance score
2. **Demote** lowest-relevance files if over budget
3. **Promote** highest-relevance files if budget remains

Budget utilization: 95%. No wasted tokens.

## Six Knowledge Types

Files are auto-classified. At equal relevance, higher-priority types get better depth:

| Type | Priority | Examples |
|------|----------|---------|
| **Ground Truth** | 1st | Source code, schemas, API docs, PRDs |
| **Framework** | 2nd | Architecture docs, guidelines, conventions |
| **Evidence** | 3rd | Research, benchmarks, competitive analysis |
| **Signal** | 4th | Meeting notes, feedback, interviews |
| **Hypothesis** | 5th | Proposals, RFCs, roadmaps |
| **Artifact** | 6th | READMEs, changelogs, generated outputs |

Source code always gets better depth than a changelog, even at equal keyword match.

## How It Compares

| | Flat-chunk RAG | Aider repo-map | Koylan's Agent Skills | **This** |
|---|---|---|---|---|
| **What it is** | Vector search pipeline | Symbol listing | Educational guides | Working context packer |
| **Granularity** | Fixed-size chunks | 1 level (tags) | N/A (conceptual) | 5 depth levels per file |
| **Budget control** | Top-k cutoff | Truncate | N/A | Elastic demote/promote |
| **Structure** | Destroyed by chunking | AST tags | Described in prose | Heading tree + import graph |
| **Classification** | None | None | None | 6 knowledge types |
| **Semantic search** | Embeddings required | Not available | N/A | Optional (hybrid mode) |
| **Relations** | None | None | Described conceptually | imports, calls, tested_by, documents, configured_by |
| **Evaluated** | Varies | Not published | Not applicable | 30 queries, 3 repos, 5 budgets |
| **AST parsing** | N/A | tree-sitter | N/A | tree-sitter (14 langs) with regex fallback |
| **Runnable** | Yes (many deps) | Yes (aider) | No | Yes (Python stdlib + optional deps) |

## What Fixed the Most

| Fix | Impact on Recall@4K |
|-----|-------------------|
| camelCase splitting (`treeIndexer` → [tree, indexer]) | 0.490 → **0.983** |
| Semantic embedding resolution | 0 results → **full coverage** on conceptual queries |
| Stemmer expansion (`traversal` ↔ `traverser`) | +5% on morphological variants |
| KT priority sorting (ground_truth before artifacts) | Better depth allocation at equal relevance |
| Promotion threshold tuning (0.70 → 0.92) | Budget utilization 75% → **95%** |

## Architecture

```
context-engineering/
├── SKILL.md                          # Agent instructions (drop into any agent skill dir)
├── scripts/
│   ├── pack_context_lib.py           # Core: scoring, packing, knowledge types
│   ├── embed_resolve.py              # Embedding resolver: build, resolve, hybrid
│   ├── ast_extract.py                # tree-sitter AST extraction (14 languages)
│   ├── mcp_server.py                 # MCP server (stdio + HTTP)
│   ├── embeddingResolver.ts          # TypeScript port (for Node.js/browser agents)
│   ├── code_graph.py                 # Import graph: build + BFS traversal
│   ├── pack_context.py               # CLI: query → depth-packed output
│   ├── index_workspace.py            # Index local files → JSON
│   ├── index_github_repo.py          # Index GitHub repo via API → JSON
│   └── eval/                         # Evaluation suite (3 repos)
├── references/
│   └── eval-results.md               # Full methodology + per-query data
└── cache/                            # Runtime data (gitignored)
```

Python scripts: stdlib only for keyword/graph modes. `tree-sitter` for AST parsing (14 languages). `requests` for semantic mode.

TypeScript resolver: drop-in for any Node.js agent wanting hybrid resolution.

## Integration

### As an MCP server (Claude Desktop, Cursor, any MCP client)

```bash
# Install
pip install "mcp[cli]" requests

# Run (stdio for local agents)
python3 context-engineering/scripts/mcp_server.py

# Run (HTTP for remote agents)
python3 context-engineering/scripts/mcp_server.py --http 8000
```

MCP tools exposed:

| Tool | What it does |
|------|-------------|
| `pack` | Query → depth-packed context. Modes: keyword, semantic, graph, semantic+graph |
| `index_workspace` | Index a local directory |
| `index_github_repo` | Index a GitHub repo via API |
| `build_embeddings` | Build/refresh embedding cache for semantic mode |
| `resolve` | Find relevant files without packing (for debugging) |
| `stats` | Show index and cache statistics |

Claude Desktop config (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "context-engineering": {
      "command": "python3",
      "args": ["/path/to/context-engineering/scripts/mcp_server.py"]
    }
  }
}
```

### As an agent skill (Claude Code, Cursor, Sauna, etc.)

Copy `SKILL.md` into your agent's skill directory. The agent reads it, learns the commands, runs the scripts.

### As a Python library

```python
from pack_context_lib import tokenize_query, score_file, pack_context

tokens = tokenize_query("authentication middleware")
scored = [{'path': f['path'], 'relevance': score_file(f, tokens, "authentication middleware"),
           'tokens': f['tokens'], 'tree': f.get('tree')} for f in index['files']]
packed = pack_context([s for s in scored if s['relevance'] > 0], token_budget=8000)
```

### As a TypeScript module

```typescript
import { resolveHybridEntryPoints, buildEmbeddingCache } from './embeddingResolver.js';

const cache = await buildEmbeddingCache(graph, existingCache, apiKey);
const entries = await resolveHybridEntryPoints(query, graph, cache, apiKey);
```

## Origin

Extracted from [modular-patchbay](https://github.com/victorgjn/modular-patchbay), a context engineering IDE. The depth system, knowledge type classification, budget-aware traversal, and adaptive retrieval are the core innovations from that project.

## Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| No synonym matching in keyword mode | "billing" misses "payment" | Use `--semantic` mode |
| Generic filenames ("utils.ts") | Low relevance score | Graph mode finds them via imports |
| Precision ~10% at 50 files | Many low-relevance files included | `--quality` flag caps at 15 files |
| tree-sitter AST (optional) | Falls back to regex if tree-sitter not installed | `pip install tree-sitter tree-sitter-languages` |

## License

MIT
