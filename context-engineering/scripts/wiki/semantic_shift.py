"""Semantic-shift detector — when to re-consolidate an entity (per GAM).

The synthesizer (Phase 2) does not run on every new event. It runs when, for a
given entity, EITHER:

  1. Drift threshold: the cosine distance between the centroid of new
     unconsolidated event embeddings and the entity's saved centroid exceeds
     `drift_threshold` (default 0.35).
  2. Volume threshold: at least `volume_threshold` events (default 8) have
     accumulated for the entity since the last consolidation.
  3. Explicit trigger: `force=True`.

This decouples write-cost from read-quality, exactly as GAM intends:
ongoing dialogue / signals stream into events/, consolidation is deferred.
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class ShiftReport:
    consolidate: bool
    reason: str
    drift: float
    n_events: int
    centroid_changed: bool


def cosine_distance(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    sim = dot / (na * nb)
    sim = max(-1.0, min(1.0, sim))
    return 1.0 - sim


def _centroid(vectors: list[list[float]]) -> list[float]:
    """Mean of equal-length vectors. Pick the most common dimension as the
    canonical one (so a single outlier in first position can't discard all
    otherwise-compatible embeddings), and skip vectors that don't match it.
    Returns [] when no usable vectors remain.

    Embedding model/version drift can leak differently-sized vectors into a
    cached event stream — we'd rather return a slightly stale centroid than
    IndexError out of consolidation, AND we'd rather not silently drop the
    majority just because one bad sample arrived first.
    """
    if not vectors:
        return []
    from collections import Counter
    dim_counts = Counter(len(v) for v in vectors if v)
    if not dim_counts:
        return []
    canonical_dim, _ = dim_counts.most_common(1)[0]
    if canonical_dim == 0:
        return []
    out = [0.0] * canonical_dim
    valid = 0
    for v in vectors:
        if len(v) != canonical_dim:
            continue
        for i, x in enumerate(v):
            out[i] += x
        valid += 1
    if valid == 0:
        return []
    return [x / valid for x in out]


def should_consolidate(*,
                       entity_centroid: list[float] | None,
                       new_event_embeddings: list[list[float]],
                       drift_threshold: float = 0.35,
                       volume_threshold: int = 8,
                       force: bool = False) -> ShiftReport:
    """Apply the three-part rule and return a structured report."""
    n = len(new_event_embeddings)

    if force:
        return ShiftReport(True, 'forced', 1.0, n, True)
    if n == 0:
        return ShiftReport(False, 'no new events', 0.0, 0, False)
    # Check missing centroid BEFORE the volume trigger. A first-time entity
    # with 8+ events would otherwise hit the volume branch with
    # centroid_changed=False, leaving callers to skip writing the initial
    # centroid and anchoring all future drift checks on missing state.
    if not entity_centroid:
        return ShiftReport(True, 'no prior centroid', 1.0, n, True)
    if n >= volume_threshold:
        return ShiftReport(True, f'volume ≥ {volume_threshold}', 0.0, n, False)

    new_centroid = _centroid(new_event_embeddings)
    # If every new embedding was dim-incompatible with the others, _centroid
    # returns []. cosine_distance on an empty vector returns 1.0 — which would
    # falsely trigger consolidation as if the entity had drifted to the max.
    # Treat "no comparable embeddings" as "can't measure drift, hold off".
    if not new_centroid or len(new_centroid) != len(entity_centroid):
        return ShiftReport(False, 'no comparable embeddings (dim mismatch)',
                           0.0, n, False)
    drift = cosine_distance(entity_centroid, new_centroid)
    if drift >= drift_threshold:
        return ShiftReport(True, f'drift {drift:.3f} ≥ {drift_threshold}', drift, n, True)
    return ShiftReport(False, f'drift {drift:.3f} below {drift_threshold}', drift, n, False)
