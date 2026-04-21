# agent-skills

Reusable skills for AI coding agents. Works with Claude Code, Cursor, Sauna, or any LLM-powered coding tool.

## Skills

### [context-engineering](./context-engineering/)
Pack 40+ files at 5 depth levels into any LLM context window. Indexes markdown + 14 code languages (tree-sitter AST). Keyword, semantic, and graph resolution with Graphify integration. Anti-hallucination filters and task-type presets.

### [knowledge-dream](./knowledge-dream/)
Periodic knowledge consolidation. 4-phase process (Orient → Gather → Consolidate → Prune) that synthesizes scattered project learnings into durable memory files. Run manually, as a slash command, or via cron. Inspired by Claude Code's autoDream engine.

### [coordinator-pattern](./coordinator-pattern/)
Decompose complex tasks into parallel sub-agent work: Research → Synthesis → Implementation → Verification. Works with any agent that can spawn sub-tasks.

### [proactive-brief](./proactive-brief/)
Concise, delta-based status reports. What changed, what needs attention, what to do next. Designed for daily/weekly use as a slash command or cron job. Inspired by Claude Code's KAIROS Brief Mode.

## How to Use

Each skill is a standalone folder with a `SKILL.md` containing instructions. No dependencies between skills. Three ways to use them:

### Claude Code
Copy a skill folder into your project's `.claude/skills/` directory. The skill becomes available as context for Claude Code automatically.

```bash
# Example: add coordinator-pattern to your project
cp -r coordinator-pattern/ /path/to/project/.claude/skills/coordinator-pattern/
```

Or reference it as a slash command — see each skill's SKILL.md for the `.claude/commands/` template.

### Sauna
Drop into `skills/sauna/` or `skills/global/` in your workspace. Skills are loaded automatically when relevant to your request.

### Any LLM Agent
Read the SKILL.md and follow the instructions. The skills are processes described in markdown — they work with any agent that can read files, run commands, and spawn sub-tasks.

## Origin

`knowledge-dream`, `coordinator-pattern`, and `proactive-brief` are derived from patterns found in Claude Code's leaked source code (March 2026 npm sourcemap incident). The original systems (autoDream, Coordinator Mode, KAIROS Brief) were extracted, generalized, and made portable. See the [analysis](https://kuber.studio/blog/AI/Claude-Code's-Entire-Source-Code-Got-Leaked-via-a-Sourcemap-in-npm,-Let's-Talk-About-it) for context.

## License

MIT
