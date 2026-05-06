"""Phase B.4 tests — SPEC § 3.0.5 coverage transparency on find/pack responses.

Covers:
- coverage block present on every find/pack success
- corpus_size_files = sum of meta.file_count across requested corpora
- ranked_with reflects what the engine ACTUALLY ran (not what was requested
  when soft-fallback fires)
- ranked_with_lane = "live" on success, "fallback" when query embed failed
- files_eligible_for_mode == file_count for keyword; == embedded_count for
  semantic; aggregates across corpora
- files_skipped_unembedded > 0 only when semantic ran on a partial-coverage
  corpus
- fallback_to_keyword True when mode=semantic was asked and engine ran kw
- trace_id is a non-empty string per request and stable within one request
- Multi-corpus aggregation: sums file_count, takes union of eligible files
- Bench-relevant: a corpus with 100 embedded out of 200 files queried with
  mode=semantic produces the right eligible_recall denominator

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase_b4_coverage.py
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

from _lib import corpus_access, corpus_store, embed  # noqa: E402
from _lib.auth import TokenInfo  # noqa: E402
from _lib.tools import find, pack  # noqa: E402


def _admin_token() -> TokenInfo:
    return TokenInfo(token_id="t", role="admin", data_classification_max="restricted")


def _unit_vec(d: int, axis: int) -> list[float]:
    v = [0.0] * d
    v[axis] = 1.0
    return v


def _file(path: str, *, text: str = "x") -> dict:
    return {
        "path": path, "contentHash": f"h-{path}", "tokens": len(text),
        "tree": {"title": path, "firstSentence": text, "firstParagraph": text,
                 "text": text, "children": []},
        "knowledge_type": "evidence",
    }


def _write_corpus(cache_dir: Path, corpus_id: str, *,
                  files: list[dict], embeddings: dict[str, list[float]] | None = None,
                  dims: int = 4, classification: str = "internal") -> None:
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
            "embedded_count": len(embeddings or {}),
            "version": 1,
            "last_refresh_completed_at": "2026-05-06T00:00:00Z",
            "commit_sha": f"sha-{corpus_id[:8]}",
            "lifecycle_state": "active",
        },
        "files": files,
        "embeddings": embeddings or {},
    }
    (cache_dir / f"{corpus_id}.index.json").write_text(json.dumps(index_obj), encoding="utf-8")


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    return cd


# ── Helper unit tests ──

def test_build_coverage_keyword_eligible_is_full_corpus(cache_dir):
    files = [_file(f"f{i}.py") for i in range(5)]
    _write_corpus(cache_dir, "alpha", files=files)
    loaded = corpus_store.load_corpus("alpha")
    assert loaded is not None

    cov = corpus_access.build_coverage(
        [loaded], mode_requested="keyword", mode_used="keyword",
        fell_back=False, trace_id="t-1",
    )
    assert cov["corpus_size_files"] == 5
    assert cov["files_eligible_for_mode"] == 5
    assert cov["files_skipped_unembedded"] == 0
    assert cov["ranked_with"] == "keyword"
    assert cov["fallback_to_keyword"] is False
    assert cov["trace_id"] == "t-1"


def test_build_coverage_semantic_eligible_is_embedded_count_only(cache_dir):
    """Partial-coverage corpus: 5 files, 3 embeddings → semantic eligible=3."""
    files = [_file(f"f{i}.py") for i in range(5)]
    embeddings = {f"f{i}.py": _unit_vec(4, 0) for i in range(3)}  # only 0..2
    _write_corpus(cache_dir, "alpha", files=files, embeddings=embeddings)
    loaded = corpus_store.load_corpus("alpha")
    assert loaded is not None

    cov = corpus_access.build_coverage(
        [loaded], mode_requested="semantic", mode_used="semantic",
        fell_back=False, trace_id="t-2",
    )
    assert cov["corpus_size_files"] == 5
    assert cov["files_eligible_for_mode"] == 3
    assert cov["files_skipped_unembedded"] == 2
    assert cov["fallback_to_keyword"] is False


def test_build_coverage_fellback_marks_flag_and_uses_keyword_eligible(cache_dir):
    """Caller asked semantic, engine ran keyword (no key, no embeddings, etc.)
    The mode_used is keyword → eligible counts as full corpus, fall-back flag set."""
    files = [_file(f"f{i}.py") for i in range(3)]
    _write_corpus(cache_dir, "alpha", files=files)  # no embeddings at all
    loaded = corpus_store.load_corpus("alpha")
    assert loaded is not None

    cov = corpus_access.build_coverage(
        [loaded], mode_requested="semantic", mode_used="keyword",
        fell_back=True, trace_id="t-3",
    )
    assert cov["files_eligible_for_mode"] == 3   # keyword eligible = all
    assert cov["fallback_to_keyword"] is True


def test_build_coverage_multi_corpus_aggregates_file_count_and_eligible(cache_dir):
    files_a = [_file(f"a{i}.py") for i in range(4)]
    files_b = [_file(f"b{i}.py") for i in range(6)]
    emb_a = {f"a{i}.py": _unit_vec(4, 0) for i in range(2)}
    emb_b = {f"b{i}.py": _unit_vec(4, 0) for i in range(5)}
    _write_corpus(cache_dir, "alpha", files=files_a, embeddings=emb_a)
    _write_corpus(cache_dir, "beta", files=files_b, embeddings=emb_b)

    a = corpus_store.load_corpus("alpha")
    b = corpus_store.load_corpus("beta")
    assert a and b

    cov = corpus_access.build_coverage(
        [a, b], mode_requested="semantic", mode_used="semantic",
        fell_back=False, trace_id="t-multi",
    )
    assert cov["corpus_size_files"] == 10  # 4 + 6
    assert cov["files_eligible_for_mode"] == 7  # 2 + 5 (embeddings)
    assert cov["files_skipped_unembedded"] == 3  # (4-2) + (6-5)


# ── Wire-shape tests on find/pack ──

def test_find_response_carries_coverage_keyword_full_corpus(cache_dir):
    files = [_file(f"f{i}.py", text="auth") for i in range(4)]
    _write_corpus(cache_dir, "alpha", files=files)

    out = find.handle({"query": "auth", "corpus_id": "alpha", "mode": "keyword"},
                      _admin_token())
    assert "coverage" in out
    cov = out["coverage"]
    assert cov["corpus_size_files"] == 4
    assert cov["files_eligible_for_mode"] == 4
    assert cov["ranked_with"] == "keyword"
    assert cov["ranked_with_lane"] == "live"
    assert cov["fallback_to_keyword"] is False
    assert cov["trace_id"] and isinstance(cov["trace_id"], str)


def test_find_response_coverage_when_semantic_fellback_marks_flag(cache_dir, monkeypatch):
    """mode=semantic but the corpus has no embeddings → engine runs keyword.
    Coverage: ranked_with='keyword', fallback_to_keyword=True."""
    files = [_file(f"f{i}.py", text="auth") for i in range(3)]
    _write_corpus(cache_dir, "alpha", files=files)  # no embeddings

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = find.handle({"query": "auth", "corpus_id": "alpha", "mode": "semantic"},
                      _admin_token())
    assert "coverage" in out
    cov = out["coverage"]
    assert cov["ranked_with"] == "keyword"
    assert cov["fallback_to_keyword"] is True


def test_find_coverage_lane_is_fallback_when_query_embed_fails(cache_dir, monkeypatch):
    """When the Mistral embed call raises, ranked_with_lane = 'fallback'.
    Distinct signal from 'live': caller can tell network/provider degradation
    apart from a corpus-shape mismatch."""
    files = [_file("a.py", text="auth")]
    embeddings = {"a.py": _unit_vec(4, 0)}
    _write_corpus(cache_dir, "alpha", files=files, embeddings=embeddings)

    def boom(q, **kw):
        raise embed.EmbedError("EMBED_HTTP", "Mistral 503")
    monkeypatch.setattr(embed, "embed_query", boom)

    out = find.handle({"query": "auth", "corpus_id": "alpha", "mode": "semantic"},
                      _admin_token())
    cov = out["coverage"]
    assert cov["ranked_with_lane"] == "fallback"
    assert cov["fallback_to_keyword"] is True


def test_find_coverage_semantic_partial_embeddings_reports_skipped(cache_dir, monkeypatch):
    """Semantic-eligible recall denominator: files with embeddings, not corpus size.

    This is the bench-blocking scenario from SPEC § 3.0.5:
    a corpus where 200 of 300 files have embeddings, queried with mode=semantic,
    must report eligible=200 + skipped=100 so the bench harness can compute
    eligible_recall correctly.
    """
    files = [_file(f"f{i}.py", text=f"file {i}") for i in range(10)]
    embeddings = {f"f{i}.py": _unit_vec(4, 0) for i in range(7)}  # 7 of 10
    _write_corpus(cache_dir, "alpha", files=files, embeddings=embeddings)

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = find.handle({"query": "x", "corpus_id": "alpha", "mode": "semantic"},
                      _admin_token())
    cov = out["coverage"]
    assert cov["corpus_size_files"] == 10
    assert cov["files_eligible_for_mode"] == 7
    assert cov["files_skipped_unembedded"] == 3
    assert cov["fallback_to_keyword"] is False
    assert cov["ranked_with"] == "semantic"


def test_pack_response_carries_coverage(cache_dir):
    files = [_file(f"f{i}.py", text="auth content") for i in range(3)]
    _write_corpus(cache_dir, "alpha", files=files)

    out = pack.handle({"query": "auth", "corpus_id": "alpha",
                       "mode": "keyword", "budget": 8000,
                       "response_format": "structured"},
                      _admin_token())
    assert "coverage" in out
    cov = out["coverage"]
    assert cov["corpus_size_files"] == 3
    assert cov["ranked_with"] == "keyword"


def test_pack_coverage_multi_corpus_aggregates(cache_dir, monkeypatch):
    files_a = [_file(f"a{i}.py") for i in range(2)]
    files_b = [_file(f"b{i}.py") for i in range(3)]
    emb_a = {f"a{i}.py": _unit_vec(4, 0) for i in range(2)}
    emb_b = {f"b{i}.py": _unit_vec(4, 0) for i in range(3)}
    _write_corpus(cache_dir, "alpha", files=files_a, embeddings=emb_a)
    _write_corpus(cache_dir, "beta", files=files_b, embeddings=emb_b)

    monkeypatch.setattr(embed, "embed_query", lambda q, **kw: _unit_vec(4, 0))

    out = pack.handle({"query": "x", "corpus_ids": ["alpha", "beta"],
                       "mode": "semantic", "budget": 8000,
                       "response_format": "structured"},
                      _admin_token())
    cov = out["coverage"]
    assert cov["corpus_size_files"] == 5
    assert cov["files_eligible_for_mode"] == 5
    assert cov["fallback_to_keyword"] is False


def test_find_coverage_trace_id_unique_per_request(cache_dir):
    """Two back-to-back calls must produce different trace_ids so callers
    can correlate logs without ambiguity."""
    files = [_file("a.py", text="auth")]
    _write_corpus(cache_dir, "alpha", files=files)

    out1 = find.handle({"query": "auth", "corpus_id": "alpha", "mode": "keyword"},
                       _admin_token())
    out2 = find.handle({"query": "auth", "corpus_id": "alpha", "mode": "keyword"},
                       _admin_token())
    assert out1["coverage"]["trace_id"] != out2["coverage"]["trace_id"]
