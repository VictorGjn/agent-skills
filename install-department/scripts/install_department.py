"""
install-department: orchestrate the 5-phase install for a department.

Usage:
  python -m scripts.install_department --department <function>
  python -m scripts.install_department --department product --resume

Phases: connect → probe → interview → synthesize → validate.
State is persisted to cache/state/<function>.json so abandoned runs resume.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
STATE_DIR = CACHE / "state"
PROBE_DIR = CACHE / "probe"
BRAIN_DEFAULT = Path.home() / ".anabasis" / "brain"

PHASES = ("connect", "probe", "interview", "synthesize", "validate")


def state_path(department: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{department}.json"


def load_state(department: str) -> dict:
    p = state_path(department)
    if not p.exists():
        return {"department": department, "phase": "connect", "started_at": _now()}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    p = state_path(state["department"])
    state["updated_at"] = _now()
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def phase_connect(state: dict) -> dict:
    """Phase 1: verify Pipedream tools are connected."""
    print("\n— Phase 1: Connect —")
    from probe_tool import list_connected_tools

    tools = list_connected_tools()
    if not tools:
        print("ERROR: no tools connected via Pipedream / Syroco Connect.")
        print("       Authorize at least one tool, then re-run.")
        sys.exit(2)

    print(f"  Found {len(tools)} connected tool(s):")
    for t in tools:
        print(f"    - {t}")
    if len(tools) < 3:
        print("  WARNING: fewer than 3 tools connected. Spec will be thin.")

    state["connected_tools"] = tools
    state["phase"] = "probe"
    return state


def phase_probe(state: dict) -> dict:
    """Phase 2: launch one probe sub-agent per connected tool."""
    print("\n— Phase 2: Probe —")
    from probe_tool import probe

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for tool in state["connected_tools"]:
        print(f"  Probing {tool}…", end=" ", flush=True)
        try:
            entities = probe(tool)
            out = PROBE_DIR / f"{tool}.jsonl"
            with out.open("w", encoding="utf-8") as f:
                for e in entities:
                    f.write(json.dumps(e) + "\n")
            results[tool] = {"entities": len(entities), "path": str(out)}
            print(f"ok ({len(entities)} entities)")
        except NotImplementedError:
            print("skipped (probe not yet implemented for this tool)")
            results[tool] = {"entities": 0, "path": None, "skipped": True}
        except Exception as e:
            print(f"FAILED: {e}")
            results[tool] = {"entities": 0, "path": None, "error": str(e)}

    state["probe_results"] = results
    state["phase"] = "interview"
    return state


def phase_interview(state: dict) -> dict:
    """Phase 3: structured 7-section interview with the dept head."""
    print("\n— Phase 3: Interview —")
    from interview import run_interview

    answers = run_interview(state["connected_tools"], state["probe_results"])
    state["interview_answers"] = answers
    state["phase"] = "synthesize"
    return state


def phase_synthesize(state: dict, brain_root: Path) -> dict:
    """Phase 4: produce department-spec.md and department.json drafts."""
    print("\n— Phase 4: Synthesize —")
    from synthesize import synthesize

    out_dir = brain_root / "departments" / state["department"]
    out_dir.mkdir(parents=True, exist_ok=True)
    drafts = synthesize(
        department=state["department"],
        interview_answers=state["interview_answers"],
        probe_results=state["probe_results"],
        out_dir=out_dir,
        committed=False,
    )
    print(f"  Drafts written to {out_dir} (not yet committed).")
    state["draft_paths"] = {k: str(v) for k, v in drafts.items()}
    state["phase"] = "validate"
    return state


def phase_validate(state: dict, brain_root: Path) -> dict:
    """Phase 5: validate, show to head, commit on acceptance."""
    print("\n— Phase 5: Validate + Commit —")
    from validate import validate_spec

    md_path = Path(state["draft_paths"]["spec_md"])
    json_path = Path(state["draft_paths"]["spec_json"])

    issues = validate_spec(md_path, json_path)
    if issues:
        print("  Validation issues:")
        for i in issues:
            print(f"    - {i}")
        print("  Re-run interview for affected sections, then re-synthesize.")
        return state

    print(f"  Draft: {md_path}")
    print("  Review the draft. Type 'accept' to commit, anything else to abort.")
    answer = input("  > ").strip().lower()
    if answer != "accept":
        print("  Aborted. Drafts remain in cache; re-run validate to retry.")
        return state

    state["committed"] = True
    state["committed_at"] = _now()
    state["phase"] = "done"
    print(f"  Committed. Department spec lives at {md_path.parent}.")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a department's slice of the company brain.")
    parser.add_argument("--department", required=True, help="Department slug (product, sales, marketing, …)")
    parser.add_argument("--brain-root", default=str(BRAIN_DEFAULT), help="Where the brain lives (default: ~/.anabasis/brain)")
    parser.add_argument("--resume", action="store_true", help="Resume from cached state")
    parser.add_argument("--restart", action="store_true", help="Discard cached state and start over")
    args = parser.parse_args()

    brain_root = Path(args.brain_root).expanduser()

    if args.restart:
        sp = state_path(args.department)
        if sp.exists():
            sp.unlink()

    state = load_state(args.department) if args.resume else {
        "department": args.department,
        "phase": "connect",
        "started_at": _now(),
    }

    phase_dispatch = {
        "connect": lambda s: phase_connect(s),
        "probe": lambda s: phase_probe(s),
        "interview": lambda s: phase_interview(s),
        "synthesize": lambda s: phase_synthesize(s, brain_root),
        "validate": lambda s: phase_validate(s, brain_root),
    }

    while state["phase"] in phase_dispatch:
        state = phase_dispatch[state["phase"]](state)
        save_state(state)
        if state["phase"] == "validate" and not state.get("committed"):
            return 0

    print(f"\nDone. Department: {args.department}, committed: {state.get('committed', False)}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
