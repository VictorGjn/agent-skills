"""ce_get_job_status — § 3.7.

Surface async-job progress + terminal state. Phase B.3 of v1.1 plan: this
now reads from `_lib.jobs` (KV-backed in production via the Phase B.1
primitive; in-memory in tests). The legacy `_lib.job_store` synthetic-
records path is kept as a fallback so existing tests + sync paths that
register completion through the old API still work.

For job IDs we don't recognize, return JOB_NOT_FOUND per § 3.7.
"""
from __future__ import annotations

from typing import Any

from .. import errors, job_store, jobs
from ..auth import TokenInfo


def _err(code: str, msg: str, details: dict | None = None) -> dict:
    return errors.tool_error(code, msg, details=details)


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    job_id = args.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return _err("INVALID_ARGUMENT", "job_id is required and must be a non-empty string")

    # Phase B.3: prefer the KV-backed jobs API (durable across cold starts
    # for async jobs). Fall back to the in-memory job_store for legacy
    # synthetic records produced by sync upload_corpus / index_github_repo.
    rec = jobs.status(job_id)
    if rec is not None:
        return rec

    legacy = job_store.get(job_id)
    if legacy is None:
        return _err("JOB_NOT_FOUND",
                    f"no job with id {job_id!r}",
                    details={"job_id": job_id})
    return legacy.to_wire()
