"""In-memory job registry for v1.

v1 production server has no async backend (Vercel Cron + queue lands in v1.1).
Sync index/upload calls register their result here so `ce_get_job_status` can
report `complete` immediately.

The store lives in process memory — Vercel function instances may evict between
invocations, so jobs may surface as JOB_NOT_FOUND if polled across cold starts.
SPEC § 3.7 says job IDs expire after 7 days; v1's eviction is more aggressive
but stays within the contract.

Future: replace with Redis or Vercel KV for cross-instance persistence.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobRecord:
    job_id: str
    corpus_id: str
    status: str  # queued | running | complete | failed | timeout
    started_at: float | None = None
    completed_at: float | None = None
    progress: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    result_commit_sha: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "corpus_id": self.corpus_id,
            "status": self.status,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "progress": self.progress,
            "error": self.error,
            "result_commit_sha": self.result_commit_sha,
        }


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


_JOBS: dict[str, JobRecord] = {}


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def register_complete(corpus_id: str, commit_sha: str,
                      files_indexed: int = 0, files_total: int = 0) -> str:
    """Register a synthetic 'complete' job for a sync-finished operation.

    Returns the job_id so callers can echo it for clients that want a uniform
    poll-or-no shape.
    """
    job_id = new_job_id()
    now = time.time()
    _JOBS[job_id] = JobRecord(
        job_id=job_id,
        corpus_id=corpus_id,
        status="complete",
        started_at=now,
        completed_at=now,
        progress={"files_indexed": files_indexed, "files_total": files_total, "phase": "done"},
        result_commit_sha=commit_sha,
    )
    return job_id


def get(job_id: str) -> JobRecord | None:
    return _JOBS.get(job_id)
