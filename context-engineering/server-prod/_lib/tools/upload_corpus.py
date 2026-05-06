"""ce_upload_corpus — § 3.3.

Register a client-supplied indexed corpus. The client has already done the
indexing (locally or via a custom adapter the server can't reach) and uploads
the manifest + file entries + embeddings.

Storage: filesystem v1 — writes to `<cache>/<corpus_id>.index.json` mirroring
the existing index format. v1.1 will replace this with a brain-repo writer
(syrocolab/company-brain) without changing the wire shape.

Idempotency: a second call with identical (corpus_id, files[].contentHash) is
a no-op — returns the existing commit_sha without rewriting the file.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .. import corpus_store, errors, job_store, locks
from ..auth import TokenInfo


MAX_INLINE_BYTES = 32 * 1024 * 1024  # § 3.3 inline cap
MAX_CORPUS_BYTES = 1024 * 1024 * 1024  # 1 GB

VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
VALID_EMBED_FORMATS = {"json", "presigned"}

VALID_SOURCE_TYPES = {"github_repo", "local_workspace", "granola", "notion", "unknown"}


def _err(code: str, msg: str, details: dict | None = None) -> dict:
    return errors.tool_error(code, msg, details=details)


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


def _derive_corpus_id(source: dict) -> str | None:
    """SPEC § 4.1: server-derived corpus_id is `gh-{owner}-{repo}-{branch}` for github,
    `local-...`, etc. Return None if source shape is unknown."""
    stype = source.get("type")
    if stype == "github_repo":
        uri = source.get("uri", "")
        # Extract owner/repo from a github URI
        m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?(?:/|$)", uri)
        if not m:
            return None
        owner, repo = m.group(1), m.group(2)
        branch = source.get("branch") or "main"
        return _slugify(f"gh-{owner}-{repo}-{branch}")
    if stype == "local_workspace":
        path = source.get("uri", "")
        return _slugify(f"local-{path}")
    return None


def _validate_args(args: dict) -> dict | None:
    source = args.get("source")
    if not isinstance(source, dict) or not source.get("type"):
        return _err("INVALID_ARGUMENT", "source must be an object with a 'type' field")
    if source["type"] not in VALID_SOURCE_TYPES:
        return _err("INVALID_ARGUMENT", f"unknown source.type: {source['type']!r}",
                    details={"valid": sorted(VALID_SOURCE_TYPES)})

    cid = args.get("corpus_id")
    if cid is not None:
        if not corpus_store.is_valid_corpus_id(cid):
            return _err("INVALID_ARGUMENT", f"invalid corpus_id format: {cid!r}")

    classification = args.get("data_classification")
    if classification not in VALID_CLASSIFICATIONS:
        return _err("INVALID_ARGUMENT",
                    f"data_classification must be one of {sorted(VALID_CLASSIFICATIONS)}",
                    details={"got": classification})

    embedding = args.get("embedding")
    if not isinstance(embedding, dict):
        return _err("INVALID_ARGUMENT", "embedding must be an object")
    for k in ("provider", "model", "dims"):
        if k not in embedding:
            return _err("INVALID_ARGUMENT", f"embedding missing required field: {k!r}")
    if not isinstance(embedding["dims"], int) or embedding["dims"] < 0:
        return _err("INVALID_ARGUMENT", "embedding.dims must be a non-negative integer")

    files = args.get("files")
    if not isinstance(files, list):
        return _err("INVALID_ARGUMENT", "files must be a list")
    for i, f in enumerate(files):
        if not isinstance(f, dict) or "path" not in f or "contentHash" not in f:
            return _err("INVALID_ARGUMENT",
                        f"files[{i}] must be an object with 'path' and 'contentHash'")

    embeddings = args.get("embeddings")
    if not isinstance(embeddings, dict):
        return _err("INVALID_ARGUMENT", "embeddings must be an object")
    fmt = embeddings.get("format", "json")
    if fmt not in VALID_EMBED_FORMATS:
        return _err("INVALID_ARGUMENT", f"unknown embeddings.format: {fmt!r}",
                    details={"valid": sorted(VALID_EMBED_FORMATS)})

    if fmt == "presigned":
        # v1 doesn't run a presigned-upload coordinator. Tell client to inline
        # under the 32 MB cap or wait for v1.1.
        return _err("PAYLOAD_TOO_LARGE",
                    "presigned uploads are not implemented in v1; inline under 32 MB",
                    details={"hint": "split corpus or use ce_index_github_repo for server-side indexing"})

    paths = embeddings.get("paths")
    hashes = embeddings.get("hashes")
    vectors = embeddings.get("vectors")
    if not isinstance(paths, list) or not isinstance(hashes, list) or not isinstance(vectors, list):
        return _err("INVALID_ARGUMENT",
                    "embeddings.{paths, hashes, vectors} must all be arrays for format=json")
    n = len(paths)
    if not (n == len(hashes) == len(vectors)):
        return _err("EMBEDDING_MISMATCH",
                    "embeddings.paths, hashes, vectors must have equal length",
                    details={"n_paths": len(paths), "n_hashes": len(hashes), "n_vectors": len(vectors)})

    dims = embedding["dims"]
    if dims > 0:
        for i, v in enumerate(vectors):
            if not isinstance(v, list) or len(v) != dims:
                return _err("EMBEDDING_MISMATCH",
                            f"embeddings.vectors[{i}] dim mismatch: expected {dims}, got {len(v) if isinstance(v, list) else type(v).__name__}",
                            details={"row": i, "expected_dims": dims})

    return None


def _existing_content_hash(loaded: corpus_store.LoadedCorpus | None) -> str | None:
    """Compute deterministic hash of an existing corpus's (path, contentHash) tuples."""
    if loaded is None:
        return None
    pairs = sorted([(f.get("path", ""), f.get("contentHash", "")) for f in loaded.files])
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def _new_content_hash(files: list[dict]) -> str:
    pairs = sorted([(f.get("path", ""), f.get("contentHash", "")) for f in files])
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    start = time.time()
    err = _validate_args(args)
    if err:
        return err

    source = args["source"]
    classification = args["data_classification"]
    embedding = args["embedding"]
    files = args["files"]
    embeddings = args["embeddings"]

    # Derive corpus_id if not supplied
    corpus_id = args.get("corpus_id") or _derive_corpus_id(source)
    if not corpus_id:
        return _err("INVALID_ARGUMENT",
                    "corpus_id required when server cannot derive it from source")

    # Inline size sanity check
    payload_size = len(json.dumps(args, separators=(",", ":")).encode("utf-8"))
    if payload_size > MAX_INLINE_BYTES:
        return _err("PAYLOAD_TOO_LARGE",
                    f"inline payload {payload_size} bytes exceeds {MAX_INLINE_BYTES} bytes",
                    details={"payload_bytes": payload_size, "max": MAX_INLINE_BYTES})

    cache_dir = corpus_store.cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Idempotency: same (corpus_id, content_hashes) → return existing commit_sha
    existing = corpus_store.load_corpus(corpus_id)
    new_hash = _new_content_hash(files)
    if existing and _existing_content_hash(existing) == new_hash:
        return {
            "corpus_id": corpus_id,
            "commit_sha": existing.meta.commit_sha,
            "version": existing.meta.version,
            "stats": {
                "file_count": existing.meta.file_count,
                "embedded_count": existing.meta.embedded_count,
                "size_bytes": existing.meta.size_bytes,
            },
            "took_ms": int((time.time() - start) * 1000),
        }

    # Phase B (Codex P1 on PR #50): writes target a SHARED backend (Blob in
    # prod). Filesystem lock is intra-instance only — two instances could
    # both pass it and overwrite each other. acquire_corpus_write_lock
    # picks the right primitive (fs for LocalBackend, KV for BlobBackend).
    held = locks.acquire_corpus_write_lock(corpus_id)
    if held is None:
        return _err("CORPUS_LOCKED",
                    f"corpus {corpus_id!r} is being written by another caller; retry",
                    details={"corpus_id": corpus_id})

    try:
        # Merge embeddings into file entries by path so the read path can pick
        # them up later. v1 keyword-only doesn't use them, but Phase 5+ semantic
        # mode will. Store in a top-level "embeddings" map for forward-compat.
        path_to_vec = dict(zip(embeddings["paths"], embeddings["vectors"]))
        path_to_hash = dict(zip(embeddings["paths"], embeddings["hashes"]))

        # Sanity: embeddings should reference the same paths as files[]. Drop
        # mismatches (extra embeddings unreferenced by files) silently — they're
        # not authoritative.
        embeddings_for_files = {
            p: path_to_vec[p] for p in (f["path"] for f in files) if p in path_to_vec
        }

        # Compute commit_sha (deterministic over manifest + content hashes + embedding triple)
        commit_input = {
            "corpus_id": corpus_id,
            "source": source,
            "classification": classification,
            "embedding": {"provider": embedding["provider"], "model": embedding["model"], "dims": embedding["dims"]},
            "content_hash": new_hash,
        }
        commit_sha = hashlib.sha256(
            json.dumps(commit_input, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]

        version = (existing.meta.version + 1) if existing else 1

        embedded_count = sum(1 for f in files if f["path"] in path_to_vec) if embedding["dims"] > 0 else 0

        # Write atomically: temp file → rename
        from datetime import datetime, timezone
        index_obj = {
            "_meta": {
                "corpus_id": corpus_id,
                "source": source,
                "data_classification": classification,
                "embedding": embedding,
                "file_count": len(files),
                "embedded_count": embedded_count,
                "version": version,
                "last_refresh_completed_at": datetime.now(timezone.utc).isoformat(),
                "commit_sha": commit_sha,
                "lifecycle_state": "active",
            },
            "files": files,
            "embeddings": embeddings_for_files,
        }
        # Phase A (v1.1): write through the storage backend (Blob in prod,
        # filesystem in tests). Backend handles atomicity (Blob is
        # transactional via PUT; LocalBackend uses temp+rename).
        body = json.dumps(index_obj, separators=(",", ":")).encode("utf-8")
        size_bytes = len(body)
        if size_bytes > MAX_CORPUS_BYTES:
            return _err("PAYLOAD_TOO_LARGE",
                        f"corpus exceeds 1 GB cap ({size_bytes} bytes)",
                        details={"size_bytes": size_bytes, "max": MAX_CORPUS_BYTES})
        corpus_store.write_corpus(corpus_id, body)

        # Register synthetic completion job for ce_get_job_status callers
        job_store.register_complete(
            corpus_id, commit_sha,
            files_indexed=len(files), files_total=len(files),
        )

        return {
            "corpus_id": corpus_id,
            "commit_sha": commit_sha,
            "version": version,
            "stats": {
                "file_count": len(files),
                "embedded_count": embedded_count,
                "size_bytes": size_bytes,
            },
            "took_ms": int((time.time() - start) * 1000),
        }
    finally:
        locks.release_corpus_write_lock(corpus_id, held)
