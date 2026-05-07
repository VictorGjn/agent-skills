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
    # P2.4 fix: fetch_tree now returns (candidates, resolved_branch). On the
    # happy path resolved_branch == input branch.
    candidates, branch = vendor_indexer.fetch_tree("x", "y", "main", token=None)
    paths = sorted(c["path"] for c in candidates)
    assert paths == ["README.md", "src/auth.py", "src/login.py"]
    assert branch == "main"


def test_index_chunk_processes_slice_returns_done_when_exhausted(vendor_indexer):
    candidates, _ = vendor_indexer.fetch_tree("x", "y", "main", token=None)
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
    candidates, _ = vendor_indexer.fetch_tree("x", "y", "main", token=None)
    chunk = vendor_indexer.index_chunk(
        "x", "y", "main", None, candidates,
        start_idx=0, max_files=2, time_budget_s=999,
    )
    assert chunk["done"] is False
    assert chunk["next_idx"] == 2
    assert len(chunk["files"]) == 2


def test_finalize_builds_manifest_with_kt_distribution_and_dirs(vendor_indexer):
    candidates, _ = vendor_indexer.fetch_tree("x", "y", "main", token=None)
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


def test_advance_one_tick_persists_resolved_branch_when_auto_resolve_fires(
    fake_kv, cache_dir, monkeypatch,
):
    """Codex P1 regression on PR #54 (async path): when fetch_tree returns a
    resolved branch different from the input, advance_one_tick must:
      - persist resolved branch into the partial blob's _meta.source.branch
        on first tick, AND
      - read that value back on subsequent tick and pass it as the index_chunk
        ref (NOT the original branch from job args).

    Without both, content URLs on subsequent ticks point at the wrong ref
    and every file fetch returns empty.
    """
    vendor = _HERE.parent.parent / "_lib" / "vendor"
    sys.path.insert(0, str(vendor))
    import index_github_repo as _gh  # type: ignore

    # Stub fetch_tree: caller passed branch="main", we return resolved="master"
    chunks_seen_branch: list[str] = []

    def fake_fetch_tree(owner, name, branch, token, **kwargs):
        # P2.4: return tuple. The "real" auto-resolve happened upstream;
        # here we just simulate the signature contract.
        return [{"path": "src/auth.py", "type": "blob", "size": 100, "sha": "a1"}], "master"

    def fake_index_chunk(owner, name, branch, token, candidates, **kwargs):
        chunks_seen_branch.append(branch)
        return {
            "files": [{"path": "src/auth.py", "language": "python",
                       "tokens": 10, "totalTokens": 10,
                       "tree": {"text": "x", "title": "auth", "tokens": 10,
                                "totalTokens": 10, "children": [], "depth": 0},
                       "knowledge_type": "code", "contentHash": "h1"}],
            "next_idx": len(candidates),
            "done": True,
            "time_used_s": 1.0,
        }

    monkeypatch.setattr(_gh, "fetch_tree", fake_fetch_tree)
    monkeypatch.setattr(_gh, "index_chunk", fake_index_chunk)

    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main",  # caller's wrong branch
                           "data_classification": "public"},
                          owner="t-1")

    # Tick 1: should write partial with _meta.source.branch="master" (resolved)
    job = jobs.claim_next()
    r1 = async_indexer.advance_one_tick(job)
    assert r1["status"] == "advanced"

    backend = corpus_store._backend()
    partial_raw = backend.get_bytes("gh-x-y-main.partial.json")
    assert partial_raw is not None, "first tick must write partial blob"
    partial = json.loads(partial_raw.decode("utf-8"))
    assert partial["_meta"]["source"]["branch"] == "master", (
        "first tick must persist the RESOLVED branch (master) into partial._meta.source.branch, "
        "not the input branch (main) — otherwise subsequent ticks fetch with the wrong ref"
    )

    # Tick 2: should call index_chunk with branch="master", not "main"
    job = jobs.claim_next()
    r2 = async_indexer.advance_one_tick(job)
    assert r2["status"] == "complete", r2
    assert chunks_seen_branch == ["master"], (
        f"index_chunk must use the resolved branch (master) — saw {chunks_seen_branch}"
    )


