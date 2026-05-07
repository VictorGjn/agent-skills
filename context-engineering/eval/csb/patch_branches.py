"""Patch spec.json files where `branch=main` doesn't match the repo's real
default branch.

Drives off a hard-coded mapping (queried via GitHub `default_branch` during
2026-05-06 bench launch). Updates both `branch` and `corpus_id` so the bench's
derived corpus_id matches what the indexer will produce on next run.

Idempotent: re-running over already-patched files is a no-op.

`--verify-live` re-queries GitHub for the current default branch of each
mapped repo and warns if the static map has drifted (e.g. upstream renamed
their default branch). No spec.jsons are modified in this mode — it's a
read-only audit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Confirmed via GitHub /repos/<o>/<n>.default_branch on 2026-05-06.
# Only includes repos where the real default differs from 'main'.
DEFAULT_BRANCH_MAP = {
    "apache/beam": "master",
    "NodeBB/NodeBB": "master",
    "ansible/ansible": "devel",
    "apache/flink": "master",
    "apache/kafka": "trunk",
    "curl/curl": "master",
    "element-hq/element-web": "develop",
    "kubernetes/kubernetes": "master",
    "pingcap/tidb": "master",
    "postgres/postgres": "master",
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


def fetch_live_default_branch(repo: str, gh_token: str | None) -> str | None:
    """GET /repos/<owner>/<name>; return `default_branch` or None on failure."""
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "csb-patch-branches"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8")).get("default_branch")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None


def verify_live(gh_token: str | None, allow_unreachable: bool = False) -> int:
    """Compare DEFAULT_BRANCH_MAP against GitHub's current state.

    Returns nonzero exit code on drift OR on unreachable lookups (the
    latter unless `allow_unreachable=True`). Codex P2 fix on PR #52: an
    unreachable lookup means the audit didn't actually verify that repo,
    so passing 0 with unverified state was a false-green. CI pre-checks
    using `--verify-live` should treat that as a hard fail by default.

    The `allow_unreachable` escape hatch is for unauthenticated runs where
    rate-limit hits are expected and acceptable (e.g. local sanity checks
    where the operator will re-run with a token).
    """
    drift = []
    unreachable = []
    for repo, mapped in sorted(DEFAULT_BRANCH_MAP.items()):
        live = fetch_live_default_branch(repo, gh_token)
        if live is None:
            unreachable.append(repo)
            print(f"[!] {repo}: GitHub lookup failed (rate limit? token scope?)")
            continue
        if live != mapped:
            drift.append((repo, mapped, live))
            print(f"[X] {repo}: map says {mapped!r}, GitHub says {live!r}")
        else:
            print(f"[ok] {repo}: {mapped}")
    if drift:
        print(f"\nDrift detected on {len(drift)} repo(s). Update DEFAULT_BRANCH_MAP and re-run patch.")
    if unreachable:
        print(f"\n{len(unreachable)} repo(s) unreachable; verify auth or rate limits.")
        if not allow_unreachable:
            print("Treating unreachable lookups as audit failure. Pass --allow-unreachable "
                  "to override (e.g. unauthenticated local runs).")
    if drift:
        return 1
    if unreachable and not allow_unreachable:
        return 1
    return 0


def patch_specs() -> tuple[int, int]:
    root = Path("eval/csb/converted-tasks")
    if not root.is_dir():
        print(f"ERROR: {root} not found; run from context-engineering/", file=sys.stderr)
        sys.exit(1)

    patched = 0
    skipped = 0
    for task_dir in sorted(root.iterdir()):
        spec_path = task_dir / "spec.json"
        if not spec_path.is_file():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        repo = spec.get("repo", "")
        if repo not in DEFAULT_BRANCH_MAP:
            continue
        new_branch = DEFAULT_BRANCH_MAP[repo]
        if spec.get("branch") == new_branch:
            skipped += 1
            continue
        owner, name = repo.split("/", 1)
        new_cid = slug(f"gh-{owner}-{name}-{new_branch}")
        spec["branch"] = new_branch
        spec["corpus_id"] = new_cid
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        patched += 1
        print(f"[+] {task_dir.name}: branch -> {new_branch}, corpus_id -> {new_cid}")
    return patched, skipped


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--verify-live", action="store_true",
                   help="Audit DEFAULT_BRANCH_MAP against GitHub's current "
                        "default_branch for each mapped repo. Read-only; no "
                        "spec.jsons modified. Nonzero exit on drift OR "
                        "unreachable lookup (override the latter with "
                        "--allow-unreachable).")
    p.add_argument("--allow-unreachable", action="store_true",
                   help="When --verify-live, downgrade unreachable lookups "
                        "(rate-limit / no-token) to warnings instead of audit "
                        "failures. Use only when the operator accepts the "
                        "audit isn't actually verifying those repos.")
    args = p.parse_args()

    if args.verify_live:
        gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not gh_token:
            print("WARN: no GITHUB_TOKEN/GH_TOKEN; live lookups may be rate-limited.", file=sys.stderr)
        return verify_live(gh_token, allow_unreachable=args.allow_unreachable)

    patched, skipped = patch_specs()
    print(f"\nDone. patched={patched} skipped={skipped} (already correct)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
