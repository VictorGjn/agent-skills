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

from _lib import corpus_store  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_corpus_store_backend():
    """Clear the cached storage backend before each test so env-var changes
    in monkeypatch fixtures actually re-resolve the backend on next access."""
    corpus_store.set_backend(None)
    yield
    corpus_store.set_backend(None)
