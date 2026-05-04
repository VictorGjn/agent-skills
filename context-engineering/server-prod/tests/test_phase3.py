"""Phase 3 tests — read tools (ce_pack_context, ce_find_relevant_files, ce_list_corpora).

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase3.py
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


# ── Fixtures ──

@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Point the corpus store at a clean temp dir for each test."""
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    return cd


def _make_index(cache_dir: Path, corpus_id: str, *,
                files: list[dict] | None = None,
                data_classification: str = "internal",
                embedding: dict | None = None,
                lifecycle_state: str = "active",
                source_type: str = "github_repo",
                commit_sha: str = "abc1234",
                version: int = 1) -> Path:
    """Drop a `<corpus_id>.index.json` into cache_dir."""
    if files is None:
        files = [
            {
                "path": "src/auth.py",
                "contentHash": "h1",
                "tokens": 800,
                "tree": {
                    "title": "src/auth.py",
                    "firstSentence": "Authentication module — bearer token validation.",
                    "firstParagraph": "Authentication module — bearer token validation.",
                    "text": "def authenticate(token):\n    pass",
                    "children": [],
                },
                "knowledge_type": "ground_truth",
            },
            {
                "path": "docs/README.md",
                "contentHash": "h2",
                "tokens": 400,
                "tree": {
                    "title": "docs/README.md",
                    "firstSentence": "Project overview and authentication setup.",
                    "firstParagraph": "Project overview and authentication setup.",
                    "text": "# README\n\nUse `authenticate()` to verify tokens.",
                    "children": [],
                },
                "knowledge_type": "evidence",
            },
            {
                "path": "tests/unrelated.py",
                "contentHash": "h3",
                "tokens": 200,
                "tree": {
                    "title": "tests/unrelated.py",
                    "firstSentence": "Frobnicator widget tests.",
                    "firstParagraph": "Frobnicator widget tests.",
                    "text": "# tests for an unrelated widget",
                    "children": [],
                },
                "knowledge_type": "artifact",
            },
        ]
    if embedding is None:
        embedding = {"provider": "none", "model": "n/a", "dims": 0}
    raw = {
        "_meta": {
            "corpus_id": corpus_id,
            "source": {"type": source_type, "uri": f"https://example.com/{corpus_id}", "branch": "main"},
            "data_classification": data_classification,
            "embedding": embedding,
            "file_count": len(files),
            "version": version,
            "last_refresh_completed_at": None,
            "commit_sha": commit_sha,
            "lifecycle_state": lifecycle_state,
        },
        "files": files,
    }
    p = cache_dir / f"{corpus_id}.index.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def _admin_token():
    # Import deferred so the env-var-set fixture can run first.
    from _lib import auth
    info = auth.authenticate("Bearer test-token")
    assert info is not None and info.role == "admin"
    return info


