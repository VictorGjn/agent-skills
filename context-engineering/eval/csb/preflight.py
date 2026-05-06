"""Pre-flight validity check for CSB bench launches.

Walks `eval/csb/converted-tasks/` and reports issues that would silently
degrade or invalidate a bench run BEFORE any prod call:

1. **Extension reachability**: for each task, count GT files whose extension
   is in the indexer's INDEXABLE_EXTENSIONS set. Tasks with 0 reachable GT
   files are guaranteed-zero across every IR config.
2. **Branch existence**: for each unique repo+branch, query GitHub to confirm
   the branch exists. Catches stale spec.json entries before they cost a
   90s timeout in the bench loop.
3. **Corpus_id coherence**: derived corpus_id from spec.json's branch
   matches what the indexer will produce.

Exit nonzero if any task is fully unreachable OR any branch is missing,
unless `--allow-unreachable` is passed (e.g. when sg-evals/* tasks are
known-skipped pending P4 GH App migration).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

# Mirror of `_lib/vendor/index_github_repo.py:INDEXABLE_EXTENSIONS` after P2.1.
# Kept in sync manually — preflight is intentionally cheap and stdlib-only.
# When the indexer set changes, update here too. The test
# `test_preflight_extensions_match_indexer` (in tests/) catches drift.
INDEXABLE_EXTENSIONS = {
    '.md', '.mdx', '.txt', '.rst',
    '.ts', '.tsx', '.js', '.jsx', '.mjs',
    '.py', '.pyi',
    '.rs', '.go', '.java', '.kt', '.swift', '.rb',
    '.vue', '.svelte', '.astro',
    '.yaml', '.yml', '.json', '.toml',
    '.css', '.scss',
    '.sh', '.bash',
    '.sql',
    '.graphql', '.gql',
    '.proto',
    '.env.example', '.env.sample',
    '.c', '.cc', '.cpp', '.h', '.hpp',
    '.cs',
    '.scala',
    '.j2',
}
SPECIAL_NAMES = {'Dockerfile', 'Makefile', '.gitignore', '.env.example'}


def is_indexable(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in INDEXABLE_EXTENSIONS or p.name in SPECIAL_NAMES


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


def github_branch_exists(repo: str, branch: str, token: str | None) -> bool:
    """HEAD-style check: GET /repos/<o>/<n>/branches/<b>; 200 = exists, 404 = missing."""
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "csb-preflight"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return False
        raise
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tasks-dir", default="eval/csb/converted-tasks", type=Path)
    p.add_argument("--check-branches", action="store_true",
                   help="Query GitHub to confirm each spec.json's branch exists. "
                        "Slow (1 API call per unique repo+branch); skip for fast local checks.")
    p.add_argument("--gh-token-env", default="GITHUB_TOKEN")
    p.add_argument("--allow-unreachable", action="store_true",
                   help="Don't fail when tasks have 0 reachable GT files (use when "
                        "running a partial bench that intentionally skips some tasks).")
    p.add_argument("--allow-missing-branches", action="store_true",
                   help="Don't fail when GitHub branch lookups return 404. Useful "
                        "when v1.2 indexer auto-resolves the right branch at index time.")
    args = p.parse_args()

    if not args.tasks_dir.is_dir():
        print(f"ERROR: {args.tasks_dir} not found", file=sys.stderr)
        return 1

    fully_unreachable: list[tuple[str, list[str]]] = []
    partial_reach: list[tuple[str, int, int]] = []
    fully_reachable = 0
    repo_branches: dict[tuple[str, str], list[str]] = defaultdict(list)

    for task_dir in sorted(args.tasks_dir.iterdir()):
        spec_path = task_dir / "spec.json"
        gt_path = task_dir / "ground_truth.json"
        if not (spec_path.is_file() and gt_path.is_file()):
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        repo = spec.get("repo", "")
        branch = spec.get("branch") or "main"
        if repo:
            repo_branches[(repo, branch)].append(task_dir.name)

        # Extension reachability
        if not isinstance(gt, list):
            continue
        reachable = [f for f in gt if is_indexable(f)]
        n_total = len(gt)
        n_reach = len(reachable)
        if n_total == 0:
            continue
        if n_reach == 0:
            fully_unreachable.append((task_dir.name, gt))
        elif n_reach < n_total:
            partial_reach.append((task_dir.name, n_reach, n_total))
        else:
            fully_reachable += 1

        # Corpus_id coherence
        if "/" in repo:
            owner, name = repo.split("/", 1)
            expected = slug(f"gh-{owner}-{name}-{branch}")
            actual = spec.get("corpus_id", "")
            if expected != actual:
                print(f"[X] {task_dir.name}: corpus_id={actual!r}, expected={expected!r}")

    print()
    print("# Pre-flight report")
    print()
    print(f"## Extension reachability ({len(fully_unreachable) + len(partial_reach) + fully_reachable} tasks scored)")
    print()
    print(f"  fully reachable:    {fully_reachable}")
    print(f"  partial reachable:  {len(partial_reach)}")
    print(f"  fully unreachable:  {len(fully_unreachable)}")
    print()
    if fully_unreachable:
        print("### Tasks with 0 reachable GT files (will score 0 across all configs):")
        for tid, gt in fully_unreachable[:20]:
            exts = sorted({Path(f).suffix.lower() for f in gt})
            print(f"  {tid}: extensions={exts}")
        if len(fully_unreachable) > 20:
            print(f"  ... and {len(fully_unreachable) - 20} more")
        print()
    if partial_reach:
        print("### Tasks with partial GT reachability (recall capped):")
        for tid, n, total in partial_reach[:10]:
            print(f"  {tid}: {n}/{total} reachable")
        if len(partial_reach) > 10:
            print(f"  ... and {len(partial_reach) - 10} more")
        print()

    failures = 0
    if fully_unreachable and not args.allow_unreachable:
        failures += 1

    if args.check_branches:
        gh_token = os.environ.get(args.gh_token_env)
        if not gh_token:
            print("WARN: no GITHUB_TOKEN/GH_TOKEN; branch lookups may rate-limit", file=sys.stderr)
        print(f"## Branch existence check ({len(repo_branches)} unique repo+branch combos)")
        print()
        missing: list[tuple[str, str, list[str]]] = []
        for (repo, branch), task_ids in sorted(repo_branches.items()):
            if not github_branch_exists(repo, branch, gh_token):
                missing.append((repo, branch, task_ids))
                print(f"  [X] {repo}@{branch}  ({len(task_ids)} task(s))")
            else:
                print(f"  [ok] {repo}@{branch}  ({len(task_ids)} task(s))")
        print()
        if missing:
            print(f"{len(missing)} repo+branch combo(s) missing on GitHub.")
            print("v1.2 indexer auto-resolves default branch on 404; safe to proceed if those "
                  "repos use a different default branch than spec.json claims.")
            if not args.allow_missing_branches:
                failures += 1

    if failures:
        print(f"\nPRE-FLIGHT FAILED ({failures} block(s)). Pass --allow-unreachable / "
              "--allow-missing-branches to override.")
        return 1
    print("\nPre-flight OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
