"""Phase 4 tests — write tools (ce_upload_corpus, ce_index_github_repo, ce_get_job_status).

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase4.py
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


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    return cd


def _admin_token():
    from _lib import auth
    info = auth.authenticate("Bearer test-token")
    assert info is not None and info.role == "admin"
    return info


def _writer_token():
    from _lib import auth
    return auth.TokenInfo(token_id="t", role="writer", data_classification_max="confidential")


def _reader_token():
    from _lib import auth
    return auth.TokenInfo(token_id="t", role="reader", data_classification_max="internal")


def _dispatch(name, arguments, token=None, request_id=1):
    from _lib import tools as _tools  # noqa: F401
    from _lib.transport import dispatch as _dispatch_fn
    payload = {
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return _dispatch_fn(payload, token or _admin_token())


def _file_entry(path: str, content_hash: str = "h", tokens: int = 100) -> dict:
    return {
        "path": path,
        "contentHash": content_hash,
        "tokens": tokens,
        "tree": {"depth": 0, "title": path, "firstSentence": "x",
                 "firstParagraph": "x", "text": "x", "children": []},
        "knowledge_type": "evidence",
    }


# ── ce_upload_corpus ──

def test_upload_corpus_minimal_no_embeddings(cache_dir):
    files = [_file_entry("src/auth.py", "h1"), _file_entry("docs/README.md", "h2")]
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/owner/repo", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": files,
        "embeddings": {"format": "json", "paths": [f["path"] for f in files],
                        "hashes": [f["contentHash"] for f in files], "vectors": [[], []]},
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["corpus_id"] == "gh-owner-repo-main"
    assert s["version"] == 1
    assert s["stats"]["file_count"] == 2
    assert s["stats"]["embedded_count"] == 0  # dims=0 → not counted
    # File written to cache
    assert (cache_dir / "gh-owner-repo-main.index.json").exists()


def test_upload_corpus_with_embeddings(cache_dir):
    files = [_file_entry("a.md", "h1"), _file_entry("b.md", "h2")]
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/owner/repo", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "mistral", "model": "codestral-embed", "dims": 4},
        "files": files,
        "embeddings": {
            "format": "json",
            "paths": ["a.md", "b.md"],
            "hashes": ["h1", "h2"],
            "vectors": [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]],
        },
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["stats"]["embedded_count"] == 2


def test_upload_corpus_idempotent_same_content_hashes(cache_dir):
    files = [_file_entry("a.md", "h1")]
    args = {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": files,
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"], "vectors": [[]]},
    }
    r1, _ = _dispatch("ce_upload_corpus", args, request_id=1)
    r2, _ = _dispatch("ce_upload_corpus", args, request_id=2)
    s1, s2 = r1["result"]["structuredContent"], r2["result"]["structuredContent"]
    assert s1["commit_sha"] == s2["commit_sha"]
    assert s1["version"] == s2["version"]  # idempotent — version not bumped


def test_upload_corpus_changed_content_bumps_version(cache_dir):
    args1 = {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [_file_entry("a.md", "h1")],
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"], "vectors": [[]]},
    }
    r1, _ = _dispatch("ce_upload_corpus", args1, request_id=1)
    args2 = {**args1, "files": [_file_entry("a.md", "h2")],
             "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h2"], "vectors": [[]]}}
    r2, _ = _dispatch("ce_upload_corpus", args2, request_id=2)
    s1, s2 = r1["result"]["structuredContent"], r2["result"]["structuredContent"]
    assert s2["version"] == s1["version"] + 1
    assert s2["commit_sha"] != s1["commit_sha"]


def test_upload_corpus_embedding_mismatch_array_lengths(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [_file_entry("a.md", "h1")],
        "embeddings": {"format": "json", "paths": ["a.md", "b.md"], "hashes": ["h1"], "vectors": [[]]},
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "EMBEDDING_MISMATCH"


def test_upload_corpus_embedding_mismatch_vector_dims(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "mistral", "model": "x", "dims": 4},
        "files": [_file_entry("a.md", "h1")],
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"],
                       "vectors": [[0.1, 0.2, 0.3]]},  # 3 dims, expected 4
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "EMBEDDING_MISMATCH"


def test_upload_corpus_invalid_classification(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r"},
        "data_classification": "secret",  # invalid
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [],
        "embeddings": {"format": "json", "paths": [], "hashes": [], "vectors": []},
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_upload_corpus_presigned_format_returns_payload_too_large(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r"},
        "data_classification": "internal",
        "embedding": {"provider": "mistral", "model": "x", "dims": 4},
        "files": [],
        "embeddings": {"format": "presigned", "vectors_url": "https://..."},
    })
    assert status == 413
    assert response["result"]["structuredContent"]["code"] == "PAYLOAD_TOO_LARGE"


def test_upload_corpus_unknown_source_type(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "alien_codex"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [],
        "embeddings": {"format": "json", "paths": [], "hashes": [], "vectors": []},
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_upload_corpus_writer_role_can_call(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [_file_entry("a.md", "h1")],
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"], "vectors": [[]]},
    }, token=_writer_token())
    assert status == 200


def test_upload_corpus_reader_role_denied(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [],
        "embeddings": {"format": "json", "paths": [], "hashes": [], "vectors": []},
    }, token=_reader_token())
    assert status == 403
    assert response["error"]["data"]["code_name"] == "PERMISSION_DENIED"


def test_upload_corpus_corpus_locked(cache_dir, monkeypatch):
    """Simulate concurrent writer holding the lock."""
    from _lib import corpus_store
    target = corpus_store.index_path_for("gh-o-r-main")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix(".lock")
    lock.write_text(str(__import__("time").time()))
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [_file_entry("a.md", "h1")],
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"], "vectors": [[]]},
    })
    assert status == 409
    assert response["result"]["structuredContent"]["code"] == "CORPUS_LOCKED"
    lock.unlink(missing_ok=True)


def test_upload_corpus_corpus_id_derived_when_omitted(cache_dir):
    """SPEC § 4.1: server derives gh-{owner}-{repo}-{branch}."""
    files = [_file_entry("a.md", "h1")]
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/Foo-Bar/repo.name", "branch": "main"},
        "data_classification": "public",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": files,
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"], "vectors": [[]]},
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    # Slugified — case folded, dots replaced
    assert s["corpus_id"] == "gh-foo-bar-repo-name-main"


def test_upload_corpus_explicit_corpus_id_overrides_derivation(cache_dir):
    response, status = _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "corpus_id": "my-custom-id",
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": [_file_entry("a.md", "h1")],
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"], "vectors": [[]]},
    })
    assert status == 200
    assert response["result"]["structuredContent"]["corpus_id"] == "my-custom-id"


def test_upload_corpus_round_trips_through_list_and_pack(cache_dir):
    """End-to-end: upload → list → pack."""
    files = [_file_entry("auth.py", "h1")]
    _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": files,
        "embeddings": {"format": "json", "paths": ["auth.py"], "hashes": ["h1"], "vectors": [[]]},
    })
    # list
    r_list, _ = _dispatch("ce_list_corpora", {})
    s_list = r_list["result"]["structuredContent"]
    assert any(c["corpus_id"] == "gh-o-r-main" for c in s_list["corpora"])
    # pack
    r_pack, _ = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_id": "gh-o-r-main", "budget": 2000,
    })
    assert r_pack["result"]["structuredContent"]["files"]


# ── ce_get_job_status ──

def test_get_job_status_unknown_job(cache_dir):
    response, status = _dispatch("ce_get_job_status", {"job_id": "job_doesnotexist"})
    assert status == 404
    assert response["result"]["structuredContent"]["code"] == "JOB_NOT_FOUND"


def test_get_job_status_after_upload_succeeds(cache_dir):
    """Upload registers a synthetic complete job; get_job_status should find it.

    Trick: we don't currently echo the job_id back from upload_corpus, so this
    test reaches into the in-memory store. v1.1 should add `job_id` to the
    upload response or expose ce_list_jobs.
    """
    from _lib import job_store
    files = [_file_entry("a.md", "h1")]
    _dispatch("ce_upload_corpus", {
        "source": {"type": "github_repo", "uri": "https://github.com/o/r", "branch": "main"},
        "data_classification": "internal",
        "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        "files": files,
        "embeddings": {"format": "json", "paths": ["a.md"], "hashes": ["h1"], "vectors": [[]]},
    })
    # Find the most recent job in the store
    assert job_store._JOBS, "upload should have registered a job"
    job_id = list(job_store._JOBS.keys())[-1]
    response, status = _dispatch("ce_get_job_status", {"job_id": job_id})
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["status"] == "complete"
    assert s["corpus_id"] == "gh-o-r-main"
    assert s["result_commit_sha"]
    assert s["progress"]["files_indexed"] == 1


def test_get_job_status_invalid_id(cache_dir):
    response, status = _dispatch("ce_get_job_status", {"job_id": ""})
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


# ── ce_index_github_repo (validation only — no live GitHub fetch) ──

def test_index_github_repo_invalid_repo_format(cache_dir):
    response, status = _dispatch("ce_index_github_repo", {
        "repo": "not-a-valid-format",
        "data_classification": "public",
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_index_github_repo_async_enqueues_job(cache_dir):
    """Phase B.3: async=true now enqueues a chunked-indexing job and
    returns {job_id, corpus_id, status='queued'} for the cron worker to
    pick up. Pre-B.3 this returned NOT_IMPLEMENTED."""
    response, status = _dispatch("ce_index_github_repo", {
        "repo": "owner/repo",
        "data_classification": "public",
        "async": True,
    })
    assert status == 200
    structured = response["result"]["structuredContent"]
    assert structured["status"] == "queued"
    assert structured["corpus_id"] == "gh-owner-repo-main"
    assert isinstance(structured["job_id"], str) and structured["job_id"]


def test_index_github_repo_missing_classification(cache_dir):
    response, status = _dispatch("ce_index_github_repo", {
        "repo": "owner/repo",
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_index_github_repo_reader_denied(cache_dir):
    response, status = _dispatch("ce_index_github_repo", {
        "repo": "owner/repo", "data_classification": "public",
    }, token=_reader_token())
    assert status == 403
    assert response["error"]["data"]["code_name"] == "PERMISSION_DENIED"


def test_index_github_repo_source_not_found(cache_dir, monkeypatch):
    """Mock the indexer to raise a 404."""
    from _lib.tools import index_github_repo as tool

    def fake_run(owner, name, branch, token):
        raise RuntimeError("HTTP 404: repo not found")
    monkeypatch.setattr(tool, "_run_indexer", fake_run)

    response, status = _dispatch("ce_index_github_repo", {
        "repo": "ghost/nope", "data_classification": "public",
    })
    assert status == 404
    assert response["result"]["structuredContent"]["code"] == "SOURCE_NOT_FOUND"


def test_index_github_repo_source_forbidden(cache_dir, monkeypatch):
    """Mock the indexer to raise a 403."""
    from _lib.tools import index_github_repo as tool

    def fake_run(owner, name, branch, token):
        raise RuntimeError("HTTP 403: forbidden — App lacks access")
    monkeypatch.setattr(tool, "_run_indexer", fake_run)

    response, status = _dispatch("ce_index_github_repo", {
        "repo": "private/repo", "data_classification": "internal",
    })
    assert status == 403
    assert response["result"]["structuredContent"]["code"] == "SOURCE_FORBIDDEN"


def test_index_github_repo_sync_writes_corpus(cache_dir, monkeypatch):
    """Mock the indexer to return a synthetic index; verify it's persisted."""
    from _lib.tools import index_github_repo as tool

    def fake_run(owner, name, branch, token):
        return {
            "files": [
                {"path": "a.md", "contentHash": "h1", "tokens": 50,
                 "tree": {"depth": 0, "title": "a.md", "firstSentence": "x",
                          "firstParagraph": "x", "text": "x", "children": []},
                 "knowledge_type": "evidence"},
            ],
        }
    monkeypatch.setattr(tool, "_run_indexer", fake_run)

    response, status = _dispatch("ce_index_github_repo", {
        "repo": "owner/repo", "data_classification": "public",
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["corpus_id"] == "gh-owner-repo-main"
    assert s["stats"]["file_count"] == 1
    assert s["commit_sha"]
    # Verify file written
    assert (cache_dir / "gh-owner-repo-main.index.json").exists()


def test_index_github_repo_idempotent_same_commit(cache_dir, monkeypatch):
    from _lib.tools import index_github_repo as tool

    def fake_run(owner, name, branch, token):
        return {"files": [{"path": "a.md", "contentHash": "h1", "tokens": 50,
                            "tree": {"depth": 0, "title": "a.md", "firstSentence": "x",
                                     "firstParagraph": "x", "text": "x", "children": []}}]}
    monkeypatch.setattr(tool, "_run_indexer", fake_run)

    r1, _ = _dispatch("ce_index_github_repo", {
        "repo": "o/r", "data_classification": "public",
    })
    r2, _ = _dispatch("ce_index_github_repo", {
        "repo": "o/r", "data_classification": "public",
    })
    s1, s2 = r1["result"]["structuredContent"], r2["result"]["structuredContent"]
    assert s1["commit_sha"] == s2["commit_sha"]
    assert s2["version"] == s1["version"]  # idempotent


def test_index_github_repo_indexed_paths_filter(cache_dir, monkeypatch):
    """SPEC § 3.4: indexed_paths restricts which files end up in the corpus."""
    from _lib.tools import index_github_repo as tool

    def fake_run(owner, name, branch, token):
        return {"files": [
            {"path": "src/a.py", "contentHash": "h1", "tokens": 50, "tree": {"depth": 0, "title": "a"}},
            {"path": "docs/b.md", "contentHash": "h2", "tokens": 50, "tree": {"depth": 0, "title": "b"}},
        ]}
    monkeypatch.setattr(tool, "_run_indexer", fake_run)

    response, status = _dispatch("ce_index_github_repo", {
        "repo": "o/r", "data_classification": "public",
        "indexed_paths": ["src/"],
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["stats"]["file_count"] == 1


# ── Codex review fixes (lock behavior) ──

def test_index_normalizes_legacy_hash_field(cache_dir, monkeypatch):
    """Codex P1: scripts/index_*.py emit `hash`, not `contentHash`. Without
    normalization, commit_sha would never reflect content changes."""
    from _lib.tools import index_github_repo as tool

    def fake_run(owner, name, branch, token):
        # Indexer emits `hash` (legacy field name)
        return {"files": [
            {"path": "a.md", "hash": "h1", "tokens": 50,
             "tree": {"depth": 0, "title": "a", "firstSentence": "x",
                      "firstParagraph": "x", "text": "x", "children": []}},
        ]}
    monkeypatch.setattr(tool, "_run_indexer", fake_run)

    response, status = _dispatch("ce_index_github_repo", {
        "repo": "o/r", "data_classification": "public",
    })
    assert status == 200
    sha1 = response["result"]["structuredContent"]["commit_sha"]

    # Same content (h1) → idempotent
    response, _ = _dispatch("ce_index_github_repo", {
        "repo": "o/r", "data_classification": "public",
    })
    assert response["result"]["structuredContent"]["commit_sha"] == sha1

    # Changed content (h2) → different commit_sha
    def fake_run_changed(owner, name, branch, token):
        return {"files": [
            {"path": "a.md", "hash": "h2", "tokens": 50,
             "tree": {"depth": 0, "title": "a", "firstSentence": "x",
                      "firstParagraph": "x", "text": "x", "children": []}},
        ]}
    monkeypatch.setattr(tool, "_run_indexer", fake_run_changed)

    response, status = _dispatch("ce_index_github_repo", {
        "repo": "o/r", "data_classification": "public",
    })
    assert status == 200
    sha2 = response["result"]["structuredContent"]["commit_sha"]
    assert sha2 != sha1, "commit_sha must change when content changes"


def test_index_github_repo_corpus_locked(cache_dir, monkeypatch):
    """Codex P1: index path must respect the .lock file held by upload."""
    from _lib import corpus_store
    from _lib.tools import index_github_repo as tool

    def fake_run(owner, name, branch, token):
        return {"files": [{"path": "a.md", "hash": "h1", "tokens": 50,
                            "tree": {"depth": 0, "title": "a"}}]}
    monkeypatch.setattr(tool, "_run_indexer", fake_run)

    target = corpus_store.index_path_for("gh-o-r-main")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix(".lock")
    lock.write_text(str(__import__("time").time()))

    response, status = _dispatch("ce_index_github_repo", {
        "repo": "o/r", "data_classification": "public",
    })
    assert status == 409
    assert response["result"]["structuredContent"]["code"] == "CORPUS_LOCKED"
    lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
