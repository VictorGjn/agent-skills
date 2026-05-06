"""Corpus store — pluggable storage backend (filesystem | Vercel Blob).

v1 wrote `<corpus_id>.index.json` files to `cache/` (or `CE_CORPUS_CACHE_DIR`).
v1.1 / Phase A: storage is abstracted behind `_lib/storage/`. Production now
defaults to Vercel Blob when `BLOB_READ_WRITE_TOKEN` is set, with a
per-instance `/tmp` warm cache to avoid round-tripping Blob on every read.

Index file shape (mirrors server-stub's format, validated against v1):

    {
      "_meta": {
        "corpus_id": "<string>",
        "source": { "type": "...", "uri": "...", "branch": "..." },
        "data_classification": "public|internal|confidential|restricted",
        "embedding": { "provider": "...", "model": "...", "dims": int },
        "file_count": int,
        "version": int,
        "last_refresh_completed_at": "<iso8601> | null",
        "commit_sha": "<string>",
        "lifecycle_state": "active|idle|archived|frozen",  # optional, default "active"
        "archive_location": "<string> | null"               # optional
      },
      "files": [...],
      "embeddings": { "<path>": [float, ...], ... }   # optional
    }
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import storage


# Backwards-compat: still emit/read this path for the warm cache + tests.
_DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "cache"

# Same regex the stub used. Per § 4.1: `[a-z0-9][a-z0-9-]{0,127}`.
_CORPUS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")

# Lazy backend instance. Tests monkeypatch via _backend()/set_backend.
_BACKEND: storage.StorageBackend | None = None


def _backend() -> storage.StorageBackend:
    """Resolve and cache the configured storage backend.

    Per-instance singleton — all reads/writes within one Vercel function
    invocation share the same backend object. Tests can override via
    `set_backend(...)` to inject a stub.
    """
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = storage.get_backend()
    return _BACKEND


def set_backend(backend: storage.StorageBackend | None) -> None:
    """Override the active backend (test hook). Pass None to reset."""
    global _BACKEND
    _BACKEND = backend


@dataclass(frozen=True)
class CorpusMeta:
    corpus_id: str
    source: dict
    data_classification: str
    embedding: dict
    file_count: int
    embedded_count: int
    version: int
    commit_sha: str
    last_refresh_completed_at: str | None
    lifecycle_state: str
    archive_location: str | None
    size_bytes: int

    def to_list_entry(self) -> dict[str, Any]:
        """Shape per SPEC § 3.5 corpora[] entry."""
        return {
            "corpus_id": self.corpus_id,
            "source": self.source,
            "lifecycle_state": self.lifecycle_state,
            "data_classification": self.data_classification,
            "embedding": self.embedding,
            "stats": {
                "file_count": self.file_count,
                "embedded_count": self.embedded_count,
                "size_bytes": self.size_bytes,
            },
            "version": self.version,
            "last_refresh_completed_at": self.last_refresh_completed_at,
            "archive_location": self.archive_location,
        }


@dataclass
class LoadedCorpus:
    meta: CorpusMeta
    files: list[dict]
    # path → embedding vector. Empty when corpus was indexed without embeddings
    # (server-side ce_index_github_repo today, or upload with provider="none").
    # Phase 5.5: semantic mode uses this map; missing path → that file is
    # excluded from semantic ranking and falls back to keyword.
    embeddings: dict[str, list[float]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.embeddings is None:
            object.__setattr__(self, "embeddings", {})


# ── Cache dir / index path: kept for legacy callers + warm-cache hint ──

def cache_dir() -> Path:
    """Filesystem location used by:
    - LocalBackend storage root (when CE_STORAGE_BACKEND=local or no Blob token)
    - The /tmp warm-cache layer used by the BlobBackend path

    Override via `CE_CORPUS_CACHE_DIR`.
    """
    override = os.environ.get("CE_CORPUS_CACHE_DIR")
    return Path(override).resolve() if override else _DEFAULT_CACHE


def is_valid_corpus_id(corpus_id: str) -> bool:
    return bool(corpus_id) and isinstance(corpus_id, str) and bool(_CORPUS_ID_RE.match(corpus_id))


def _key_for(corpus_id: str) -> str:
    """Storage key (relative path / blob pathname) for a corpus."""
    return f"{corpus_id}.index.json"


def index_path_for(corpus_id: str) -> Path:
    """Filesystem path for a corpus index — used by callers that still
    write atomically via temp+rename (upload_corpus, index_github_repo lock
    files). The Blob backend writes via _backend().put_bytes() instead and
    these paths only refer to the warm-cache mirror.
    """
    return cache_dir() / _key_for(corpus_id)


# ── Read path ──

def _coerce_meta(raw: dict, size_bytes: int) -> CorpusMeta:
    file_count = int(raw.get("file_count", 0) or 0)
    embedding = raw.get("embedding") or {"provider": "none", "model": "n/a", "dims": 0}
    if "embedded_count" in raw:
        embedded_count = int(raw["embedded_count"] or 0)
    else:
        embedded_count = file_count if int(embedding.get("dims", 0) or 0) > 0 else 0
    return CorpusMeta(
        corpus_id=raw.get("corpus_id", ""),
        source=raw.get("source") or {"type": "unknown", "uri": "unknown"},
        data_classification=raw.get("data_classification", "internal"),
        embedding=embedding,
        file_count=file_count,
        embedded_count=embedded_count,
        version=int(raw.get("version", 1) or 1),
        commit_sha=raw.get("commit_sha", ""),
        last_refresh_completed_at=raw.get("last_refresh_completed_at"),
        lifecycle_state=raw.get("lifecycle_state", "active"),
        archive_location=raw.get("archive_location"),
        size_bytes=size_bytes,
    )


def _warm_cache_ttl_s() -> float:
    """How long to trust a warm `/tmp` cache hit before re-validating Blob.

    Multi-instance Vercel: instance A writes a fresh corpus to Blob; instance
    B has a stale `/tmp` from a prior request. Without a TTL, B serves stale
    content forever (Codex P1 on PR #50). 60s is the default — a handful of
    cache hits per cold-write window, then we re-check Blob.

    Override via `CE_WARM_CACHE_TTL_S` (set to `0` to disable warm cache
    entirely; never use stale, every read goes to Blob).
    """
    raw = os.environ.get("CE_WARM_CACHE_TTL_S")
    if raw is None:
        return 60.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def _read_bytes(corpus_id: str) -> bytes | None:
    """Backend read with a per-instance /tmp warm cache.

    Order of operations:
    1. Check the warm cache (current cache_dir) — fast path on warm instances,
       BUT only when the cache file's mtime is within the warm-cache TTL.
       Beyond that, treat as miss and re-fetch (Codex P1: stale-cache gap).
    2. Fall back to the configured backend (Blob in production).
    3. On Blob hit, populate the warm cache (atomic temp+rename) so
       subsequent reads in the same instance skip the round-trip.

    The warm cache is bounded by /tmp's natural size + the function's life
    + the configurable TTL.
    """
    import time as _time
    key = _key_for(corpus_id)
    backend = _backend()

    # When the active backend IS LocalBackend on the same path, the warm-cache
    # check is the same lookup as the backend read. Skip the duplicate work
    # AND the TTL revalidation (filesystem reads are authoritative there).
    from .storage import local as _local
    is_local = isinstance(backend, _local.LocalBackend)
    if not is_local:
        warm = cache_dir() / key
        if warm.exists():
            ttl = _warm_cache_ttl_s()
            if ttl > 0:
                try:
                    age = _time.time() - warm.stat().st_mtime
                    if age <= ttl:
                        return warm.read_bytes()
                    # Stale: fall through to backend fetch. Don't unlink
                    # eagerly — the next put will atomically replace it.
                except OSError:
                    pass
            # ttl == 0 → bypass cache entirely; ttl > 0 + age > ttl →
            # bypass this hit. Either way: re-fetch from Blob below.

    body = backend.get_bytes(key)
    if body is None:
        return None

    # Populate warm cache (best-effort; ignore failures — /tmp full, etc.)
    # Atomic temp+rename so a crash mid-write doesn't leave a partial file
    # the next read would treat as a cache hit. Concern from Codex review
    # of PR #50.
    if not is_local:
        try:
            warm = cache_dir() / key
            warm.parent.mkdir(parents=True, exist_ok=True)
            tmp = warm.with_suffix(warm.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(warm)
        except OSError:
            pass

    return body


def load_corpus(corpus_id: str) -> LoadedCorpus | None:
    """Load a corpus index. Returns None if missing or invalid id."""
    if not is_valid_corpus_id(corpus_id):
        return None
    body = _read_bytes(corpus_id)
    if body is None:
        return None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    meta_raw = raw.get("_meta", {})
    if not meta_raw.get("corpus_id"):
        meta_raw = {**meta_raw, "corpus_id": corpus_id}
    size_bytes = len(body)
    meta = _coerce_meta(meta_raw, size_bytes)

    files = raw.get("files") or []
    if isinstance(files, dict):
        files = list(files.values())
    embeddings = raw.get("embeddings") or {}
    if not isinstance(embeddings, dict):
        embeddings = {}
    return LoadedCorpus(meta=meta, files=files, embeddings=embeddings)


# ── Write path ──

def write_corpus(corpus_id: str, body: bytes) -> int:
    """Write a corpus index file. Returns size_bytes.

    Goes to the configured backend (Blob in prod). Also writes the warm
    cache so the next read in the same instance is a fast-path hit.
    """
    key = _key_for(corpus_id)
    backend = _backend()
    backend.put_bytes(key, body)

    from .storage import local as _local
    if not isinstance(backend, _local.LocalBackend):
        # Mirror to warm cache (best-effort)
        try:
            warm = cache_dir() / key
            warm.parent.mkdir(parents=True, exist_ok=True)
            tmp = warm.with_suffix(warm.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(warm)
        except OSError:
            pass
    return len(body)


# ── Integrity helpers (unchanged from v1) ──

def content_fingerprint(loaded: LoadedCorpus) -> str:
    """Deterministic 12-char fingerprint of sorted (path, contentHash) pairs.

    Used as an ETag fallback when `_meta.commit_sha` is missing or empty —
    without this, the ETag would depend only on request args, not on the
    corpus content, so a 304 could pin a stale response indefinitely
    (Codex P1). This guarantees ETag tracks content even on hand-built
    indices that skip the commit_sha field.
    """
    import hashlib
    pairs = sorted([(f.get("path", ""), f.get("contentHash", "")) for f in loaded.files])
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def commit_key(loaded: LoadedCorpus) -> str:
    """Return the ETag commit_key for a corpus: commit_sha if present, else content_fingerprint."""
    sha = (loaded.meta.commit_sha or "").strip()
    if sha:
        return sha
    return f"cf-{content_fingerprint(loaded)}"


# ── List path ──

def list_metas() -> list[CorpusMeta]:
    """List all corpus metas (no file payloads).

    Backed by the active storage backend. For Blob, this is a paged LIST
    plus per-blob HEAD; we read the full body since v1 doesn't expose a
    head-only metadata endpoint and the meta is in the index file.
    """
    backend = _backend()
    out: list[CorpusMeta] = []
    for key in backend.list_keys(prefix=""):
        if not key.endswith(".index.json"):
            continue
        body = backend.get_bytes(key)
        if body is None:
            continue
        try:
            raw = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        meta_raw = raw.get("_meta", {})
        if not meta_raw.get("corpus_id"):
            stem = key[:-len(".index.json")]
            meta_raw = {**meta_raw, "corpus_id": stem}
        out.append(_coerce_meta(meta_raw, len(body)))
    return out
