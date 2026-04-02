---
name: proactive-brief
description: "Generate concise, actionable status briefs from scattered sources — inspired by Claude Code's KAIROS Brief Mode and Memory Extract system. Use as a scheduled task or manually to produce delta-based reports: what changed, what needs attention, what to do next."
requiredApps: []
---

# Proactive Brief

Generate concise, actionable status briefs from scattered sources — inspired by Claude Code's KAIROS Brief Mode.

## When to Use

- **Morning briefing**: What happened overnight, what's on today's plate
- **End-of-week digest**: What was accomplished, what carries over
- **Project checkpoint**: Status across multiple workstreams
- **After-meeting synthesis**: Extract decisions + actions from meeting notes
- As a **scheduled task** for any of the above

## Design Principles (from Claude Code's KAIROS)

1. **Brief, not verbose** — max 20 words per item. If it needs more, link to a file.
2. **Delta-based** — only report what CHANGED since last brief. No restating known facts.
3. **Actionable** — every item should be either informational ("X happened") or actionable ("Y needs attention")
4. **15-second rule** — the brief should be readable in 15 seconds. If it's longer, it's not brief.
5. **Source-linked** — every claim links to its source (session, message, ticket, doc)

## Brief Format

```markdown
# Brief — [Date] [Time]

## Needs Attention (act today)
- [Issue]: [1-line description] → [suggested action] ([source])

## Changed Since Last Brief
- [Topic]: [what changed] ([source])

## Upcoming (next 48h)
- [Event/deadline]: [1-line description]

## Extracted Knowledge (durable facts learned)
- [Fact]: [value] — saved to [file] ([source])
```

## Source Gathering

The brief pulls from multiple sources in priority order:

### Tier 1: Direct signals (always check)
- **Recent sessions** — decisions, discoveries, blockers from conversations
- **Calendar** — meetings in the next 48h
- **Email** — unread threads requiring action

### Tier 2: Work management (check if connected)
- **Linear/Jira** — tickets assigned, status changes, blockers
- **GitHub** — PRs awaiting review, CI failures, new issues assigned
- **Slack** — unread mentions in key channels

### Tier 3: Knowledge delta (check if relevant)
- **Notion** — pages modified in shared workspaces
- **Meeting notes** — recent Granola transcripts with unextracted actions

## Scheduled Task Template

Create a schedule file at `schedules/morning-brief/schedule.md`:

```yaml
---
name: "morning-brief"
description: "Daily morning briefing — what changed overnight, what needs attention"
cron: "0 8 * * 1-5"
timezone: "Europe/Paris"
enabled: true
---

Generate a morning brief following the proactive-brief skill pattern.

1. Check calendar for today's meetings
2. Check email for unread threads requiring action
3. Check Linear/Jira for assigned ticket status changes
4. Check GitHub for PRs needing review or CI failures
5. Search recent session memory for unresolved items from yesterday

Format the brief using the standard template (Needs Attention → Changed → Upcoming → Extracted Knowledge).

Keep each item under 20 words. The whole brief should be readable in 15 seconds.

Save the brief to session/morning-brief.md.
```

## Memory Extract Pattern

From each brief, extract durable knowledge — facts that should persist beyond this session:

```
Extraction rules:
1. Decisions made → save to relevant project knowledge file
2. Facts discovered → save with provenance and date
3. Status changes → update project status files
4. Blockers identified → flag in project files with date

Only extract if signal ≥ 2 (has at least: title + source + 1-line purpose)
```

## Example Briefs

### Morning Brief
```markdown
# Brief — 2026-04-01 08:00

## Needs Attention
- PR #247: CI failing on auth tests → fix before standup (GitHub)
- Linear SYR-412: Blocked by API spec, needs @jean input (Linear)

## Changed Since Last Brief
- Modular-crew Phase 1 marked complete (session yesterday)
- Syroco staging deployed v2.4.1 (Slack #releases)

## Upcoming (next 48h)
- 10:00 Product sync — agenda: Q2 roadmap finalization
- Tomorrow 14:00 — Sprint review demo

## Extracted Knowledge
- modular-crew `crew diff` shows 33% token savings → saved to projects/modular-crew/
```

### End-of-Week Digest
```markdown
# Week Digest — W14 2026

## Completed
- Claude Code leak analysis + next-gen feature extraction (3 sessions)
- Plane research report finalized (documents/01 - Discovery/)
- Agent skills repo: context-engineering published

## Carries Over
- Product workflow implementation plan (needs modular-crew session)
- Syroco engineering guidelines update (blocked on team review)

## Key Decisions
- Adopted Plane over Linear for project management
- Context engineering budget: 8K tokens default, 16K for graph queries

## Numbers
- 12 sessions this week, 3 knowledge files updated, 1 new skill published
```

## Integration with Knowledge Dream

The proactive brief and knowledge-dream skills are complementary:
- **Brief** = fast, daily, surface-level — "what's happening now"
- **Dream** = slow, weekly, deep — "what have we learned"

Brief extracts signal → Dream consolidates signal into durable knowledge.
