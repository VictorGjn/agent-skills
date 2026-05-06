# Scribe / enricher / consumer proposals

Draft proposals for new components — written by the **brain-designer** routine when it detects a gap.

## Workflow

1. Designer writes `{YYYY-MM-DD}-{slug}.md` here on detection of a gap (max one per run).
2. Victor reads, fills the `## Decision` block:
   - **Ship as-is** → spec lands at `agent-skills/scribes/<name>/SKILL.md`, code lands at `syrocolab/syroco-product-ops/scribes/<name>/`, output writes to `company-brain/corpora/<corpus>/` via `CE_BRAIN_DIR`
   - **Defer until: …** → leave file here annotated; Designer skips it next week
   - **Reject** → annotate why; Designer's `designer-log/` will see it and avoid the same shape

## Routing (per `project_context_engineering_scope`)

| Layer | Repo | What lives here |
|---|---|---|
| Contract | `agent-skills/scribes/` | SKILL.md, SCRIBE.toml, the spec |
| Implementation | `syrocolab/syroco-product-ops/scribes/<name>/` | `ingest.py`, claim extraction, state files |
| Brain output | `company-brain/corpora/<corpus>/` | events.jsonl, wiki/ |
| CE engine | `agent-skills/context-engineering/` | corpus-agnostic; never source-specific |

## What this is NOT

- Not a backlog (`PLAN.md` is canonical)
- Not a substitute for committed Tier-1 scribes
- Not auto-deployed — every proposal needs a human read

## Routine that writes here

`company-knowledge/schedules/brain-designer/schedule.md`
