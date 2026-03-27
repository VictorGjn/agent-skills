# Agent Skills

Reusable skills for AI coding agents. Drop into any LLM agent's skill directory.

## context-engineering

Depth-packed context loading for codebases and knowledge bases. Two modes:

```
# Keyword: fast, broad scan
python3 scripts/pack_context.py "auth middleware session" --budget 8000

# Graph: follows imports/deps from entry points
python3 scripts/pack_context.py "auth middleware session" --budget 8000 --graph
```

**How it works:**

```
Index files → Score by keyword/stem → [optional: traverse import graph] → Pack at 5 depth levels → Output
```

The calling LLM is the semantic layer (query reformulation, synonym bridging). The packer handles structure and budget. No embedding model needed.

**What makes it different from flat-chunk RAG:**

| | Flat RAG | This |
|---|---|---|
| Granularity | Fixed-size chunks | 5 depth levels per file |
| Budget | Truncate at limit | Demote/promote per relevance |
| Structure | Ignored | Heading tree + import graph |
| Classification | None | 6 knowledge types with priority |
| Embeddings | Required | Not needed (LLM = semantic layer) |

See [context-engineering/SKILL.md](context-engineering/SKILL.md) for full docs.

## Architecture

```
skill-name/
├── SKILL.md        # Instructions for the LLM agent (<2500 tokens)
├── scripts/        # Executable code (Python, no deps beyond stdlib)
├── references/     # Eval results, methodology docs
└── cache/          # Runtime data (gitignored)
```

## Eval Results

Tested on 3 repos, 30 test cases, 5 budget levels:

| Repo | Lang | Files | Recall@8K | CritHit@16K |
|------|------|-------|-----------|-------------|
| modular-patchbay | TypeScript | 350 | **100%** | **80%** |
| fastify | JavaScript | 250 | **100%** | **80%** |
| flask | Python | 181 | **95%** | **80%** |

Graph mode adds +7% recall on structural queries (files connected by imports that keywords miss).

## Origin

Adapted from [modular-patchbay](https://github.com/victorgjn/modular-patchbay)'s graph engine, depth packer, and knowledge pipeline.

## License

Apache 2.0
