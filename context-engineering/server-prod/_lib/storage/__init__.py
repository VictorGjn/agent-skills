"""Pluggable corpus-storage backends.

`corpus_store` reads/writes corpus index files via a backend chosen by env:
- `local` — filesystem under cache_dir() (default; current behavior, used by
  tests and local dev). v1 production also fell back to /tmp on Vercel,
  which is per-instance ephemeral (corpora vanish on cold starts).
- `blob` — Vercel Blob (durable, S3-class object store). Cold-start-safe.
  Selected automatically when `BLOB_READ_WRITE_TOKEN` is in env, or
  explicitly via `CE_STORAGE_BACKEND=blob`.

Both backends expose the same interface: `Backend.get_bytes(key) -> bytes |
None`, `put_bytes(key, body)`, `delete(key)`, `list_keys(prefix)`. The local
backend is async-naive (filesystem); the Blob backend is sync over stdlib
HTTP (no `requests`/`httpx` dep added to the Vercel function bundle).

Phase A of plan/ce-v1.1-bench-readiness.md.
"""
from __future__ import annotations

import os
from typing import Protocol


class StorageBackend(Protocol):
    """Minimal byte-store interface used by corpus_store."""

    def get_bytes(self, key: str) -> bytes | None: ...
    def put_bytes(self, key: str, body: bytes) -> None: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...


def get_backend() -> StorageBackend:
    """Return the configured backend instance.

    Resolution order:
    1. `CE_STORAGE_BACKEND=local|blob` — explicit override
    2. `BLOB_READ_WRITE_TOKEN` set → blob
    3. local (default)
    """
    explicit = os.environ.get("CE_STORAGE_BACKEND", "").lower()
    if explicit == "blob":
        from . import blob
        return blob.BlobBackend()
    if explicit == "local":
        from . import local
        return local.LocalBackend()
    if os.environ.get("BLOB_READ_WRITE_TOKEN"):
        from . import blob
        return blob.BlobBackend()
    from . import local
    return local.LocalBackend()
