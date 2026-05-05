# Bench diff

## Aggregate metrics

| metric | C1 | C2 | C3 | C4 | Δ (vs C1) |
|---|---|---|---|---|---|
| file_recall | 0.000 (0.000–0.000) | 0.362 (0.017–0.708) | 0.362 (0.017–0.708) | 0.362 (0.017–0.708) | +0.362 |
| precision_at_k | 0.000 (0.000–0.000) | 0.300 (0.100–0.500) | 0.300 (0.100–0.500) | 0.300 (0.100–0.500) | +0.300 |
| recall_at_k | 0.000 (0.000–0.000) | 0.362 (0.017–0.708) | 0.362 (0.017–0.708) | 0.362 (0.017–0.708) | +0.362 |
| f1_at_k | 0.000 (0.000–0.000) | 0.306 (0.029–0.583) | 0.306 (0.029–0.583) | 0.306 (0.029–0.583) | +0.306 |

## Per-task deltas (top 20 by |Δ file_recall|)

| task_id | C1 | C2 | C3 | C4 | Δ |
|---|---|---|---|---|---|
| flipt-degraded-context-fix-001 | 0.000 | 0.750 | 0.750 | 0.750 | +0.750 |
| flipt-repo-scoped-access-001 | 0.000 | 0.667 | 0.667 | 0.667 | +0.667 |
| flipt-dep-refactor-001 | 0.000 | 0.033 | 0.033 | 0.033 | +0.033 |
| flipt-flagexists-refactor-001 | 0.000 | 0.000 | 0.000 | 0.000 | +0.000 |

## Hypothesis verdicts

### H1 — codestral > keyword
file_recall: C1=0.000, C2=0.362, Δ=+0.362 → **PASS ✓**

### H2-IR — MMR changes top-K composition
Jaccard<0.7 on 4/4 = 100.0% → **PASS ✓**

### H4 / H7
Reward + budget-sweep gates require Haiku (Phase 1 / Phase 3 of bench plan). IR-only path can't decide them.
