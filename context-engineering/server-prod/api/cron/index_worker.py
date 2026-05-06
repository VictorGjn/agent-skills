"""Vercel Cron worker for async indexing.

Phase B.2 of v1.1 plan. Vercel Cron fires this endpoint per the schedule
in `vercel.json` (1-min minimum on Pro tier — anabasis-tech is on Pro).
Each invocation:

1. Authenticates the cron request (Bearer CRON_SECRET, sent by Vercel).
2. Pops one job from the `queue:pending` list (atomic LPOP).
3. Calls `async_indexer.advance_one_tick(job)` to do one tick of work.
4. Returns a status dict so logs can show what happened.

Endpoint is public-routable (Vercel Cron hits it via HTTPS), so the
authentication step is load-bearing — without it, anyone with the URL
could drain the queue.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from _lib import async_indexer, jobs  # noqa: E402


def _authenticate(headers) -> bool:
    """Check that this request comes from Vercel Cron.

    Vercel sends `Authorization: Bearer <CRON_SECRET>` when the project
    has CRON_SECRET set. Bearer with a strong CRON_SECRET (32+ random
    bytes) is the documented Vercel pattern — sufficient on its own.
    Without CRON_SECRET set, all requests are rejected — fail-safe.

    Codex P2 on PR #52: previously also required User-Agent containing
    'vercel-cron'. Vercel can change the UA string without notice; if
    they do, every cron tick starts 401'ing silently and async jobs
    back up indefinitely. Bearer-only is more robust.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if not expected:
        return False
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {expected}"


def _process_one() -> dict:
    """Pop one job + advance it. Returns a status dict for logging."""
    job = jobs.claim_next()
    if job is None:
        return {"status": "idle", "queue_len": 0}
    try:
        result = async_indexer.advance_one_tick(job)
        result["queue_kind"] = job.get("kind")
        return result
    except Exception as exc:  # noqa: BLE001 — last-resort defense
        # Log + fail with retry. The job goes back on the queue so the
        # next tick can try again; if the failure is deterministic, the
        # worker will hit MAX_STALE_POPS eventually.
        try:
            jobs.fail(job["id"], code="INTERNAL", message=str(exc), retry=True)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "exception", "job_id": job.get("id"),
                "error": f"{type(exc).__name__}: {exc}"}


class handler(BaseHTTPRequestHandler):
    """HTTP handler — Vercel routes /api/cron/index_worker here."""

    def _respond(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def do_GET(self):
        if not _authenticate(self.headers):
            self._respond(401, {"ok": False, "error": "unauthenticated"})
            return
        result = _process_one()
        self._respond(200, {"ok": True, **result})

    # POST is allowed too — Vercel Cron may use either depending on config
    def do_POST(self):
        self.do_GET()

    def log_message(self, format, *args):
        return
