"""Phase B.2 tests — async chunked indexer (advance_one_tick) + cron worker auth.

Covers:
- fetch_tree / index_chunk / finalize: pure-function refactor of the
  vendored indexer. Sync wrapper preserves the original index_github_repo
  return shape.
- async_indexer.advance_one_tick first-tick: fetches tree, plants
  candidates + cursor in KV, writes empty partial corpus, requeues, NO
  files indexed yet.
- advance_one_tick subsequent tick: pops chunk, appends to partial corpus,
  saves cursor, requeues.
- advance_one_tick done branch: finalizes manifest, writes final corpus,
  cleans up KV state + partial blob, marks job complete.
- Mid-flight tree-fetch failure (404 / 401) maps to SOURCE_NOT_FOUND /
  SOURCE_FORBIDDEN job failure.
- Cron worker auth: rejects requests without Bearer + vercel-cron UA.
- Tick boundary preserved across re-queues (idempotent: re-claim same
  job, resume from saved next_idx).

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase_b2_async_indexer.py
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

from _lib import async_indexer, corpus_store, jobs  # noqa: E402
from _lib.storage import kv as kv_module  # noqa: E402


# ── Refactor sanity: fetch_tree + index_chunk + finalize round-trip ──

@pytest.fixture
def vendor_indexer(monkeypatch):
    """Make the vendored indexer importable + stub the GitHub HTTP layer."""
    vendor = _HERE.parent.parent / "_lib" / "vendor"
    sys.path.insert(0, str(vendor))
    import index_github_repo as _gh  # type: ignore

    # Tree response: 3 indexable code files
    fake_tree = {
        "tree": [
            {"path": "src/auth.py", "type": "blob", "size": 100, "sha": "a1"},
            {"path": "src/login.py", "type": "blob", "size": 200, "sha": "a2"},
            {"path": "README.md", "type": "blob", "size": 50, "sha": "a3"},
        ]
    }
    fake_contents = {
        "src/auth.py": "def authenticate(token):\n    pass\n",
        "src/login.py": "from auth import authenticate\n",
        "README.md": "# project\n\nSetup notes.\n",
    }

    def fake_get(url, token=None):
        return fake_tree if "trees/" in url else {}

    def fake_get_raw(url, token=None):
        for path, content in fake_contents.items():
            if path in url:
                return content
        return ""

    monkeypatch.setattr(_gh, "github_get", fake_get)
    monkeypatch.setattr(_gh, "github_get_raw", fake_get_raw)
    return _gh


def test_fetch_tree_returns_filtered_candidates(vendor_indexer):
    candidates = vendor_indexer.fetch_tree("x", "y", "main", token=None)
    paths = sorted(c["path"] for c in candidates)
    assert paths == ["README.md", "src/auth.py", "src/login.py"]


def test_index_chunk_processes_slice_returns_done_when_exhausted(vendor_indexer):
    candidates = vendor_indexer.fetch_tree("x", "y", "main", token=None)
    chunk = vendor_indexer.index_chunk(
        "x", "y", "main", None, candidates,
        start_idx=0, max_files=10, time_budget_s=999,
    )
    assert chunk["done"] is True
    assert chunk["next_idx"] == 3
    assert len(chunk["files"]) == 3
    # Each file has the expected shape
    paths = sorted(f["path"] for f in chunk["files"])
    assert paths == ["README.md", "src/auth.py", "src/login.py"]


def test_index_chunk_partial_returns_not_done_with_next_idx(vendor_indexer):
    candidates = vendor_indexer.fetch_tree("x", "y", "main", token=None)
    chunk = vendor_indexer.index_chunk(
        "x", "y", "main", None, candidates,
        start_idx=0, max_files=2, time_budget_s=999,
    )
    assert chunk["done"] is False
    assert chunk["next_idx"] == 2
    assert len(chunk["files"]) == 2


def test_finalize_builds_manifest_with_kt_distribution_and_dirs(vendor_indexer):
    candidates = vendor_indexer.fetch_tree("x", "y", "main", token=None)
    chunk = vendor_indexer.index_chunk(
        "x", "y", "main", None, candidates,
        start_idx=0, max_files=10, time_budget_s=999,
    )
    manifest = vendor_indexer.finalize(chunk["files"], "x", "y", "main")
    assert manifest["root"] == "x/y@main"
    assert manifest["totalFiles"] == 3
    assert manifest["totalTokens"] > 0
    assert "src" in manifest["directories"]
    assert isinstance(manifest["knowledgeTypeDistribution"], dict)


def test_sync_index_github_repo_wrapper_matches_old_shape(vendor_indexer):
    """The pre-Phase-B.2 monolithic shape must still be returned by the
    sync wrapper — local CLI + sync ce_index_github_repo path depend on it."""
    out = vendor_indexer.index_github_repo("x", "y", "main", token=None)
    assert "files" in out
    assert "directories" in out
    assert "knowledgeTypeDistribution" in out
    assert out["totalFiles"] == 3


# ── advance_one_tick: state machine ──

@pytest.fixture
def fake_kv(monkeypatch):
    """Replace kv._exec with an in-memory simulator so advance_one_tick can
    plant candidates / cursor / partial state without hitting Vercel KV."""
    store: dict = {}
    lists: dict = {}

    def _exec(command, *, timeout=None):
        cmd = [str(p) for p in command]
        op = cmd[0].upper()
        if op == "SET":
            key, val = cmd[1], cmd[2]
            nx = "NX" in cmd[3:]
            if nx and key in store:
                return {"result": None}
            store[key] = val
            return {"result": "OK"}
        if op == "GET":
            return {"result": store.get(cmd[1])}
        if op == "DEL":
            existed = cmd[1] in store
            store.pop(cmd[1], None)
            lists.pop(cmd[1], None)
            return {"result": 1 if existed else 0}
        if op == "EXISTS":
            return {"result": 1 if cmd[1] in store else 0}
        if op == "EXPIRE":
            return {"result": 1 if cmd[1] in store else 0}
        if op == "RPUSH":
            lists.setdefault(cmd[1], []).append(cmd[2])
            return {"result": len(lists[cmd[1]])}
        if op == "LPOP":
            lst = lists.get(cmd[1])
            if not lst:
                return {"result": None}
            return {"result": lst.pop(0)}
        if op == "LLEN":
            return {"result": len(lists.get(cmd[1], []))}
        if op == "EVAL":
            key = cmd[3]
            expected = cmd[4]
            if store.get(key) == expected:
                del store[key]
                return {"result": 1}
            return {"result": 0}
        return {"result": None}

    monkeypatch.setenv("KV_REST_API_URL", "https://x.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "test-token")
    monkeypatch.setattr(kv_module, "_exec", _exec)

    # Use KV-backed jobs backend (it'll go through the same fake)
    jobs.set_backend(jobs.KVJobsBackend())
    yield store, lists
    jobs.set_backend(None)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    return cd


def test_advance_one_tick_first_tick_plants_state(vendor_indexer, fake_kv, cache_dir):
    """First tick: fetch_tree, plant candidates + cursor in KV, write
    empty partial, requeue. NO files indexed yet."""
    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main",
                           "data_classification": "public"},
                          owner="t-1")
    job = jobs.claim_next()
    assert job is not None

    result = async_indexer.advance_one_tick(job)
    assert result["status"] == "advanced"
    assert result["files_indexed"] == 0
    assert result["files_total"] == 3
    assert result["done"] is False

    # KV state populated
    store, lists = fake_kv
    assert async_indexer._candidates_key(job_id) in store
    assert async_indexer._cursor_key(job_id) in store
    # Job re-queued for next tick
    assert "queue:pending" in lists and len(lists["queue:pending"]) == 1


def test_advance_one_tick_runs_to_completion_across_two_ticks(
    vendor_indexer, fake_kv, cache_dir,
):
    """Two ticks: tick 1 plants state; tick 2 indexes all 3 files +
    finalizes + completes."""
    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main",
                           "data_classification": "public"},
                          owner="t-1")

    # Tick 1
    job = jobs.claim_next()
    r1 = async_indexer.advance_one_tick(job)
    assert r1["status"] == "advanced"
    assert r1["done"] is False

    # Tick 2
    job = jobs.claim_next()
    r2 = async_indexer.advance_one_tick(job)
    assert r2["status"] == "complete", r2
    assert r2["files_indexed"] == 3
    assert r2["done"] is True

    # Final corpus exists
    final = corpus_store.load_corpus("gh-x-y-main")
    assert final is not None
    assert final.meta.file_count == 3

    # KV state cleaned up
    store, _ = fake_kv
    assert async_indexer._candidates_key(job_id) not in store
    assert async_indexer._cursor_key(job_id) not in store

    # Job marked complete
    rec = jobs.status(job_id)
    assert rec is not None
    assert rec["status"] == "complete"
    assert rec["files_indexed"] == 3


def test_advance_one_tick_tree_fetch_404_marks_job_failed(fake_kv, cache_dir, monkeypatch):
    """fetch_tree raises with '404' in message → job failed with
    SOURCE_NOT_FOUND, no retry."""
    vendor = _HERE.parent.parent / "_lib" / "vendor"
    sys.path.insert(0, str(vendor))
    import index_github_repo as _gh  # type: ignore

    def boom(owner, name, branch, token):
        raise RuntimeError("HTTP 404 Not Found")
    monkeypatch.setattr(_gh, "fetch_tree", boom)

    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "ghost/repo", "branch": "main",
                           "data_classification": "public"},
                          owner="t-1")
    job = jobs.claim_next()
    result = async_indexer.advance_one_tick(job)
    assert result["status"] == "failed"
    assert result["reason"] == "source_not_found"

    rec = jobs.status(job_id)
    assert rec is not None
    assert rec["status"] == "failed"
    assert rec["error_code"] == "SOURCE_NOT_FOUND"


def test_advance_one_tick_rejects_wrong_kind(fake_kv, cache_dir):
    """A job with kind != 'index_github_repo' fails fast — no retry."""
    job_id = jobs.enqueue("not_a_real_kind", {}, owner="t-1")
    job = jobs.claim_next()
    result = async_indexer.advance_one_tick(job)
    assert result["status"] == "failed"
    assert result["reason"] == "wrong_kind"


# ── Cron worker auth ──

def test_cron_worker_auth_requires_bearer_only(monkeypatch):
    """Without CRON_SECRET set, all requests rejected. With it, Bearer
    matching the secret is sufficient — UA is intentionally NOT checked
    (Codex P2: Vercel can change the UA without notice)."""
    from api.cron import index_worker as worker

    # No CRON_SECRET → reject everything
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert worker._authenticate({"Authorization": "Bearer abc"}) is False

    # CRON_SECRET set, headers don't match
    monkeypatch.setenv("CRON_SECRET", "secret123")
    assert worker._authenticate({"Authorization": "Bearer wrong"}) is False
    assert worker._authenticate({}) is False  # no Authorization
    assert worker._authenticate({"Authorization": "Basic xxx"}) is False

    # Correct Bearer → pass (UA not required)
    assert worker._authenticate({"Authorization": "Bearer secret123"}) is True
    # Mozilla UA still passes — UA not load-bearing
    assert worker._authenticate({"Authorization": "Bearer secret123",
                                  "User-Agent": "Mozilla/5.0"}) is True


def test_cron_worker_process_one_returns_idle_on_empty_queue(fake_kv):
    """When the queue is empty, _process_one returns status: idle."""
    from api.cron import index_worker as worker
    result = worker._process_one()
    assert result["status"] == "idle"


# ── Async wire-path P1/P2 fixes (Codex review of PR #52) ──

def test_async_response_includes_poll_with_per_spec(monkeypatch):
    """SPEC § 3.4: async=true response promises {corpus_id, job_id,
    status: 'queued', poll_with: 'ce_get_job_status'}. Earlier draft was
    missing poll_with — Codex P1."""
    from _lib.tools import index_github_repo as _tool
    from _lib.auth import TokenInfo
    out = _tool.handle({"repo": "x/y", "branch": "main",
                        "data_classification": "public", "async": True},
                       TokenInfo(token_id="t", role="admin",
                                 data_classification_max="restricted"))
    assert "isError" not in out
    assert out["poll_with"] == "ce_get_job_status"
    assert out["status"] == "queued"
    assert out["corpus_id"] == "gh-x-y-main"


def test_async_embed_true_without_key_rejects_at_enqueue(monkeypatch):
    """Codex P2: explicit embed=true without MISTRAL_API_KEY must error
    rather than queue a doomed job that silently degrades to keyword."""
    from _lib.tools import index_github_repo as _tool
    from _lib.auth import TokenInfo
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    out = _tool.handle({"repo": "x/y", "branch": "main",
                        "data_classification": "public",
                        "async": True, "embed": True},
                       TokenInfo(token_id="t", role="admin",
                                 data_classification_max="restricted"))
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "PROVIDER_UNAVAILABLE"


def test_async_embed_auto_no_key_still_queues(monkeypatch):
    """embed=null/auto without key is fine — enqueues a keyword-only
    indexing job (no PROVIDER_UNAVAILABLE since caller didn't insist)."""
    from _lib.tools import index_github_repo as _tool
    from _lib.auth import TokenInfo
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    out = _tool.handle({"repo": "x/y", "branch": "main",
                        "data_classification": "public", "async": True},
                       TokenInfo(token_id="t", role="admin",
                                 data_classification_max="restricted"))
    assert "isError" not in out
    assert out["status"] == "queued"


def test_partial_corpora_under_different_suffix_dont_appear_in_list_metas(tmp_path, monkeypatch):
    """Codex P2 on PR #51: previous design used `partial-<id>.index.json`
    which collided with valid user corpora named `partial-*`. New design
    stores partials under `<id>.partial.json` (NOT `.index.json`), so
    list_metas's suffix filter naturally excludes them — AND user
    corpora named `partial-foo` show up correctly."""
    from _lib import corpus_store
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    corpus_store.set_backend(None)

    real_obj = {
        "_meta": {"corpus_id": "real-corpus",
                  "source": {"type": "github_repo", "uri": "x"},
                  "data_classification": "public",
                  "embedding": {"provider": "none", "model": "n/a", "dims": 0},
                  "file_count": 1, "embedded_count": 0, "version": 1,
                  "last_refresh_completed_at": "2026-05-06T00:00:00Z",
                  "commit_sha": "real", "lifecycle_state": "active"},
        "files": []}
    partial_named = {**real_obj, "_meta": {**real_obj["_meta"],
                                            "corpus_id": "partial-foo"}}
    partial_blob = {**real_obj}
    # Real corpus + a user corpus legitimately named `partial-foo` + an
    # in-flight partial blob (different suffix)
    (cd / "real-corpus.index.json").write_text(json.dumps(real_obj))
    (cd / "partial-foo.index.json").write_text(json.dumps(partial_named))
    (cd / "real-corpus.partial.json").write_text(json.dumps(partial_blob))

    metas = corpus_store.list_metas()
    ids = sorted(m.corpus_id for m in metas)
    # The user's `partial-foo` corpus should NOT be hidden.
    # The in-flight `.partial.json` blob should NOT show up.
    assert ids == ["partial-foo", "real-corpus"], (
        f"unexpected list_metas output: {ids}"
    )


def test_get_job_status_translates_kv_record_to_spec_wire_shape(monkeypatch):
    """Codex P1 on PR #51: ce_get_job_status was returning jobs.status()
    verbatim with internal field names (id, kind, files_indexed,
    error_code). Spec § 3.7 promises {job_id, corpus_id, status, progress,
    error, result_commit_sha, started_at, completed_at}. This test pins
    the translation."""
    from _lib import jobs
    from _lib.tools import get_job_status as gjs
    from _lib.auth import TokenInfo

    backend = jobs.InMemoryJobsBackend()
    jobs.set_backend(backend)

    # Plant a complete async-indexer job record
    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main",
                           "corpus_id": "gh-x-y-main",
                           "data_classification": "public"},
                          owner="t-1")
    jobs.claim_next()
    jobs.complete(job_id, commit_sha="cafebabe1234",
                  file_count=42, embedded_count=42)

    out = gjs.handle({"job_id": job_id},
                     TokenInfo(token_id="t", role="admin",
                               data_classification_max="restricted"))
    assert "isError" not in out
    # Wire shape per § 3.7
    assert out["job_id"] == job_id  # NOT "id"
    assert out["corpus_id"] == "gh-x-y-main"
    assert out["status"] == "complete"
    assert out["progress"]["files_indexed"] == 42
    assert out["progress"]["files_total"] == 42
    assert out["progress"]["embedded_count"] == 42
    assert out["result_commit_sha"] == "cafebabe1234"  # NOT "commit_sha"
    assert out["error"] is None
    # Internal field names absent
    assert "id" not in out
    assert "kind" not in out
    assert "files_indexed" not in out
    assert "error_code" not in out

    jobs.set_backend(None)


