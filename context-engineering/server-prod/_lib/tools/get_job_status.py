"""ce_get_job_status — § 3.7.

Surface async-job progress + terminal state. Wire contract per SPEC § 3.7:

    {
      "job_id":            str,
      "corpus_id":         str | null,
      "status":            "queued" | "running" | "complete" | "failed" | "timeout",
      "started_at":        iso8601 | null,
      "completed_at":      iso8601 | null,
      "progress":          {"files_indexed": int, "files_total": int | null,
                            "embedded_count": int} | null,
      "error":             {"code": str, "message": str} | null,
      "result_commit_sha": str | null,
    }

Phase B.3 of v1.1 plan: this reads from `_lib.jobs` (KV-backed in
production, in-memory in tests) and translates the internal record shape
to the wire contract above. Falls back to legacy `_lib.job_store` for
synthetic records produced by sync paths.

Codex P1 on PR #51: previously returned `jobs.status()` verbatim, which
used internal keys (`id`, `kind`, `files_indexed`, `error_code`) instead
of the wire contract. Async polling clients would have parsed the wrong
fields.
"""
from __future__ import annotations

from typing import Any

from .. import errors, job_store, jobs
from ..auth import TokenInfo


def _err(code: str, msg: str, details: dict | None = None) -> dict:
    return errors.tool_error(code, msg, details=details)


def _kv_record_to_wire(rec: dict) -> dict[str, Any]:
    """Translate an internal `jobs.status()` dict to the SPEC § 3.7 wire
    shape. Internal field renames + nesting:
    - `id` → `job_id`
    - `files_indexed`+`files_total`+`embedded_count` → `progress` object
    - `error_code`+`error_message` → `error` object (or null)
    - `commit_sha` → `result_commit_sha`
    - `kind` is dropped (caller already knows what they enqueued)
    - `created_at` is dropped (not part of the wire contract)
    """
    err = None
    if rec.get("error_code") or rec.get("error_message"):
        err = {
            "code": rec.get("error_code") or "INTERNAL",
            "message": rec.get("error_message") or "",
        }
    return {
        "job_id": rec["id"],
        "corpus_id": rec.get("corpus_id"),
        "status": rec["status"],
        "started_at": rec.get("started_at"),
        "completed_at": rec.get("completed_at"),
        "progress": {
            "files_indexed": rec.get("files_indexed", 0),
            "files_total": rec.get("files_total"),
            "embedded_count": rec.get("embedded_count", 0),
        },
        "error": err,
        "result_commit_sha": rec.get("commit_sha"),
    }


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    job_id = args.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return _err("INVALID_ARGUMENT", "job_id is required and must be a non-empty string")

    # Phase B.3: prefer the KV-backed jobs API (durable across cold starts
    # for async jobs). Translate to the SPEC § 3.7 wire shape.
    rec = jobs.status(job_id)
    if rec is not None:
        return _kv_record_to_wire(rec)

    # Fall back to the legacy in-memory job_store for synthetic records
    # produced by the sync upload_corpus / index_github_repo paths. Its
    # to_wire() already matches the contract.
    legacy = job_store.get(job_id)
    if legacy is None:
        return _err("JOB_NOT_FOUND",
                    f"no job with id {job_id!r}",
                    details={"job_id": job_id})
    return legacy.to_wire()
