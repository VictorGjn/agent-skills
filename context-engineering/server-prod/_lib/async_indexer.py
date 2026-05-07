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


def _partial_blob_key(corpus_id: str) -> str:
    """Storage-backend key for the in-flight partial corpus during async
    indexing. NOT a corpus_id — uses a `.partial.json` suffix so it
    doesn't end in `.index.json` and is naturally invisible to
    `corpus_store.list_metas()` (which filters on the canonical suffix).

    Codex P2 on PR #51: previously prefixed the corpus_id with `partial-`,
    which collided with valid user corpora named `partial-*` AND could
    push 121+ char corpus_ids past the 128-char `is_valid_corpus_id` cap
    on later ticks. Using a separate key namespace keyed by the original
    corpus_id avoids both.
    """
    return f"{corpus_id}.partial.json"


def _ensure_scripts_path() -> None:
    """Make the vendored chunkable indexer importable."""
    vendor = Path(__file__).resolve().parent / "vendor"
    p = str(vendor)
    if p not in sys.path:
        sys.path.insert(0, p)


def _resolve_github_token() -> str | None:
    # Shared App-first / PAT-fallback resolver. See _lib/github_auth.py.
    from . import github_auth
    return github_auth.resolve_github_token()


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
    partial_key = _partial_blob_key(corpus_id)

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
            corpus_id, partial_key, indexed_paths,
        )
    finally:
        locks.release_corpus_write_lock(f"job-{job_id}", held_job)


