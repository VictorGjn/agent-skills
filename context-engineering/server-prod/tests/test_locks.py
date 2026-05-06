"""Tests for backend-aware corpus write locks (_lib/locks.py).

Codex P1 on PR #50: filesystem locks don't protect cross-instance Blob
writes. acquire_corpus_write_lock picks fs lock for LocalBackend (intra-
instance, kept for tests + local dev) and KV lock for BlobBackend (cross-
instance safe).

Covers:
- LocalBackend → fs lock acquired/released; second acquire blocks until
  released
- BlobBackend → KV lock acquired/released; uses kv.acquire_lock under the
  hood with the corpus_id-prefixed key
- Lock refused (None returned) when already held by another caller
- Release is idempotent (no-op if not held)
- Wrong holder release on KV path silently no-ops (check-and-delete)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

from _lib import corpus_store, locks  # noqa: E402
from _lib.storage import blob as blob_mod  # noqa: E402


@pytest.fixture
def local_cache(tmp_path, monkeypatch):
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("CE_STORAGE_BACKEND", raising=False)
    yield cd


def test_local_lock_acquired_and_released(local_cache):
    held = locks.acquire_corpus_write_lock("test-corpus")
    assert held is not None
    assert held.kind == "fs"
    locks.release_corpus_write_lock("test-corpus", held)


def test_local_lock_second_acquire_returns_none_until_released(local_cache):
    first = locks.acquire_corpus_write_lock("test-corpus")
    assert first is not None

    # Second attempt while first is held → None (CORPUS_LOCKED)
    second = locks.acquire_corpus_write_lock("test-corpus")
    assert second is None

    locks.release_corpus_write_lock("test-corpus", first)

    # Now another caller can take it
    third = locks.acquire_corpus_write_lock("test-corpus")
    assert third is not None
    locks.release_corpus_write_lock("test-corpus", third)


def test_local_lock_release_is_idempotent(local_cache):
    """A double-release (e.g. in finally + an outer except) shouldn't crash."""
    held = locks.acquire_corpus_write_lock("test-corpus")
    assert held is not None
    locks.release_corpus_write_lock("test-corpus", held)
    locks.release_corpus_write_lock("test-corpus", held)  # no-op


# ── KV (Blob backend) path ──

class _FakeKVForLocks:
    """Records acquire/release calls + simulates SET NX semantics."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.calls: list[tuple] = []

    def acquire_lock(self, name: str, *, ttl_seconds: int = 90,
                     holder: str | None = None) -> str | None:
        self.calls.append(("acquire", name, ttl_seconds))
        if name in self.store:
            return None
        import uuid
        h = holder or uuid.uuid4().hex
        self.store[name] = h
        return h

    def release_lock(self, name: str, holder: str) -> bool:
        self.calls.append(("release", name, holder))
        if self.store.get(name) == holder:
            del self.store[name]
            return True
        return False


@pytest.fixture
def blob_backend(tmp_path, monkeypatch):
    """Force corpus_store to use BlobBackend (so locks picks the KV path).
    Provide a fake kv module so we don't make real network calls."""
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    # Set the env that kv.py needs even though we'll patch the module —
    # otherwise corpus_store.get_backend() picks LocalBackend first.
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-blob-token")
    corpus_store.set_backend(blob_mod.BlobBackend())

    fake_kv = _FakeKVForLocks()
    # Patch the kv functions locks.py imports
    from _lib.storage import kv as kv_module
    monkeypatch.setattr(kv_module, "acquire_lock", fake_kv.acquire_lock)
    monkeypatch.setattr(kv_module, "release_lock", fake_kv.release_lock)
    yield fake_kv


def test_kv_lock_acquired_for_blob_backend(blob_backend):
    held = locks.acquire_corpus_write_lock("test-corpus")
    assert held is not None
    assert held.kind == "kv"
    # The KV key should be namespaced under corpus:<id>
    acquire_calls = [c for c in blob_backend.calls if c[0] == "acquire"]
    assert acquire_calls
    assert acquire_calls[0][1] == "corpus:test-corpus"


def test_kv_lock_blocks_concurrent_writer(blob_backend):
    held = locks.acquire_corpus_write_lock("test-corpus")
    assert held is not None
    # A second instance trying to acquire (simulated by the same in-memory
    # KV store) gets None.
    second = locks.acquire_corpus_write_lock("test-corpus")
    assert second is None

    locks.release_corpus_write_lock("test-corpus", held)
    third = locks.acquire_corpus_write_lock("test-corpus")
    assert third is not None


def test_kv_lock_release_only_with_matching_holder(blob_backend):
    """The check-and-delete EVAL script in kv.release_lock means a stale
    caller can't accidentally release a lock another worker now holds."""
    held = locks.acquire_corpus_write_lock("test-corpus")
    assert held is not None
    # Simulate someone forgetting to release within TTL: force a wrong-holder
    # release attempt
    fake_held = locks.LockHeld(kind="kv", token="someone-else-token")
    locks.release_corpus_write_lock("test-corpus", fake_held)
    # Real holder still has the lock — second caller still blocked
    second = locks.acquire_corpus_write_lock("test-corpus")
    assert second is None

    # Real holder releases — now another caller can acquire
    locks.release_corpus_write_lock("test-corpus", held)
    third = locks.acquire_corpus_write_lock("test-corpus")
    assert third is not None
