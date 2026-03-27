# Agent Skills

Reusable skills for AI coding agents. Drop into any LLM agent's skill directory.

## context-engineering

Depth-packed context loading for codebases and knowledge bases.

### The Problem

LLM agents load files all-or-nothing. Ask about authentication, get 2-3 files at 100% depth, miss the 15 related files. Or load everything and blow the context window.

### The Solution

Pack 50 files at 5 depth levels within a token budget. Full content for critical files, signatures for related files, just paths for peripheral ones. The calling LLM handles semantics (query expansion, synonym bridging). The packer handles structure and budget.

```
LLM reformulates query → Packer (structural match + knowledge types) → depth-packed output → LLM reads
```

No embedding model needed. Zero dependencies beyond Python stdlib.

### Quick Start

```bash
# Index a local directory
python3 context-engineering/scripts/index_workspace.py /path/to/files/

# Pack context for a query (keyword mode)
python3 context-engineering/scripts/pack_context.py "auth middleware session" --budget 8000

# Pack context (graph mode: follows imports from entry points)
python3 context-engineering/scripts/pack_context.py "auth middleware" --graph --budget 8000
```

### How It Compares

| | Flat-chunk RAG | Aider repo-map | This |
|---|---|---|---|
| **Granularity** | Fixed-size chunks | Symbol list (1 level) | 5 depth levels per file |
| **Budget control** | Truncate at limit | Truncate | Demote/promote per relevance |
| **Structure** | Ignored | AST-based | Heading tree + import graph |
| **Classification** | None | None | 6 knowledge types with priority |
| **Embeddings** | Required | Not used | Not needed (LLM = semantic layer) |
| **Language support** | Any | Python, JS, TS | Python, JS, TS, RST, MD, YAML |

---

## Evaluation

Tested on 3 repos (30 test cases, 5 budget levels each). Full methodology and per-query breakdowns in [references/eval-results.md](context-engineering/references/eval-results.md).

### Budget Curve (averaged across repos)

```
Budget   Recall   CritHit@depth≤2   Token cost vs full repo
──────   ──────   ────────────────   ──────────────────────
  2K      84%          0%            0.3%
  4K      98%          0%            0.5%
  8K     100%         42-65%         1.0%
 16K     100%         80%            2.0%
 32K     100%         85-90%         4.0%
```

**Key finding:** 100% recall at 8K tokens (1% of repo). Every ground truth file is found. At 16K (2%), 80% of critical files are at Summary depth or better.

### Cross-Repo Consistency

| Repo | Language | Files | Total tokens | Recall@8K | CritHit@16K |
|------|----------|-------|-------------|-----------|-------------|
| modular-patchbay | TypeScript | 350 | 809K | **100%** | **80%** |
| fastify | JavaScript | 250 | 506K | **100%** | **80%** |
| flask | Python+RST | 181 | 280K | **95%** | **80%** |

Results are stable across languages and repo structures. Flask at 95% because 2 generic-named docs (`config.rst`, `quickstart.rst`) don't match domain queries via keyword/stem.

### Graph Mode vs Keyword Mode

| Mode | Recall | Best for |
|------|--------|----------|
| Keyword | 90% | Broad topical queries ("voyage optimization", "error handling") |
| Graph | **97%** | Structural queries ("what depends on X?", "blast radius of this change") |

Graph follows imports/deps via BFS, discovering files that share no keywords with the query but are structurally connected. +7% recall on structural queries.

### What Fixed the Most

| Fix | Impact on Recall@4K |
|-----|-------------------|
| camelCase splitting (`treeIndexer` → [tree, indexer]) | 0.490 → **0.983** |
| Stemmer expansion (`traversal` ↔ `traverser`) | +5% on morphological variants |
| KT priority sorting (ground_truth before artifacts) | Better depth allocation |
| Promotion threshold tuning (0.70 → 0.92) | Budget utilization 75% → **95%** |

### Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| No synonym matching | "billing" misses "payment" | LLM expands queries before packing |
| Generic filenames invisible | `quickstart.rst` not found | Use `--graph` mode |
| Low precision (~10% at 50 files) | Many false positives | Use `--quality` for 15 files, ~30% precision |

---

## Architecture

```
context-engineering/
├── SKILL.md                          # Agent instructions (<700 tokens)
├── scripts/
│   ├── pack_context_lib.py           # Core: scoring, packing, knowledge types
│   ├── code_graph.py                 # Import graph: build + BFS traversal
│   ├── pack_context.py               # CLI: query → depth-packed output
│   ├── index_workspace.py            # Index local files → JSON
│   ├── index_github_repo.py          # Index GitHub repo via API → JSON
│   └── eval/                         # Eval scripts (3 repos)
│       ├── run_eval.py               # modular-patchbay eval
│       ├── run_eval_fastify.py       # fastify eval
│       ├── run_eval_flask.py         # flask eval
│       └── eval_graph_vs_keyword.py  # graph vs keyword comparison
├── references/
│   └── eval-results.md               # Full methodology + data
└── cache/                            # Runtime data (gitignored)
```

All scripts are Python 3.9+ with zero external dependencies (stdlib only).

## Origin

Adapted from [modular-patchbay](https://github.com/victorgjn/modular-patchbay)'s graph engine, depth packer, and knowledge pipeline. The depth system, knowledge type classification, and budget-aware traversal are the core innovations from that project.

## License

Apache 2.0
