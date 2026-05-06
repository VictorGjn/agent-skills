"""Phase B.1 tests — Vercel KV client + async job lifecycle.

Covers:
- kv._exec wire shape: POST <api> with JSON-array body, Bearer auth
- kv.set/get/delete/exists/expire round-trips against a mock HTTP layer
- kv.set with NX flag returns False when key exists
- kv.rpush/lpop atomic queue semantics
- kv.acquire_lock / release_lock pattern (NX + EVAL check-and-delete)
- jobs InMemoryJobsBackend round-trips
- jobs.enqueue + claim_next + complete: full happy path
- jobs.claim_next returns None on empty queue
- jobs.fail with retry=True requeues; without retry doesn't
- jobs.update_progress preserves other fields
- jobs.status filters internal fields (cursor, args)

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase_b1_kv_and_jobs.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

from _lib import jobs  # noqa: E402
from _lib.storage import kv  # noqa: E402


# ── KV client tests ──

class _FakeKVHttp:
    """Records calls; routes responses by command name (first arg)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.routes: dict[str, dict] = {}
        self.default_result: object = None
        # Simulated key-value store for round-trip tests
        self.kv_store: dict[str, str] = {}
        self.kv_lists: dict[str, list[str]] = {}

    def add_route(self, command: str, *, result: object):
        self.routes[command.upper()] = {"result": result}

    def __call__(self, command: list, *, timeout=None):
        # _exec coerces every part to str; match that shape
        cmd = [str(p) for p in command]
        self.calls.append({"command": cmd})
        op = cmd[0].upper()

        # Built-in simulator: SET/GET/DEL/RPUSH/LPOP/EXISTS/EXPIRE/EVAL
        if op == "SET":
            key, val = cmd[1], cmd[2]
            nx = "NX" in cmd[3:]
            if nx and key in self.kv_store:
                return {"result": None}
            self.kv_store[key] = val
            return {"result": "OK"}
        if op == "GET":
            return {"result": self.kv_store.get(cmd[1])}
        if op == "DEL":
            existed = cmd[1] in self.kv_store
            self.kv_store.pop(cmd[1], None)
            self.kv_lists.pop(cmd[1], None)
            return {"result": 1 if existed else 0}
        if op == "EXISTS":
            return {"result": 1 if cmd[1] in self.kv_store else 0}
        if op == "EXPIRE":
            return {"result": 1 if cmd[1] in self.kv_store else 0}
        if op == "RPUSH":
            self.kv_lists.setdefault(cmd[1], []).append(cmd[2])
            return {"result": len(self.kv_lists[cmd[1]])}
        if op == "LPOP":
            lst = self.kv_lists.get(cmd[1])
            if not lst:
                return {"result": None}
            return {"result": lst.pop(0)}
        if op == "LLEN":
            return {"result": len(self.kv_lists.get(cmd[1], []))}
        if op == "EVAL":
            # Simulate the lock-release script: del if value matches
            # cmd: ['EVAL', script, '1', key, expected_value]
            key = cmd[3]
            expected = cmd[4]
            if self.kv_store.get(key) == expected:
                del self.kv_store[key]
                return {"result": 1}
            return {"result": 0}

        # Explicit override
        if op in self.routes:
            return self.routes[op]
        return {"result": self.default_result}