def test_advance_one_tick_tree_fetch_404_marks_job_failed(fake_kv, cache_dir, monkeypatch):
    """fetch_tree raises with '404' in message → job failed with
    SOURCE_NOT_FOUND, no retry."""
    vendor = _HERE.parent.parent / "_lib" / "vendor"
    sys.path.insert(0, str(vendor))
    import index_github_repo as _gh  # type: ignore

    def boom(owner, name, branch, token, **kwargs):
        # **kwargs absorbs the keyword-only args (max_files, etc.) async_indexer
        # passes through after the P2.3 split-cap change.
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


def test_async_embed_true_rejected_regardless_of_key(monkeypatch):
    """Codex P2 round 4 on PR #51: embed=true on async is ALWAYS rejected
    in v1.1, even when MISTRAL_API_KEY is set. The async_indexer done
    branch writes embedding=none + embedded_count=0 unconditionally
    (async embedding is a v1.2 item), so embed=true would silently
    produce a keyword-only corpus. Subsumes the round-1
    PROVIDER_UNAVAILABLE check."""
    from _lib.tools import index_github_repo as _tool
    from _lib.auth import TokenInfo

    # Case A: no key — same outcome as before, but now under
    # EMBED_NOT_SUPPORTED_ASYNC (more precise error code than
    # PROVIDER_UNAVAILABLE for this scenario).
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    out_no_key = _tool.handle(
        {"repo": "x/y", "branch": "main", "data_classification": "public",
         "async": True, "embed": True},
        TokenInfo(token_id="t", role="admin",
                  data_classification_max="restricted"))
    assert out_no_key.get("isError") is True
    assert out_no_key["structuredContent"]["code"] == "EMBED_NOT_SUPPORTED_ASYNC"

    # Case B: key IS set — the regression Codex caught. Round-1 check
    # would have let this through and silently downgraded to keyword.
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    out_with_key = _tool.handle(
        {"repo": "x/y", "branch": "main", "data_classification": "public",
         "async": True, "embed": True},
        TokenInfo(token_id="t", role="admin",
                  data_classification_max="restricted"))
    assert out_with_key.get("isError") is True
    assert out_with_key["structuredContent"]["code"] == "EMBED_NOT_SUPPORTED_ASYNC", (
        "embed=true on async must be rejected even when MISTRAL_API_KEY is set "
        "— async path is keyword-only in v1.1"
    )


def test_async_embed_auto_no_key_still_queues(monkeypatch):
    """embed=null/auto without key is fine — enqueues a keyword-only
    indexing job (no error since caller didn't insist on embed)."""
    from _lib.tools import index_github_repo as _tool
    from _lib.auth import TokenInfo
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    out = _tool.handle({"repo": "x/y", "branch": "main",
                        "data_classification": "public", "async": True},
                       TokenInfo(token_id="t", role="admin",
                                 data_classification_max="restricted"))
    assert "isError" not in out
    assert out["status"] == "queued"
    # No skipped_reason when there's no key — caller never expected embeddings.
    assert "embed_skipped_reason" not in out


def test_async_embed_auto_with_key_signals_skip_in_v1_1(monkeypatch):
    """Codex P2 round 4 on PR #51: when embed=null/auto AND
    MISTRAL_API_KEY is set, the caller would reasonably expect
    embeddings (sync path would deliver them). The async path is
    keyword-only in v1.1, so signal that explicitly via
    embed_skipped_reason — bench callers can then avoid scoring the
    corpus on semantic-only metrics."""
    from _lib.tools import index_github_repo as _tool
    from _lib.auth import TokenInfo
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    out = _tool.handle({"repo": "x/y", "branch": "main",
                        "data_classification": "public", "async": True},
                       TokenInfo(token_id="t", role="admin",
                                 data_classification_max="restricted"))
    assert "isError" not in out
    assert out["status"] == "queued"
    assert "embed_skipped_reason" in out, (
        "auto-mode with key should signal that async path won't embed in v1.1"
    )
    assert "v1.1" in out["embed_skipped_reason"]


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


