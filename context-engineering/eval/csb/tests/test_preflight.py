"""Tests for preflight.py.

Most important here is the drift-catcher: preflight has its own copy of
INDEXABLE_EXTENSIONS (kept stdlib-only, no server-prod imports). If the
indexer's set ever changes without preflight following, the bench's
"reachable" reading desyncs from what the indexer actually fetches.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
CSB = HERE.parent.parent
sys.path.insert(0, str(CSB))


# ── Drift catcher: preflight matches the v1.2 indexer ─────────────────────────

def test_preflight_extensions_match_indexer():
    """preflight.INDEXABLE_EXTENSIONS must mirror the vendored indexer's set.

    The two are kept manually in sync (preflight is intentionally stdlib-only
    and doesn't import server-prod). When you add an extension to the indexer,
    add it to preflight.py too. This test fires if you forget.
    """
    import preflight

    # Read the indexer's set without importing server-prod (avoid sys.path games).
    indexer_path = CSB.parent.parent / "server-prod" / "_lib" / "vendor" / "index_github_repo.py"
    src = indexer_path.read_text(encoding="utf-8")
    # Pull out the literal set body between INDEXABLE_EXTENSIONS = { ... }
    import re
    m = re.search(r"INDEXABLE_EXTENSIONS\s*=\s*\{([^}]*)\}", src, re.S)
    assert m, "couldn't locate INDEXABLE_EXTENSIONS literal in indexer"
    body = m.group(1)
    # Extract every quoted token
    indexer_exts = set(re.findall(r"'([^']+)'", body))
    # Drop the special-case names that preflight tracks separately
    indexer_exts -= {"Dockerfile", "Makefile"}

    preflight_exts = set(preflight.INDEXABLE_EXTENSIONS)
    missing = indexer_exts - preflight_exts
    extra = preflight_exts - indexer_exts
    assert not missing, (
        f"preflight is missing {sorted(missing)} from INDEXABLE_EXTENSIONS — "
        "the indexer added these but preflight wasn't updated. Update "
        "preflight.py:INDEXABLE_EXTENSIONS to match."
    )
    assert not extra, (
        f"preflight has {sorted(extra)} that the indexer doesn't — preflight "
        "will report these as reachable but the indexer won't fetch them. "
        "Either add to indexer or drop from preflight."
    )


# ── is_indexable contract ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("src/foo.py", True),
    ("src/foo.c", True),       # P2.1
    ("src/foo.cpp", True),     # P2.1
    ("src/foo.cs", True),      # P2.1
    ("templates/x.j2", True),  # P2.1
    ("Dockerfile", True),
    ("Makefile", True),
    ("src/foo.png", False),
    ("src/foo.unknown", False),
])
def test_is_indexable_matches_extension_logic(path, expected):
    import preflight
    assert preflight.is_indexable(path) is expected


# ── End-to-end: synthetic task tree ───────────────────────────────────────────

def test_preflight_reports_zero_unreachable_when_all_gt_indexable(tmp_path, monkeypatch, capsys):
    """Happy path: every task has fully reachable GT."""
    import preflight

    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for i, ext in enumerate([".py", ".c", ".cs", ".scala"]):
        d = tasks / f"task-{i}"
        d.mkdir()
        (d / "spec.json").write_text(json.dumps({
            "repo": "owner/repo", "branch": "main", "corpus_id": "gh-owner-repo-main",
        }), encoding="utf-8")
        (d / "ground_truth.json").write_text(json.dumps([f"src/file{ext}"]), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "preflight.py",
        "--tasks-dir", str(tasks),
    ])
    rc = preflight.main()
    out = capsys.readouterr().out
    assert "fully unreachable:  0" in out
    assert rc == 0


def test_preflight_fails_on_fully_unreachable_task(tmp_path, monkeypatch, capsys):
    """One task with all GT in non-indexable extensions → exit 1 by default."""
    import preflight

    tasks = tmp_path / "tasks"
    tasks.mkdir()
    d = tasks / "bad"
    d.mkdir()
    (d / "spec.json").write_text(json.dumps({
        "repo": "owner/repo", "branch": "main", "corpus_id": "gh-owner-repo-main",
    }), encoding="utf-8")
    # All GT in extensions outside INDEXABLE_EXTENSIONS
    (d / "ground_truth.json").write_text(json.dumps([
        "src/file.unknown", "x.weird"
    ]), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["preflight.py", "--tasks-dir", str(tasks)])
    rc = preflight.main()
    out = capsys.readouterr().out
    assert "fully unreachable:  1" in out
    assert rc == 1


def test_preflight_allow_unreachable_returns_zero(tmp_path, monkeypatch, capsys):
    """--allow-unreachable downgrades fully-unreachable from blocker to warning."""
    import preflight

    tasks = tmp_path / "tasks"
    tasks.mkdir()
    d = tasks / "bad"
    d.mkdir()
    (d / "spec.json").write_text(json.dumps({
        "repo": "owner/repo", "branch": "main", "corpus_id": "gh-owner-repo-main",
    }), encoding="utf-8")
    (d / "ground_truth.json").write_text(json.dumps(["src/file.unknown"]), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "preflight.py", "--tasks-dir", str(tasks), "--allow-unreachable",
    ])
    rc = preflight.main()
    assert rc == 0