@pytest.fixture
def fake_kv(monkeypatch):
    monkeypatch.setenv("KV_REST_API_URL", "https://example.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "test-token")
    fake = _FakeKVHttp()
    monkeypatch.setattr(kv, "_exec", fake)
    return fake


def test_kv_set_get_round_trip(fake_kv):
    assert kv.set("foo", "bar") is True
    assert kv.get("foo") == "bar"


def test_kv_set_nx_returns_false_on_existing(fake_kv):
    kv.set("k", "v1")
    # NX should refuse the overwrite
    assert kv.set("k", "v2", nx=True) is False
    assert kv.get("k") == "v1"


def test_kv_set_with_ex_seconds_passes_command_args(fake_kv):
    kv.set("k", "v", ex_seconds=60)
    # The fake doesn't honour TTL but we check the command shape went through
    last = fake_kv.calls[-1]["command"]
    assert "EX" in last
    assert "60" in last


def test_kv_delete_returns_count(fake_kv):
    kv.set("k", "v")
    assert kv.delete("k") == 1
    assert kv.delete("k") == 0


def test_kv_exists_reflects_state(fake_kv):
    assert kv.exists("k") is False
    kv.set("k", "v")
    assert kv.exists("k") is True


def test_kv_rpush_lpop_queue_semantics(fake_kv):
    """Atomic LPOP — two workers can't claim the same job."""
    kv.rpush("queue", "job-1")
    kv.rpush("queue", "job-2")
    assert kv.llen("queue") == 2
    assert kv.lpop("queue") == "job-1"
    assert kv.lpop("queue") == "job-2"
    assert kv.lpop("queue") is None  # empty


def test_kv_acquire_lock_blocks_second_acquire(fake_kv):
    holder = kv.acquire_lock("alpha", ttl_seconds=60)
    assert holder is not None
    # Second try fails — already held
    assert kv.acquire_lock("alpha", ttl_seconds=60) is None


def test_kv_release_lock_only_with_matching_holder(fake_kv):
    """Atomic check-and-delete prevents stomping a lock another worker
    acquired after the original holder's TTL expired."""
    holder = kv.acquire_lock("alpha", ttl_seconds=60)
    assert holder is not None
    # Wrong holder → release fails
    assert kv.release_lock("alpha", "not-the-holder") is False
    # Correct holder → release succeeds
    assert kv.release_lock("alpha", holder) is True
    # Now another worker can acquire
    holder2 = kv.acquire_lock("alpha", ttl_seconds=60)
    assert holder2 is not None and holder2 != holder


def test_kv_request_includes_bearer_auth_header(monkeypatch):
    """The wire shape: POST <KV_REST_API_URL> body=JSON-array, Bearer auth."""
    monkeypatch.setenv("KV_REST_API_URL", "https://x.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "secret-token")

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["method"] = req.get_method()
        captured["body"] = req.data

        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"result":"OK"}'
            headers = {}
        return FakeResp()

    monkeypatch.setattr(kv.urllib.request, "urlopen", fake_urlopen)

    kv._exec(["SET", "k", "v"])
    assert captured["url"] == "https://x.upstash.io"
    assert captured["method"] == "POST"
    # urllib title-cases header names
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["Content-type"] == "application/json"
    assert json.loads(captured["body"].decode()) == ["SET", "k", "v"]


def test_kv_missing_token_raises_provider_unavailable(monkeypatch):
    monkeypatch.delenv("KV_REST_API_TOKEN", raising=False)
    monkeypatch.setenv("KV_REST_API_URL", "https://x.upstash.io")
    with pytest.raises(kv.KVError) as exc:
        kv._token()
    assert exc.value.code == "PROVIDER_UNAVAILABLE"


def test_kv_command_error_raises_kv_command(monkeypatch):
    """When Upstash returns {"error": "..."} we raise KVError(KV_COMMAND)."""
    monkeypatch.setenv("KV_REST_API_URL", "https://x.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "tok")

    def fake_urlopen(req, timeout=None):
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"error":"ERR wrong number of arguments"}'
            headers = {}
        return FakeResp()
    monkeypatch.setattr(kv.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(kv.KVError) as exc:
        kv._exec(["GET"])
    assert exc.value.code == "KV_COMMAND"


# ── Jobs API tests ──

@pytest.fixture(autouse=True)
def _isolate_jobs_backend():
    """Each jobs test gets a fresh in-memory backend so queue/dict state
    doesn't leak (orthogonal to the conftest singleton reset)."""
    backend = jobs.InMemoryJobsBackend()
    jobs.set_backend(backend)
    yield backend
    jobs.set_backend(None)


def test_jobs_enqueue_and_status_round_trip(_isolate_jobs_backend):
    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main"},
                          owner="t-1")
    state = jobs.status(job_id)
    assert state is not None
    assert state["id"] == job_id
    assert state["kind"] == "index_github_repo"
    assert state["status"] == "queued"
    assert state["files_indexed"] == 0
    assert "args" not in state  # internal — not echoed
    assert "cursor" not in state


def test_jobs_claim_next_marks_running_and_pops_queue(_isolate_jobs_backend):
    job_id = jobs.enqueue("index_github_repo", {"repo": "x/y"}, owner="t-1")
    backend = _isolate_jobs_backend
    assert backend.queue_len() == 1

    claimed = jobs.claim_next()
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert claimed["started_at"] is not None
    assert backend.queue_len() == 0


def test_jobs_claim_next_returns_none_on_empty_queue(_isolate_jobs_backend):
    assert jobs.claim_next() is None


def test_jobs_two_workers_dont_claim_same_job(_isolate_jobs_backend):
    """Atomic LPOP semantics: even with simultaneous claim_next calls, only
    one worker gets the job."""
    job_id = jobs.enqueue("index_github_repo", {"repo": "x/y"}, owner="t-1")

    first = jobs.claim_next()
    second = jobs.claim_next()
    assert first is not None and first["id"] == job_id
    assert second is None  # queue drained


def test_jobs_update_progress_preserves_other_fields(_isolate_jobs_backend):
    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main"},
                          owner="t-1")
    jobs.claim_next()
    jobs.update_progress(job_id, cursor=50, files_indexed=50,
                         files_total=300, embedded_count=50)
    record = _isolate_jobs_backend.get(job_id)
    assert record is not None
    assert record["cursor"] == 50
    assert record["files_indexed"] == 50
    assert record["files_total"] == 300
    assert record["embedded_count"] == 50
    # Owner + kind preserved
    assert record["owner"] == "t-1"
    assert record["kind"] == "index_github_repo"


