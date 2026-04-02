---
name: knowledge-dream
description: "Periodic knowledge consolidation inspired by Claude Code's autoDream system. Run as a scheduled task or manually to synthesize scattered session learnings into durable, well-organized knowledge files. 4-phase process: Orient → Gather → Consolidate → Prune."
requiredApps: []
---

# Knowledge Dream

Synthesize scattered session learnings into durable knowledge — inspired by Claude Code's autoDream memory consolidation engine.

## When to Use

- As a **scheduled task** (weekly/daily) to keep knowledge fresh
- **Manually** after a burst of sessions to consolidate what was learned
- When knowledge files feel stale, contradictory, or bloated

## The 4-Phase Process

### Phase 1: Orient
Scan the current knowledge landscape. Read the top-level index, skim existing topic files, understand what's already captured.

```
Action: List documents/ directory structure
Action: Read key index files (if they exist)
Action: Note which topics have files, which are empty, which look stale
Output: Mental map of current knowledge state
```

### Phase 2: Gather Recent Signal
Find new information worth persisting from recent sessions and external sources.

**Sources in priority order:**
1. Recent session files (`session/`) — what was worked on, decided, discovered
2. Memory search — conversations with durable decisions or facts
3. Connected services delta — new Notion pages, Slack threads, Linear issues since last consolidation

```
Action: Search recent sessions for decisions, discoveries, corrections
Action: Memory search for topics matching existing knowledge files
Action: Check if any connected-service data contradicts or enriches existing knowledge
Output: List of new facts, decisions, corrections with sources
```

### Phase 3: Consolidate
Write or update knowledge files. Apply these rules:

- **Convert relative dates to absolute** ("yesterday" → "2026-03-31")
- **Delete contradicted facts** — new info supersedes old
- **Merge duplicates** — combine scattered references to the same topic
- **Add provenance** — note where each fact came from (session, meeting, Slack, etc.)
- **Keep atomic** — one topic per file, well-titled

```
Action: For each new fact, find its natural home in documents/
Action: Update existing files (edit, don't rewrite) or create new ones
Action: Resolve contradictions explicitly — note what changed and why
```

### Phase 4: Prune & Index
Keep knowledge lean and navigable.

- **Size limit**: No single file over 200 lines / ~25KB
- **Staleness**: Flag or remove facts older than 90 days without recent validation
- **Index**: Update any top-level index to reflect new/changed/removed files
- **Contradictions**: Surface any unresolved conflicts for human review

```
Action: Check file sizes, split if needed
Action: Remove or archive stale content
Action: Update index files
Output: Summary of what changed
```

## Scheduled Task Template

Create a schedule file at `schedules/knowledge-dream/schedule.md`:

```yaml
---
name: "knowledge-dream"
description: "Weekly knowledge consolidation — synthesize recent session learnings"
cron: "0 9 * * 1"
timezone: "Europe/Paris"
enabled: true
---

Run a knowledge consolidation dream. Follow these steps:

1. **Orient**: List the documents/ directory. Read any index files. Understand what knowledge exists.

2. **Gather**: Search the last 7 days of session memory for decisions, discoveries, and corrections. Check if any facts in documents/ are now stale or contradicted.

3. **Consolidate**: Update knowledge files in documents/ with new findings. Convert relative dates to absolute. Delete contradicted facts. Add provenance notes.

4. **Prune**: Ensure no file exceeds 200 lines. Archive stale content (>90 days without validation). Update any index files.

5. **Report**: Write a brief summary of changes to session/dream-report.md — what was added, updated, removed, and any unresolved contradictions flagged for human review.
```

## Manual Usage

When triggered manually, the dream should:

1. Ask what time range to consolidate (default: since last dream or 7 days)
2. Run all 4 phases
3. Present a diff summary before writing
4. Save a report

## Design Principles (from Claude Code's autoDream)

- **Read-only investigation, write-only consolidation** — gather before you change
- **Three-gate trigger** for scheduled runs: enough time elapsed + enough sessions + no concurrent runs
- **Forked execution** — the dream runs as a background/sub-task, not blocking the user
- **Append-only logs** — never delete raw session data, only synthesize it into knowledge
- **Prune aggressively** — knowledge that isn't accessed or validated decays

## Integration with Context Engineering

After a dream run, the workspace index used by `context-engineering` should be refreshed:

```bash
python3 skills/sauna/context-engineering/scripts/index_workspace.py documents/
```

This ensures subsequent context packs reflect the latest consolidated knowledge.
