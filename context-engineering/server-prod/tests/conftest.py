"""Shared pytest fixtures.

Auto-reset corpus_store's backend singleton between tests so a Phase A
test that sets BlobBackend doesn't leak into a Phase 3 test that expects
LocalBackend (since pytest runs tests alphabetically, the leak would be
asymmetric and hard to debug).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

from _lib import corpus_store, job_store  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_module_singletons():
    """Clear module-level mutable state between tests so cross-test leakage
    can't mask real bugs.

    Active singletons (per Codex review of PR #50, Concern 5):
    - corpus_store._BACKEND  — env-driven backend resolver cache
    - job_store._JOBS        — in-memory job registry (Phase B will move
                               to Vercel KV; until then it's process-scoped)

    Read-only registries (no reset needed): transport._REGISTRY (filled at
    import); _ROLE_CAPS, _CLASSIFICATION_RANK, _DEPTH_NAMES, _KT_PATTERNS
    (frozen config).
    """
    corpus_store.set_backend(None)
    job_store._JOBS.clear()
    yield
    corpus_store.set_backend(None)
    job_store._JOBS.clear()
