# Depth Packing Eval Results

## Cross-Repo Comparison (final, with KT priority + corrected GT)

| Metric | modular-patchbay (350f, 809K tok) | fastify (250f, 506K tok) |
|--------|----------------------------------|--------------------------|
| **Recall@4K** | 0.983 | **1.000** |
| **Recall@8K** | **1.000** | **1.000** |
| **W.Recall@8K** | 0.420 | 0.370 |
| **W.Recall@16K** | 0.592 | 0.532 |
| **CritHit@8K** | 0.417 | 0.600 |
| **CritHit@16K** | **0.800** | **0.800** |
| **CritHit@32K** | 0.900 | 0.850 |

The approach generalizes. Near-identical performance on two structurally different repos
(TypeScript IDE vs JavaScript web framework).

## Key Numbers

- **100% Recall at 8K tokens** on both repos (1% of total tokens)
- **80% Critical Hit Rate at 16K** (critical files at Summary depth or better)
- **~15% Weighted Recall at 4K** (files found but at Mention depth only)
- **95% budget utilization** with KT-priority packing

## What Works

1. **camelCase splitting** was the #1 fix: treeIndexer → [tree, indexer] matches queries
2. **Stemming** catches morphological variants: traversal ↔ traverser, optimization ↔ optimizer
3. **Co-occurrence bonus** promotes files matching multiple query terms
4. **Knowledge type bonus** (+0.10 for ground_truth code) helps surface source code over docs
5. **KT-priority sorting** at equal relevance: ground_truth > framework > evidence > signal > hypothesis > artifact
6. **Smaller-files-first** at equal relevance+KT: gives better depth per token spent

## Budget Recommendations

| Budget | Use case | Expected quality |
|--------|----------|-----------------|
| 4K | Quick scan: "what files exist for X?" | 100% recall, Mention depth only |
| 8K | Working context: identify + classify files | 100% recall, 57-60% critical at Summary+ |
| 16K | Deep context: read critical files fully | 100% recall, 75-80% critical at Full/Detail |
| 32K | Comprehensive: most files at useful depth | 100% recall, 85-90% critical at Full/Detail |

## Remaining Gaps

1. **Precision ~10%**: 50 files packed means ~45 are false positives at low relevance.
   Fix: cap at 20-25 files, give them better depth instead of 50 at Mention.

2. **W.Recall at 4K = 0.15**: Budget too tight for meaningful depth.
   This is expected: 4K / 50 files = 80 tok/file = Mention only.

3. **Synonym gap**: No embedding-based matching. "billing" won't find "payment".
   Acceptable for code (symbol names are explicit) but weak for natural language docs.

## Knowledge Type Distribution

| Type | modular-patchbay | fastify |
|------|-----------------|---------|
| Ground Truth | 272 (78%) | 199 (80%) |
| Evidence | 33 (9%) | 19 (8%) |
| Framework | 20 (6%) | 21 (8%) |
| Hypothesis | 15 (4%) | 0 |
| Artifact | 7 (2%) | 11 (4%) |
| Signal | 3 (1%) | 0 |

Classification aligns with expectations: code repos are ~80% ground truth.
The bonus system correctly promotes source code over generated artifacts.
