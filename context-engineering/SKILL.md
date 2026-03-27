---
name: context-engineering
description: "Depth-packed context loading for any codebase or document collection. Use when an LLM agent needs broad file awareness within a token budget: indexes files, classifies by knowledge type, packs at 5 depth levels. No embeddings needed; the calling LLM is the semantic layer."
requiredApps: []
---

# Context Engineering

Pack many files at varying depth into a token budget, instead of loading 2-3 fully.

## Architecture

```
LLM reformulates query → Packer (structural match) → depth-packed output → LLM reads
```

The calling LLM handles semantics (synonym bridging, query expansion). The packer handles structure, classification, and budget allocation. No embedding model needed.

## Usage

### 1. Index

```bash
# Local directory (markdown, code, config)
python3 scripts/index_workspace.py /path/to/files/

# GitHub repo (via API, needs GITHUB_TOKEN env)
python3 scripts/index_github_repo.py owner/repo --branch main
```

Output: `cache/workspace-index.json`

### 2. Pack

```bash
python3 scripts/pack_context.py "authentication middleware session" --budget 8000
python3 scripts/pack_context.py "query" --quality   # fewer files, deeper
python3 scripts/pack_context.py "query" --json       # structured output
```

### 3. Read

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

## Scoring

Keyword + stem matching on: path segments (camelCase-split), heading titles (full tree), root summary, filename parts. Co-occurrence bonus for multi-term queries. Knowledge type bonus.

## LLM Integration Pattern

1. **Expand** the user query with domain synonyms before calling the packer
2. **Scan** at 8K budget for landscape (finds all relevant files)
3. **Dive** into 2-3 critical files at full depth
4. **Re-pack** with different terms if gaps remain

## Scripts

| Script | Purpose |
|--------|---------|
| `pack_context_lib.py` | Core: scoring, packing, knowledge types |
| `index_workspace.py` | Index local files → JSON |
| `index_github_repo.py` | Index GitHub repo via API → JSON |
| `pack_context.py` | CLI: query → depth-packed output |

Details: `references/eval-results.md` for methodology and numbers.