def test_chunk_replay_does_not_duplicate_files_in_partial(
    vendor_indexer, fake_kv, cache_dir, monkeypatch,
):
    """Codex P1 round 6 on PR #51: cursor advancement can lag a successful
    partial-blob put_bytes (KV outage between blob write and cursor
    save). The cron retry path then re-runs the same next_idx chunk
    against an already-appended partial corpus, and finalize() doesn't
    dedupe — without the merge-time dedupe in advance_one_tick, the
    final corpus would have duplicate rows + inflated totalFiles.

    Simulate the exact failure window: succeed put_bytes, fail kv.set
    on cursor, replay the same tick. Final corpus must contain each
    path exactly once.
    """
    from _lib import async_indexer
    from _lib.storage import kv as kv_module

    # Force multi-tick by capping max_files to 2 (3-file repo → 2 ticks).
    # The non-done branch is what saves cursor; the done branch deletes it.
    monkeypatch.setattr(async_indexer, "TICK_MAX_FILES", 2)

    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main",
                           "data_classification": "public"},
                          owner="t-1")

    # Tick 1: plant state cleanly
    job = jobs.claim_next()
    r1 = async_indexer.advance_one_tick(job)
    assert r1["status"] == "advanced"
    assert r1["files_indexed"] == 0  # first tick is just fetch_tree

    # Tick 2: arm a "cursor save fails AFTER partial write succeeds"
    # failure mode. Lower-level: kv.set on the cursor key throws, but
    # only on advancement (not the first-tick plant which already happened).
    real_set = kv_module.set

    def boom_on_cursor_save(key, value, *, ex_seconds=None):
        if "cursor" in key:
            raise RuntimeError("simulated upstash 503 on cursor save")
        return real_set(key, value, ex_seconds=ex_seconds)

    monkeypatch.setattr(kv_module, "set", boom_on_cursor_save)
    job = jobs.claim_next()
    try:
        async_indexer.advance_one_tick(job)
    except RuntimeError:
        # The cron worker would catch this via its outer except and call
        # fail(retry=True). Simulate that path.
        jobs.fail(job_id, code="INTERNAL", message="cursor save failed", retry=True)

    # At this point: partial blob has the first 2 files appended; cursor
    # in KV still says next_idx=0 (was never advanced). Replay reads
    # stale cursor, fetches the SAME 2 files again. Without dedupe, the
    # partial would now contain 4 file rows (2 original + 2 dupes).
    monkeypatch.setattr(kv_module, "set", real_set)

    # Tick 3 (replay): re-run the same 2-file chunk
    job = jobs.claim_next()
    r3 = async_indexer.advance_one_tick(job)
    assert r3["status"] == "advanced"

    # Tick 4: pick up the remaining file → done branch
    job = jobs.claim_next()
    r4 = async_indexer.advance_one_tick(job)
    assert r4["status"] == "complete", f"expected complete, got {r4}"

    final = corpus_store.load_corpus("gh-x-y-main")
    assert final is not None
    paths = [f.get("path") for f in final.files]
    # Each path appears EXACTLY once — replay didn't duplicate
    assert len(paths) == len(set(paths)), (
        f"chunk replay produced duplicate files in final corpus: {paths}"
    )
    assert final.meta.file_count == 3, (
        f"file_count must reflect deduped count (3), not raw append (5+); "
        f"got {final.meta.file_count}"
    )


