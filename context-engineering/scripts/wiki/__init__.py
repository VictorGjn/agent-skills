"""context-engineering wiki package — additive helpers for compounding code knowledge.

  events           — append-only event log writer (the GAM event progression layer)
  semantic_shift   — cosine-drift / volume-threshold trigger for re-consolidation
  source_adapter   — Source ABC + EventStreamSource (push-shaped: skills emit
                     events back into the brain, closing the Anabasis loop)

Source's feature_map / concept_labeler already cover the entity-clustering side.
The pieces here let you defer the LLM relabel cost until enough drift has
accumulated (semantic_shift) and let any skill write back into the brain via
the same events log every other connector uses (source_adapter).

Reference: Wu et al, "Hierarchical Graph-based Agentic Memory for LLM Agents"
(arXiv:2604.12285, April 2026).
"""
from .events import append_event, read_events  # noqa: F401
from .semantic_shift import (  # noqa: F401
    cosine_distance, should_consolidate, ShiftReport,
)
from .source_adapter import Source, EventStreamSource  # noqa: F401
