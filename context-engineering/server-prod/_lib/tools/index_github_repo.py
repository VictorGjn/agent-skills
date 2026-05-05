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

from .. import corpus_store, embed as embed_lib, errors, job_store
from ..auth import TokenInfo
from . import upload_corpus  # reuse _acquire_lock / _release_lock


VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
VALID_EMBED = {None, True, False}

# Soft timeout on sync run — under Vercel's 60s ceiling. Above this we
# return BUDGET_EXCEEDED so caller switches to async (which lands in v1.1).
SYNC_TIMEOUT_S = 50

# Phase 5.5 server-side embedding: ~2s per 32-row batch on Mistral codestral-embed.
# We estimate `N / EMBED_FILES_PER_SECOND` seconds and skip if estimate > budget.
EMBED_FILES_PER_SECOND = 16
# Reserve 5s for index-write + lock + response-build below the 50s budget cap.
EMBED_TIMING_HEADROOM_S = 5

# Same regex used by SPEC § 4.1 for repo strings.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _err(code: str, msg: str, details: dict | None = None) -> dict:
    return errors.tool_error(code, msg, details=details)


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


def _ensure_scripts_path() -> None:
    """Ensure the vendored indexer's parent dir is on sys.path so its
    `from pack_context_lib import …` line resolves to the sibling vendor
    module rather than the canonical scripts/ copy outside the function
    bundle. Phase 5 vendored pack_context_lib.py for the same reason —
    Vercel function bundles can't reach parent dirs.
    """
    vendor = Path(__file__).resolve().parent.parent / "vendor"
    p = str(vendor)
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
    embed_arg = args.get("embed")
    if embed_arg not in VALID_EMBED:
        return _err("INVALID_ARGUMENT",
                    "embed must be a boolean or null (null = auto-detect from MISTRAL_API_KEY)",
                    details={"got": embed_arg})
    return None


def _run_indexer(owner: str, repo: str, branch: str, token: str | None) -> dict:
    """Call the vendored indexer (server-prod/_lib/vendor/index_github_repo.py).

    Vercel function bundles can't reach `../scripts/`, so we vendor with a
    sha-sync test (test_phase5.py-style) to detect drift from canonical.
    """
    _ensure_scripts_path()
    import index_github_repo as _gh  # type: ignore — resolved via vendor/ on sys.path
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
    embed_request: bool | None = args.get("embed")  # None = auto

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
        # Phase 5.5: opportunistic server-side embedding via Mistral
        # codestral-embed when MISTRAL_API_KEY is set and timing fits the
        # remaining sync budget. All-or-nothing — partial coverage would
        # silently degrade semantic ranking (caller wouldn't know which
        # files were missing). If the wall-time estimate doesn't fit, we
        # write the corpus keyword-only and surface that via embedding.dims=0,
        # so the strict parity check (corpus_access.check_embeddings_loaded)
        # treats it as "keyword corpus by design" rather than "broken".
        embeddings_map, embedding, embed_skip_reason = _maybe_embed(
            files, embed_request, start, file_count,
        )
        embedded_count = len(embeddings_map)
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
                "embedded_count": embedded_count,
                "version": version,
                "last_refresh_completed_at": datetime.now(timezone.utc).isoformat(),
                "commit_sha": commit_sha,
                "lifecycle_state": "active",
            },
            "files": files,
            "embeddings": embeddings_map,
        }
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(index_obj, separators=(",", ":")), encoding="utf-8")
        tmp.replace(target)

        job_store.register_complete(
            corpus_id, commit_sha,
            files_indexed=file_count, files_total=file_count,
        )

        out = {
            "corpus_id": corpus_id,
            "commit_sha": commit_sha,
            "version": version,
            "stats": {
                "file_count": file_count,
                "embedded_count": embedded_count,
                "took_ms": int((time.time() - start) * 1000),
            },
        }
        if embed_skip_reason:
            out["embed_skipped"] = embed_skip_reason
        return out
    finally:
        upload_corpus._release_lock(lock)