def test_done_tick_marks_complete_even_when_cleanup_fails(
    vendor_indexer, fake_kv, cache_dir, monkeypatch,
):
    """Engineer-review P1 on PR #51: done branch must call jobs.complete
    BEFORE the kv.delete cleanup. Previously cleanup ran first; if any
    kv.delete raised (Upstash 5xx), the exception propagated past
    jobs.complete, the cron worker's outer except called fail(retry=True),
    and the next tick re-entered branch 2 with a deleted partial blob —
    infinite retry while the durable corpus existed.

    Fix: complete first, then per-call try/except on each cleanup step.
    """
    from _lib import async_indexer
    from _lib.storage import kv as kv_module

    # Force cursor cleanup to fail. The done tick must STILL mark complete.
    real_delete = kv_module.delete
    delete_calls = {"n": 0}

    def boom_on_cursor_cleanup(key):
        delete_calls["n"] += 1
        if "cursor" in key:
            raise RuntimeError("simulated upstash 503 during cleanup")
        return real_delete(key)

    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main",
                           "data_classification": "public"},
                          owner="t-1")
    # Tick 1: plant state
    job = jobs.claim_next()
    r1 = async_indexer.advance_one_tick(job)
    assert r1["status"] == "advanced"

    # Tick 2: arm the boom on cleanup, then run done branch
    monkeypatch.setattr(kv_module, "delete", boom_on_cursor_cleanup)
    job = jobs.claim_next()
    r2 = async_indexer.advance_one_tick(job)

    # Job must be complete despite the cleanup failure
    assert r2["status"] == "complete", (
        f"done tick must mark complete even when cleanup throws. got: {r2}"
    )
    rec = jobs.status(job_id)
    assert rec is not None
    assert rec["status"] == "complete"
    assert rec["commit_sha"] is not None

    # Final corpus durable
    final = corpus_store.load_corpus("gh-x-y-main")
    assert final is not None
    assert final.meta.file_count == 3

    # candidates was cleaned up (didn't fail), cursor leaked (TTLs in 7d) —
    # acceptable; the success criterion is that the durable corpus +
    # complete-state are atomic from the client's view.
    monkeypatch.setattr(kv_module, "delete", real_delete)


def test_first_tick_partial_blob_failure_leaves_no_orphan_kv_state(
    vendor_indexer, fake_kv, cache_dir, monkeypatch,
):
    """Codex P1 round 3 on PR #51: order matters — write partial blob
    BEFORE saving cursor + candidates to KV. If put_bytes throws (e.g.
    transient Blob 5xx), neither cursor nor candidates should land,
    so the next tick re-enters first-tick cleanly. The previous order
    (KV first, blob last) would persist a cursor pointing at a
    non-existent partial — every subsequent tick would skip
    initialization and fail with `partial_missing` until KV TTL."""
    from _lib import async_indexer

    # Force corpus_store backend put_bytes to fail
    backend = corpus_store._backend()
    boom_count = {"n": 0}
    real_put_bytes = backend.put_bytes

    def boom(key, data):
        boom_count["n"] += 1
        raise RuntimeError("simulated transient blob 502")

    monkeypatch.setattr(backend, "put_bytes", boom)

    job_id = jobs.enqueue("index_github_repo",
                          {"repo": "x/y", "branch": "main",
                           "data_classification": "public"},
                          owner="t-1")
    job = jobs.claim_next()
    with pytest.raises(RuntimeError, match="simulated transient blob"):
        async_indexer.advance_one_tick(job)

    # NEITHER cursor NOR candidates should be in KV — first-tick must
    # re-enter cleanly on retry, not get stuck in chunk-processing branch.
    store, _ = fake_kv
    assert async_indexer._candidates_key(job_id) not in store, (
        "candidates leaked into KV despite blob-write failure"
    )
    assert async_indexer._cursor_key(job_id) not in store, (
        "cursor leaked into KV despite blob-write failure — next tick "
        "would skip first-tick init and fail with partial_missing forever"
    )

    # Restore + retry should now succeed (blob write goes through, KV
    # state plants normally).
    monkeypatch.setattr(backend, "put_bytes", real_put_bytes)
    jobs.requeue(job_id)
    job2 = jobs.claim_next()
    result = async_indexer.advance_one_tick(job2)
    assert result["status"] == "advanced"
    assert async_indexer._candidates_key(job_id) in store
    assert async_indexer._cursor_key(job_id) in store


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
