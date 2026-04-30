"""
Synthesize the Department Spec.

synthesize(department, interview_answers, probe_results, out_dir, committed)
  → {"spec_md": Path, "spec_json": Path}

Writes:
  - department-spec.md  (markdown rendered from the canonical template)
  - department.json     (machine manifest)
  - ??-needs-verification.md (annex; only if incomplete sections exist)

The synthesizer is intentionally small. The methodology lives in the
interview answers; this script just renders + cross-checks claims.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

SECTIONS = ("tools", "roles", "cadence", "pipeline", "taxonomy", "automations", "metrics")


def synthesize(
    department: str,
    interview_answers: dict,
    probe_results: dict,
    out_dir: Path,
    committed: bool,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "department-spec.md"
    json_path = out_dir / "department.json"

    incomplete = {s: a for s, a in interview_answers.items() if a.get("incomplete")}
    md = _render_md(department, interview_answers, probe_results, incomplete)
    md_path.write_text(md, encoding="utf-8")

    manifest = _render_manifest(department, interview_answers, probe_results, committed)
    json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    out: dict[str, Path] = {"spec_md": md_path, "spec_json": json_path}

    if incomplete:
        annex_path = out_dir / "??-needs-verification.md"
        annex_path.write_text(_render_annex(incomplete), encoding="utf-8")
        out["annex"] = annex_path

    return out


def _render_md(department: str, answers: dict, probe_results: dict, incomplete: dict) -> str:
    head = answers.get("_head_name", "(head name not captured)")
    parts: list[str] = []
    parts.append(f"# {department.capitalize()} — Department Spec\n")
    parts.append(f"> **Head:** {head}")
    parts.append(f"> **Installed:** {_today()}")
    parts.append(f"> **Spec version:** 1.0")
    parts.append(f"> **Source skill:** install-department v0.1.0\n\n---\n")

    section_titles = {
        "tools": "1. Tools",
        "roles": "2. Roles",
        "cadence": "3. Cadence",
        "pipeline": "4. Pipeline",
        "taxonomy": "5. Taxonomy",
        "automations": "6. Automations",
        "metrics": "7. Metrics",
    }
    for s in SECTIONS:
        parts.append(f"\n## {section_titles[s]}\n")
        if s in incomplete:
            parts.append("*(INCOMPLETE — see verification annex)*\n")
            continue
        ans = answers[s]["answer"]
        if s == "tools":
            parts.append(_render_tools_section(ans, probe_results))
        else:
            parts.append(ans)
        parts.append("\n---\n")

    return "\n".join(parts)


def _render_tools_section(raw_answer: str, probe_results: dict) -> str:
    """Cross-check tool claims against probe inventory and render."""
    lines = [raw_answer, ""]
    lines.append("**Probed inventory cross-check:**\n")
    for tool, info in probe_results.items():
        if info.get("skipped"):
            lines.append(f"- `{tool}` — probe not implemented; claim is unverified")
        elif info.get("error"):
            lines.append(f"- `{tool}` — probe failed; claim is unverified")
        else:
            lines.append(f"- `{tool}` — {info['entities']} entities probed at install time")
    return "\n".join(lines)


def _render_manifest(department: str, answers: dict, probe_results: dict, committed: bool) -> dict:
    return {
        "department": department,
        "head": answers.get("_head_name"),
        "installed_at": _now() if committed else None,
        "draft_at": _now(),
        "committed": committed,
        "spec_version": "1.0",
        "tools": [
            {
                "name": tool,
                "entities_probed": info.get("entities", 0),
                "probe_skipped": info.get("skipped", False),
                "probe_error": info.get("error"),
            }
            for tool, info in probe_results.items()
        ],
        "interview_completeness": {
            s: not a.get("incomplete", False) for s, a in answers.items() if s in SECTIONS
        },
        "pipeline_stages": _extract_stage_names(answers.get("pipeline", {}).get("answer", "")),
        "cadence": _extract_cadence(answers.get("cadence", {}).get("answer", "")),
        "metrics": _extract_metric_names(answers.get("metrics", {}).get("answer", "")),
    }


def _extract_stage_names(text: str) -> list[str]:
    """Extract stage names from arrow-notation in the pipeline answer."""
    arrows = re.findall(r"([A-Za-z][\w &/-]{1,30})\s*(?:→|->)\s*", text)
    if arrows:
        tail_match = re.search(r"(?:→|->)\s*([A-Za-z][\w &/-]{1,30})\s*$", text.strip(), re.MULTILINE)
        if tail_match:
            arrows.append(tail_match.group(1))
    return [s.strip() for s in arrows]


def _extract_cadence(text: str) -> list[dict]:
    """Best-effort extraction of cadence items from a markdown table or bullets."""
    cadences: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(("-", "|", "*")):
            continue
        parts = [p.strip() for p in re.split(r"[|:—]", line.lstrip("-*| "), maxsplit=2)]
        if len(parts) >= 2 and parts[0] and parts[1]:
            cadences.append({"name": parts[0], "frequency": parts[1]})
    return cadences


def _extract_metric_names(text: str) -> list[dict]:
    """Best-effort extraction of metric names from bullets."""
    metrics: list[dict] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*| ").strip()
        if not line or len(line) < 3:
            continue
        match = re.match(r"^(?:###\s*)?([A-Za-z][\w \-/%()]{2,40})", line)
        if match:
            metrics.append({"name": match.group(1).strip()})
    return metrics


def _render_annex(incomplete: dict) -> str:
    parts = ["# Needs verification\n"]
    parts.append("These sections did not satisfy their required answer shape during the interview.")
    parts.append("Re-interview the dept head on each, then re-synthesize.\n")
    for section, info in incomplete.items():
        parts.append(f"## {section}")
        parts.append(f"**Reason:** {info.get('reason', 'shape not satisfied after 3 follow-ups')}")
        parts.append("**Last answer:**")
        parts.append("```")
        parts.append(info.get("answer", "(empty)"))
        parts.append("```\n")
    return "\n".join(parts)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