def test_jobs_complete_marks_status_and_records_sha(_isolate_jobs_backend):
    job_id = jobs.enqueue("index_github_repo", {"repo": "x/y"}, owner="t-1")
    jobs.claim_next()
    jobs.complete(job_id, commit_sha="deadbeef", file_count=300,
                  embedded_count=300)
    state = jobs.status(job_id)
    assert state is not None
    assert state["status"] == "complete"
    assert state["commit_sha"] == "deadbeef"
    assert state["files_indexed"] == 300
    assert state["embedded_count"] == 300
    assert state["completed_at"] is not None


def test_jobs_fail_without_retry_does_not_requeue(_isolate_jobs_backend):
    job_id = jobs.enqueue("index_github_repo", {"repo": "x/y"}, owner="t-1")
    jobs.claim_next()
    jobs.fail(job_id, code="SOURCE_NOT_FOUND", message="repo missing")
    assert _isolate_jobs_backend.queue_len() == 0
    state = jobs.status(job_id)
    assert state is not None
    assert state["status"] == "failed"
    assert state["error_code"] == "SOURCE_NOT_FOUND"


def test_jobs_fail_with_retry_requeues_as_queued(_isolate_jobs_backend):
    """Codex P2 round 3 on PR #51: retry=True must NOT terminalize the
    record (status='failed'). claim_next only flips queued→running, so
    a 'failed'-status record would stay 'failed' from the wire view —
    clients polling ce_get_job_status would see a terminal failure for
    a job the worker is happily retrying. Fix: retry=True records error
    diagnostics but keeps status='queued', no completed_at.

    Engineer-review P1 on PR #51: error_code/error_message MUST be cleared
    when claim_next reclaims a queued retry — otherwise the wire shape
    surfaces {status:"running", error:{code,message}} for a healthy job
    and any client keying off `error != null` treats the live job as
    broken.
    """
    job_id = jobs.enqueue("index_github_repo", {"repo": "x/y"}, owner="t-1")
    first_claim = jobs.claim_next()
    original_started_at = first_claim["started_at"]
    jobs.fail(job_id, code="EMBED_HTTP", message="Mistral 503", retry=True)
    assert _isolate_jobs_backend.queue_len() == 1
    state = jobs.status(job_id)
    assert state is not None
    assert state["status"] == "queued", "retry=True must not terminalize"
    assert state["error_code"] == "EMBED_HTTP"
    assert state["error_message"] == "Mistral 503"
    assert state["completed_at"] is None, "retry=True must not set completed_at"
    # Reclaim — claim_next flips queued→running, retry continues normally
    re_claimed = jobs.claim_next()
    assert re_claimed is not None
    assert re_claimed["status"] == "running"
    # Engineer-review fix: error fields cleared on reclaim
    assert re_claimed["error_code"] is None, (
        "stale error_code on healthy retry would surface as ghost failure "
        "in ce_get_job_status wire shape"
    )
    assert re_claimed["error_message"] is None
    # started_at preserved from original claim — not bumped to reclaim time
    assert re_claimed["started_at"] == original_started_at, (
        "started_at must reflect when work began, not the latest reclaim cycle"
    )


