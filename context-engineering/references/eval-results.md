# Evaluation Results

## Methodology

### Setup
- 3 public repos, 3 languages, varying structure
- 10 test cases per repo (30 total), each with 3-7 ground truth files + 1-2 critical files
- 5 budget levels: 2K, 4K, 8K, 16K, 32K tokens
- Ground truth verified against actual repo file tree (corrected after initial false negatives from wrong filenames)

### Repos

| Repo | Language | Files indexed | Total tokens | Structure |
|------|----------|--------------|--------------|-----------|
| victorgjn/modular-patchbay | TypeScript | 350 | 809,207 | IDE: services, graph engine, stores, components |
| fastify/fastify | JavaScript | 250 | 505,672 | Framework: lib/, types/, docs/, tests/ |
| pallets/flask | Python + RST | 181 | 279,743 | Framework: src/flask/, docs/, examples/ |

### Metrics

| Metric | Definition |
|--------|-----------|
| **Recall** | Fraction of ground truth files appearing in packed output (at any depth) |
| **Weighted Recall (WR)** | Recall weighted by depth: Full=1.0, Detail=0.8, Summary=0.5, Headlines=0.3, Mention=0.15 |
| **Precision** | Fraction of packed files that are in ground truth |
| **Critical Hit Rate (CH)** | Fraction of critical files at Summary depth or better (depth <= 2) |
| **Budget Utilization** | tokens_used / budget |

### Ground Truth Selection

Test queries cover distinct architectural areas per repo. Ground truth files are the minimal set an engineer would need to understand that area. Critical files are the 1-2 files that MUST be read fully.

Example (modular-patchbay):
- Query: "context graph engine traversal"
- GT: `src/graph/index.ts`, `traverser.ts`, `types.ts`, `resolver.ts`, `packer.ts`, `db.ts`, `scanner.ts` (7 files)
- Critical: `traverser.ts`, `types.ts`, `index.ts` (3 files, must be at Summary+)

---

## Results: Budget Curve

### modular-patchbay (350 files, 809K tokens)

| Budget | Recall | W.Recall | Precision | CritHit | Files | Tok Used | Utilization |
|--------|--------|----------|-----------|---------|-------|----------|-------------|
| 2K | 0.843 | 0.129 | 0.195 | 0.000 | 24 | 1,937 | 97% |
| 4K | **0.983** | 0.152 | 0.114 | 0.000 | 45 | 3,910 | 98% |
| 8K | **1.000** | 0.420 | 0.104 | 0.417 | 50 | 7,765 | 97% |
| 16K | **1.000** | 0.592 | 0.104 | **0.800** | 50 | 15,458 | 97% |
| 32K | **1.000** | 0.697 | 0.104 | **0.900** | 50 | 31,325 | 98% |

### fastify (250 files, 506K tokens)

| Budget | Recall | W.Recall | Precision | CritHit | Files | Tok Used |
|--------|--------|----------|-----------|---------|-------|----------|
| 2K | 0.853 | 0.128 | 0.290 | 0.000 | 14 | 1,847 |
| 4K | **1.000** | 0.153 | 0.135 | 0.000 | 37 | 3,946 |
| 8K | **1.000** | 0.370 | 0.098 | 0.600 | 50 | 7,192 |
| 16K | **1.000** | 0.532 | 0.098 | **0.800** | 50 | 14,616 |
| 32K | **1.000** | 0.637 | 0.098 | **0.850** | 50 | 28,573 |

### flask (181 files, 280K tokens)

| Budget | Recall | W.Recall | Precision | CritHit | Files | Tok Used |
|--------|--------|----------|-----------|---------|-------|----------|
| 2K | 0.827 | 0.135 | 0.105 | 0.000 | 33 | 1,962 |
| 4K | **0.947** | 0.220 | 0.077 | 0.150 | 50 | 3,895 |
| 8K | **0.947** | 0.482 | 0.078 | 0.650 | 50 | 7,802 |
| 16K | **0.947** | 0.644 | 0.078 | **0.800** | 50 | 15,059 |
| 32K | **0.947** | 0.771 | 0.078 | **0.900** | 50 | 29,064 |

---

## Results: Graph Mode vs Keyword Mode

Tested on modular-patchbay, 5 queries, 3 budgets.

| Budget | KW Recall | Graph Recall | KW Precision | Graph Precision | WR Delta |
|--------|-----------|-------------|--------------|-----------------|----------|
| 4K | 0.900 | **0.967** | 0.173 | **0.187** | +0.023 |
| 8K | 0.900 | **0.967** | 0.173 | **0.187** | +0.002 |
| 16K | 0.900 | **0.967** | 0.173 | **0.187** | -0.026 |

