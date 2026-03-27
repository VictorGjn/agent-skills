---
name: context-engineering
description: "Depth-packed context loading for any codebase or document collection. Use when an LLM agent needs broad file awareness within a token budget: indexes files, classifies by knowledge type, packs at 5 depth levels. Supports keyword, graph, and semantic (embedding) modes."
requiredApps: []
---

# Context Engineering

Pack many files at varying depth into a token budget, instead of loading 2-3 fully.

## Architecture

```
Query → [Keyword scorer + Embedding resolver] → Entry points
                                                     ↓
                                              Graph traversal (optional)
                                                     ↓
                                              Depth-aware packing
                                                     ↓
                                              LLM reads packed context
```

Three resolution modes, composable:
- **Keyword** (default): stem/path matching. Fast, zero cost, good for keyword-rich queries.
- **Semantic** (`--semantic`): hybrid keyword + embedding similarity. Bridges vocabulary gap ("authentication" finds `session-manager.ts`). Requires one OpenAI API call per query.
- **Graph** (`--graph`): follows imports/deps from entry points. Finds structurally related files.
- **Semantic + Graph** (`--semantic --graph`): embedding-resolved entry points fed into graph traversal. The full pipeline.

## Usage

### 1. Index

```bash
# Local directory (markdown, code, config)
python3 scripts/index_workspace.py /path/to/files/

# GitHub repo (via API, needs GITHUB_TOKEN env)
python3 scripts/index_github_repo.py owner/repo --branch main
```

Output: `cache/workspace-index.json`

### 2. Build embeddings (for semantic mode)

```bash
# One-time: generate embeddings for all indexed files
# Only recomputes when file hash changes. ~$0.01 per 500 files.
python3 scripts/embed_resolve.py build cache/workspace-index.json
```

Output: `cache/embeddings.json`

### 3. Pack

```bash
# Keyword mode (fast, zero cost)
python3 scripts/pack_context.py "authentication middleware session" --budget 8000

# Semantic mode (hybrid keyword + embedding)
python3 scripts/pack_context.py "how does auth work" --semantic --budget 8000

# Graph mode (follows imports/deps)
python3 scripts/pack_context.py "PaymentService" --graph --budget 8000

# Full pipeline: semantic entry points → graph traversal
python3 scripts/pack_context.py "how does auth work" --semantic --graph --budget 8000

# Quality mode (fewer files, better depth per file)
python3 scripts/pack_context.py "query" --quality

# JSON output
python3 scripts/pack_context.py "query" --json
```

### When to use which mode

| Query type | Mode | Why |
|---|---|---|
| Keyword-rich ("PaymentService error handling") | `keyword` | Direct string match works fine |
| Conceptual ("how does auth work?") | `--semantic` | Bridges vocabulary gap |
| Structural ("what depends on X?") | `--graph` | Follows import/dep edges |
| Broad understanding ("explain the payment flow") | `--semantic --graph` | Full discovery |

### 4. Read

Use packed output for orientation. Read critical files fully with your file-read tool.

## Depth Levels

| Level | Content | Cost |
|-------|---------|------|
| Full | Complete file | 100% |
| Detail | Headings + first paragraphs | 40% |
| Summary | Headings + first sentences | 20% |
| Headlines | Heading tree only | 8% |
| Mention | Path + token count | 3% |

## Knowledge Types

Files are auto-classified. At equal relevance, higher-priority types get better depth:

| Type | Priority | What it matches |
|------|----------|----------------|
| Ground Truth | 1st | Source code, PRDs, API docs, schemas |
| Framework | 2nd | Guidelines, architecture, conventions |
| Evidence | 3rd | Research, benchmarks, competitive intel |
| Signal | 4th | Feedback, meeting notes, interviews |
| Hypothesis | 5th | Plans, proposals, roadmaps, RFCs |
| Artifact | 6th | READMEs, changelogs, generated outputs |

## Semantic Resolution (embed_resolve.py)

Fixes the main weakness of keyword-only resolution: when query terms don't appear literally in file paths, symbol names, or headings.

**How it works:**
1. Each file gets a compact "identity string" (~100 tokens): path + exports + headings + first sentence
2. Identities are embedded via `text-embedding-3-small` (512 dims, cached per content hash)
3. At query time: embed query → cosine similarity against all file identities
4. Hybrid scoring: `combined = keyword * 0.4 + semantic * 0.6`

**Standalone usage:**
```bash
# Build embeddings
python3 scripts/embed_resolve.py build

# Semantic-only resolve
python3 scripts/embed_resolve.py resolve "how does authentication work?"

# Hybrid resolve (keyword + semantic)
python3 scripts/embed_resolve.py resolve "authentication middleware" --hybrid

# Cache stats
python3 scripts/embed_resolve.py stats
```

**Cost:** ~$0.01 per 500 files indexed. One API call per query (~$0.0001).

## TypeScript Integration (modular-patchbay)

`embeddingResolver.ts` is a drop-in replacement for `resolver.ts` in the context graph:

```typescript
import { resolveHybridEntryPoints, buildEmbeddingCache } from './embeddingResolver.js';

// Build cache once (persists, only re-embeds changed files)
const cache = await buildEmbeddingCache(graph, existingCache, apiKey);

// Hybrid resolve: lexical + semantic
const entries = await resolveHybridEntryPoints(query, graph, cache, apiKey);

// Feed into existing traverser
const result = traverseGraph(entries, graph, preset);
const packed = packContext(result, tokenBudget);
```

## Scoring

**Keyword mode:** stem matching on path segments (camelCase-split), heading titles (full tree), root summary, filename parts. Co-occurrence bonus for multi-term queries. Knowledge type bonus.

**Semantic mode:** cosine similarity between query embedding and file identity embedding. Combined with keyword score: `0.4 * keyword + 0.6 * semantic`.

**Graph mode:** BFS from entry points with relevance decay (0.65 per hop). Relation weights: imports=1.0, calls=0.7, extends=0.9, tested_by=0.6, documents=0.5.

## LLM Integration Pattern

1. **Expand** the user query with domain synonyms before calling the packer
2. **Scan** at 8K budget with `--semantic` for landscape (finds files invisible to keyword search)
3. **Dive** into 2-3 critical files at full depth
4. **Re-pack** with `--semantic --graph` if structural context needed

## Scripts

| Script | Purpose |
|--------|---------|
| `pack_context_lib.py` | Core: scoring, packing, knowledge types |
| `embed_resolve.py` | Embedding resolver: build, resolve, hybrid |
| `embeddingResolver.ts` | TypeScript equivalent for modular-patchbay |
| `code_graph.py` | Import/dependency graph: build + BFS traversal |
| `index_workspace.py` | Index local files → JSON |
| `index_github_repo.py` | Index GitHub repo via API → JSON |
| `pack_context.py` | CLI: query → depth-packed output (keyword, semantic, graph) |

Details: `references/eval-results.md` for methodology and numbers.
