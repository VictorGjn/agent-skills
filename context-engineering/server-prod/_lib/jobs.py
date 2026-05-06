"""Async job lifecycle — KV-backed (or in-memory for tests).

Phase B.1 of v1.1 plan. Replaces the in-memory `job_store._JOBS` dict with
a durable Redis-backed implementation so async indexing (Phase B.2) can
survive cold starts + cron worker re-invocations.

Two storage backends:
- `KVJobsBackend` — Vercel KV (Upstash). Default in production.
- `InMemoryJobsBackend` — process-local dict. Default in tests so they
  don't need a live KV.

Backend resolution mirrors `_lib/storage/__init__.py`: pick KV when
`KV_REST_API_URL`+`KV_REST_API_TOKEN` are set, else in-memory.

Job state shape (stored under `job:<id>`):
    {
      "id": "<uuid>",
      "kind": "index_github_repo" | ...,
      "status": "queued" | "running" | "complete" | "failed",
      "args": { ... original tool args ... },
      "owner": "<token_id>",
      "files_indexed": int,
      "files_total": int | null,
      "embedded_count": int,
      "commit_sha": str | null,
      "error_code": str | null,
      "error_message": str | null,
      "cursor": int | null,        # next file index for resume
      "created_at": iso8601,
      "started_at": iso8601 | null,
      "completed_at": iso8601 | null,
    }

Queue (`queue:pending`) holds job_ids ready for worker pickup. Workers
LPOP atomically — two workers can't claim the same job. After a worker
advances a job (chunk processed), if not done it RPUSHes back to the
tail.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol


JOB_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days — bench corpora resolve well within
QUEUE_KEY = "queue:pending"

# Keep the legacy in-process registry around so existing code paths in
# job_store.py keep working while we migrate. New code should hit the
# JobsBackend abstraction directly.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_key(job_id: str) -> str:
    return f"job:{job_id}"


# ── Backend protocol ──

class JobsBackend(Protocol):
    """Minimal interface job lifecycle calls into."""

    def get(self, job_id: str) -> dict | None: ...
    def put(self, job_id: str, record: dict) -> None: ...
    def delete(self, job_id: str) -> None: ...
    def queue_push(self, job_id: str) -> None: ...
    def queue_pop(self) -> str | None: ...
    def queue_len(self) -> int: ...


# ── In-memory backend (tests / local dev without KV) ──

class InMemoryJobsBackend:
    """Process-local. Single-instance only — Vercel cold starts wipe it.

    Phase B.1 keeps this as the default for tests. Production picks the
    KV backend automatically when the env vars are set.
    """

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._queue: list[str] = []

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def put(self, job_id: str, record: dict) -> None:
        self._jobs[job_id] = dict(record)

    def delete(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def queue_push(self, job_id: str) -> None:
        self._queue.append(job_id)

    def queue_pop(self) -> str | None:
        if not self._queue:
            return None
        return self._queue.pop(0)

    def queue_len(self) -> int:
        return len(self._queue)

    def reset(self) -> None:
        """Test hook — clears queue + jobs."""
        self._jobs.clear()
        self._queue.clear()


# ── KV backend (production) ──

class KVJobsBackend:
    """Vercel KV-backed. Survives cold starts; cross-instance safe."""

    def get(self, job_id: str) -> dict | None:
        from .storage import kv
        raw = kv.get(_job_key(job_id))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def put(self, job_id: str, record: dict) -> None:
        from .storage import kv
        kv.set(_job_key(job_id), json.dumps(record, separators=(",", ":")),
               ex_seconds=JOB_TTL_SECONDS)

    def delete(self, job_id: str) -> None:
        from .storage import kv
        kv.delete(_job_key(job_id))

    def queue_push(self, job_id: str) -> None:
        from .storage import kv
        kv.rpush(QUEUE_KEY, job_id)

    def queue_pop(self) -> str | None:
        from .storage import kv
        return kv.lpop(QUEUE_KEY)

    def queue_len(self) -> int:
        from .storage import kv
        return kv.llen(QUEUE_KEY)


# ── Backend resolution ──

_BACKEND: JobsBackend | None = None


def _backend() -> JobsBackend:
    global _BACKEND
    if _BACKEND is None:
        if os.environ.get("KV_REST_API_URL") and os.environ.get("KV_REST_API_TOKEN"):
            _BACKEND = KVJobsBackend()
        else:
            _BACKEND = InMemoryJobsBackend()
    return _BACKEND


def set_backend(backend: JobsBackend | None) -> None:
    """Test override. Pass None to reset to env-driven default."""
    global _BACKEND
    _BACKEND = backend


# ── Lifecycle API ──

def enqueue(kind: str, args: dict, *, owner: str = "") -> str:
    """Create a queued job, return the job_id. Worker picks it up via
    claim_next() on the next cron tick.
    """
    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "args": args,
        "owner": owner,
        "files_indexed": 0,
        "files_total": None,
        "embedded_count": 0,
        "commit_sha": None,
        "error_code": None,
        "error_message": None,
        "cursor": 0,
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
    }
    backend = _backend()
    backend.put(job_id, record)
    backend.queue_push(job_id)
    return job_id


_MAX_STALE_POPS = 100  # bound the inner loop — defense against a corrupt queue


def claim_next() -> dict | None:
    """Worker pulls the next pending job. Atomic — two workers can't claim
    the same job_id (LPOP guarantees). Marks status='running', sets
    started_at if first claim. Returns the full job record.

    Loops past stale queue heads (job_id whose record was TTL'd / deleted)
    until a live record is found or the queue is truly empty. Codex P2 on
    PR #50: a single stale entry shouldn't make a non-empty queue look
    empty for a whole cron tick.

    Bounded by _MAX_STALE_POPS to prevent a corrupt queue (or a buggy
    sweeper writing nothing but stale ids) from spinning a worker forever.
    Returns None if the queue is empty OR every head we drained was stale.
    """
    backend = _backend()
    for _ in range(_MAX_STALE_POPS):
        job_id = backend.queue_pop()
        if job_id is None:
            return None
        record = backend.get(job_id)
        if record is None:
            # Stale queue entry — record TTL'd / deleted. Try the next one.
            continue
        if record["status"] == "queued":
            record["status"] = "running"
            # Engineer-review P1 on PR #51: clear last-attempt error fields
            # on the queued→running transition. fail(retry=True) records
            # error_code/error_message + sets status='queued'; without
            # clearing on reclaim, a healthy retry would surface in the
            # wire shape as {status:"running", error:{code,message}} and
            # any client keying off `error != null` would treat the live
            # job as broken. Cleared on reclaim — the error is preserved
            # in the diagnostics window between fail(retry) and the next
            # tick, but evaporates the moment the worker picks it up.
            record["error_code"] = None
            record["error_message"] = None
            # Set started_at on FIRST claim only — preserve original timestamp
            # on retry-reclaim so wire-side started_at reflects when work
            # actually began, not the latest cycle.
            if record.get("started_at") is None:
                record["started_at"] = _now()
            backend.put(job_id, record)
        return record
    return None


def update_progress(job_id: str, *, cursor: int, files_indexed: int,
                    files_total: int | None = None,
                    embedded_count: int | None = None) -> None:
    """Save a chunk-worker's intermediate state. Caller MUST then either
    requeue (if not done) or call complete()/fail()."""
    backend = _backend()
    record = backend.get(job_id)
    if record is None:
        return
    record["cursor"] = cursor
    record["files_indexed"] = files_indexed
    if files_total is not None:
        record["files_total"] = files_total
    if embedded_count is not None:
        record["embedded_count"] = embedded_count
    backend.put(job_id, record)


def requeue(job_id: str) -> None:
    """Push the job_id back onto the pending queue (RPUSH so it goes to
    the tail; FIFO fairness with newer arrivals)."""
    _backend().queue_push(job_id)


def complete(job_id: str, *, commit_sha: str, file_count: int,
             embedded_count: int) -> None:
    """Mark a job complete with its final output reference."""
    backend = _backend()
    record = backend.get(job_id)
    if record is None:
        return
    record.update({
        "status": "complete",
        "commit_sha": commit_sha,
        "files_indexed": file_count,
        "files_total": file_count,
        "embedded_count": embedded_count,
        "completed_at": _now(),
    })
    backend.put(job_id, record)


def fail(job_id: str, *, code: str, message: str, retry: bool = False) -> None:
    """Mark a job failed (terminal) or queued-for-retry (non-terminal).

    Codex P2 round 3 on PR #51: with retry=True we must NOT set
    status='failed'. claim_next() only flips queued→running, so a
    failed-then-requeued record would stay in status='failed' from the
    client's view (ce_get_job_status returns 'failed') even while the
    worker is happily retrying. Clients that stop polling on first
    'failed' would treat recoverable jobs as terminal failures.

    Behavior:
    - retry=False: status='failed', completed_at set (terminal).
    - retry=True: status='queued', completed_at NOT set, error_code +
      error_message recorded as last-attempt diagnostics. Push back to
      queue. claim_next picks it up and flips queued→running normally.
    """
    backend = _backend()
    record = backend.get(job_id)
    if record is None:
        return
    if retry:
        record.update({
            "status": "queued",  # non-terminal — claim_next will run() it
            "error_code": code,
            "error_message": message,
            # NOT setting completed_at — job isn't finished
        })
        backend.put(job_id, record)
        backend.queue_push(job_id)
    else:
        record.update({
            "status": "failed",
            "error_code": code,
            "error_message": message,
            "completed_at": _now(),
        })
        backend.put(job_id, record)


def status(job_id: str) -> dict | None:
    """Return the public-facing subset of the job record (no internal
    cursor / queue-only fields). Used by ce_get_job_status — and
    `tools/get_job_status.py` translates this shape into the SPEC § 3.7
    wire contract.

    Includes `corpus_id` extracted from args so the caller doesn't have
    to round-trip the raw record. Excludes args itself (caller-private)
    and cursor (internal state).
    """
    backend = _backend()
    record = backend.get(job_id)
    if record is None:
        return None
    args = record.get("args") or {}
    return {
        "id": record["id"],
        "kind": record["kind"],
        "corpus_id": args.get("corpus_id"),
        "status": record["status"],
        "files_indexed": record.get("files_indexed", 0),
        "files_total": record.get("files_total"),
        "embedded_count": record.get("embedded_count", 0),
        "commit_sha": record.get("commit_sha"),
        "error_code": record.get("error_code"),
        "error_message": record.get("error_message"),
        "created_at": record.get("created_at"),
        "started_at": record.get("started_at"),
        "completed_at": record.get("completed_at"),
    }
