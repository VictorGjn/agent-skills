"""
Structured 7-section interview with the dept head.

run_interview(connected_tools, probe_results) → dict[section_key, answer_struct]

Each prompt has a required answer shape; the interview re-prompts using the
follow-ups in references/interview-prompts.md until the shape is satisfied or
the head has been re-prompted 3 times (then the section is marked INCOMPLETE).
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SECTIONS = ("tools", "roles", "cadence", "pipeline", "taxonomy", "automations", "metrics")

PROMPT_BANK = {
    "tools": {
        "prompt": (
            "Here's the inventory we probed from your connected tools:\n{inventory}\n\n"
            "For each tool, in one sentence: what is the *primary thing* you use this tool for?"
        ),
        "shape": "one_sentence_per_tool",
        "follow_ups": [
            "Pick the single most important workflow you do in {tool}. What is it?",
            "When was the last time you opened {tool}? What did you do there?",
            "We saw {tool} connected — should it be excluded from this department's spec?",
        ],
    },
    "roles": {
        "prompt": (
            "Who is on your team, and for each person: "
            "what's the one thing they are *accountable* for that no one else can decide on their behalf?"
        ),
        "shape": "list_of_roles_with_accountability",
        "follow_ups": [
            "Who would the team escalate to about a contentious topic — budget, prioritization, hiring?",
            "Who do you delegate to? Who covers when you're out?",
            "Are there external collaborators (contractors, vendors) who own anything?",
        ],
    },
    "cadence": {
        "prompt": (
            "Walk me through your week. What recurring meetings, reviews, or deadlines structure your time?"
        ),
        "shape": "list_of_recurring_with_frequency_attendees_trigger",
        "follow_ups": [
            "When did you last meet with your whole team? Why did you call that meeting?",
            "What happens at month-end? Quarter-end?",
            "What forces you to drop everything when it happens?",
        ],
    },
    "pipeline": {
        "prompt": (
            "Take one piece of work currently in flight in your function. "
            "Walk me through every state it has been in, and what moved it from each state to the next."
        ),
        "shape": "ordered_stages_with_transitions",
        "follow_ups": [
            "What happens between {stage_a} and {stage_b}? Walk me through the silent step.",
            "Has work ever moved forward and then had to move back? What happened?",
            "Where physically does this work live in {stage}? Notion? Linear? Slack?",
        ],
    },
    "taxonomy": {
        "prompt": (
            "How do you classify the work in your function — what categories, themes, tags, or labels do you use?"
        ),
        "shape": "named_taxonomy_with_categories_and_rule",
        "follow_ups": [
            "When you report up, how do you group what you've done? By client? Product area? Cost center?",
            "If we look at {tool_with_tag_field}, how do those values map to what you're describing?",
            "When something fits two categories, which wins? Why?",
        ],
    },
    "automations": {
        "prompt": (
            "What runs without you touching it? Cron jobs, webhooks, integrations, sync scripts."
        ),
        "shape": "list_of_automations_with_trigger_action_owner",
        "follow_ups": [
            "What would happen Monday morning if no automation ran over the weekend?",
            "Who do you ping when an automation breaks?",
            "We saw {integration} configured in {tool}. What does that do?",
        ],
    },
    "metrics": {
        "prompt": (
            "What numbers do you report on or watch, and what threshold means 'something is wrong'?"
        ),
        "shape": "list_of_metrics_with_source_threshold",
        "follow_ups": [
            "When did you last say 'this is going well' or 'this is going badly'? What told you that?",
            "At what value do you stop the presses? At what value do you celebrate?",
            "Where do you actually look at this number? Which tool, which dashboard?",
        ],
    },
}


def _format_inventory(probe_results: dict) -> str:
    lines = []
    for tool, info in probe_results.items():
        if info.get("skipped"):
            lines.append(f"  - {tool} (probe not implemented)")
        elif info.get("error"):
            lines.append(f"  - {tool} (probe failed: {info['error']})")
        else:
            lines.append(f"  - {tool} ({info['entities']} entities)")
    return "\n".join(lines) if lines else "  (no probe results)"


def _ask(prompt: str) -> str:
    print(f"\n{prompt}\n")
    print("(End with a single line containing only 'EOA' to finish your answer.)")
    lines = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if line.strip() == "EOA":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _shape_ok(answer: str, shape: str) -> tuple[bool, str | None]:
    """Heuristic shape check. Returns (ok, reason_if_not_ok).

    The full implementation parses the answer into the structured payload
    referenced in references/interview-prompts.md. This stub catches the
    most common bad answers (empty, single word, refusal phrases).
    """
    a = answer.strip()
    if not a:
        return False, "answer is empty"
    if len(a.split()) < 4:
        return False, f"answer is too short (<4 words) for shape '{shape}'"
    refusals = ("i don't know", "we just figure it out", "no methodology", "not really", "n/a", "none")
    lower = a.lower()
    if any(lower.startswith(r) for r in refusals):
        return False, "answer matches a refusal pattern; use the follow-up"
    return True, None


def _run_section(section: str, probe_results: dict) -> dict:
    spec = PROMPT_BANK[section]
    inventory = _format_inventory(probe_results)
    prompt = spec["prompt"].format(inventory=inventory)
    follow_up_idx = 0

    while True:
        answer = _ask(prompt)
        ok, reason = _shape_ok(answer, spec["shape"])
        if ok:
            return {"section": section, "answer": answer, "shape": spec["shape"], "incomplete": False}
        if follow_up_idx >= len(spec["follow_ups"]):
            print(f"\n  Marking {section} INCOMPLETE — re-interview later.")
            return {"section": section, "answer": answer, "shape": spec["shape"], "incomplete": True, "reason": reason}
        prompt = spec["follow_ups"][follow_up_idx]
        follow_up_idx += 1


def run_interview(connected_tools: list[str], probe_results: dict) -> dict:
    print("\n  This interview takes 30–60 minutes.")
    print("  Each section has up to 3 follow-up prompts if your first answer is too vague.")
    print("  At any time you can answer with raw text + 'EOA' on a new line to finish.")

    answers: dict[str, dict] = {}
    for section in SECTIONS:
        print(f"\n→ Section {SECTIONS.index(section) + 1}/{len(SECTIONS)}: {section}")
        answers[section] = _run_section(section, probe_results)

    incomplete = [s for s, a in answers.items() if a.get("incomplete")]
    if incomplete:
        print(f"\n  Sections marked INCOMPLETE: {', '.join(incomplete)}")
        print("  These will go to the verification annex; spec body will omit them.")

    return answers