def _advance_one_tick_locked(
    job_id: str, args: dict, repo: str, branch: str, classification: str,
    corpus_id: str, partial_key: str, indexed_paths: list,
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
            # P2.3 (v1.2): use the async cap (10k) — the chunked indexer
            # processes ~50 files / tick at 1-min cadence, so a 10k corpus
            # finishes in ~3.5h of cron ticks. Sync wrapper still defaults
            # to the smaller 2k cap to fit the 280s function budget.
            #
            # P2.4 fix (Codex P1 on PR #54): fetch_tree returns
            # (candidates, resolved_branch). resolved_branch may differ
            # from the input `branch` if auto-resolve fired on a 404. We
            # persist the resolved branch into the partial blob's
            # _meta.source.branch so subsequent ticks (which can't see this
            # local var) call index_chunk against the right ref. Without
            # this, content URLs after a 404 retry use the wrong ref and
            # every file fetch returns empty.
            candidates, resolved_branch = _gh.fetch_tree(
                owner, name, branch,
                _resolve_github_token(),
                max_files=_gh.MAX_FILES_TO_FETCH_ASYNC,
            )
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

        # Codex P1 round 3 on PR #51: write the partial-corpus Blob FIRST,
        # then the KV cursor + candidates. If put_bytes throws (transient
        # Blob failure), neither cursor nor candidates land in KV, so the
        # next tick re-enters first-tick cleanly and retries fetch_tree +
        # blob init. The previous order set durable cursor state pointing
        # at a non-existent partial — every subsequent tick would skip
        # initialization and fail with `partial_missing` until KV TTL.
        empty_partial = {
            "_meta": {
                "corpus_id": corpus_id,  # the eventual corpus, not a partial alias
                "source": {"type": "github_repo",
                           "uri": f"https://github.com/{repo}",
                           # Use resolved_branch (post-auto-resolve) so the
                           # manifest reflects what was actually fetched and
                           # subsequent ticks pick up the correct ref.
                           "branch": resolved_branch,
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
        backend = corpus_store._backend()
        backend.put_bytes(
            partial_key,
            json.dumps(empty_partial, separators=(",", ":")).encode("utf-8"),
        )

        # Now that the partial blob is durable, plant cursor + candidates.
        # Order matters: candidates first (cheap), cursor last — cursor
        # presence is what flips a job into the chunk-processing branch,
        # so it must be the final write so we never see cursor without
        # candidates.
        kv.set(_candidates_key(job_id),
               json.dumps(candidates, separators=(",", ":")),
               ex_seconds=jobs.JOB_TTL_SECONDS)
        kv.set(_cursor_key(job_id),
               json.dumps({"next_idx": 0, "files_indexed_so_far": 0,
                           "candidates_count": len(candidates),
                           "time_total_s": 0.0}, separators=(",", ":")),
               ex_seconds=jobs.JOB_TTL_SECONDS)

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
    # Engineer-review P1 on PR #51: TTL must cover the full tick budget
    # (220s) plus headroom for partial-blob round-trip. Default 90s is
    # too short — a slow GitHub fetch could let the lock TTL expire mid-
    # tick, allowing a concurrent ce_upload_corpus on the same corpus_id
    # to legitimately acquire the lock and start writing while we still
    # hold our (now-released) reference. Match the per-job lock at 300s.
    held = locks.acquire_corpus_write_lock(corpus_id, ttl_seconds=300)
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
        # P2.4 fix (Codex P1 on PR #54): the resolved branch is persisted
        # in the partial blob's _meta.source.branch on first tick. We
        # read the partial here BEFORE index_chunk so we can pass the
        # actual ref (which may differ from the original `branch` arg if
        # first-tick auto-resolved a 404). Without this, every
        # subsequent-tick content URL points at a non-existent ref and
        # fetches return empty.
        #
        # The same partial is reused below for the chunk merge — within
        # the per-corpus write lock it can't change between read and
        # merge, so a single read suffices.
        backend = corpus_store._backend()
        partial_raw = backend.get_bytes(partial_key)
        if partial_raw is None:
            # Codex P1 round 2 on PR #51: fall back to the legacy
            # `partial-<corpus_id>.index.json` scheme for any job that was
            # mid-flight when this code shipped. Practically defensive —
            # no production ever ran the prefix scheme — but cheap.
            legacy_key = f"partial-{corpus_id}.index.json"
            partial_raw = backend.get_bytes(legacy_key)
        if partial_raw is None:
            jobs.fail(job_id, code="INTERNAL",
                      message=f"partial corpus {partial_key!r} missing mid-flight",
                      retry=True)
            return {"job_id": job_id, "status": "failed", "reason": "partial_missing"}
        try:
            partial = json.loads(partial_raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            jobs.fail(job_id, code="INTERNAL",
                      message=f"partial corpus {partial_key!r} corrupted JSON",
                      retry=True)
            return {"job_id": job_id, "status": "failed", "reason": "partial_corrupted"}
        resolved_branch_for_chunk = (
            (partial.get("_meta") or {}).get("source", {}).get("branch")
            or branch
        )

        try:
            chunk = _gh.index_chunk(
                owner, name, resolved_branch_for_chunk, _resolve_github_token(),
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

        # Codex P1 round 6 on PR #51: dedupe by path on every merge.
        # Failure window: put_bytes(partial) succeeds → cursor save fails
        # (KV outage) → cron worker outer except calls fail(retry=True) →
        # next tick reads stale cursor + already-appended partial → fetches
        # the SAME chunk from GitHub → appends those files AGAIN → finalize
        # doesn't dedupe → final corpus has duplicate rows + inflated
        # totalFiles/token counts. Dedupe here is the simplest fix: dict
        # preserves insertion order, last-write-wins per path so a replay
        # updates to the latest fetch (stable across retries since SDLC
        # repos are sha-pinned per candidate). Also catches any other
        # path-collision pathology in the partial state.
        file_map: dict[str, dict] = {}
        for f in list(partial.get("files") or []) + list(chunk["files"]):
            p = f.get("path")
            if p:
                file_map[p] = f
        accumulated_files = list(file_map.values())
        partial_meta = partial.get("_meta") or {}
        partial_obj = {
            "_meta": {
                "corpus_id": corpus_id,
                "source": partial_meta.get("source"),
                "data_classification": partial_meta.get("data_classification", classification),
                "embedding": partial_meta.get("embedding") or {"provider": "none", "model": "n/a", "dims": 0},
                "file_count": len(accumulated_files),
                "embedded_count": 0,
                "version": partial_meta.get("version", 1),
                "last_refresh_completed_at": partial_meta.get("last_refresh_completed_at"),
                "commit_sha": "",
                "lifecycle_state": "active",
            },
            "files": accumulated_files,
            "embeddings": {},
        }
        backend.put_bytes(
            partial_key,
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

            # Engineer-review P1 on PR #51: complete the job FIRST
            # (durable corpus is the success criterion), THEN clean up.
            # Previously cleanup ran first; if any kv.delete raised
            # (Upstash 5xx) the exception unwound past jobs.complete,
            # the cron worker's outer except called fail(retry=True),
            # next tick saw cursor+candidates still in KV → entered
            # branch 2 → partial_key already deleted → infinite retry
            # loop while the durable corpus existed.
            jobs.complete(job_id, commit_sha=commit_sha,
                          file_count=manifest["totalFiles"],
                          embedded_count=0)

            # Cleanup is best-effort. Each call individually try/except'd
            # so a single Upstash/Blob hiccup doesn't strand the others.
            # Anything left behind TTLs out at JOB_TTL_SECONDS (7 days).
            try:
                kv.delete(_cursor_key(job_id))
            except Exception:  # noqa: BLE001
                pass
            try:
                kv.delete(_candidates_key(job_id))
            except Exception:  # noqa: BLE001
                pass
            try:
                backend.delete(partial_key)
            except Exception:  # noqa: BLE001
                pass
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
