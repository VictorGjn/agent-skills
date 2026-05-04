"""ce_index_github_repo — § 3.4.

Server-side indexer. Fetches a GitHub repo via its API tree, builds a
workspace index using `scripts/index_github_repo.py`, writes it to the corpus
store, and (when configured) runs the embedding provider.

v1 scope:
- `async=false` (default): sync run via sys.path import of the existing
  indexer. If the run would exceed function timeout, return BUDGET_EXCEEDED
  hint. (We use a soft timeout — Vercel's 60s ceiling is the real wall.)
- `async=true`: NOT_IMPLEMENTED in v1; queue + worker land in v1.1.
- Embeddings: v1 stores the keyword index only. Vector computation lives in
  Phase 5+ (when codestral-embed via MISTRAL_API_KEY is wired through the
  server).

Idempotency: re-indexing an unchanged commit returns the existing commit_sha
without rewriting.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import corpus_store, errors, job_store
from ..auth import TokenInfo
from . import upload_corpus  # reuse _acquire_lock / _release_lock


VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}

# Soft timeout on sync run — under Vercel's 60s ceiling. Above this we
# return BUDGET_EXCEEDED so caller switches to async (which lands in v1.1).
SYNC_TIMEOUT_S = 50

# Same regex used by SPEC § 4.1 for repo strings.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _err(code: str, msg: str, details: dict | None = None) -> dict:
    return errors.tool_error(code, msg, details=details)


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


def _ensure_scripts_path() -> None:
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    p = str(scripts)
    if p not in sys.path:
        sys.path.insert(0, p)


def _validate_args(args: dict) -> dict | None:
    repo = args.get("repo")
    if not isinstance(repo, str) or not _REPO_RE.match(repo):
        return _err("INVALID_ARGUMENT",
                    f"repo must be in 'owner/name' format, got {repo!r}")
    branch = args.get("branch")
    if branch is not None and not isinstance(branch, str):
        return _err("INVALID_ARGUMENT", "branch must be a string when set")
    cid = args.get("corpus_id")
    if cid is not None and not corpus_store.is_valid_corpus_id(cid):
        return _err("INVALID_ARGUMENT", f"invalid corpus_id format: {cid!r}")
    classification = args.get("data_classification")
    if classification not in VALID_CLASSIFICATIONS:
        return _err("INVALID_ARGUMENT",
                    f"data_classification must be one of {sorted(VALID_CLASSIFICATIONS)}",
                    details={"got": classification})
    indexed_paths = args.get("indexed_paths", [])
    if not isinstance(indexed_paths, list) or not all(isinstance(x, str) for x in indexed_paths):
        return _err("INVALID_ARGUMENT", "indexed_paths must be a list of strings")
    is_async = args.get("async", False)
    if not isinstance(is_async, bool):
        return _err("INVALID_ARGUMENT", "async must be a boolean")
    return None


def _run_indexer(owner: str, repo: str, branch: str, token: str | None) -> dict:
    """Call into scripts/index_github_repo.py:index_github_repo()."""
    _ensure_scripts_path()
    import index_github_repo as _gh  # type: ignore
    return _gh.index_github_repo(owner, repo, branch, token)


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    start = time.time()
    err = _validate_args(args)
    if err:
        return err

    repo: str = args["repo"]
    branch = args.get("branch") or "main"
    classification = args["data_classification"]
    indexed_paths = args.get("indexed_paths", [])
    is_async = args.get("async", False)
    explicit_cid = args.get("corpus_id")

    owner, name = repo.split("/", 1)
    derived_cid = _slugify(f"gh-{owner}-{name}-{branch}")
    corpus_id = explicit_cid or derived_cid

    if is_async:
        # v1 has no async backend — Cron + queue is v1.1.
        return errors.tool_error(
            "NOT_IMPLEMENTED",
            "async=true is not implemented in v1; retry with async=false (or wait for v1.1)",
            details={"phase": "4", "spec_section": "§ 3.4"},
        )

    # Idempotency: if existing corpus has same source.commit_sha, no-op.
    # We don't have the source commit_sha until after fetching the tree, so
    # we check after _run_indexer below.

    # Run the indexer (subprocess-free, in-process import). Catch GitHub errors.
    try:
        gh_token = _resolve_github_token()
        index = _run_indexer(owner, name, branch, gh_token)
    except Exception as exc:  # noqa: BLE001
        # Map common upstream errors to spec error codes.
        msg = str(exc)
        if "404" in msg:
            return _err("SOURCE_NOT_FOUND", f"repo {repo!r} (branch {branch!r}) not found",
                        details={"repo": repo, "branch": branch})
        if "401" in msg or "403" in msg:
            return _err("SOURCE_FORBIDDEN",
                        f"server cannot read repo {repo!r}; use ce_upload_corpus instead",
                        details={"repo": repo, "branch": branch})
        # Any other failure surfaces as INTERNAL via transport's catch-all
        raise

    # Apply indexed_paths filter, if provided
    if indexed_paths:
        index["files"] = [
            f for f in index.get("files", [])
            if any(f.get("path", "").startswith(p) for p in indexed_paths)
        ]

    elapsed = time.time() - start
    if elapsed > SYNC_TIMEOUT_S:
        return _err("BUDGET_EXCEEDED",
                    f"sync indexing took {elapsed:.1f}s (>{SYNC_TIMEOUT_S}s); retry with async=true (v1.1)",
                    details={"elapsed_s": elapsed, "timeout_s": SYNC_TIMEOUT_S})

    files = index.get("files") or []
    # Normalize: existing scripts/index_github_repo.py + scripts/index_workspace.py
    # emit `hash`; the corpus_store / pack pipeline reads `contentHash`.
    # Without this normalization, commit_sha derivation only saw paths, not
    # content, so re-indexing modified files looked like a no-op (Codex P1).
    for f in files:
        if "contentHash" not in f and "hash" in f:
            f["contentHash"] = f["hash"]
    file_count = len(files)

    # Compute commit_sha over the indexed content
    pairs = sorted([(f.get("path", ""), f.get("contentHash", "")) for f in files])
    commit_sha = hashlib.sha256(
        json.dumps({"repo": repo, "branch": branch, "pairs": pairs}, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]

    existing = corpus_store.load_corpus(corpus_id)
    if existing and existing.meta.commit_sha == commit_sha:
        return {
            "corpus_id": corpus_id,
            "commit_sha": commit_sha,
            "version": existing.meta.version,
            "stats": {
                "file_count": existing.meta.file_count,
                "embedded_count": existing.meta.embedded_count,
                "took_ms": int((time.time() - start) * 1000),
            },
        }

    version = (existing.meta.version + 1) if existing else 1

    cache_dir = corpus_store.cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = corpus_store.index_path_for(corpus_id)
    lock = target.with_suffix(".lock")

    # Acquire the same lock ce_upload_corpus uses — without this, an upload
    # racing against an index for the same corpus_id could trample each other
    # (Codex P1). § 3.4 contract: CORPUS_LOCKED is retryable.
    if not upload_corpus._acquire_lock(lock):
        return _err("CORPUS_LOCKED",
                    f"corpus {corpus_id!r} is being written by another caller; retry",
                    details={"corpus_id": corpus_id})

    try:
        embedding = {"provider": "none", "model": "n/a", "dims": 0}
        index_obj = {
            "_meta": {
                "corpus_id": corpus_id,
                "source": {"type": "github_repo",
                           "uri": f"https://github.com/{repo}",
                           "branch": branch,
                           "indexed_paths": indexed_paths},
                "data_classification": classification,
                "embedding": embedding,
                "file_count": file_count,
                "embedded_count": 0,
                "version": version,
                "last_refresh_completed_at": datetime.now(timezone.utc).isoformat(),
                "commit_sha": commit_sha,
                "lifecycle_state": "active",
            },
            "files": files,
        }
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(index_obj, separators=(",", ":")), encoding="utf-8")
        tmp.replace(target)

        job_store.register_complete(
            corpus_id, commit_sha,
            files_indexed=file_count, files_total=file_count,
        )

        return {
            "corpus_id": corpus_id,
            "commit_sha": commit_sha,
            "version": version,
            "stats": {
                "file_count": file_count,
                "embedded_count": 0,
                "took_ms": int((time.time() - start) * 1000),
            },
        }
    finally:
        upload_corpus._release_lock(lock)


def _resolve_github_token() -> str | None:
    import os
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
