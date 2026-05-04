"""ce_get_job_status — § 3.7.

Surface async-job progress + terminal state. v1 has no real async backend
(Vercel Cron worker is v1.1) — we register synthetic complete jobs from sync
upload_corpus / index_github_repo calls so callers that always poll get a
uniform shape.

For job IDs we don't recognize (or that have been evicted from in-memory
storage across cold starts), return JOB_NOT_FOUND per § 3.7.
"""
from __future__ import annotations

from typing import Any

from .. import errors, job_store
from ..auth import TokenInfo


def _err(code: str, msg: str, details: dict | None = None) -> dict:
    return errors.tool_error(code, msg, details=details)


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    job_id = args.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return _err("INVALID_ARGUMENT", "job_id is required and must be a non-empty string")

    rec = job_store.get(job_id)
    if rec is None:
        return _err("JOB_NOT_FOUND",
                    f"no job with id {job_id!r} (v1 in-memory store; jobs may evict on cold start)",
                    details={"job_id": job_id})
    return rec.to_wire()
