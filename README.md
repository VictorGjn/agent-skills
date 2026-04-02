# agent-skills

Reusable AI agent skills for [Sauna](https://sauna.ai) and any LLM-powered workflow.

## Skills

### [context-engineering](./context-engineering/)
Pack 40+ files at 5 depth levels into any LLM context window. Keyword, semantic, and graph resolution. 100% recall at 1% of repo token cost. Drop-in for any AI agent.

### [knowledge-dream](./knowledge-dream/)
Periodic knowledge consolidation — synthesize scattered session learnings into durable, well-organized knowledge files. 4-phase process: Orient → Gather → Consolidate → Prune. Inspired by Claude Code's autoDream memory consolidation engine.

### [coordinator-pattern](./coordinator-pattern/)
Decompose complex tasks into parallel sub-agent work using the Research → Synthesis → Implementation → Verification pattern. Use when a task is too large for a single agent pass. Works with any sub-agent spawning system.

### [proactive-brief](./proactive-brief/)
Generate concise, actionable status briefs from scattered sources. Delta-based reports: what changed, what needs attention, what to do next. Designed for scheduled daily/weekly use. Inspired by Claude Code's KAIROS Brief Mode.

## Usage

Each skill is a standalone folder with a `SKILL.md` that describes when and how to use it. Skills are designed for:

- **Sauna** — drop into `skills/sauna/` or `skills/global/`
- **Claude Code** — use as custom skills via `.claude/skills/`
- **Any LLM agent** — follow the instructions in SKILL.md

## Origin

The `knowledge-dream`, `coordinator-pattern`, and `proactive-brief` skills are derived from patterns discovered in Claude Code's leaked source (March 2026 npm sourcemap incident). See the [analysis](https://kuber.studio/blog/AI/Claude-Code's-Entire-Source-Code-Got-Leaked-via-a-Sourcemap-in-npm,-Let's-Talk-About-it) for context.

## License

MIT