def test_jobs_status_returns_none_for_unknown_id(_isolate_jobs_backend):
    assert jobs.status("not-a-job") is None


def test_jobs_unknown_id_in_queue_is_skipped(_isolate_jobs_backend):
    """If a stale job_id is in the queue (record TTL'd or was deleted),
    claim_next returns None rather than crashing — but it must drain the
    stale entry off the queue before returning."""
    backend = _isolate_jobs_backend
    backend.queue_push("stale-job-id")
    # No corresponding record in the dict
    assert jobs.claim_next() is None
    # Stale entry should have been popped (drained) so it doesn't keep
    # blocking subsequent claim_next calls.
    assert backend.queue_len() == 0


def test_jobs_claim_next_loops_past_stale_to_live(_isolate_jobs_backend):
    """Codex P2 on PR #50: a stale queue head should NOT cause claim_next
    to return None when there's a live job behind it. Without the loop,
    each stale entry costs a whole cron tick of latency before real work
    runs.
    """
    backend = _isolate_jobs_backend
    # Plant a stale id BEFORE a real one
    backend.queue_push("stale-1")
    backend.queue_push("stale-2")
    real_id = jobs.enqueue("index_github_repo", {"repo": "x/y"}, owner="t-1")
    # Queue order: [stale-1, stale-2, real_id]
    assert backend.queue_len() == 3

    claimed = jobs.claim_next()
    assert claimed is not None, (
        "claim_next stopped on first stale entry instead of looping past it"
    )
    assert claimed["id"] == real_id
    # Both stale entries + the real one should be off the queue
    assert backend.queue_len() == 0


def test_jobs_claim_next_returns_none_when_only_stale(_isolate_jobs_backend):
    """If every queue head is stale, claim_next eventually returns None
    (after draining them) rather than spinning forever."""
    backend = _isolate_jobs_backend
    for i in range(5):
        backend.queue_push(f"stale-{i}")
    assert jobs.claim_next() is None
    assert backend.queue_len() == 0


def test_jobs_kv_backend_picked_when_env_set(monkeypatch):
    """Backend resolution: KV vars present → KVJobsBackend picked."""
    monkeypatch.setenv("KV_REST_API_URL", "https://x.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "tok")
    jobs.set_backend(None)
    backend = jobs._backend()
    assert isinstance(backend, jobs.KVJobsBackend)
    jobs.set_backend(None)


def test_jobs_in_memory_backend_default_when_no_env(monkeypatch):
    monkeypatch.delenv("KV_REST_API_URL", raising=False)
    monkeypatch.delenv("KV_REST_API_TOKEN", raising=False)
    jobs.set_backend(None)
    backend = jobs._backend()
    assert isinstance(backend, jobs.InMemoryJobsBackend)
    jobs.set_backend(None)
