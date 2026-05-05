"""Convert CodeScaleBench task layouts to the spec.json + ground_truth.json
shape that run_ir_bench.py consumes.

CSB task dir contains:
    task.toml              — task metadata (id, repo, pre_fix_rev)
    instruction.md         — agent prompt; we use as the IR query
    tests/ground_truth.json — verifier ground truth in one of three shapes:
        1. {"files": [...]}            — fix/feature/debug/test
        2. {"expected_files": [...]}   — refactor/secure
        3. {"required_findings": [...]} — understand/document/design
           (verifier-pattern style; NOT directly IR-scorable, skipped)

Output spec layout (per task), written to --out-dir/<task_id>/:
    spec.json:
        {
          "description": str,                # from instruction.md (head ~2KB)
          "repo": "owner/name",
          "branch": str | null,              # null = use HEAD/main, server slugifies
          "commit_sha": str | null,          # task.toml's pre_fix_rev (informational)
          "corpus_id": str,                  # gh-{owner}-{name}-main slugified
        }
    ground_truth.json:
        ["path/to/file1", "path/to/file2", ...]

Usage:
    python csb_to_spec.py \\
        --csb-root /path/to/codescalebench \\
        --categories csb_sdlc_fix csb_sdlc_feature \\
        --out-dir ./csb-tasks-converted \\
        [--limit 5]

Use --list-categories to see which CSB SDLC dirs map to which shape.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover — fallback for older
    import tomli as tomllib  # type: ignore


# CSB SDLC categories that ship IR-scorable ground-truth (shape 1 or 2).
IR_SCORABLE_CATEGORIES = [
    "csb_sdlc_debug",
    "csb_sdlc_feature",
    "csb_sdlc_fix",
    "csb_sdlc_refactor",
    "csb_sdlc_secure",
    "csb_sdlc_test",
]

# CSB SDLC categories with verifier-pattern ground-truth (shape 3) — skipped.
NOT_IR_SCORABLE = [
    "csb_sdlc_understand",
    "csb_sdlc_document",
    "csb_sdlc_design",
]

INSTRUCTION_MAX_CHARS = 4000  # match server's MAX_QUERY_CHARS guard


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


def _derive_corpus_id(repo: str, branch: str = "main") -> str:
    if "/" not in repo:
        return _slugify(repo)
    owner, name = repo.split("/", 1)
    return _slugify(f"gh-{owner}-{name}-{branch}")


def _extract_files_from_ground_truth(gt_obj: dict) -> list[str] | None:
    """Return a list of paths or None if the shape isn't IR-scorable.

    CSB tasks ship four file-list shapes across SDLC categories:
    - "files"          — fix/feature/test (canonical)
    - "expected_files" — refactor/secure
    - "buggy_files"    — debug (linux SDLC pins via this key)
    - none of the above (verifier-pattern style: required_findings) — skipped
    """
    for key in ("files", "expected_files", "buggy_files"):
        v = gt_obj.get(key)
        if isinstance(v, list):
            paths = [p for p in v if isinstance(p, str)]
            if paths:
                return paths
    return None


_GH_URL_RE = re.compile(
    # owner / repo where repo greedy-matches everything up to .git or whitespace
    # (incl. -- separator used by sg-evals snapshots e.g. aspnetcore--87525573).
    r"https://github\.com/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]+?)(?:\.git|\s|$)"
)

# Match `git clone` lines that pin a branch/tag/ref. Both the long form
# (`--branch v5.6.7`) and short form (`-b master`) appear in CSB Dockerfiles.
_BRANCH_RE = re.compile(r"git\s+clone\b[^\n]*?(?:--branch|-b)\s+([A-Za-z0-9._/-]+)")


def _extract_from_dockerfile(task_dir: Path) -> tuple[str | None, str | None]:
    """Return (repo, branch) extracted from the task's Dockerfile.

    Most CSB tasks store the canonical repo as `sg-evals/<repo>--<sha>` in
    a `git clone` line in environment/Dockerfile (a snapshot fork pinned to
    the task's pre_fix_rev). Some tasks (linux-* SDLC) pin a specific tag
    via `--branch v5.6.7` — without extracting that, the converter would
    hardcode branch=main and ce_index_github_repo would 404 on the snapshot
    repo's actual default ref. Returns the FIRST match for both.
    """
    for name in ("Dockerfile", "Dockerfile.sg_only", "Dockerfile.artifact_only"):
        p = task_dir / "environment" / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        repo_m = _GH_URL_RE.search(text)
        repo = f"{repo_m.group(1)}/{repo_m.group(2)}" if repo_m else None
        branch_m = _BRANCH_RE.search(text)
        branch = branch_m.group(1) if branch_m else None
        if repo or branch:
            return repo, branch
    return None, None


def _read_instruction(task_dir: Path) -> str:
    """Pick the most-context instruction file. Fall back to instruction_mcp.md."""
    for name in ("instruction.md", "instruction_mcp.md"):
        p = task_dir / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                return text[:INSTRUCTION_MAX_CHARS]
    return ""


def convert_one(task_dir: Path, out_dir: Path) -> dict | None:
    """Convert one CSB task. Returns the spec dict (also written to disk),
    or None if not IR-scorable / missing pieces."""
    task_toml = task_dir / "task.toml"
    gt_path = task_dir / "tests" / "ground_truth.json"
    if not task_toml.exists() or not gt_path.exists():
        return None

    with open(task_toml, "rb") as f:
        toml = tomllib.load(f)
    task_meta = toml.get("task", {}) or {}
    repo = task_meta.get("repo")

    # task.toml repo is often a bare name (e.g. "aspnetcore"); canonical
    # owner/name + branch (pinned tag like v5.6.7) live in the Dockerfile.
    dockerfile_repo, dockerfile_branch = _extract_from_dockerfile(task_dir)
    if not repo or "/" not in repo:
        repo = dockerfile_repo
    if not repo or "/" not in repo:
        return None

    description = _read_instruction(task_dir)
    if not description:
        return None

    try:
        gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    paths = _extract_files_from_ground_truth(gt_obj)
    if not paths:
        return None

    # Prefer the branch the Dockerfile pinned (e.g. linux v5.6.7 tasks).
    # Without this, ce_index_github_repo would default to `main` against a
    # snapshot fork whose only ref is the pinned tag → SOURCE_NOT_FOUND.
    branch = dockerfile_branch or "main"
    corpus_id = _derive_corpus_id(repo, branch)
    spec = {
        "description": description,
        "repo": repo,
        "branch": branch,
        "commit_sha": task_meta.get("pre_fix_rev"),
        "corpus_id": corpus_id,
        "csb_task_id": task_meta.get("id") or task_dir.name,
    }

    target = out_dir / task_dir.name
    target.mkdir(parents=True, exist_ok=True)
    (target / "spec.json").write_text(
        json.dumps(spec, indent=2), encoding="utf-8")
    (target / "ground_truth.json").write_text(
        json.dumps(paths, indent=2), encoding="utf-8")
    return spec


def main() -> int:
    p = argparse.ArgumentParser(description="Convert CSB tasks → run_ir_bench spec format.")
    p.add_argument("--csb-root", type=Path, required=True,
                   help="Root of a CSB checkout (contains benchmarks/csb_sdlc_*).")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="Where to write per-task spec.json + ground_truth.json subdirs.")
    p.add_argument("--categories", nargs="+",
                   default=IR_SCORABLE_CATEGORIES,
                   help=f"CSB SDLC categories to convert (default: all IR-scorable: "
                        f"{', '.join(IR_SCORABLE_CATEGORIES)}).")
    p.add_argument("--limit", type=int, default=None,
                   help="Convert only the first N tasks per category (for smoke runs).")
    p.add_argument("--list-categories", action="store_true",
                   help="Print which CSB dirs map to which ground-truth shape and exit.")
    args = p.parse_args()

    if args.list_categories:
        print("IR-scorable (file-list ground-truth):")
        for c in IR_SCORABLE_CATEGORIES:
            print(f"  {c}")
        print("\nNOT IR-scorable (pattern-match verifier ground-truth):")
        for c in NOT_IR_SCORABLE:
            print(f"  {c}")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    benchmarks = args.csb_root / "benchmarks"
    if not benchmarks.exists():
        print(f"no benchmarks/ under {args.csb_root}", file=sys.stderr)
        return 1

    converted = 0
    skipped: dict[str, int] = {}
    for cat in args.categories:
        cat_dir = benchmarks / cat
        if not cat_dir.exists():
            print(f"# missing category dir: {cat}", file=sys.stderr)
            continue
        tasks = sorted([d for d in cat_dir.iterdir() if d.is_dir()])
        if args.limit:
            tasks = tasks[: args.limit]
        for t in tasks:
            spec = convert_one(t, args.out_dir)
            if spec is None:
                skipped[cat] = skipped.get(cat, 0) + 1
                continue
            converted += 1

    print(f"\nConverted {converted} tasks to {args.out_dir}", file=sys.stderr)
    if skipped:
        for cat, n in skipped.items():
            print(f"  skipped {n} from {cat} (missing pieces / non-IR shape)",
                  file=sys.stderr)

    # Print unique repos so caller can pre-index them
    unique_repos: set[str] = set()
    for sub in args.out_dir.iterdir():
        if not sub.is_dir():
            continue
        spec_path = sub / "spec.json"
        if spec_path.exists():
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                unique_repos.add(spec["repo"])
            except (json.JSONDecodeError, KeyError):
                pass
    print(f"\nUnique repos to index ({len(unique_repos)}):", file=sys.stderr)
    for r in sorted(unique_repos):
        print(f"  {r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
