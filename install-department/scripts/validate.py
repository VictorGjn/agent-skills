"""
Validate a draft Department Spec.

validate_spec(md_path, json_path) → list[str] of human-readable issues.
Empty list means the spec passes.

Rules enforced:
- All 7 canonical sections present and non-empty
- Section heading order matches the canonical template
- Manifest has matching department + non-empty tools[]
- Pipeline section has at least 2 stages (per `pipeline_stages` in manifest)
- Cadence has at least 1 entry
- Metrics has at least 1 entry
"""

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANONICAL_HEADINGS = (
    "## 1. Tools",
    "## 2. Roles",
    "## 3. Cadence",
    "## 4. Pipeline",
    "## 5. Taxonomy",
    "## 6. Automations",
    "## 7. Metrics",
)


def validate_spec(md_path: Path, json_path: Path) -> list[str]:
    issues: list[str] = []
    if not md_path.exists():
        issues.append(f"missing {md_path}")
        return issues
    if not json_path.exists():
        issues.append(f"missing {json_path}")
        return issues

    md = md_path.read_text(encoding="utf-8")
    issues += _check_headings(md)
    issues += _check_sections_nonempty(md)
    issues += _check_manifest(json_path)
    return issues


def _check_headings(md: str) -> list[str]:
    issues: list[str] = []
    last_idx = -1
    for canonical in CANONICAL_HEADINGS:
        idx = md.find(canonical)
        if idx < 0:
            issues.append(f"missing canonical heading: '{canonical}'")
            continue
        if idx <= last_idx:
            issues.append(f"heading order broken at: '{canonical}'")
        last_idx = idx
    return issues


def _check_sections_nonempty(md: str) -> list[str]:
    issues: list[str] = []
    sections = _split_sections(md)
    for canonical in CANONICAL_HEADINGS:
        body = sections.get(canonical, "").strip()
        if not body:
            issues.append(f"section '{canonical}' is empty")
            continue
        if "INCOMPLETE" in body.upper():
            issues.append(f"section '{canonical}' is marked INCOMPLETE")
    return issues


def _split_sections(md: str) -> dict[str, str]:
    """Carve the markdown into {heading: body} pairs."""
    out: dict[str, str] = {}
    pattern = re.compile(r"(## \d+\. \w+)\s*\n", re.MULTILINE)
    parts = pattern.split(md)
    if len(parts) < 3:
        return out
    headings = parts[1::2]
    bodies = parts[2::2]
    for h, b in zip(headings, bodies):
        out[h.strip()] = _strip_separator(b)
    return out


def _strip_separator(body: str) -> str:
    """Drop the trailing '---' divider that ends each canonical section."""
    return re.sub(r"\n---\s*$", "", body, flags=re.MULTILINE).strip()


def _check_manifest(json_path: Path) -> list[str]:
    issues: list[str] = []
    try:
        manifest = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        issues.append(f"manifest JSON is invalid: {e}")
        return issues

    if not manifest.get("department"):
        issues.append("manifest missing 'department'")
    if not manifest.get("tools"):
        issues.append("manifest 'tools' is empty — every department uses at least one tool")
    pipeline = manifest.get("pipeline_stages") or []
    if len(pipeline) < 2:
        issues.append(f"manifest 'pipeline_stages' has {len(pipeline)} stage(s) — at least 2 required")
    if not manifest.get("cadence"):
        issues.append("manifest 'cadence' is empty — at least 1 recurring item required")
    if not manifest.get("metrics"):
        issues.append("manifest 'metrics' is empty — at least 1 metric required")
    completeness = manifest.get("interview_completeness") or {}
    incomplete = [s for s, ok in completeness.items() if not ok]
    if incomplete:
        issues.append(f"interview marked incomplete for: {', '.join(incomplete)}")
    return issues


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: validate.py <department-spec.md> <department.json>", file=sys.stderr)
        return 2
    md_path = Path(sys.argv[1])
    json_path = Path(sys.argv[2])
    issues = validate_spec(md_path, json_path)
    if issues:
        print(f"{len(issues)} issue(s):")
        for i in issues:
            print(f"  - {i}")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