Graph mode improves recall by +7% through structural discovery: files connected via imports that keywords miss. Example: "knowledge pipeline retrieval provenance" found `contrastiveRetrieval.ts` and `treeAwareRetriever.ts` via imports from `knowledgePipeline.ts`.

Graph slightly reduces WR at high budgets because newly discovered files consume budget that would otherwise promote existing files to higher depth.

**Use graph for:** specific modules, blast radius, dependency questions.
**Use keyword for:** broad topical queries.

---

## Results: Knowledge Type Classification

### Distribution

| Type | modular-patchbay | fastify | flask |
|------|-----------------|---------|-------|
| Ground Truth | 272 (78%) | 199 (80%) | 86 (48%) |
| Framework | 20 (6%) | 21 (8%) | 28 (15%) |
| Evidence | 33 (9%) | 19 (8%) | 3 (2%) |
| Hypothesis | 15 (4%) | 0 | 0 |
| Artifact | 7 (2%) | 11 (4%) | 62 (34%) |
| Signal | 3 (1%) | 0 | 2 (1%) |

Code repos are ~80% ground_truth. Flask has more artifacts (READMEs, examples, config).

### KT Priority Impact

At equal relevance, ground_truth files are promoted over evidence/artifact:

- **Without KT priority:** Audit file (evidence, 2K tok) at Full; PRD (ground_truth, 14K tok) at Mention
- **With KT priority:** PRD at Summary (2.8K tok), other PRDs at Headlines; audit at Mention
- **Effect:** Canonical source material gets meaningful depth instead of peripheral documents

---

## Per-Query Detail (modular-patchbay @8K)

| Query | R | WR | CH | Notes |
|-------|---|----|----|-------|
| context graph engine traversal | 1.00 | 0.54 | 0.67 | All 7 graph files found; traverser.ts at Full |
| tree indexer depth filter | 1.00 | 0.45 | 1.00 | treeIndexer.ts at Full via camelCase split |
| knowledge pipeline retrieval provenance | 1.00 | 0.28 | 0.00 | All 6 found but critical files at Mention (large) |
| MCP server tools integration | 1.00 | 0.39 | 0.00 | 5/5 found; "mcp" matches path segment |
| repo indexer feature clustering | 1.00 | 0.70 | 0.00 | 3/3 at high depth (small files) |
| agent export Claude Code | 1.00 | 0.32 | 0.50 | "claude-config" matched via stem |
| embedding service semantic search | 1.00 | 0.43 | 1.00 | embeddingService split correctly |
| memory system fact extraction | 1.00 | 0.32 | 0.00 | 6/6 found; factExtractor matched |
| metaprompt v2 pattern generation | 1.00 | 0.15 | 0.00 | All at Mention (many files, tight budget) |
| connector notion slack github | 1.00 | 0.62 | 1.00 | notion.ts, slack.ts, github.ts all at Full |

---

## Key Fixes and Their Impact

| Fix | Metric | Before | After |
|-----|--------|--------|-------|
| camelCase splitting | Recall@4K | 0.490 | **0.983** |
| Stemmer (al, ial, able) | traversal/traverser match | miss | hit |
| Co-occurrence bonus | Multi-term precision | low | +0.3 for coverage |
| KT priority sorting | PRD at Summary vs Mention | Mention | **Summary** |
| Promotion threshold 0.7→0.92 | Budget utilization | ~75% | **~95%** |

---

## Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|-----------|
| Synonym gap | No embeddings; "billing" misses "payment" | LLM should expand queries with synonyms before packing |
| Generic filenames | `config.rst`, `quickstart.rst` don't match domain queries | Use `--graph` to find via import/reference links |
| Large files at Mention | 25K file at Mention costs 750 tok for just the path | Use `--quality` (15 files, better depth per file) |
| Low precision (~10%) | 50 files packed, ~45 are false positives | Acceptable for scan; use `--quality` for ~30% precision |
| Flask recall cap at 94.7% | 2 generic-named docs invisible to keywords | Would need content-level matching (not just path/heading) |

---

## How to Reproduce

```bash
# 1. Fetch repo files via GitHub API (use eval scripts or manual fetch)
# Session files needed: session/{repo}-files.json (array of {path, size, content})

# 2. Run eval on modular-patchbay
python3 scripts/eval/run_eval.py

# 3. Run eval on fastify
python3 scripts/eval/run_eval_fastify.py

# 4. Run eval on flask
python3 scripts/eval/run_eval_flask.py

# 5. Compare graph vs keyword
python3 scripts/eval/eval_graph_vs_keyword.py

# Test cases are TESTCASES arrays inside each eval script.
# Ground truth was verified against actual repo file trees.
```
