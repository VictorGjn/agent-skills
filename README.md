# Agent Skills

Reusable skills for AI coding agents. Each skill is a self-contained package of instructions, scripts, and references that any LLM agent can load and use.

## Skills

### context-engineering

Depth-packed context loading for codebases and knowledge bases. Reduces token cost by 95-99% while maintaining 100% signal retrieval.

**Key idea:** Instead of loading 2-3 files fully, load 50 files at 5 depth levels (Full/Detail/Summary/Headlines/Mention). The LLM is the semantic layer; the packer handles structure and budget.

**Eval-proven on 3 repos, 30 test cases:**

| Budget | Recall | CritHit@depth≤2 | Token cost vs full |
|--------|--------|------------------|--------------------|
| 8K | **100%** | 42-65% | **1%** |
| 16K | **100%** | **80%** | 2% |
| 32K | **100%** | 85-90% | 4% |

See [context-engineering/SKILL.md](context-engineering/SKILL.md) for usage.

## Architecture

Each skill follows the standard skill folder structure:

```
skill-name/
├── SKILL.md        # Instructions for the LLM agent
├── scripts/        # Executable code (Python/TypeScript/Bash)
├── references/     # Documentation loaded as needed
├── assets/         # Templates, images for output
└── cache/          # Runtime data (gitignored)
```

## Origin

Built from patterns in [modular-patchbay](https://github.com/victorgjn/modular-patchbay) (context engineering IDE for AI agents). The depth packing system, knowledge type classification, and budget-aware traversal are adapted from that project's graph engine and knowledge pipeline.

## License

Apache 2.0