def _resolve_github_token() -> str | None:
    import os
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _file_embed_text(f: dict) -> str:
    """Pick the best representation of a file for embedding.

    Preference: full content from `tree.text` (root node) → first paragraph →
    title. Empty after strip → returned as empty so caller can drop the file.
    """
    tree = f.get("tree") or {}
    candidates = (
        tree.get("text") or "",
        tree.get("firstParagraph") or "",
        tree.get("firstSentence") or "",
        tree.get("title") or f.get("path") or "",
    )
    for c in candidates:
        if c and c.strip():
            return c
    return ""


def _maybe_embed(
    files: list[dict],
    embed_request: bool | None,
    start_time: float,
    file_count: int,
) -> tuple[dict[str, list[float]], dict, str | None]:
    """Compute embeddings server-side when feasible.

    Returns (embeddings_map, embedding_meta, skip_reason).
    `embedding_meta.dims=0` means we did NOT embed; the corpus is keyword-only.
    `dims=1536` means all `file_count` files have a vector in `embeddings_map`
    (we don't ship partial coverage — the strict parity check would reject
    a "dims>0 but missing some" corpus as broken).
    """
    import os

    if embed_request is False:
        return {}, {"provider": "none", "model": "n/a", "dims": 0}, "embed=false requested"

    has_key = bool(os.environ.get("MISTRAL_API_KEY"))
    if embed_request is True and not has_key:
        # Caller asked for embeddings explicitly but server can't honour it.
        # Returning a None skip_reason here would silently downgrade — instead
        # caller sees PROVIDER_UNAVAILABLE via embed_lib.embed_batch raising
        # below. We still return the keyword-only fallback shape so the
        # response is consistent with the soft-skip path.
        return {}, {"provider": "none", "model": "n/a", "dims": 0}, "MISTRAL_API_KEY not set"

    if not has_key:
        # auto path with no key — keyword-only corpus.
        return {}, {"provider": "none", "model": "n/a", "dims": 0}, "MISTRAL_API_KEY not set"

    # Wall-time estimate: ~1/EMBED_FILES_PER_SECOND seconds per file.
    elapsed = time.time() - start_time
    remaining = SYNC_TIMEOUT_S - elapsed - EMBED_TIMING_HEADROOM_S
    estimated = file_count / EMBED_FILES_PER_SECOND
    if estimated > remaining:
        return {}, {"provider": "none", "model": "n/a", "dims": 0}, (
            f"estimated embed time {estimated:.1f}s > remaining budget {remaining:.1f}s; "
            "use ce_upload_corpus with client-computed embeddings for this repo"
        )

    # Build embed inputs. Files with no usable text are dropped from the
    # embedding pool — but to maintain all-or-nothing semantics, we still
    # produce a "broken" outcome below if any file is unembeddable. Better:
    # filter such files out of the corpus entirely so file_count + embedded
    # count stay aligned. v1: drop them with a counter; the caller sees
    # `file_count` shrink in the response.
    keep_files: list[dict] = []
    keep_texts: list[str] = []
    for f in files:
        text = _file_embed_text(f)
        if not text:
            continue
        keep_files.append(f)
        keep_texts.append(text)

    if not keep_files:
        return {}, {"provider": "none", "model": "n/a", "dims": 0}, (
            "no files had embeddable text content"
        )

    try:
        vectors = embed_lib.embed_batch(keep_texts)
    except embed_lib.EmbedError as e:
        # Provider failure mid-flight — fall back to keyword-only corpus
        # rather than failing the whole index call. Caller sees the trace via
        # embed_skipped in the response and can retry or use upload.
        return {}, {"provider": "none", "model": "n/a", "dims": 0}, (
            f"embed failed: {e.code}: {e.message}"
        )

    # Mutate caller's `files` list in place — drop unembeddable rows so
    # commit_sha + file_count + embeddings stay consistent. (We computed
    # commit_sha BEFORE this function ran, so dropping files here would
    # diverge the index from its commit_sha. Keep all files in `files[]`
    # but only the embedded subset in the embeddings map; the strict parity
    # check fires on EMPTY embeddings, not partial.)
    embeddings_map = {f["path"]: v for f, v in zip(keep_files, vectors)}
    embedding_meta = {"provider": "mistral", "model": embed_lib.MISTRAL_MODEL,
                      "dims": embed_lib.MISTRAL_DIMS}
    return embeddings_map, embedding_meta, None