def _dispatch(name: str, arguments: dict, request_id: int = 1):
    from _lib import tools as _tools  # noqa: F401  — registers handlers
    from _lib.transport import dispatch as _dispatch_fn
    payload = {
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return _dispatch_fn(payload, _admin_token())


# ── ce_pack_context: single-corpus ──

def test_pack_single_corpus_keyword(cache_dir):
    _make_index(cache_dir, "test-corpus")
    response, status = _dispatch("ce_pack_context", {
        "query": "authentication", "corpus_id": "test-corpus", "budget": 4000,
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["corpus_commit_sha"] == "abc1234"
    assert s["corpus_commit_shas"] is None
    assert s["tokens_budget"] == 4000
    paths = [f["path"] for f in s["files"]]
    assert "src/auth.py" in paths  # matched query
    assert "tests/unrelated.py" not in paths  # frobnicator doesn't match
    assert "markdown" in s
    assert "## Full" in s["markdown"] or "## Detail" in s["markdown"] or "src/auth.py" in s["markdown"]
    # Files don't carry corpus_id in single-corpus mode
    assert all("corpus_id" not in f for f in s["files"])


def test_pack_response_format_structured(cache_dir):
    _make_index(cache_dir, "test-corpus")
    response, status = _dispatch("ce_pack_context", {
        "query": "authentication", "corpus_id": "test-corpus",
        "budget": 4000, "response_format": "structured",
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert "markdown" not in s
    # All files have rendered content embedded
    assert s["files"]
    for f in s["files"]:
        assert "content" in f and isinstance(f["content"], str) and f["content"]


def test_pack_response_format_both(cache_dir):
    _make_index(cache_dir, "test-corpus")
    response, status = _dispatch("ce_pack_context", {
        "query": "authentication", "corpus_id": "test-corpus",
        "budget": 4000, "response_format": "both",
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert "markdown" in s and isinstance(s["markdown"], str)
    assert s["files"] and all("content" in f for f in s["files"])


def test_pack_corpus_not_found(cache_dir):
    response, status = _dispatch("ce_pack_context", {
        "query": "x", "corpus_id": "does-not-exist", "budget": 2000,
    })
    assert status == 404
    s = response["result"]["structuredContent"]
    assert s["code"] == "CORPUS_NOT_FOUND"


def test_pack_invalid_corpus_id_format(cache_dir):
    response, status = _dispatch("ce_pack_context", {
        "query": "x", "corpus_id": "INVALID--id..format", "budget": 2000,
    })
    assert status == 400
    s = response["result"]["structuredContent"]
    assert s["code"] == "INVALID_ARGUMENT"


def test_pack_mutual_exclusion(cache_dir):
    _make_index(cache_dir, "a")
    response, status = _dispatch("ce_pack_context", {
        "query": "x", "corpus_id": "a", "corpus_ids": ["a"], "budget": 2000,
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"

    response, status = _dispatch("ce_pack_context", {"query": "x", "budget": 2000})
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_pack_budget_at_schema_min_accepted(cache_dir):
    """Budget=1000 (schema-min) is accepted. BUDGET_TOO_SMALL would only fire
    on sub-500 budgets, which are unreachable through input validation —
    the code keeps the check defensively in case model_context scaling ever
    changes its floor."""
    _make_index(cache_dir, "a")
    response, status = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_id": "a", "budget": 1000,
    })
    assert status == 200


def test_pack_budget_below_schema_min_rejected(cache_dir):
    _make_index(cache_dir, "a")
    response, status = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_id": "a", "budget": 500,
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_pack_invalid_mode(cache_dir):
    _make_index(cache_dir, "a")
    response, status = _dispatch("ce_pack_context", {
        "query": "x", "corpus_id": "a", "mode": "telepathy",
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_pack_archived_corpus(cache_dir):
    _make_index(cache_dir, "a", lifecycle_state="archived")
    response, status = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_id": "a",
    })
    assert status == 410
    assert response["result"]["structuredContent"]["code"] == "CORPUS_ARCHIVED"


# ── ce_pack_context: multi-corpus ──

def test_pack_multi_corpus(cache_dir):
    _make_index(cache_dir, "alpha", commit_sha="aaa1111")
    _make_index(cache_dir, "beta", commit_sha="bbb2222")
    response, status = _dispatch("ce_pack_context", {
        "query": "authentication",
        "corpus_ids": ["alpha", "beta"],
        "budget": 8000,
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["corpus_commit_sha"] is None
    # Lex-sorted on the wire
    assert list(s["corpus_commit_shas"].keys()) == ["alpha", "beta"]
    assert s["corpus_commit_shas"]["alpha"] == "aaa1111"
    # Paths are prefixed
    paths = [f["path"] for f in s["files"]]
    assert any(p.startswith("alpha:") for p in paths)
    assert any(p.startswith("beta:") for p in paths)
    # Files have corpus_id in multi-corpus mode
    for f in s["files"]:
        assert "corpus_id" in f


def test_pack_multi_corpus_one_missing(cache_dir):
    _make_index(cache_dir, "alpha")
    response, status = _dispatch("ce_pack_context", {
        "query": "x",
        "corpus_ids": ["alpha", "missing-one"],
        "budget": 4000,
    })
    assert status == 404
    s = response["result"]["structuredContent"]
    assert s["code"] == "CORPUS_NOT_FOUND"
    assert s["details"]["missing_corpora"] == ["missing-one"]


def test_pack_multi_corpus_embedding_mismatch_semantic(cache_dir):
    _make_index(cache_dir, "alpha", embedding={"provider": "mistral", "model": "codestral-embed", "dims": 1536})
    _make_index(cache_dir, "beta", embedding={"provider": "openai", "model": "text-embedding-3-large", "dims": 3072})
    response, status = _dispatch("ce_pack_context", {
        "query": "auth",
        "corpus_ids": ["alpha", "beta"],
        "mode": "semantic",
        "budget": 4000,
    })
    assert status == 400
    s = response["result"]["structuredContent"]
    assert s["code"] == "EMBEDDING_PROVIDER_MISMATCH"
    assert "providers" in s["details"]


def test_pack_multi_corpus_keyword_mode_ignores_embedding(cache_dir):
    """Keyword/auto modes don't use vectors → no parity check."""
    _make_index(cache_dir, "alpha", embedding={"provider": "mistral", "model": "codestral-embed", "dims": 1536})
    _make_index(cache_dir, "beta", embedding={"provider": "openai", "model": "text-embedding-3-large", "dims": 3072})
    response, status = _dispatch("ce_pack_context", {
        "query": "auth",
        "corpus_ids": ["alpha", "beta"],
        "mode": "keyword",
        "budget": 4000,
    })
    assert status == 200


def test_pack_multi_corpus_prefix_collision(cache_dir):
    """Two corpora where one id is a prefix of the other."""
    _make_index(cache_dir, "myorg")
    _make_index(cache_dir, "myorg-extra")
    response, status = _dispatch("ce_pack_context", {
        "query": "auth",
        "corpus_ids": ["myorg", "myorg-extra"],
        "budget": 4000,
    })
    assert status == 400
    s = response["result"]["structuredContent"]
    assert s["code"] == "CORPUS_PREFIX_COLLISION"


def test_pack_multi_corpus_duplicate_ids(cache_dir):
    _make_index(cache_dir, "a")
    response, status = _dispatch("ce_pack_context", {
        "query": "x", "corpus_ids": ["a", "a"], "budget": 4000,
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_pack_multi_corpus_too_many(cache_dir):
    """Cap at 10 corpus_ids per § 3.1."""
    response, status = _dispatch("ce_pack_context", {
        "query": "x", "corpus_ids": [f"c{i}" for i in range(11)], "budget": 4000,
    })
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


# ── ce_pack_context: model_context auto-budget scaling ──

def test_pack_model_context_scales_budget(cache_dir):
    _make_index(cache_dir, "a")
    # 1M context → 12% = 120000 → clamped to 64000
    response, status = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_id": "a", "model_context": 1_000_000,
    })
    assert status == 200
    assert response["result"]["structuredContent"]["tokens_budget"] == 64000


def test_pack_model_context_clamped_minimum(cache_dir):
    _make_index(cache_dir, "a")
    # 8K context → 12% = 960 → clamped to 4000
    response, status = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_id": "a", "model_context": 8000,
    })
    assert status == 200
    assert response["result"]["structuredContent"]["tokens_budget"] == 4000


# ── ce_find_relevant_files ──

def test_find_single_corpus(cache_dir):
    _make_index(cache_dir, "test-corpus")
    response, status = _dispatch("ce_find_relevant_files", {
        "query": "authentication", "corpus_id": "test-corpus", "top_k": 5,
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["corpus_commit_sha"] == "abc1234"
    assert s["corpus_commit_shas"] is None
    assert all("content" not in f for f in s["files"])  # paths only, no content
    assert all("relevance" in f and "keyword_score" in f for f in s["files"])


def test_find_top_k_caps_results(cache_dir):
    _make_index(cache_dir, "test-corpus")
    response, status = _dispatch("ce_find_relevant_files", {
        "query": "authentication", "corpus_id": "test-corpus", "top_k": 1,
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    assert len(s["files"]) <= 1


def test_find_multi_corpus_prefixed_paths(cache_dir):
    _make_index(cache_dir, "alpha")
    _make_index(cache_dir, "beta")
    response, status = _dispatch("ce_find_relevant_files", {
        "query": "authentication",
        "corpus_ids": ["alpha", "beta"],
        "top_k": 10,
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    paths = [f["path"] for f in s["files"]]
    assert any(p.startswith("alpha:") for p in paths) or any(p.startswith("beta:") for p in paths)
    # corpus_id only in multi-corpus mode
    if s["files"]:
        assert "corpus_id" in s["files"][0]


def test_find_top_k_out_of_range(cache_dir):
    _make_index(cache_dir, "a")
    response, status = _dispatch("ce_find_relevant_files", {
        "query": "x", "corpus_id": "a", "top_k": 0,
    })
    assert status == 400


# ── ce_list_corpora ──

def test_list_corpora_basic(cache_dir):
    _make_index(cache_dir, "alpha")
    _make_index(cache_dir, "beta")
    _make_index(cache_dir, "gamma")
    response, status = _dispatch("ce_list_corpora", {})
    assert status == 200
    s = response["result"]["structuredContent"]
    assert s["total_count"] == 3
    assert len(s["corpora"]) == 3
    assert s["has_more"] is False
    assert s["next_offset"] is None
    assert "brain_head_sha" in s
    # Stable sort
    ids = [c["corpus_id"] for c in s["corpora"]]
    assert ids == ["alpha", "beta", "gamma"]


def test_list_corpora_pagination(cache_dir):
    for c in ["alpha", "beta", "gamma", "delta"]:
        _make_index(cache_dir, c)
    response, status = _dispatch("ce_list_corpora", {"limit": 2, "offset": 0})
    assert status == 200
    s = response["result"]["structuredContent"]
    assert len(s["corpora"]) == 2
    assert s["total_count"] == 4
    assert s["has_more"] is True
    assert s["next_offset"] == 2

    # Second page
    response, status = _dispatch("ce_list_corpora", {"limit": 2, "offset": 2})
    s = response["result"]["structuredContent"]
    assert len(s["corpora"]) == 2
    assert s["has_more"] is False
    assert s["next_offset"] is None


def test_list_corpora_brain_head_sha_stable(cache_dir):
    _make_index(cache_dir, "alpha")
    _make_index(cache_dir, "beta")
    r1, _ = _dispatch("ce_list_corpora", {"limit": 1, "offset": 0})
    r2, _ = _dispatch("ce_list_corpora", {"limit": 1, "offset": 1})
    sha1 = r1["result"]["structuredContent"]["brain_head_sha"]
    sha2 = r2["result"]["structuredContent"]["brain_head_sha"]
    assert sha1 == sha2  # corpora set unchanged across pages


def test_list_corpora_brain_head_sha_changes_on_drift(cache_dir):
    _make_index(cache_dir, "alpha")
    r1, _ = _dispatch("ce_list_corpora", {})
    sha1 = r1["result"]["structuredContent"]["brain_head_sha"]
    _make_index(cache_dir, "beta")
    r2, _ = _dispatch("ce_list_corpora", {})
    sha2 = r2["result"]["structuredContent"]["brain_head_sha"]
    assert sha1 != sha2


def test_list_corpora_lifecycle_filter(cache_dir):
    _make_index(cache_dir, "active1", lifecycle_state="active")
    _make_index(cache_dir, "archived1", lifecycle_state="archived")
    response, status = _dispatch("ce_list_corpora", {})  # default = active+idle
    s = response["result"]["structuredContent"]
    ids = [c["corpus_id"] for c in s["corpora"]]
    assert "active1" in ids
    assert "archived1" not in ids

    # Explicit inclusion
    response, status = _dispatch("ce_list_corpora", {"lifecycle_state": ["archived"]})
    s = response["result"]["structuredContent"]
    ids = [c["corpus_id"] for c in s["corpora"]]
    assert ids == ["archived1"]


def test_list_corpora_classification_filter(cache_dir):
    _make_index(cache_dir, "pub", data_classification="public")
    _make_index(cache_dir, "int", data_classification="internal")
    _make_index(cache_dir, "conf", data_classification="confidential")
    # Default cmax=internal hides confidential
    response, status = _dispatch("ce_list_corpora", {})
    s = response["result"]["structuredContent"]
    ids = sorted(c["corpus_id"] for c in s["corpora"])
    assert ids == ["int", "pub"]
    # Explicit confidential (admin role permits)
    response, status = _dispatch("ce_list_corpora", {"data_classification_max": "confidential"})
    s = response["result"]["structuredContent"]
    ids = sorted(c["corpus_id"] for c in s["corpora"])
    assert ids == ["conf", "int", "pub"]


def test_list_corpora_source_type_filter(cache_dir):
    _make_index(cache_dir, "a", source_type="github_repo")
    _make_index(cache_dir, "b", source_type="local_workspace")
    response, status = _dispatch("ce_list_corpora", {"source_type": "github_repo"})
    s = response["result"]["structuredContent"]
    ids = [c["corpus_id"] for c in s["corpora"]]
    assert ids == ["a"]


def test_list_corpora_invalid_offset(cache_dir):
    response, status = _dispatch("ce_list_corpora", {"offset": -1})
    assert status == 400
    assert response["result"]["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_list_corpora_role_caps_classification(cache_dir):
    """Reader caller cannot see confidential corpora even if data_classification_max=confidential is requested."""
    from _lib import auth, transport, tools as _tools  # noqa: F401
    _make_index(cache_dir, "conf", data_classification="confidential")
    _make_index(cache_dir, "int", data_classification="internal")
    reader = auth.TokenInfo(token_id="t", role="reader", data_classification_max="internal")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ce_list_corpora", "arguments": {"data_classification_max": "confidential"}},
    }
    response, status = transport.dispatch(payload, reader)
    s = response["result"]["structuredContent"]
    # Reader caps cmax to internal — confidential is hidden.
    ids = [c["corpus_id"] for c in s["corpora"]]
    assert ids == ["int"]


# ── Codex review fixes (lock behavior) ──

def test_pack_merge_quota_skips_oversized_head(cache_dir):
    """Codex P2: oversized head must NOT block smaller trailing items.

    We exercise this by giving alpha a single huge file and beta a small one,
    then asking for a budget where only beta's file fits. The merge must NOT
    starve when alpha's head is too big.
    """
    big_files = [{
        "path": "huge.md", "tokens": 10_000,
        "tree": {"depth": 0, "title": "huge.md", "firstSentence": "auth",
                 "firstParagraph": "auth", "text": "auth", "children": []},
        "knowledge_type": "evidence",
    }]
    small_files = [{
        "path": "tiny.md", "tokens": 200,
        "tree": {"depth": 0, "title": "tiny.md", "firstSentence": "auth",
                 "firstParagraph": "auth", "text": "auth", "children": []},
        "knowledge_type": "evidence",
    }]
    _make_index(cache_dir, "alpha", files=big_files)
    _make_index(cache_dir, "beta", files=small_files)
    response, status = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_ids": ["alpha", "beta"], "budget": 2000,
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    paths = [f["path"] for f in s["files"]]
    # Beta's small file MUST be included; without the fix, alpha's oversized
    # head would consume the iteration without progress.
    assert any(p.startswith("beta:") for p in paths)


def test_load_corpus_filename_fallback_when_meta_missing_id(cache_dir, tmp_path):
    """Codex P1: load_corpus must derive corpus_id from filename when _meta lacks it."""
    from _lib import corpus_store

    raw = {
        "_meta": {
            # corpus_id missing — simulates a hand-built or partially migrated index
            "commit_sha": "deadbeef",
            "data_classification": "internal",
            "embedding": {"provider": "none", "model": "n/a", "dims": 0},
        },
        "files": [{
            "path": "x.md", "tokens": 50,
            "tree": {"depth": 0, "title": "x.md", "firstSentence": "x",
                     "firstParagraph": "x", "text": "x", "children": []},
        }],
    }
    (cache_dir / "from-filename.index.json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = corpus_store.load_corpus("from-filename")
    assert loaded is not None
    assert loaded.meta.corpus_id == "from-filename"
    assert loaded.meta.commit_sha == "deadbeef"


def test_pack_uses_filename_corpus_id_in_multi_corpus_prefix(cache_dir):
    """Multi-corpus path prefixes must use filename-derived corpus_id when _meta is stale."""
    raw = {
        "_meta": {"commit_sha": "abc"},  # missing corpus_id
        "files": [{"path": "f.md", "tokens": 50,
                   "tree": {"depth": 0, "title": "f.md", "firstSentence": "auth",
                            "firstParagraph": "auth", "text": "auth", "children": []}}],
    }
    (cache_dir / "alpha.index.json").write_text(json.dumps(raw), encoding="utf-8")
    _make_index(cache_dir, "beta")
    response, status = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_ids": ["alpha", "beta"], "budget": 4000,
    })
    assert status == 200
    s = response["result"]["structuredContent"]
    # Without the filename fallback, alpha's prefix would render as ":f.md".
    paths = [f["path"] for f in s["files"]]
    assert any(p.startswith("alpha:") for p in paths)
    assert "alpha" in s["corpus_commit_shas"]


def test_list_corpora_brain_head_sha_scoped_to_caller_visibility(cache_dir):
    """Codex P2: hidden-corpus churn must not change a reader's brain_head_sha."""
    from _lib import auth, transport, tools as _tools  # noqa: F401

    _make_index(cache_dir, "pub", data_classification="public")
    _make_index(cache_dir, "int", data_classification="internal")

    reader = auth.TokenInfo(token_id="t", role="reader", data_classification_max="internal")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ce_list_corpora", "arguments": {}},
    }
    r1, _ = transport.dispatch(payload, reader)
    sha_before = r1["result"]["structuredContent"]["brain_head_sha"]

    # Add a confidential corpus the reader cannot see
    _make_index(cache_dir, "conf", data_classification="confidential")

    r2, _ = transport.dispatch(payload, reader)
    sha_after = r2["result"]["structuredContent"]["brain_head_sha"]

    # Reader's view didn't change → sha must not change either.
    assert sha_before == sha_after


def test_list_corpora_brain_head_sha_changes_on_visible_drift(cache_dir):
    """Sanity check: visible churn DOES change the sha."""
    from _lib import auth, transport, tools as _tools  # noqa: F401
    _make_index(cache_dir, "pub", data_classification="public")
    reader = auth.TokenInfo(token_id="t", role="reader", data_classification_max="internal")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ce_list_corpora", "arguments": {}},
    }
    r1, _ = transport.dispatch(payload, reader)
    sha_before = r1["result"]["structuredContent"]["brain_head_sha"]
    _make_index(cache_dir, "int", data_classification="internal")
    r2, _ = transport.dispatch(payload, reader)
    sha_after = r2["result"]["structuredContent"]["brain_head_sha"]
    assert sha_before != sha_after


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
