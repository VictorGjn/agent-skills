"""Patch spec.json files where `branch=main` doesn't match the repo's real
default branch.

Drives off a hard-coded mapping (queried via GitHub `default_branch` during
2026-05-06 bench launch). Updates both `branch` and `corpus_id` so the bench's
derived corpus_id matches what the indexer will produce on next run.

Idempotent: re-running over already-patched files is a no-op.
"""
from __future__ import annotations

import json
import re
import sys
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


def main():
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
    print(f"\nDone. patched={patched} skipped={skipped} (already correct)")


if __name__ == "__main__":
    main()
