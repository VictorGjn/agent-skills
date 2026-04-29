"""context-engineering wiki package — additive helpers for compounding code knowledge.

Today this package ships only the GAM-style consolidation primitives that source's
existing `feature_map.py` + `concept_labeler.py` + `community_detect.py` pipeline
doesn't yet have:

  events           — append-only event log writer (the GAM event progression layer)
  semantic_shift   — cosine-drift / volume-threshold trigger for re-consolidation

Source's feature_map / concept_labeler already cover the entity-clustering side.
The two pieces here let you defer the LLM relabel cost until enough drift has
accumulated, instead of relabeling on every re-index.

Reference: Wu et al, "Hierarchical Graph-based Agentic Memory for LLM Agents"
(arXiv:2604.12285, April 2026).
"""
from .events import append_event, read_events  # noqa: F401
from .semantic_shift import (  # noqa: F401
    cosine_distance, should_consolidate, ShiftReport,
)
