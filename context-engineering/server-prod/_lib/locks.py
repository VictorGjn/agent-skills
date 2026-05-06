"""Corpus write locks — backend-aware.

Phase A added intra-instance filesystem locks. Phase B (Codex P1 on PR #50)
exposed that filesystem locks don't protect cross-instance Blob writes —
two Vercel instances could each pass their own /tmp lock and both write to
the same Blob key, last-writer-wins.

Phase B.2 (PR #52, P3 cleanup): the filesystem lock primitives now live
HERE rather than in `tools/upload_corpus.py`. This breaks the soft
circular import between `locks.py` and `tools/upload_corpus.py` —
upload_corpus.py / index_github_repo.py both import locks (one direction).

Strategy: pick the lock primitive based on the active storage backend.
- LocalBackend: filesystem `.lock` file (kept; fine because the
  LocalBackend storage is also filesystem — same instance is the only
  writer).
- BlobBackend: Vercel KV lock via `SET NX EX` + `EVAL` check-and-delete
  (built in Phase B.1). Cross-instance safe.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union


# ── Filesystem lock primitives (moved from tools/upload_corpus.py) ──

_STALE_LOCK_AGE_S = 300  # 5 min — older lock files are forcibly reclaimed


def _acquire_fs_lock(lock_path: Path) -> bool:
    """Try to create `lock_path` exclusively (O_EXCL). Returns False if
    another writer already holds it. Stale locks (older than
    `_STALE_LOCK_AGE_S`) are forcibly reclaimed once.
    """
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(time.time()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > _STALE_LOCK_AGE_S:
                lock_path.unlink(missing_ok=True)
                return _acquire_fs_lock(lock_path)
        except OSError:
            pass
        return False


def _release_fs_lock(lock_path: Path) -> None:
    """Idempotent unlink — missing file is OK."""
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


# ── Backend-aware corpus locks ──

@dataclass(frozen=True)
class LockHeld:
    """Returned by acquire_corpus_write_lock on success. Caller passes it
    back to release_corpus_write_lock so we can discriminate fs vs kv
    without round-tripping the backend lookup again.

    Phase B.2 (P3 cleanup): tightened from `token: object` to a proper
    union — the dataclass now invites less misuse.
    """
    kind: str               # "fs" | "kv"
    token: Union[Path, str] # Path for fs path; holder string for kv path


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
        target = corpus_store.index_path_for(corpus_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(".lock")
        if _acquire_fs_lock(lock_path):
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
    (someone else acquired after our TTL expired — they own it now).
    """
    if held.kind == "fs":
        if isinstance(held.token, Path):
            _release_fs_lock(held.token)
        return
    if held.kind == "kv":
        from .storage import kv
        kv.release_lock(f"corpus:{corpus_id}", str(held.token))
        return
    # Unknown kind — silently no-op rather than crash a caller's `finally`.
