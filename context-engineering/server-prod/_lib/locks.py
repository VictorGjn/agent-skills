"""Corpus write locks — backend-aware.

Phase A added intra-instance filesystem locks in `tools/upload_corpus.py`
(`_acquire_lock` / `_release_lock`). Phase B exposes that the lock no
longer protects the actual write target: Blob is a SHARED backend, so two
Vercel instances can each pass their own filesystem lock and both write
to the same Blob key — last-writer-wins, silently. (Codex P1 on PR #50.)

Strategy: pick the lock primitive based on the active storage backend.
- LocalBackend: filesystem lock (kept; fine because the LocalBackend
  storage is also filesystem — same instance is the only writer).
- BlobBackend: Vercel KV lock via `SET NX EX` + `EVAL` check-and-delete
  (built in Phase B.1). Cross-instance safe.

The two `_acquire_lock` / `_release_lock` private helpers in
`tools/upload_corpus.py` stay for now (used directly by tests + the
filesystem path); production callers should migrate to
`acquire_corpus_write_lock()`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LockHeld:
    """Returned by acquire_corpus_write_lock on success. Caller passes it
    back to release_corpus_write_lock so we can discriminate fs vs kv
    without round-tripping the backend lookup again."""
    kind: str       # "fs" | "kv"
    token: object   # Path for fs; holder string for kv


def _is_local_backend() -> bool:
    """True when corpus_store is configured to write to local filesystem.
    False when writing to Vercel Blob (or any cross-instance backend)."""
    from . import corpus_store
    from .storage import local as _local
    return isinstance(corpus_store._backend(), _local.LocalBackend)


def acquire_corpus_write_lock(corpus_id: str, *,
                              ttl_seconds: int = 90) -> LockHeld | None:
    """Try to take a writer lock for `corpus_id`. Returns a LockHeld token
    on success, None if another writer holds it (caller surfaces
    CORPUS_LOCKED, retryable per § 7.1).

    LocalBackend → filesystem `.lock` file (instance-local; matches v1
    behavior + tests). BlobBackend → Vercel KV `SET NX EX <ttl>` so two
    Vercel instances can't both hold it.
    """
    if _is_local_backend():
        from . import corpus_store
        from .tools import upload_corpus as _uc  # filesystem lock helpers
        target = corpus_store.index_path_for(corpus_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(".lock")
        if _uc._acquire_lock(lock_path):
            return LockHeld(kind="fs", token=lock_path)
        return None

    from .storage import kv
    holder = kv.acquire_lock(f"corpus:{corpus_id}", ttl_seconds=ttl_seconds)
    if holder is None:
        return None
    return LockHeld(kind="kv", token=holder)


def release_corpus_write_lock(corpus_id: str, held: LockHeld) -> None:
    """Release the lock acquired via acquire_corpus_write_lock. Idempotent
    — calling twice (e.g. in `finally` after another raise) is safe.

    For the kv path, release_lock is a check-and-delete: if our holder
    token doesn't match what's currently in KV, we silently no-op
    (someone else acquired after our TTL expired — they own it now)."""
    if held.kind == "fs":
        from .tools import upload_corpus as _uc
        _uc._release_lock(held.token)
        return
    from .storage import kv
    kv.release_lock(f"corpus:{corpus_id}", str(held.token))
