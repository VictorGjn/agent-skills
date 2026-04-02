---
name: knowledge-dream
description: "Periodic knowledge consolidation for AI coding agents. Synthesize scattered project learnings into durable, well-organized memory files. 4-phase process: Orient → Gather → Consolidate → Prune. Inspired by Claude Code's autoDream engine. Use when project memory is stale, after a burst of work, or as a recurring maintenance pass."
requiredApps: []
---

# Knowledge Dream

Synthesize scattered project learnings into durable knowledge — inspired by Claude Code's autoDream memory consolidation engine.

## Concept

AI agents accumulate context across sessions: decisions made, patterns discovered, bugs fixed, architecture choices. Without consolidation, this knowledge scatters and decays. The Dream is a periodic pass that gathers recent signal and writes it into persistent, well-organized files that future sessions can orient from quickly.

## Where Memory Lives

Memory is just markdown files in your project. No special infrastructure needed.

```
your-project/
  .claude/
    memory/          ← Agent memory lives here (Claude Code convention)
      MEMORY.md      ← Index: what's known, pointers to topic files
      architecture.md
      decisions.md
      patterns.md
      team-conventions.md
  docs/              ← Or here, if you prefer project docs
  CLAUDE.md          ← Project-level context (Claude Code reads this automatically)
```

**Any directory works.** The skill doesn't depend on a specific path. Pick a convention:
- `.claude/memory/` — Claude Code's native memory dir
- `docs/knowledge/` — if you want it in project docs
- `.ai/memory/` — generic, works with any agent
- `CLAUDE.md` / `AGENTS.md` — single-file for small projects

## The 4-Phase Process

### Phase 1: Orient

Scan the current knowledge landscape before changing anything.

```
1. List the memory directory — what topic files exist?
2. Read the index file (MEMORY.md or equivalent) — is it current?
3. Note: which topics are covered, which are empty, which look stale
4. Check file dates — anything not updated in 30+ days is a staleness candidate
```

**Output**: Mental map of what's already captured.

### Phase 2: Gather Recent Signal

Find new information worth persisting. Sources depend on your setup.

**Universal sources (any project)**:
- Git log since last consolidation — what was changed, committed, merged
- Recent conversation transcripts / session files
- New or modified docs, READMEs, configs
- TODO/FIXME comments added to code

**If you have integrations**:
- PR descriptions and review comments
- Issue tracker updates (Linear, Jira, GitHub Issues)
- Slack/Discord threads with technical decisions
- Meeting notes

```
git log --since="7 days ago" --oneline --no-merges  # What happened recently
grep -r "TODO\|FIXME\|HACK\|NOTE" src/ --include="*.ts" -l  # Unresolved items
```

**Output**: List of new facts, decisions, corrections — each with its source.

### Phase 3: Consolidate

Write or update memory files. Rules:

- **Convert relative dates to absolute** — "yesterday" → "2026-04-01"
- **Delete contradicted facts** — new info supersedes old, note why
- **Merge duplicates** — scattered references to the same topic → one place
- **Add provenance** — where each fact came from (commit, conversation, PR, etc.)
- **Keep atomic** — one topic per file, descriptive filename

**What to persist**:
| Worth saving | Not worth saving |
|---|---|
| Architecture decisions + rationale | Debugging steps that led nowhere |
| API patterns / conventions adopted | Temporary workarounds already removed |
| Team agreements (code style, review process) | Exact error messages from fixed bugs |
| Integration gotchas (auth, rate limits, quirks) | Task-level progress ("done 3 of 5 items") |
| Key metrics / thresholds | Raw meeting transcripts |

**Format for a memory file**:
```markdown
# [Topic]

Last updated: 2026-04-01
Sources: [commit abc123, PR #42, session 2026-03-28]

## Key Facts

- [Fact 1]: [detail] (source: [ref])
- [Fact 2]: [detail] (source: [ref])

## Decisions

- [Decision]: [rationale] — decided [date] (source: [ref])

## Open Questions

- [Question]: [context] — flagged [date]
```

### Phase 4: Prune & Index

Keep memory lean. Bloated memory = worse retrieval.

- **Size limit**: No single file over 200 lines. Split if needed.
- **Staleness**: Facts older than 90 days without revalidation get flagged or archived.
- **Index**: Update MEMORY.md to reflect what exists and where to find it.
- **Contradictions**: Surface any unresolved conflicts — don't silently pick a winner.

**MEMORY.md index format**:
```markdown
# Project Memory

Last dream: 2026-04-01

## Topics
- architecture.md — System architecture, service boundaries, data flow
- decisions.md — Key technical decisions with rationale
- patterns.md — Code patterns, conventions, anti-patterns to avoid
- integrations.md — Third-party API gotchas, auth flows, rate limits

## Stale (needs review)
- deployment.md — last updated 2025-12-15, deployment process may have changed
```

## Running a Dream

### Manual (any agent)

Paste this into your agent:

```
Run a knowledge dream on this project:

1. Orient: List .claude/memory/ (or wherever memory lives). Read MEMORY.md. 
   Understand what knowledge already exists.

2. Gather: Check git log for the last 7 days. Search for recent decisions, 
   patterns, or corrections that aren't captured yet. Check for stale facts.

3. Consolidate: Update memory files with new findings. Convert relative dates 
   to absolute. Delete contradicted facts. Add provenance notes.

4. Prune: Ensure no file exceeds 200 lines. Flag stale content (>90 days 
   without validation). Update the index.

5. Report: Summarize what changed — added, updated, removed, flagged.
```

### Automated (Claude Code)

Add to `.claude/commands/dream.md`:
```markdown
---
description: Run a knowledge consolidation dream
allowed-tools: Read, Write, Edit, Bash(git log:*), Bash(grep:*), Bash(find:*), Glob, Grep
---

Run a knowledge dream following the 4-phase process in the knowledge-dream skill.
Memory lives in .claude/memory/. Report changes when done.
```

Then trigger with `/dream` in Claude Code.

### Scheduled (cron / CI)

```bash
# Weekly Monday 9am knowledge consolidation
0 9 * * 1 cd /path/to/project && claude -p "Run a knowledge dream. Memory is in .claude/memory/. Follow the 4-phase process: orient, gather (last 7 days), consolidate, prune. Save report to .claude/memory/dream-log.md"
```

## Three-Gate Trigger (for automated runs)

Don't dream too often or too rarely:

1. **Time gate**: At least 24h since last dream
2. **Activity gate**: At least 3 sessions / meaningful commits since last dream
3. **Concurrency gate**: No other dream running (check a lock file)

```bash
# Simple lock check
LOCK=".claude/memory/.dream-lock"
[ -f "$LOCK" ] && echo "Dream already running" && exit 0
touch "$LOCK"
# ... run dream ...
rm "$LOCK"
```

## Design Principles

- **Read-only investigation, write-only consolidation** — gather before you change
- **Append-only source data** — never delete raw logs/transcripts, only synthesize
- **Prune aggressively** — knowledge not accessed or validated decays
- **Provenance always** — every fact traces to a source
- **Human review for conflicts** — flag, don't silently resolve contradictions
