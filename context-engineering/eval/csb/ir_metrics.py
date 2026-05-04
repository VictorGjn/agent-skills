"""Information-retrieval metrics for CSB ground-truth scoring.

CSB ships per-task `ground_truth.json` files listing the file paths that
correctly answer the task. We score CE's packed paths against that list.

Three metrics, all comparable to CSB's published headline numbers:
- file_recall = |retrieved ∩ truth| / |truth|
- precision_at_k (default k=5) = |retrieved[:k] ∩ truth| / k
- f1_at_k = harmonic mean of recall@k and precision@k

Path-normalization caveat: CE multi-corpus mode prefixes paths as
`<corpus_id>:<path>`. Strip the prefix before scoring against ground
truth (which uses bare repo-relative paths).
"""
from __future__ import annotations

from typing import Sequence


def _strip_corpus_prefix(path: str) -> str:
    """Multi-corpus mode prefixes `<corpus_id>:<path>`. Ground truth is bare paths."""
    if ":" in path and "/" not in path.split(":", 1)[0]:
        return path.split(":", 1)[1]
    return path


def _norm(paths: Sequence[str]) -> list[str]:
    return [_strip_corpus_prefix(p).replace("\\", "/").lstrip("./") for p in paths]


def file_recall(retrieved: Sequence[str], truth: Sequence[str]) -> float:
    """|retrieved ∩ truth| / |truth|. 0.0 when truth is empty (degenerate)."""
    if not truth:
        return 0.0
    r, t = set(_norm(retrieved)), set(_norm(truth))
    return len(r & t) / len(t)


def precision_at_k(retrieved: Sequence[str], truth: Sequence[str], k: int = 5) -> float:
    """|retrieved[:k] ∩ truth| / k. Honors retrieval order."""
    if k <= 0:
        return 0.0
    top = _norm(retrieved[:k])
    t = set(_norm(truth))
    hits = sum(1 for p in top if p in t)
    return hits / k


def recall_at_k(retrieved: Sequence[str], truth: Sequence[str], k: int = 5) -> float:
    """|retrieved[:k] ∩ truth| / |truth|."""
    if not truth:
        return 0.0
    top = set(_norm(retrieved[:k]))
    t = set(_norm(truth))
    return len(top & t) / len(t)


def f1_at_k(retrieved: Sequence[str], truth: Sequence[str], k: int = 5) -> float:
    """Harmonic mean of precision@k and recall@k."""
    p = precision_at_k(retrieved, truth, k)
    r = recall_at_k(retrieved, truth, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def score(retrieved: Sequence[str], truth: Sequence[str], k: int = 5) -> dict:
    """Bundle the three metrics into a single record."""
    return {
        "file_recall": file_recall(retrieved, truth),
        "precision_at_k": precision_at_k(retrieved, truth, k),
        "recall_at_k": recall_at_k(retrieved, truth, k),
        "f1_at_k": f1_at_k(retrieved, truth, k),
        "k": k,
        "n_retrieved": len(retrieved),
        "n_truth": len(truth),
    }


def aggregate(scores: list[dict]) -> dict:
    """Mean of each metric across tasks. Discards 0-truth tasks for recall/F1."""
    if not scores:
        return {}
    metrics = ["file_recall", "precision_at_k", "recall_at_k", "f1_at_k"]
    out: dict[str, float] = {}
    for m in metrics:
        valid = [s[m] for s in scores if s.get("n_truth", 0) > 0]
        out[m + "_mean"] = sum(valid) / len(valid) if valid else 0.0
    out["n_tasks"] = len(scores)
    out["n_zero_truth_tasks"] = sum(1 for s in scores if s.get("n_truth", 0) == 0)
    return out
