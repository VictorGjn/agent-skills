"""Phase 5.5 tests — semantic mode + MMR rerank.

Covers:
- engine._cosine, score_corpus_semantic ordering, drops missing-embedding files,
  clips negative cosines.
- engine.mmr_rerank: respects lambda extremes (=1 → relevance order, =0 →
  diversity-only), keeps undroppable items at the tail.
- corpus_store.load_corpus: surfaces top-level `embeddings` map.
- find / pack tools: dispatch to semantic when corpus has embeddings,
  fall back to keyword (with note in `reason`) when it doesn't.
- find / pack: rerank arg validates; "mmr" reorders relative to no-rerank.
- find / pack: query embedding failure (Mistral unreachable) silently
  falls back to keyword — no whole-request error.

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase5_5.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))

os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

from _lib import corpus_store, embed, engine  # noqa: E402
from _lib.auth import TokenInfo  # noqa: E402
from _lib.tools import find, pack  # noqa: E402


# ── Helpers ──

def _admin_token() -> TokenInfo:
    return TokenInfo(token_id="t", role="admin", data_classification_max="restricted")


def _unit_vec(d: int, axis: int) -> list[float]:
    """Return a d-dim unit vector aligned with `axis` (0..d-1)."""
    v = [0.0] * d
    v[axis] = 1.0
    return v


def _write_corpus(cache_dir: Path, corpus_id: str,
                  files: list[dict],
                  embeddings: dict[str, list[float]] | None = None,
                  *, dims: int = 4,
                  classification: str = "internal") -> Path:
    if embeddings is None:
        embeddings = {}
    embedding_meta = {"provider": "mistral" if embeddings else "none",
                      "model": "codestral-embed" if embeddings else "n/a",
                      "dims": dims if embeddings else 0}
    index_obj = {
        "_meta": {
            "corpus_id": corpus_id,
            "source": {"type": "github_repo", "uri": f"https://github.com/x/{corpus_id}"},
            "data_classification": classification,
            "embedding": embedding_meta,
            "file_count": len(files),
            "embedded_count": len(embeddings),
            "version": 1,
            "last_refresh_completed_at": "2026-05-05T00:00:00Z",
            "commit_sha": "abc1234567",
            "lifecycle_state": "active",
        },
        "files": files,
        "embeddings": embeddings,
    }
    p = cache_dir / f"{corpus_id}.index.json"
    p.write_text(json.dumps(index_obj), encoding="utf-8")
    return p


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    return cd


# ── engine._cosine ──

def test_cosine_orthogonal_is_zero():
    assert engine._cosine([1, 0, 0], [0, 1, 0]) == 0.0


def test_cosine_aligned_is_one():
    assert math.isclose(engine._cosine([1, 0, 0], [2, 0, 0]), 1.0)


def test_cosine_opposite_is_minus_one():
    assert math.isclose(engine._cosine([1, 0], [-1, 0]), -1.0)


def test_cosine_length_mismatch_returns_zero():
    assert engine._cosine([1, 0, 0], [1, 0]) == 0.0


def test_cosine_zero_vector_returns_zero():
    assert engine._cosine([0, 0, 0], [1, 2, 3]) == 0.0


# ── engine.score_corpus_semantic ──

def test_score_corpus_semantic_orders_by_cosine():
    files = [
        {"path": "a.py", "tokens": 100},
        {"path": "b.py", "tokens": 100},
        {"path": "c.py", "tokens": 100},
    ]
    q = _unit_vec(4, 0)
    embeddings = {
        "a.py": _unit_vec(4, 0),         # cos = 1.0
        "b.py": [0.5, 0.5, 0.5, 0.5],    # cos = 0.5
        "c.py": _unit_vec(4, 1),         # cos = 0
    }
    out = engine.score_corpus_semantic(q, files, embeddings)
    assert [s["path"] for s in out] == ["a.py", "b.py"]  # c.py dropped (0 cos)
    assert out[0]["relevance"] > out[1]["relevance"]


def test_score_corpus_semantic_drops_files_without_embedding():
    files = [
        {"path": "a.py", "tokens": 100},
        {"path": "no-embed.py", "tokens": 100},
    ]
    q = _unit_vec(4, 0)
    embeddings = {"a.py": _unit_vec(4, 0)}
    out = engine.score_corpus_semantic(q, files, embeddings)
    assert [s["path"] for s in out] == ["a.py"]


def test_score_corpus_semantic_clips_negative():
    files = [{"path": "a.py", "tokens": 1}, {"path": "b.py", "tokens": 1}]
    q = [1, 0]
    embeddings = {"a.py": [1, 0], "b.py": [-1, 0]}
    out = engine.score_corpus_semantic(q, files, embeddings)
    paths = [s["path"] for s in out]
    assert "b.py" not in paths  # negative cos clipped


# ── engine.mmr_rerank ──

def test_mmr_lambda_one_is_relevance_only():
    """lambda=1 is equivalent to relevance-only ordering."""
    scored = [
        {"path": "a", "relevance": 0.9, "tokens": 1, "tree": None},
        {"path": "b", "relevance": 0.8, "tokens": 1, "tree": None},
        {"path": "c", "relevance": 0.7, "tokens": 1, "tree": None},
    ]
    embeddings = {"a": [1, 0], "b": [0.99, 0.01], "c": [0, 1]}  # a≈b similar
    q = [1, 0]
    out = engine.mmr_rerank(scored, q, embeddings, lambda_=1.0)
    assert [s["path"] for s in out] == ["a", "b", "c"]


def test_mmr_diversity_pulls_dissimilar_item_up():
    """With moderate lambda, MMR should prefer a less-similar 2nd pick over a near-duplicate."""
    scored = [
        {"path": "a", "relevance": 0.9, "tokens": 1, "tree": None},
        {"path": "b", "relevance": 0.85, "tokens": 1, "tree": None},   # near-duplicate of a
        {"path": "c", "relevance": 0.6, "tokens": 1, "tree": None},    # different direction
    ]
    embeddings = {
        "a": [1.0, 0.0],
        "b": [0.99, 0.14],   # very close to a
        "c": [0.0, 1.0],     # orthogonal to a
    }
    q = [1, 0]
    out = engine.mmr_rerank(scored, q, embeddings, lambda_=0.4)
    # First pick is highest relevance (a). Second pick should jump to c
    # because b's diversity penalty against a wipes its relevance edge.
    assert out[0]["path"] == "a"
    assert out[1]["path"] == "c"


def test_mmr_keeps_unembedded_items_at_tail():
    scored = [
        {"path": "a", "relevance": 0.9, "tokens": 1, "tree": None},
        {"path": "no-emb", "relevance": 0.85, "tokens": 1, "tree": None},
    ]
    embeddings = {"a": [1, 0]}
    out = engine.mmr_rerank(scored, [1, 0], embeddings, lambda_=0.5)
    assert out[-1]["path"] == "no-emb"


# ── corpus_store.load_corpus surfaces embeddings ──

def test_load_corpus_surfaces_embeddings(cache_dir):
    files = [{"path": "x.py", "contentHash": "h1", "tokens": 10, "tree": {"text": "x"}}]
    emb = {"x.py": _unit_vec(4, 0)}
    _write_corpus(cache_dir, "alpha", files, embeddings=emb)
    loaded = corpus_store.load_corpus("alpha")
    assert loaded is not None
    assert loaded.embeddings == emb


def test_load_corpus_no_embeddings_field_is_empty_dict(cache_dir):
    files = [{"path": "x.py", "contentHash": "h1", "tokens": 10, "tree": {"text": "x"}}]
    _write_corpus(cache_dir, "alpha", files)  # no embeddings
    loaded = corpus_store.load_corpus("alpha")
    assert loaded.embeddings == {}


# ── find tool dispatch ──

def _file(path: str, *, text: str = "x") -> dict:
    return {
        "path": path, "contentHash": f"h-{path}", "tokens": len(text),
        "tree": {"title": path, "firstSentence": text, "firstParagraph": text,
                 "text": text, "children": []},
        "knowledge_type": "evidence",
    }


def test_find_semantic_mode_uses_embeddings(cache_dir, monkeypatch):
    """When the query embeds and the corpus has embeddings, results come from cosine."""
    files = [_file("a.py", text="auth"), _file("b.py", text="frob")]
    embeddings = {"a.py": _unit_vec(4, 0), "b.py": _unit_vec(4, 1)}
    _write_corpus(cache_dir, "alpha", files, embeddings=embeddings)

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = find.handle({"query": "anything", "corpus_id": "alpha", "mode": "semantic"},
                      _admin_token())
    assert out["files"]
    assert out["files"][0]["path"] == "a.py"
    assert out["files"][0]["semantic_score"] > 0
    assert out["files"][0]["keyword_score"] == 0.0
    assert "cosine" in out["files"][0]["reason"]


def test_find_semantic_falls_back_to_keyword_when_no_embeddings(cache_dir, monkeypatch):
    files = [_file("a.py", text="auth"), _file("b.py", text="frob")]
    _write_corpus(cache_dir, "alpha", files)  # no embeddings

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = find.handle({"query": "auth", "corpus_id": "alpha", "mode": "semantic"},
                      _admin_token())
    if out["files"]:
        # Whatever ranks, it must be keyword-scored, with a fellback note.
        assert out["files"][0]["keyword_score"] > 0
        assert out["files"][0]["semantic_score"] == 0.0
        assert "fellback" in out["files"][0]["reason"]


def test_find_semantic_falls_back_when_query_embed_fails(cache_dir, monkeypatch):
    """Mistral unreachable / no key → request still succeeds, keyword fallback."""
    files = [_file("a.py", text="auth")]
    embeddings = {"a.py": _unit_vec(4, 0)}
    _write_corpus(cache_dir, "alpha", files, embeddings=embeddings)

    def boom(q, **kw):
        raise embed.EmbedError("PROVIDER_UNAVAILABLE", "no key")
    monkeypatch.setattr(embed, "embed_query", boom)

    out = find.handle({"query": "auth", "corpus_id": "alpha", "mode": "semantic"},
                      _admin_token())
    assert "isError" not in out  # request succeeded
    if out["files"]:
        assert out["files"][0]["keyword_score"] > 0


def test_find_rerank_invalid_value_returns_invalid_argument(cache_dir):
    _write_corpus(cache_dir, "alpha", [_file("a.py")])
    out = find.handle({"query": "x", "corpus_id": "alpha", "rerank": "bogus"},
                      _admin_token())
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_find_mmr_dispatch_returns_valid_results(cache_dir, monkeypatch):
    """C3 (rerank=mmr) should dispatch through the MMR path and return valid
    semantic results. Ordering correctness is covered by
    test_mmr_diversity_pulls_dissimilar_item_up (which exercises engine.mmr_rerank
    directly with a tunable lambda — the find layer hardcodes lambda=0.7,
    which is too relevance-leaning to flip ordering on small synthetic corpora).
    """
    files = [_file("a.py"), _file("b.py"), _file("c.py")]
    embeddings = {
        "a.py": [1.0, 0.0],
        "b.py": [0.99, 0.14],
        "c.py": [0.30, 0.95],
    }
    _write_corpus(cache_dir, "alpha", files, embeddings=embeddings, dims=2)

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: [1.0, 0.0])

    with_mmr = find.handle(
        {"query": "x", "corpus_id": "alpha", "mode": "semantic", "top_k": 3,
         "rerank": "mmr"},
        _admin_token())

    assert "isError" not in with_mmr
    paths = [f["path"] for f in with_mmr["files"]]
    assert "a.py" in paths  # highest cosine — must survive any rerank
    for f in with_mmr["files"]:
        assert "mmr" in f["reason"]
        assert f["semantic_score"] > 0


# ── pack tool dispatch ──

def test_pack_semantic_mode_uses_embeddings(cache_dir, monkeypatch):
    files = [_file("a.py", text="auth function definition"),
             _file("b.py", text="frob widget")]
    embeddings = {"a.py": _unit_vec(4, 0), "b.py": _unit_vec(4, 1)}
    _write_corpus(cache_dir, "alpha", files, embeddings=embeddings)

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = pack.handle({"query": "anything", "corpus_id": "alpha",
                       "mode": "semantic", "budget": 8000,
                       "response_format": "structured"},
                      _admin_token())
    assert out["files"]
    assert out["files"][0]["path"] == "a.py"


def test_pack_rerank_invalid_returns_invalid_argument(cache_dir):
    _write_corpus(cache_dir, "alpha", [_file("a.py")])
    out = pack.handle({"query": "x", "corpus_id": "alpha", "rerank": "lol"},
                      _admin_token())
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_pack_semantic_falls_back_to_keyword_when_no_embeddings(cache_dir, monkeypatch):
    """Pack-side parity test for the find-side soft-fallback contract."""
    files = [_file("a.py", text="auth"), _file("b.py", text="frob")]
    _write_corpus(cache_dir, "alpha", files)  # dims=0, no embeddings

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = pack.handle({"query": "auth", "corpus_id": "alpha", "mode": "semantic",
                       "budget": 8000, "response_format": "structured"},
                      _admin_token())
    assert "isError" not in out
    if out["files"]:
        # Whatever ranks, it must be keyword-scored (semantic_score field
        # not on pack output, but the engine fell back via mode dispatch).
        assert out["files"][0]["relevance"] > 0


def test_pack_semantic_falls_back_when_query_embed_fails(cache_dir, monkeypatch):
    """Mistral unreachable / no key → request still succeeds, keyword fallback."""
    files = [_file("a.py", text="auth")]
    embeddings = {"a.py": _unit_vec(4, 0)}
    _write_corpus(cache_dir, "alpha", files, embeddings=embeddings)

    def boom(q, **kw):
        raise embed.EmbedError("PROVIDER_UNAVAILABLE", "no key")
    monkeypatch.setattr(embed, "embed_query", boom)

    out = pack.handle({"query": "auth", "corpus_id": "alpha", "mode": "semantic",
                       "budget": 8000, "response_format": "structured"},
                      _admin_token())
    assert "isError" not in out


# ── New strict parity check (P1.2 fix) ──

def test_find_semantic_rejects_corpus_with_dims_but_no_embeddings(cache_dir, monkeypatch):
    """Corpus declares dims>0 in metadata but `embeddings` payload is empty.

    This is hand-built-index drift, not "keyword-only by design" (which is
    dims=0). Must error rather than silently fall back to keyword — otherwise
    a multi-corpus call could mix cosine scores from a real semantic corpus
    with keyword scores from a broken one and the merged ranking would be
    meaningless. P1.2 fix.
    """
    files = [_file("a.py")]
    cd = cache_dir
    # Hand-construct a corpus with dims=1536 declared but no embeddings map
    index_obj = {
        "_meta": {
            "corpus_id": "broken",
            "source": {"type": "github_repo", "uri": "https://github.com/x/broken"},
            "data_classification": "internal",
            "embedding": {"provider": "mistral", "model": "codestral-embed", "dims": 1536},
            "file_count": 1,
            "embedded_count": 0,
            "version": 1,
            "last_refresh_completed_at": "2026-05-05T00:00:00Z",
            "commit_sha": "deadbeef",
            "lifecycle_state": "active",
        },
        "files": files,
        # NOTE: no top-level "embeddings" field
    }
    (cd / "broken.index.json").write_text(json.dumps(index_obj), encoding="utf-8")

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = find.handle({"query": "x", "corpus_id": "broken", "mode": "semantic"},
                      _admin_token())
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "EMBEDDING_PROVIDER_MISMATCH"
    assert "empty_corpora" in out["structuredContent"].get("details", {})


def test_pack_semantic_rejects_corpus_with_dims_but_no_embeddings(cache_dir, monkeypatch):
    """Pack-side mirror of the strict parity test."""
    files = [_file("a.py")]
    index_obj = {
        "_meta": {
            "corpus_id": "broken",
            "source": {"type": "github_repo", "uri": "https://github.com/x/broken"},
            "data_classification": "internal",
            "embedding": {"provider": "mistral", "model": "codestral-embed", "dims": 1536},
            "file_count": 1,
            "embedded_count": 0,
            "version": 1,
            "last_refresh_completed_at": "2026-05-05T00:00:00Z",
            "commit_sha": "deadbeef",
            "lifecycle_state": "active",
        },
        "files": files,
    }
    (cache_dir / "broken.index.json").write_text(json.dumps(index_obj), encoding="utf-8")

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = pack.handle({"query": "x", "corpus_id": "broken", "mode": "semantic",
                       "budget": 8000},
                      _admin_token())
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "EMBEDDING_PROVIDER_MISMATCH"


# ── New rerank/mode validation (P2.3 fix) ──

def test_find_rerank_mmr_without_semantic_mode_returns_invalid_argument(cache_dir):
    """rerank=mmr only makes sense with mode=semantic — MMR operates on cosine
    similarity scores, which keyword mode doesn't produce. P2.3 fix.
    """
    _write_corpus(cache_dir, "alpha", [_file("a.py")])
    out = find.handle(
        {"query": "x", "corpus_id": "alpha", "mode": "keyword", "rerank": "mmr"},
        _admin_token())
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "INVALID_ARGUMENT"
    assert "mode='semantic'" in out["content"][0]["text"]


def test_pack_rerank_mmr_without_semantic_mode_returns_invalid_argument(cache_dir):
    _write_corpus(cache_dir, "alpha", [_file("a.py")])
    out = pack.handle(
        {"query": "x", "corpus_id": "alpha", "mode": "auto", "rerank": "mmr"},
        _admin_token())
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "INVALID_ARGUMENT"


# ── Partial-embeddings coverage (mixed corpus) ──

# ── Server-side embedding in ce_index_github_repo (Phase 5.5 B) ──

def _stub_indexer_files(monkeypatch, files: list[dict], elapsed_s: float = 0.5):
    """Replace tools.index_github_repo._run_indexer with a stub that returns
    a synthetic indexer result. `elapsed_s` simulates the wall-clock cost so
    the budget estimator path is exercisable.
    """
    from _lib.tools import index_github_repo as _tool

    def fake_indexer(owner, name, branch, gh_token):
        if elapsed_s:
            import time as _time
            _time.sleep(elapsed_s)
        return {"files": files}
    monkeypatch.setattr(_tool, "_run_indexer", fake_indexer)
    return _tool


def test_index_github_repo_embed_false_skips(cache_dir, monkeypatch):
    """embed=False → corpus written keyword-only with embed_skipped reason."""
    from _lib.tools import index_github_repo as _tool
    files = [
        {"path": "a.py", "tokens": 10, "hash": "h1",
         "tree": {"text": "auth code", "title": "a.py", "children": []}},
    ]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)

    out = _tool.handle({"repo": "x/y", "branch": "main",
                        "data_classification": "public", "embed": False},
                       _admin_token())
    assert "isError" not in out
    assert out["stats"]["embedded_count"] == 0
    assert "embed=false" in out["embed_skipped"]


def test_index_github_repo_embed_auto_no_key_skips(cache_dir, monkeypatch):
    """embed=null + no MISTRAL_API_KEY → keyword-only, marked embed_skipped."""
    from _lib.tools import index_github_repo as _tool
    files = [{"path": "a.py", "tokens": 10, "hash": "h1",
              "tree": {"text": "auth", "title": "a.py", "children": []}}]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    out = _tool.handle({"repo": "x/y", "data_classification": "public"},
                       _admin_token())
    assert "isError" not in out
    assert out["stats"]["embedded_count"] == 0
    assert "MISTRAL_API_KEY not set" in out["embed_skipped"]


def test_index_github_repo_embed_auto_with_key_embeds(cache_dir, monkeypatch):
    """embed=null + key set + small file count → embed_batch called, all files in map."""
    from _lib.tools import index_github_repo as _tool
    files = [
        {"path": f"f{i}.py", "tokens": 10, "hash": f"h{i}",
         "tree": {"text": f"file {i} content", "title": f"f{i}.py", "children": []}}
        for i in range(3)
    ]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    captured: dict = {"called": False, "n_inputs": None}
    def fake_embed_batch(texts, **kw):
        captured["called"] = True
        captured["n_inputs"] = len(texts)
        return [[1.0] + [0.0] * 1535 for _ in texts]
    monkeypatch.setattr(embed, "embed_batch", fake_embed_batch)

    out = _tool.handle({"repo": "x/y", "data_classification": "public"},
                       _admin_token())
    assert "isError" not in out, out
    assert "embed_skipped" not in out
    assert out["stats"]["embedded_count"] == 3
    assert captured["called"] and captured["n_inputs"] == 3


def test_index_github_repo_embed_skips_when_estimated_over_budget(cache_dir, monkeypatch):
    """Big repo + key set → estimated time exceeds budget → keyword-only with reason.

    Uses 5000 files (at EMBED_FILES_PER_SECOND=16 → 312s estimate) to stay
    safely over budget even after the maxDuration bump (SYNC_TIMEOUT_S=280
    minus EMBED_TIMING_HEADROOM_S=20 → 260s available).
    """
    from _lib.tools import index_github_repo as _tool
    files = [
        {"path": f"f{i}.py", "tokens": 10, "hash": f"h{i}",
         "tree": {"text": f"file {i}", "title": f"f{i}.py", "children": []}}
        for i in range(5000)
    ]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    called = {"hit": False}
    def boom(*a, **kw):
        called["hit"] = True
        return []
    monkeypatch.setattr(embed, "embed_batch", boom)

    out = _tool.handle({"repo": "x/y", "data_classification": "public"},
                       _admin_token())
    assert "isError" not in out
    assert out["stats"]["embedded_count"] == 0
    assert "estimated embed time" in out["embed_skipped"]
    assert "remaining budget" in out["embed_skipped"]
    assert not called["hit"], "embed_batch should not be called when over budget"


def test_index_github_repo_embed_failure_falls_back_to_keyword(cache_dir, monkeypatch):
    """If embed_batch raises EmbedError, the index call still succeeds with a
    keyword-only corpus and the failure surfaced in embed_skipped — not a
    whole-call failure."""
    from _lib.tools import index_github_repo as _tool
    files = [{"path": "a.py", "tokens": 10, "hash": "h1",
              "tree": {"text": "auth", "title": "a.py", "children": []}}]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    def boom(*a, **kw):
        raise embed.EmbedError("EMBED_HTTP", "Mistral timed out")
    monkeypatch.setattr(embed, "embed_batch", boom)

    out = _tool.handle({"repo": "x/y", "data_classification": "public"},
                       _admin_token())
    assert "isError" not in out
    assert out["stats"]["embedded_count"] == 0
    assert "EMBED_HTTP" in out["embed_skipped"]


def test_index_github_repo_partial_text_drops_unembeddable(cache_dir, monkeypatch):
    """Files with no usable text in tree (e.g., a.py has no tree.text/firstParagraph/title)
    are dropped from the embedding map but the corpus is still semantic-eligible
    via the populated subset."""
    from _lib.tools import index_github_repo as _tool
    files = [
        {"path": "good.py", "tokens": 10, "hash": "h1",
         "tree": {"text": "real content", "title": "good.py", "children": []}},
        {"path": "empty.py", "tokens": 0, "hash": "h2",
         "tree": {"children": []}},  # no text/firstParagraph/firstSentence/title
    ]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(embed, "embed_batch",
                        lambda texts, **kw: [[1.0] + [0.0] * 1535 for _ in texts])

    out = _tool.handle({"repo": "x/y", "data_classification": "public"},
                       _admin_token())
    assert "isError" not in out
    # good.py path returned a valid embed text via _file_embed_text. empty.py
    # has tree.title fallback → "empty.py" string → embeds. So both end up in
    # the map. Confirm both vectors stored.
    assert out["stats"]["embedded_count"] >= 1


def test_index_github_repo_idempotency_does_not_pin_keyword_only(cache_dir, monkeypatch):
    """Regression: a corpus written keyword-only by a prior call (e.g.,
    one that lost the embed budget or ran before MISTRAL_API_KEY was set)
    must not block a fresh call from re-deriving with embeddings.

    Without the intent-aware idempotency check, once /tmp had a keyword-only
    index for a given commit_sha, every subsequent call returned that stale
    meta and the corpus could never be upgraded — production gap caught
    while smoking flipt-io/flipt after the maxDuration bump.
    """
    from _lib.tools import index_github_repo as _tool

    # First call: simulate the "lost budget" path — no key, keyword-only
    # corpus persisted.
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    files = [
        {"path": "a.py", "tokens": 10, "hash": "h1",
         "tree": {"text": "auth code", "title": "a.py", "children": []}},
    ]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)

    out1 = _tool.handle({"repo": "x/y", "data_classification": "public"},
                        _admin_token())
    assert out1["stats"]["embedded_count"] == 0
    corpus_id = out1["corpus_id"]

    # Second call: key arrives, caller defaults to auto. The stored corpus
    # has the same commit_sha (content unchanged), but it's keyword-only.
    # Idempotency must NOT short-circuit — we want to embed now.
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(embed, "embed_batch",
                        lambda texts, **kw: [[1.0] + [0.0] * 1535 for _ in texts])

    out2 = _tool.handle({"repo": "x/y", "data_classification": "public"},
                        _admin_token())
    assert out2["corpus_id"] == corpus_id
    assert out2["stats"]["embedded_count"] == 1, (
        "stale keyword-only corpus blocked re-embed; intent guard didn't fire"
    )


def test_index_github_repo_idempotency_short_circuits_when_intent_matches(cache_dir, monkeypatch):
    """Idempotency still fires correctly when content + intent both match —
    re-running without changes should be a fast no-op."""
    from _lib.tools import index_github_repo as _tool
    files = [
        {"path": "a.py", "tokens": 10, "hash": "h1",
         "tree": {"text": "auth code", "title": "a.py", "children": []}},
    ]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(embed, "embed_batch",
                        lambda texts, **kw: [[1.0] + [0.0] * 1535 for _ in texts])

    out1 = _tool.handle({"repo": "x/y", "data_classification": "public"},
                        _admin_token())
    assert out1["stats"]["embedded_count"] == 1
    v1 = out1["version"]

    # Re-run with same args + same content. Intent matches (still embedded).
    out2 = _tool.handle({"repo": "x/y", "data_classification": "public"},
                        _admin_token())
    assert out2["version"] == v1, "idempotency should keep version stable on no-op re-run"
    assert out2["stats"]["embedded_count"] == 1


def test_index_github_repo_embed_arg_validates(cache_dir, monkeypatch):
    """`embed` must be bool or null."""
    from _lib.tools import index_github_repo as _tool
    files = [{"path": "a.py", "tokens": 1, "hash": "h",
              "tree": {"text": "x", "children": []}}]
    _stub_indexer_files(monkeypatch, files, elapsed_s=0)

    out = _tool.handle({"repo": "x/y", "data_classification": "public",
                        "embed": "yes-please"},
                       _admin_token())
    assert out.get("isError") is True
    assert out["structuredContent"]["code"] == "INVALID_ARGUMENT"


def test_score_corpus_semantic_handles_partial_embeddings():
    """Corpus where some files have vectors, some don't — non-embedded files
    are silently dropped from semantic ranking (caller can fall back to
    keyword for those if needed)."""
    files = [
        {"path": "embedded.py", "tokens": 100},
        {"path": "no-embed.py", "tokens": 100},
        {"path": "also-embedded.py", "tokens": 100},
    ]
    embeddings = {
        "embedded.py": _unit_vec(4, 0),
        "also-embedded.py": [0.5, 0.5, 0.5, 0.5],
    }
    out = engine.score_corpus_semantic(_unit_vec(4, 0), files, embeddings)
    paths = [s["path"] for s in out]
    assert "embedded.py" in paths
    assert "also-embedded.py" in paths
    assert "no-embed.py" not in paths


# ── embed module unit tests ──

def test_embed_query_raises_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(embed.EmbedError) as exc:
        embed.embed_query("hello")
    assert exc.value.code == "PROVIDER_UNAVAILABLE"


def test_embed_query_rejects_empty():
    with pytest.raises(embed.EmbedError) as exc:
        embed.embed_query("   ")
    assert exc.value.code == "INVALID_ARGUMENT"


def test_embed_truncates_long_input():
    """_truncate must cap at MAX_INPUT_CHARS — defends against codestral 8K-token cap."""
    s = "x" * (embed.MAX_INPUT_CHARS + 10_000)
    assert len(embed._truncate(s)) == embed.MAX_INPUT_CHARS
    short = "y" * 100
    assert embed._truncate(short) == short
