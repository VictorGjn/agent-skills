"""Async chunked indexer — drives the cron worker through one job tick.

Phase B.2 of v1.1 plan. Pure functions over `jobs` + `corpus_store` +
the vendored `index_github_repo` chunkable API. The cron worker is a
thin HTTP handler that calls `advance_one_tick()`; this module owns the
state-machine.

State per job (in addition to jobs.py's record):
- KV `job:<id>:candidates` — JSON list[Candidate] from fetch_tree, set
  on the first tick. ~30KB for a 300-file repo (under Vercel KV's
  1MB-per-key limit).
- KV `job:<id>:cursor` — `{next_idx, files_indexed_so_far,
  candidates_count, time_total_s}`. Saved each tick before requeue.
- Blob `<corpus_id>.partial.json` — accumulated indexed files. Read
  → append → write atomically each tick (corpus_store.write_corpus uses
  the backend's atomic put).
- On `done`: rebuild final manifest via `finalize()`, write to Blob as
  `<corpus_id>.index.json`, delete `.partial.json` + KV state, mark job
  complete.

Why partial state in Blob (not KV): a 300-file partial corpus serializes
to ~5MB+; over Vercel KV's 1MB cap. Blob has no per-key size limit; we
already pay one Blob write per tick anyway for the finalized index.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import corpus_store, jobs, locks
from .storage import kv


# Per-tick budget — leaves headroom under maxDuration=300s for response
# build, KV writes, and partial-corpus Blob round-trip.
TICK_TIME_BUDGET_S = 220.0
TICK_MAX_FILES = 50  # ~10s of GitHub fetches at 200ms/file

# KV key shapes
def _candidates_key(job_id: str) -> str:
    return f"job:{job_id}:candidates"


def _cursor_key(job_id: str) -> str:
    return f"job:{job_id}:cursor"


def _partial_corpus_id(corpus_id: str) -> str:
    """Where partial-state lives during indexing. Note: corpus_id IDs are
    `[a-z0-9][a-z0-9-]{0,127}` per § 4.1, so we prefix with `partial-`
    rather than appending a suffix (which would break the regex)."""
    return f"partial-{corpus_id}"


def _ensure_scripts_path() -> None:
    """Make the vendored chunkable indexer importable."""
    vendor = Path(__file__).resolve().parent / "vendor"
    p = str(vendor)
    if p not in sys.path:
        sys.path.insert(0, p)


def _resolve_github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _split_repo(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise ValueError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)
    return owner, name


def _slugify_corpus_id(repo: str, branch: str) -> str:
    owner, name = _split_repo(repo)
    raw = f"gh-{owner}-{name}-{branch}".lower()
    return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")


def advance_one_tick(job: dict) -> dict:
    """Process at most one cron tick of work for a queued/running job.

    Returns a status dict for the caller to log:
        {"job_id", "status", "files_indexed", "files_total",
         "next_idx", "done", "time_used_s", "embedded_count"?}

    Three branches:
    1. First tick (cursor not in KV): fetch_tree, persist candidates +
       initial cursor, return — DON'T fetch any file content. Keeps the
       first tick deterministic + cheap so a downstream issue surfaces
       fast.
    2. Subsequent ticks (cursor present): index_chunk against the slice;
       merge into partial corpus on Blob; advance cursor. If chunk reports
       `done`, finalize + write final corpus + cleanup + jobs.complete.
       Else save cursor, jobs.requeue.
    3. Job not in expected shape (e.g. wrong kind): jobs.fail without retry.
    """
    job_id = job["id"]
    args = job.get("args") or {}
    if job.get("kind") != "index_github_repo":
        jobs.fail(job_id, code="INVALID_ARGUMENT",
                  message=f"async_indexer can't handle kind={job.get('kind')!r}")
        return {"job_id": job_id, "status": "failed", "reason": "wrong_kind"}

    repo = args.get("repo")
    branch = args.get("branch") or "main"
    classification = args.get("data_classification", "internal")
    explicit_cid = args.get("corpus_id")
    indexed_paths = args.get("indexed_paths", [])

    if not repo or "/" not in repo:
        jobs.fail(job_id, code="INVALID_ARGUMENT",
                  message=f"repo must be 'owner/name', got {repo!r}")
        return {"job_id": job_id, "status": "failed", "reason": "invalid_repo"}

    owner, name = _split_repo(repo)
    corpus_id = explicit_cid or _slugify_corpus_id(repo, branch)
    partial_id = _partial_corpus_id(corpus_id)

    # Codex P2 on PR #52: serialize ticks of the SAME job. Without this,
    # two cron workers picking up the same job_id (LPOP race during fail-
    # then-requeue) could both run advance_one_tick, both write partial
    # corpus, both set candidates+cursor in KV — last-writer-wins, with
    # one job's slice silently dropped. A per-job lock prevents the race
    # without contending with concurrent OTHER-corpus indexing. Wrapping
    # the entire body so first-tick AND subsequent-tick branches share
    # the protection.
    held_job = locks.acquire_corpus_write_lock(f"job-{job_id}", ttl_seconds=300)
    if held_job is None:
        # Another worker is mid-tick on this job. Re-queue and bail.
        jobs.requeue(job_id)
        return {"job_id": job_id, "status": "deferred_locked",
                "reason": "concurrent_tick"}
    try:
        return _advance_one_tick_locked(
            job_id, args, repo, branch, classification,
            corpus_id, partial_id, indexed_paths,
        )
    finally:
        locks.release_corpus_write_lock(f"job-{job_id}", held_job)


def _advance_one_tick_locked(
    job_id: str, args: dict, repo: str, branch: str, classification: str,
    corpus_id: str, partial_id: str, indexed_paths: list,
) -> dict:
    """Per-job-locked body of advance_one_tick. Same semantics, just
    indented to keep the lock try/finally clean in the parent."""
    owner, name = _split_repo(repo)
    cursor_raw = kv.get(_cursor_key(job_id))
    candidates_raw = kv.get(_candidates_key(job_id))

    _ensure_scripts_path()
    import index_github_repo as _gh  # type: ignore — vendored

    # ── Branch 1: first tick (no cursor yet) ──
    if cursor_raw is None or candidates_raw is None:
        try:
            candidates = _gh.fetch_tree(owner, name, branch,
                                         _resolve_github_token())
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "404" in msg or "no tree" in msg:
                jobs.fail(job_id, code="SOURCE_NOT_FOUND",
                          message=f"repo {repo!r}@{branch}: {msg}")
                return {"job_id": job_id, "status": "failed", "reason": "source_not_found"}
            if "401" in msg or "403" in msg:
                jobs.fail(job_id, code="SOURCE_FORBIDDEN", message=msg)
                return {"job_id": job_id, "status": "failed", "reason": "source_forbidden"}
            jobs.fail(job_id, code="INTERNAL", message=msg, retry=True)
            return {"job_id": job_id, "status": "failed", "reason": "tree_fetch_failed"}

        if indexed_paths:
            candidates = [c for c in candidates
                          if any(c.get("path", "").startswith(p) for p in indexed_paths)]

        kv.set(_candidates_key(job_id),
               json.dumps(candidates, separators=(",", ":")),
               ex_seconds=jobs.JOB_TTL_SECONDS)
        kv.set(_cursor_key(job_id),
               json.dumps({"next_idx": 0, "files_indexed_so_far": 0,
                           "candidates_count": len(candidates),
                           "time_total_s": 0.0}, separators=(",", ":")),
               ex_seconds=jobs.JOB_TTL_SECONDS)

        # Initialize empty partial corpus
        empty_partial = {
            "_meta": {
                "corpus_id": partial_id,
                "source": {"type": "github_repo",
                           "uri": f"https://github.com/{repo}",
                           "branch": branch,
                           "indexed_paths": indexed_paths},
                "data_classification": classification,
                "embedding": {"provider": "none", "model": "n/a", "dims": 0},
                "file_count": 0, "embedded_count": 0,
                "version": 1,
                "last_refresh_completed_at": None,
                "commit_sha": "",
                "lifecycle_state": "active",
            },
            "files": [],
            "embeddings": {},
        }
        corpus_store.write_corpus(partial_id,
                                  json.dumps(empty_partial, separators=(",", ":")).encode("utf-8"))

        jobs.update_progress(job_id, cursor=0, files_indexed=0,
                             files_total=len(candidates))
        jobs.requeue(job_id)
        return {
            "job_id": job_id, "status": "advanced",
            "files_indexed": 0, "files_total": len(candidates),
            "next_idx": 0, "done": False, "time_used_s": 0.0,
        }

    # ── Branch 2: subsequent tick — process a chunk ──
    cursor = json.loads(cursor_raw)
    candidates = json.loads(candidates_raw)
    next_idx = cursor.get("next_idx", 0)
    files_so_far = cursor.get("files_indexed_so_far", 0)
    time_total = cursor.get("time_total_s", 0.0)

    # Cross-instance write lock — same primitive that upload_corpus uses.
    held = locks.acquire_corpus_write_lock(corpus_id)
    if held is None:
        # Another instance is currently writing. Re-queue and try again
        # next tick. (Shouldn't happen in practice — workers serialize via
        # LPOP — but cheap defense against an upload+index race.)
        jobs.requeue(job_id)
        return {
            "job_id": job_id, "status": "deferred_locked",
            "files_indexed": files_so_far, "files_total": len(candidates),
        }

    try:
        try:
            chunk = _gh.index_chunk(
                owner, name, branch, _resolve_github_token(),
                candidates,
                start_idx=next_idx,
                max_files=TICK_MAX_FILES,
                time_budget_s=TICK_TIME_BUDGET_S,
            )
        except Exception as exc:  # noqa: BLE001
            jobs.fail(job_id, code="INTERNAL",
                      message=f"index_chunk failed at idx={next_idx}: {exc}",
                      retry=True)
            return {"job_id": job_id, "status": "failed", "reason": "chunk_failed"}

        # Merge new files into the partial corpus
        partial = corpus_store.load_corpus(partial_id)
        if partial is None:
            jobs.fail(job_id, code="INTERNAL",
                      message=f"partial corpus {partial_id!r} missing mid-flight",
                      retry=True)
            return {"job_id": job_id, "status": "failed", "reason": "partial_missing"}

        accumulated_files = list(partial.files) + list(chunk["files"])

        partial_obj = {
            "_meta": {
                "corpus_id": partial_id,
                "source": partial.meta.source,
                "data_classification": partial.meta.data_classification,
                "embedding": partial.meta.embedding,
                "file_count": len(accumulated_files),
                "embedded_count": 0,
                "version": partial.meta.version,
                "last_refresh_completed_at": partial.meta.last_refresh_completed_at,
                "commit_sha": "",
                "lifecycle_state": "active",
            },
            "files": accumulated_files,
            "embeddings": {},
        }
        corpus_store.write_corpus(
            partial_id,
            json.dumps(partial_obj, separators=(",", ":")).encode("utf-8"),
        )

        files_so_far += len(chunk["files"])
        time_total += chunk["time_used_s"]

        if chunk["done"]:
            # ── Done — finalize, write final corpus, clean up ──
            manifest = _gh.finalize(accumulated_files, owner, name, branch)

            # Compute commit_sha over indexed content (same shape as sync path)
            import hashlib
            for f in manifest["files"]:
                if "contentHash" not in f and "hash" in f:
                    f["contentHash"] = f["hash"]
            pairs = sorted([(f.get("path", ""), f.get("contentHash", ""))
                             for f in manifest["files"]])
            commit_sha = hashlib.sha256(
                json.dumps({"repo": repo, "branch": branch, "pairs": pairs},
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:12]

            from datetime import datetime, timezone
            final_obj = {
                "_meta": {
                    "corpus_id": corpus_id,
                    "source": {"type": "github_repo",
                               "uri": f"https://github.com/{repo}",
                               "branch": branch,
                               "indexed_paths": indexed_paths},
                    "data_classification": classification,
                    "embedding": {"provider": "none", "model": "n/a", "dims": 0},
                    "file_count": manifest["totalFiles"],
                    "embedded_count": 0,  # async embed lands in v1.2
                    "version": 1,
                    "last_refresh_completed_at": datetime.now(timezone.utc).isoformat(),
                    "commit_sha": commit_sha,
                    "lifecycle_state": "active",
                },
                "files": manifest["files"],
                "embeddings": {},
            }
            corpus_store.write_corpus(
                corpus_id,
                json.dumps(final_obj, separators=(",", ":")).encode("utf-8"),
            )

            # Cleanup KV state + partial corpus
            kv.delete(_cursor_key(job_id))
            kv.delete(_candidates_key(job_id))
            try:
                _backend = corpus_store._backend()
                _backend.delete(f"{partial_id}.index.json")
            except Exception:  # noqa: BLE001 — cleanup best-effort
                pass

            jobs.complete(job_id, commit_sha=commit_sha,
                          file_count=manifest["totalFiles"],
                          embedded_count=0)
            return {
                "job_id": job_id, "status": "complete",
                "corpus_id": corpus_id, "commit_sha": commit_sha,
                "files_indexed": manifest["totalFiles"],
                "files_total": manifest["totalFiles"],
                "done": True, "time_used_s": chunk["time_used_s"],
            }

        # Not done — save cursor + requeue
        kv.set(_cursor_key(job_id),
               json.dumps({"next_idx": chunk["next_idx"],
                           "files_indexed_so_far": files_so_far,
                           "candidates_count": len(candidates),
                           "time_total_s": time_total}, separators=(",", ":")),
               ex_seconds=jobs.JOB_TTL_SECONDS)
        jobs.update_progress(job_id, cursor=chunk["next_idx"],
                             files_indexed=files_so_far,
                             files_total=len(candidates))
        jobs.requeue(job_id)

        return {
            "job_id": job_id, "status": "advanced",
            "files_indexed": files_so_far, "files_total": len(candidates),
            "next_idx": chunk["next_idx"], "done": False,
            "time_used_s": chunk["time_used_s"],
        }
    finally:
        locks.release_corpus_write_lock(corpus_id, held)