def test_get_job_status_translates_failed_job_with_error_object(monkeypatch):
    """A failed job must have error: {code, message}, not flat
    error_code/error_message."""
    from _lib import jobs
    from _lib.tools import get_job_status as gjs
    from _lib.auth import TokenInfo

    jobs.set_backend(jobs.InMemoryJobsBackend())
    job_id = jobs.enqueue("index_github_repo", {"repo": "x/y"}, owner="t-1")
    jobs.claim_next()
    jobs.fail(job_id, code="SOURCE_NOT_FOUND",
              message="repo doesn't exist on github")

    out = gjs.handle({"job_id": job_id},
                     TokenInfo(token_id="t", role="admin",
                               data_classification_max="restricted"))
    assert out["status"] == "failed"
    assert out["error"] == {"code": "SOURCE_NOT_FOUND",
                            "message": "repo doesn't exist on github"}
    assert out["result_commit_sha"] is None
    jobs.set_backend(None)


def test_partial_blob_key_safe_for_max_length_corpus_id():
    """Codex P2 on PR #51: prefix-style `partial-<id>` could push 121+
    char corpus_ids past the 128-char cap. New suffix-style key
    `<id>.partial.json` doesn't go through corpus_id validation at all."""
    from _lib import async_indexer
    # A 128-char (max-allowed) corpus_id shouldn't fail with the new scheme
    long_id = "a" + "b" * 127  # 128 chars, valid per § 4.1
    key = async_indexer._partial_blob_key(long_id)
    assert key.endswith(".partial.json")
    # Importantly: the partial KEY (which is NOT a corpus_id) can be any
    # length the storage backend accepts; we don't run is_valid_corpus_id
    # on it. So this should work even for max-length corpus_ids.
    assert len(key) > 128  # would have been rejected by corpus_id regex
