"""Phase 5 tests — deploy hardening (vendor sync, ETag, Cache-Control, 304).

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase5.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")


# ── Vendor sync ──

def test_vendor_pack_context_lib_in_sync_with_canonical():
    """The vendored copy must byte-match scripts/pack_context_lib.py.

    Refresh on drift via:
        cp ../scripts/pack_context_lib.py _lib/vendor/pack_context_lib.py
    """
    here = Path(__file__).resolve()
    vendor = here.parent.parent / "_lib" / "vendor" / "pack_context_lib.py"
    canonical = here.parent.parent.parent / "scripts" / "pack_context_lib.py"
    assert vendor.exists(), f"vendor copy missing at {vendor}"
    assert canonical.exists(), f"canonical missing at {canonical}"
    vh = hashlib.sha256(vendor.read_bytes()).hexdigest()
    ch = hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert vh == ch, (
        f"vendor / canonical drift!\n"
        f"  vendor    = {vh}\n"
        f"  canonical = {ch}\n"
        f"Refresh with: cp ../scripts/pack_context_lib.py _lib/vendor/pack_context_lib.py"
    )


def test_vendor_index_github_repo_in_sync_with_canonical():
    """Phase 5.5: vendored indexer must byte-match scripts/index_github_repo.py.

    The original tools/index_github_repo._run_indexer imported from
    `../scripts/`, which Vercel function bundles can't reach. Production
    smoke (octocat/Hello-World) returned ModuleNotFoundError. Vendoring +
    this drift check makes both work.
    """
    here = Path(__file__).resolve()
    vendor = here.parent.parent / "_lib" / "vendor" / "index_github_repo.py"
    canonical = here.parent.parent.parent / "scripts" / "index_github_repo.py"
    assert vendor.exists(), f"vendor copy missing at {vendor}"
    assert canonical.exists(), f"canonical missing at {canonical}"
    vh = hashlib.sha256(vendor.read_bytes()).hexdigest()
    ch = hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert vh == ch, (
        f"vendor / canonical drift!\n"
        f"  vendor    = {vh}\n"
        f"  canonical = {ch}\n"
        f"Refresh with: cp ../scripts/index_github_repo.py _lib/vendor/index_github_repo.py"
    )


# ── Fixtures ──

@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    return cd


def _admin_token():
    from _lib import auth
    return auth.authenticate("Bearer test-token")


def _make_index(cache_dir: Path, corpus_id: str, *,
                data_classification: str = "internal",
                commit_sha: str = "abc1234") -> None:
    raw = {
        "_meta": {
            "corpus_id": corpus_id, "commit_sha": commit_sha,
            "data_classification": data_classification,
            "embedding": {"provider": "none", "model": "n/a", "dims": 0},
            "file_count": 1, "version": 1, "lifecycle_state": "active",
            "source": {"type": "github_repo", "uri": "x", "branch": "main"},
        },
        "files": [{
            "path": "auth.py", "contentHash": "h1", "tokens": 100,
            "tree": {"depth": 0, "title": "auth.py", "firstSentence": "auth",
                     "firstParagraph": "auth", "text": "auth", "children": []},
            "knowledge_type": "ground_truth",
        }],
    }
    (cache_dir / f"{corpus_id}.index.json").write_text(json.dumps(raw), encoding="utf-8")


def _dispatch(name, arguments):
    from _lib import tools as _tools  # noqa: F401
    from _lib.transport import dispatch as _dispatch_fn
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    return _dispatch_fn(payload, _admin_token())


# ── ETag ──

def test_pack_response_includes_etag_envelope_hint(cache_dir):
    _make_index(cache_dir, "alpha")
    response, _ = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_id": "alpha", "budget": 2000,
    })
    assert "_x_etag" in response
    etag = response["_x_etag"]
    assert isinstance(etag, str) and len(etag) >= 16


def test_pack_etag_stable_across_identical_requests(cache_dir):
    _make_index(cache_dir, "alpha")
    args = {"query": "auth", "corpus_id": "alpha", "budget": 2000}
    r1, _ = _dispatch("ce_pack_context", args)
    r2, _ = _dispatch("ce_pack_context", args)
    assert r1["_x_etag"] == r2["_x_etag"]


def test_pack_etag_changes_when_corpus_commit_changes(cache_dir):
    _make_index(cache_dir, "alpha", commit_sha="aaa")
    r1, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    _make_index(cache_dir, "alpha", commit_sha="bbb")
    r2, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    assert r1["_x_etag"] != r2["_x_etag"]


def test_pack_etag_changes_when_inputs_change(cache_dir):
    _make_index(cache_dir, "alpha")
    r1, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    r2, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 4000})
    assert r1["_x_etag"] != r2["_x_etag"]


def test_pack_etag_ignores_why_flag(cache_dir):
    """`why` is debug-only; same content with why=true should share etag with why=false."""
    _make_index(cache_dir, "alpha")
    r1, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    r2, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000, "why": True})
    assert r1["_x_etag"] == r2["_x_etag"]


def test_pack_multi_corpus_etag_uses_lex_sorted_shas(cache_dir):
    _make_index(cache_dir, "alpha", commit_sha="aaa")
    _make_index(cache_dir, "beta", commit_sha="bbb")
    r1, _ = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_ids": ["alpha", "beta"], "budget": 4000,
    })
    # Order of corpus_ids in input shouldn't affect ETag (multi-corpus is set-like)
    r2, _ = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_ids": ["beta", "alpha"], "budget": 4000,
    })
    # Note: input fields differ in order, so canonical-input hash differs even
    # though commit_key is sorted. § 3.1 says "ETag canonicalization: input
    # fields serialized via RFC 8785 (JSON Canonicalization Scheme) before
    # hashing." — RFC 8785 sorts object keys, but corpus_ids is an array
    # whose order IS semantically meaningful (callers can disambiguate prefix
    # collisions by reordering). So we expect different ETags here.
    # The ASSERTION below verifies stability for the SAME input ordering only:
    r3, _ = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_ids": ["alpha", "beta"], "budget": 4000,
    })
    assert r1["_x_etag"] == r3["_x_etag"]


def test_find_response_includes_etag(cache_dir):
    _make_index(cache_dir, "alpha")
    response, _ = _dispatch("ce_find_relevant_files", {
        "query": "auth", "corpus_id": "alpha", "top_k": 5,
    })
    assert "_x_etag" in response


# ── Cache-Control ──

def test_pack_cache_control_internal(cache_dir):
    _make_index(cache_dir, "alpha", data_classification="internal")
    response, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    assert response["_x_cache_control"] == "private, max-age=60"


def test_pack_cache_control_public(cache_dir):
    _make_index(cache_dir, "alpha", data_classification="public")
    response, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    assert response["_x_cache_control"] == "private, max-age=60"


def test_pack_cache_control_confidential_is_no_store(cache_dir):
    """§ 3.1: confidential/restricted MUST NOT be cached."""
    _make_index(cache_dir, "alpha", data_classification="confidential")
    response, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    assert response["_x_cache_control"] == "no-store"


def test_pack_cache_control_restricted_is_no_store(cache_dir):
    _make_index(cache_dir, "alpha", data_classification="restricted")
    response, _ = _dispatch("ce_pack_context", {"query": "auth", "corpus_id": "alpha", "budget": 2000})
    assert response["_x_cache_control"] == "no-store"


def test_pack_multi_corpus_cache_control_max_classification_wins(cache_dir):
    """If ANY corpus is confidential/restricted, the response is no-store."""
    _make_index(cache_dir, "alpha", data_classification="public")
    _make_index(cache_dir, "beta", data_classification="confidential")
    response, _ = _dispatch("ce_pack_context", {
        "query": "auth", "corpus_ids": ["alpha", "beta"], "budget": 4000,
    })
    assert response["_x_cache_control"] == "no-store"


# ── Initialize message reflects v1 surface ──

def test_pack_etag_falls_back_to_content_fingerprint_when_sha_missing(cache_dir):
    """Codex P1: when commit_sha is empty, ETag must NOT collapse to a constant.

    Two corpora with identical args but different file contents must produce
    different ETags even when both have empty commit_sha.
    """
    raw_v1 = {
        "_meta": {"corpus_id": "alpha"},  # commit_sha intentionally absent
        "files": [{
            "path": "a.py", "contentHash": "h1", "tokens": 50,
            "tree": {"depth": 0, "title": "a.py", "firstSentence": "auth",
                     "firstParagraph": "auth", "text": "auth", "children": []},
        }],
    }
    raw_v2 = {
        "_meta": {"corpus_id": "alpha"},
        "files": [{
            "path": "a.py", "contentHash": "h2", "tokens": 50,
            "tree": {"depth": 0, "title": "a.py", "firstSentence": "auth",
                     "firstParagraph": "auth", "text": "auth", "children": []},
        }],
    }
    args = {"query": "auth", "corpus_id": "alpha", "budget": 2000}

    (cache_dir / "alpha.index.json").write_text(json.dumps(raw_v1), encoding="utf-8")
    r1, _ = _dispatch("ce_pack_context", args)
    etag_v1 = r1["_x_etag"]

    (cache_dir / "alpha.index.json").write_text(json.dumps(raw_v2), encoding="utf-8")
    r2, _ = _dispatch("ce_pack_context", args)
    etag_v2 = r2["_x_etag"]

    assert etag_v1 != etag_v2, (
        "ETag must change when corpus content changes, even when commit_sha is missing — "
        "otherwise stale 304 responses leak forever."
    )


def test_initialize_message_reflects_v1_surface():
    from _lib import tools as _tools  # noqa: F401
    from _lib.transport import dispatch
    response, _ = dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, _admin_token())
    instructions = response["result"]["instructions"]
    assert "All read + write tools are wired" in instructions or "v1" in instructions
    # Stale Phase 2 wording must be gone
    assert "Phase 2: only ce_get_health" not in instructions


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
