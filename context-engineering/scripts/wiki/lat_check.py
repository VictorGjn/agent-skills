"""lat_check.py — broken-reference linter for the wiki + codebase.

Phase 2 of CE x lat.md interop. Thin CLI wrapper over ``audit.run_audit``
that combines a freshly-built ``code_index`` with the auditor's broken-ref
rule and exits non-zero on any broken reference (in ``--strict`` mode).

Usage::

    python scripts/wiki/lat_check.py --brain ./brain --code-root ./
    python scripts/wiki/lat_check.py --brain ./brain --code-root ./ --strict
    python scripts/wiki/lat_check.py --brain ./brain   # docs-only check (no code refs)

Exit codes:
    0 — no broken refs, OR broken refs found but ``--strict`` not set
    1 — broken refs found in ``--strict`` mode
    2 — invalid arguments / configuration

Designed for pre-commit hooks (``hooks/pre-commit.sample``) and CI runners
(``.github/workflows/ce-check.yml.sample``).

Per ``plan/PRD-latmd-integration.md`` Phase 2 acceptance criteria:

- Given a brain with 3 deliberately-broken refs (missing file, wrong
  symbol, wrong section), this exits 1 and ``audit/proposals.md`` lists
  all 3 broken refs by location + reason.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running both as a module (`python -m wiki.lat_check`) and as a
# script (`python scripts/wiki/lat_check.py`). Mirrors audit.py's pattern.
try:
    from .audit import run_audit, _print_audit_summary
    from .code_index import build_code_index, load_code_index
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from wiki.audit import run_audit, _print_audit_summary
    from wiki.code_index import build_code_index, load_code_index


def _print_broken_refs(broken_refs: list[dict]) -> None:
    """Pretty-print broken refs to stderr in a clear pre-commit-friendly form."""
    if not broken_refs:
        return
    print("", file=sys.stderr)
    print(f"lat_check: {len(broken_refs)} broken reference(s):", file=sys.stderr)
    for f in broken_refs:
        target = f.get("target", "")
        anchor = f.get("anchor")
        anchor_str = f"#{anchor}" if anchor else ""
        reason = f.get("reason", "unknown").replace("_", " ")
        print(
            f"  - in `{f['source_slug']}`: {f['ref']} "
            f"-> [{f['kind']}] {target}{anchor_str}  ({reason})",
            file=sys.stderr,
        )
    print(
        "\nFix these refs (or remove the strict flag) before committing. "
        "See audit/proposals.md for the full report.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="lat_check: broken-ref linter for CE wikis")
    p.add_argument("--brain", required=True, type=Path, help="Brain root with wiki/")
    p.add_argument(
        "--code-root",
        type=Path,
        default=None,
        help="Repository root to index for [[src/file#symbol]] resolution. "
        "If omitted, code refs are skipped (docs-only mode).",
    )
    p.add_argument(
        "--code-index",
        type=Path,
        default=None,
        help="Pre-built code_index.json to load instead of indexing live. "
        "Overrides --code-root when both supplied.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any broken reference. Without this flag, lat_check "
        "reports findings but exits 0 (advisory mode).",
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    code_index: dict | None = None
    if args.code_index is not None:
        code_index = load_code_index(args.code_index)
    elif args.code_root is not None:
        code_index = build_code_index(args.code_root)

    result = run_audit(args.brain, code_index=code_index)

    if not args.quiet:
        _print_audit_summary(result)

    broken = result.get("broken_refs", [])
    if broken and not args.quiet:
        _print_broken_refs(broken)

    if args.strict and broken:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
