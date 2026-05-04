"""Local-filesystem corpus store for production v1.

v1 stores corpora as `<corpus_id>.index.json` files under a cache root
(default `server-prod/cache/`, override via `CE_CORPUS_CACHE_DIR` env).

v1.1 will replace this with a brain-repo (`syrocolab/company-brain`)
backed reader. Same external shape — handlers don't care.

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
      "files": [
        { "path": ..., "contentHash": ..., "tokens": ..., "tree": {...},
          "symbols": [...], "knowledge_type": ... },
        ...
      ]
    }
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "cache"

# Same regex the stub used. Per § 4.1: `[a-z0-9][a-z0-9-]{0,127}`.
_CORPUS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


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


def cache_dir() -> Path:
    """Where corpus indices live. Override via CE_CORPUS_CACHE_DIR."""
    override = os.environ.get("CE_CORPUS_CACHE_DIR")
    return Path(override).resolve() if override else _DEFAULT_CACHE


def is_valid_corpus_id(corpus_id: str) -> bool:
    return bool(corpus_id) and isinstance(corpus_id, str) and bool(_CORPUS_ID_RE.match(corpus_id))


def index_path_for(corpus_id: str) -> Path:
    return cache_dir() / f"{corpus_id}.index.json"


def _coerce_meta(raw: dict, size_bytes: int) -> CorpusMeta:
    file_count = int(raw.get("file_count", 0) or 0)
    # If embedded_count isn't recorded, infer from embedding presence: dims>0 → all files
    # have vectors (server-side indexer enforces this); dims=0 → none.
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


def load_corpus(corpus_id: str) -> LoadedCorpus | None:
    """Load `<cache>/corpus_id.index.json`. Returns None if missing or invalid id."""
    if not is_valid_corpus_id(corpus_id):
        return None
    p = index_path_for(corpus_id)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    # Filename is authoritative (matches list_metas behavior). Hand-built or
    # partially migrated indices may have missing/stale `_meta.corpus_id`;
    # without this fallback, multi-corpus path prefixes and corpus_commit_shas
    # keys would render with "" and break addressing.
    meta_raw = raw.get("_meta", {})
    if not meta_raw.get("corpus_id"):
        meta_raw = {**meta_raw, "corpus_id": corpus_id}
    meta = _coerce_meta(meta_raw, p.stat().st_size)
    files = raw.get("files") or []
    if isinstance(files, dict):
        files = list(files.values())
    return LoadedCorpus(meta=meta, files=files)


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


def list_metas() -> list[CorpusMeta]:
    """List all corpus metas (no file payloads)."""
    out: list[CorpusMeta] = []
    cd = cache_dir()
    if not cd.exists():
        return out
    for p in sorted(cd.glob("*.index.json")):
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        meta_raw = raw.get("_meta", {})
        # Filename is authoritative — _meta might be missing or stale on a hand-built index.
        if not meta_raw.get("corpus_id"):
            meta_raw = {**meta_raw, "corpus_id": p.stem.removesuffix(".index")}
        out.append(_coerce_meta(meta_raw, p.stat().st_size))
    return out
