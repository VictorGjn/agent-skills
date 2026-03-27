---
name: context-engineering
description: "Depth-packed context loading for codebases and knowledge bases. Use when a task needs broad awareness across many files within a token budget. Indexes repos/workspaces, classifies files by knowledge type, and packs context at 5 depth levels. The calling LLM is the semantic layer; the packer handles structure and budget. Eval-proven: 100% recall at 1% token cost across 3 repos."
requiredApps: []
---

# Context Engineering

Load context at 5 depth levels instead of all-or-nothing. The calling LLM handles semantic understanding (query reformulation, synonym bridging, interpretation). The packer handles structural matching, knowledge type classification, and budget-optimal depth allocation.

```
LLM (semantic) → reformulated query → Packer (structural) → depth-packed context → LLM (interprets)
```

## Quick Start

```bash
# Index a local workspace
python3 scripts/index_workspace.py /path/to/docs/

# Pack context for a query
python3 scripts/pack_context.py "authentication middleware session" --budget 8000

# Quality mode: fewer files, better depth per file
python3 scripts/pack_context.py "query" --budget 8000 --quality

# JSON output for programmatic use
python3 scripts/pack_context.py "query" --json
```

## Depth Levels

| Level | Name | Content included | Cost ratio |
|-------|------|------------------|-----------|
| 0 | Full | Complete file content | 100% |
| 1 | Detail | Headings + first paragraphs | 40% |
| 2 | Summary | Headings + first sentences | 20% |
| 3 | Headlines | Heading tree structure only | 8% |
| 4 | Mention | File path + token count | 3% |

## Knowledge Type Classification

Each indexed file is auto-classified. At equal relevance, higher-priority types get better depth:

| Type | Priority | Bonus | Matches |
|------|----------|-------|---------|
| Ground Truth | 1 | +0.10 | Source code, PRDs, API docs, schemas, configs |
| Framework | 2 | +0.05 | Guidelines, architecture docs, conventions, playbooks |
| Evidence | 3 | 0.00 | Research, benchmarks, competitive intel, case studies |
| Signal | 4 | -0.05 | Feedback, meeting notes, interviews, discussions |
| Hypothesis | 5 | -0.05 | Plans, proposals, roadmaps, RFCs, drafts |
| Artifact | 6 | -0.10 | Generated outputs, READMEs, changelogs, exports |

## Scoring

Keyword + stem matching against:
- **Path segments** with camelCase splitting (`treeIndexer.ts` → [tree, indexer])
- **All heading titles** (recursive, full tree depth)
- **Root first sentence/paragraph**
- **Filename parts** (split on `-_`, camelCase)
- **Co-occurrence bonus** for multi-term queries
- **Knowledge type bonus** per table above

No embedding model needed. The calling LLM bridges synonyms by reformulating queries.

## 3-Phase Budget Packer

1. **Assign** initial depth by relevance score
2. **Demote** lowest-priority files if over budget (bottom-up, artifact first)
3. **Promote** highest-priority files if budget remains (top-down, ground_truth first)

Sort key at equal relevance: ground_truth > framework > evidence > signal > hypothesis > artifact, then smaller files first (better depth per token).

## Eval Results

Tested on 3 repos (30 test cases, 5 budget levels each):

| Repo | Language | Files | Tokens | Recall@8K | CritHit@16K |
|------|----------|-------|--------|-----------|-------------|
| modular-patchbay | TypeScript | 350 | 809K | **100%** | **80%** |
| fastify | JavaScript | 250 | 506K | **100%** | **80%** |
| flask | Python+RST | 181 | 280K | **94.7%** | **80%** |

Full results: `references/eval-results.md`

## Scripts

| Script | Purpose |
|--------|---------|
| `pack_context_lib.py` | Core library: scoring, packing, knowledge types |
| `index_workspace.py` | Index local markdown files into heading-tree JSON |
| `index_github_repo.py` | Index GitHub repos via API (code + docs) |
| `pack_context.py` | CLI: query → depth-packed markdown or JSON |

## LLM Integration Pattern

The optimal pattern for an LLM agent using this skill:

1. **Reformulate**: Before calling the packer, expand the user's query with domain terms
   - User: "how does auth work?" → LLM calls packer with: "authentication login middleware session token"
2. **Scan**: Run packer at 8K budget to get the landscape (100% recall)
3. **Dive**: Read the 2-3 most relevant files fully with the file-read tool
4. **Iterate**: If gaps remain, reformulate and re-pack with different terms

This pattern gives the LLM both breadth (50 files at varying depth) and depth (full content of critical files) without blowing the context budget.
