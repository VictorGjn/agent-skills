"""Phase P2 tests — v1.2 indexer rework.

Covers:
- INDEXABLE_EXTENSIONS broadened: .c/.cc/.cpp/.h/.hpp/.cs/.scala/.j2 land in
  the set so 11/70 v1.0-unreachable CSB tasks become reachable.
- fetch_tree extension_priority='source-first' (default): code files appear
  before docs when corpus exceeds max_files. 'docs-first' preserves legacy
  behavior. Invalid value raises ValueError.
- fetch_tree max_files split: MAX_FILES_TO_FETCH_SYNC (2000) is the default;
  MAX_FILES_TO_FETCH_ASYNC (10000) used by the async indexer.
- fetch_tree auto_resolve_branch: when caller-passed branch returns 404,
  resolve_default_branch is called and tree is refetched against the real
  default. auto_resolve_branch=False preserves strict 404 raise.
- encode_path / github_get_raw URL encoding: paths with spaces fetch cleanly
  (no InvalidURL).

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase_p2_indexer.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

# Import the vendored indexer the same way async_indexer / production code does.
sys.path.insert(0, str(_HERE.parent.parent / "_lib" / "vendor"))
import index_github_repo as gh  # type: ignore — vendored


# ── P2.1: INDEXABLE_EXTENSIONS broadened ──────────────────────────────────────

@pytest.mark.parametrize("ext", [".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".scala", ".j2"])
def test_indexable_extensions_includes_v1_2_additions(ext):
    """The 8 new extensions added in P2.1 must be in the indexable set."""
    assert ext in gh.INDEXABLE_EXTENSIONS, (
        f"{ext!r} missing from INDEXABLE_EXTENSIONS — 11/70 CSB tasks rely on these"
    )


def test_should_index_accepts_c_and_cs_files():
    """Walk through should_index — the should_index() function is the gate
    `fetch_tree` uses to filter blob items into candidates."""
    assert gh.should_index("src/foo.c") is True
    assert gh.should_index("src/foo.cpp") is True
    assert gh.should_index("src/foo.cs") is True
    assert gh.should_index("templates/jail.j2") is True
    # Negative control: still skips known-bad patterns
    assert gh.should_index("node_modules/foo.c") is False
    assert gh.should_index("src/foo.png") is False


# ── P2.2: extension_priority parameter ────────────────────────────────────────

def _stub_tree(paths_with_size):
    """Build a fake GitHub tree response for tree-fetch monkeypatch."""
    return {
        "tree": [
            {"type": "blob", "path": p, "size": s}
            for p, s in paths_with_size
        ]
    }


def test_fetch_tree_source_first_keeps_code_when_capped(monkeypatch):
    """When candidates exceed max_files, source-first (default) drops docs
    not source. v1.0 bug: .md sorted first → README-only corpora on big repos."""
    fake = _stub_tree([
        ("z_doc1.md", 100),  # alphabetically last but tier 0 in legacy sort
        ("z_doc2.md", 100),
        ("a_src1.py", 100),  # alphabetically first but tier 1 in legacy sort
        ("a_src2.go", 100),
        ("b_src.rs", 100),
    ])
    monkeypatch.setattr(gh, "github_get", lambda url, token=None: fake)
    cands = gh.fetch_tree("o", "r", "main", max_files=3)  # default source-first
    paths = [c["path"] for c in cands]
    # Source files take the first 3 slots regardless of alphabetic order
    assert all(p.endswith((".py", ".go", ".rs")) for p in paths), (
        f"source-first should retain code files, got {paths}"
    )


def test_fetch_tree_docs_first_preserves_legacy_priority(monkeypatch):
    """docs-first reproduces v1.0 behavior — docs appear before source."""
    fake = _stub_tree([
        ("a_src.py", 100),
        ("b_doc.md", 100),
        ("c_src.go", 100),
    ])
    monkeypatch.setattr(gh, "github_get", lambda url, token=None: fake)
    cands = gh.fetch_tree("o", "r", "main", max_files=1, extension_priority="docs-first")
    assert cands[0]["path"].endswith(".md")


def test_fetch_tree_invalid_priority_raises():
    """Bogus priority value raises early instead of silently miscategorizing."""
    with pytest.raises(ValueError, match="extension_priority"):
        # Don't even need a real network call — sort path is gated on
        # candidates > max_files. Force that.
        import unittest.mock as mock
        with mock.patch.object(gh, "github_get", return_value=_stub_tree([("a.py", 1), ("b.py", 1)])):
            gh.fetch_tree("o", "r", "main", max_files=1, extension_priority="bogus")


# ── P2.3: max_files split (sync vs async caps) ────────────────────────────────

def test_max_files_constants_exist():
    assert gh.MAX_FILES_TO_FETCH_SYNC == 2000
    assert gh.MAX_FILES_TO_FETCH_ASYNC == 10_000
    # Legacy alias preserved for any external callers
    assert gh.MAX_FILES_TO_FETCH == gh.MAX_FILES_TO_FETCH_SYNC


def test_fetch_tree_default_max_files_is_sync_cap(monkeypatch):
    """Default invocation caps at MAX_FILES_TO_FETCH_SYNC (2000)."""
    # 2050 candidates → cap to 2000 with default args
    paths = [(f"src/file{i}.py", 100) for i in range(2050)]
    monkeypatch.setattr(gh, "github_get", lambda url, token=None: _stub_tree(paths))
    cands = gh.fetch_tree("o", "r", "main")
    assert len(cands) == 2000


def test_fetch_tree_explicit_async_cap_accepts_more(monkeypatch):
    """Passing max_files=MAX_FILES_TO_FETCH_ASYNC accepts up to 10k."""
    paths = [(f"src/file{i}.py", 100) for i in range(5000)]
    monkeypatch.setattr(gh, "github_get", lambda url, token=None: _stub_tree(paths))
    cands = gh.fetch_tree("o", "r", "main", max_files=gh.MAX_FILES_TO_FETCH_ASYNC)
    assert len(cands) == 5000  # under cap, all retained


# ── P2.4: auto_resolve_branch on 404 ──────────────────────────────────────────

class _Fake404(urllib.error.HTTPError):
    def __init__(self):
        super().__init__("https://api.github.com/x", 404, "Not Found", {}, None)


def test_fetch_tree_auto_resolves_branch_on_404(monkeypatch):
    """Initial branch returns 404 → auto-resolves to default_branch and retries."""
    calls = []

    def fake_github_get(url, token=None):
        calls.append(url)
        if "trees/main" in url:
            raise _Fake404()
        if "/repos/o/r" in url and "trees/" not in url:
            return {"default_branch": "master"}
        if "trees/master" in url:
            return _stub_tree([("a.py", 100)])
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(gh, "github_get", fake_github_get)
    cands = gh.fetch_tree("o", "r", "main")
    assert len(cands) == 1
    # Confirm the retry path actually fired
    assert any("trees/main" in u for u in calls)
    assert any("trees/master" in u for u in calls)


def test_fetch_tree_auto_resolve_disabled_propagates_404(monkeypatch):
    """auto_resolve_branch=False raises the original 404 instead of recovering."""
    monkeypatch.setattr(gh, "github_get", lambda url, token=None: (_ for _ in ()).throw(_Fake404()))
    with pytest.raises(urllib.error.HTTPError):
        gh.fetch_tree("o", "r", "main", auto_resolve_branch=False)


def test_fetch_tree_auto_resolve_no_recovery_when_default_matches(monkeypatch):
    """If default_branch == requested branch, the 404 was real — re-raise."""
    def fake_github_get(url, token=None):
        if "trees/main" in url:
            raise _Fake404()
        if "/repos/o/r" in url:
            return {"default_branch": "main"}  # same as requested
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(gh, "github_get", fake_github_get)
    with pytest.raises(urllib.error.HTTPError):
        gh.fetch_tree("o", "r", "main")


def test_resolve_default_branch_returns_none_on_failure(monkeypatch):
    """Lookup failure (rate-limited, repo missing) → None, not raise."""
    monkeypatch.setattr(gh, "github_get", lambda url, token=None: (_ for _ in ()).throw(_Fake404()))
    assert gh.resolve_default_branch("o", "missing") is None


# ── P2.5: URL-encode paths with spaces ────────────────────────────────────────

def test_encode_path_preserves_slashes_and_encodes_spaces():
    """Path components are encoded but / stays as-is so it's still a valid URL."""
    encoded = gh.encode_path("a/b with space/c.md")
    assert encoded == "a/b%20with%20space/c.md"


def test_encode_path_handles_unicode():
    encoded = gh.encode_path("a/dépôt/b.md")
    assert "%" in encoded
    assert "/" in encoded  # slashes preserved


def test_encode_path_no_change_for_safe_chars():
    """Already-safe paths come back unchanged."""
    assert gh.encode_path("a/b/c.py") == "a/b/c.py"
