"""Maximal Marginal Relevance (MMR) — diversity-aware reranking.

Port of modular-patchbay/src/services/treeAwareRetriever.ts (classifyQuery,
getMMRLambda, cosineSimilarity, applyMMR, computeDiversityScore).

The fix for cluster-dominance bias: pure relevance picks "10 lookalike DTOs from
the same directory"; MMR penalizes each next pick by similarity to what's
already selected, so the algorithm jumps to a different cluster once the first
is well-represented.

Usage:
  from mmr import classify_query, mmr_lambda, apply_mmr, diversity_score

  qtype = classify_query(query)        # 'factual' | 'analytical' | 'exploratory'
  lam   = mmr_lambda(qtype)            # 0.9 / 0.5 / 0.7
  picks = apply_mmr(candidates, lam, k=10)
  div   = diversity_score(picks)       # 0..1, higher = more diverse
"""
from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

QueryType = str  # Literal['factual', 'analytical', 'exploratory']


# ── Query classification ──

_ANALYTICAL_RX = re.compile(
    r"\b(compare|vs|versus|evaluate|pros and cons|advantages|disadvantages|"
    r"should we|tradeoffs?|better|worse|choose|decide|recommend)\b",
    re.I,
)
_FACTUAL_RX = re.compile(
    r"\b(what is|how does|who is|when did|where is|define|definition|"
    r"version|spec|api|format|syntax|command)\b",
    re.I,
)


def classify_query(query: str) -> QueryType:
    """Same heuristic as modular's classifyQuery."""
    q = query.lower()
    # Order matters: analytical wins over factual when both match (e.g. "compare A vs B").
    if _ANALYTICAL_RX.search(q):
        return "analytical"
    if len(q) < 50 or _FACTUAL_RX.search(q):
        return "factual"
    return "exploratory"


def mmr_lambda(qtype: QueryType) -> float:
    """λ controls relevance/diversity trade-off in MMR.
    factual:     λ=0.9  (high relevance, low diversity — answer needs precision)
    analytical:  λ=0.5  (balanced — both sides of the argument matter)
    exploratory: λ=0.7  (moderate diversity — broad scan of the space)
    """
    return {"factual": 0.9, "analytical": 0.5, "exploratory": 0.7}.get(qtype, 0.7)


# ── Vector ops ──

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0 if either vector has zero magnitude."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    mag = math.sqrt(na) * math.sqrt(nb)
    return dot / mag if mag else 0.0


# ── MMR ──

def apply_mmr(
    candidates: list[dict],
    lam: float,
    k: int,
) -> list[dict]:
    """Pick `k` items from `candidates` to maximize MMR score.

    Each candidate must have:
      - 'embedding': list[float]
      - 'relevance': float (e.g. cosine to query, or hybrid score)

    MMR score for an unselected candidate c:
        score(c) = λ · relevance(c) − (1−λ) · max_{s ∈ selected} cosine(c, s)

    Greedy: pick the candidate with the highest score, repeat until we have k.
    Returns candidates in selection order (each annotated with `mmr_score`).
    """
    if not candidates or k <= 0:
        return []

    pool = [c for c in candidates if c.get("embedding")]
    if not pool:
        return []

    selected: list[dict] = []
    while pool and len(selected) < k:
        best_idx = -1
        best_score = -math.inf
        for i, c in enumerate(pool):
            relevance = float(c.get("relevance", 0.0))
            if selected:
                max_sim = max(cosine(c["embedding"], s["embedding"]) for s in selected)
            else:
                max_sim = 0.0
            score = lam * relevance - (1.0 - lam) * max_sim
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx < 0:
            break
        chosen = pool.pop(best_idx)
        chosen = {**chosen, "mmr_score": best_score}
        selected.append(chosen)
    return selected


def diversity_score(items: Iterable[dict]) -> float:
    """1 - mean(pairwise cosine). Higher = more diverse selection."""
    arr = [i["embedding"] for i in items if i.get("embedding")]
    if len(arr) <= 1:
        return 1.0
    sims = []
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            sims.append(cosine(arr[i], arr[j]))
    return 1.0 - (sum(sims) / len(sims)) if sims else 1.0


# ── Convenience: full pipeline ──

def rerank_with_mmr(
    query_embedding: Sequence[float],
    candidates: list[dict],
    query_text: str | None = None,
    k: int = 10,
    lam: float | None = None,
) -> tuple[list[dict], dict]:
    """One-call pipeline:
      1. Compute relevance = cosine(query, candidate.embedding) for each
      2. Pick λ from query type (or use override)
      3. Apply MMR with that λ
      4. Return (picks, telemetry)

    Each candidate must already have an 'embedding'. Adds 'relevance' in place.
    Telemetry includes query_type, lambda used, diversity scores before/after.
    """
    for c in candidates:
        emb = c.get("embedding")
        c["relevance"] = cosine(query_embedding, emb) if emb else 0.0

    qtype = classify_query(query_text or "")
    if lam is None:
        lam = mmr_lambda(qtype)

    pure_top = sorted(candidates, key=lambda c: c["relevance"], reverse=True)[:k]
    mmr_top = apply_mmr(candidates, lam, k)

    return mmr_top, {
        "query_type": qtype,
        "lambda": lam,
        "candidates": len(candidates),
        "k": k,
        "diversity_before_mmr": diversity_score(pure_top),
        "diversity_after_mmr": diversity_score(mmr_top),
        "pure_relevance_top_k": [c.get("path", "?") for c in pure_top],
        "mmr_top_k": [c.get("path", "?") for c in mmr_top],
    }
