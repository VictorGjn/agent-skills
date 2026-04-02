---
name: proactive-brief
description: "Generate concise, actionable status briefs from project state and recent activity. Delta-based reports: what changed, what needs attention, what to do next. Inspired by Claude Code's KAIROS Brief Mode. Works as a manual prompt, a slash command, or a cron job."
requiredApps: []
---

# Proactive Brief

Generate concise, actionable status briefs from project state and recent activity — inspired by Claude Code's KAIROS Brief Mode.

## Concept

A brief is not a summary. It's a **delta report**: only what changed, only what matters, only what needs action. If someone read the last brief, this one should tell them everything new in 15 seconds.

## Design Principles

1. **Brief, not verbose** — max 20 words per item. If it needs more, link to a file.
2. **Delta-based** — only what CHANGED since last brief. No restating known facts.
3. **Actionable** — every item is either informational ("X happened") or actionable ("Y needs attention by [when]")
4. **15-second rule** — the whole brief should be scannable in 15 seconds. If it's longer, it's not brief.
5. **Source-linked** — every claim cites its source (commit, PR, issue, conversation)

## Brief Template

```markdown
# Brief — [Date]

## Needs Attention
- [Thing]: [1-line what + why] → [suggested action] (source: [ref])

## Changed
- [Topic]: [what changed] (source: [ref])

## Upcoming (next 48h)
- [Deadline/event]: [1-line context]

## Learned (new durable facts)
- [Fact]: [value] (source: [ref])
```

**That's it.** Four sections. If a section is empty, omit it.

## Sources

What you pull from depends on your project setup. Check what's available:

### Tier 1: Always available (any project)
```bash
# Recent commits
git log --since="24 hours ago" --oneline --no-merges

# Changed files
git diff --stat HEAD~5

# Open branches
git branch --sort=-committerdate | head -10

# TODO/FIXME delta
git diff HEAD~5 -- '*.ts' '*.py' '*.rs' | grep "^+" | grep -i "todo\|fixme\|hack"
```

### Tier 2: If you have a remote
```bash
# Open PRs
gh pr list --state open --limit 10

# PR reviews needed
gh pr list --search "review-requested:@me"

# CI status
gh run list --limit 5
```

### Tier 3: If you have integrations
- **Issue tracker** (Linear, Jira, GitHub Issues): Assigned tickets, status changes, blockers
- **Slack/Discord**: Unread mentions in project channels
- **Calendar**: Meetings in next 48h
- **Email**: Threads requiring response

### Tier 4: Agent memory (if available)
- Previous session decisions not yet captured in docs
- Open questions from last conversation
- Promises made ("I'll fix X tomorrow")

## Running a Brief

### Manual (paste into any agent)

```
Generate a project brief. Follow the proactive-brief pattern:

1. Check: git log last 24h, open PRs, CI status, open issues assigned to me
2. Check: any TODO/FIXME comments added recently
3. Check: [add any integrations you use]

Format: 4 sections max (Needs Attention, Changed, Upcoming, Learned)
Rules: Max 20 words per item. Delta only — skip anything unchanged. 
       Cite sources. Omit empty sections.
```

### Claude Code slash command

Add to `.claude/commands/brief.md`:
```markdown
---
description: Generate a project status brief
allowed-tools: Bash(git:*), Bash(gh:*), Read, Glob, Grep
---

Generate a concise project brief following the proactive-brief pattern.

Check git log (24h), open PRs, CI status, and recent TODO additions.
Format as: Needs Attention → Changed → Upcoming → Learned.
Max 20 words per item. Delta-only. Cite sources. Omit empty sections.
```

Trigger with `/brief` in Claude Code.

### Cron job (daily morning brief)

```bash
# Weekdays at 8:30am
30 8 * * 1-5 cd /path/to/project && claude -p "Generate a morning brief: git log last 24h, open PRs, CI status, assigned issues. Save to .claude/briefs/$(date +%Y-%m-%d).md. Max 20 words per item, delta-only, cite sources."
```

### Multi-project brief

If you work across several repos, loop:

```bash
#!/bin/bash
REPOS=("~/code/api" "~/code/frontend" "~/code/infra")
BRIEF=""

for repo in "${REPOS[@]}"; do
  cd "$repo"
  BRIEF+="## $(basename $repo)\n"
  BRIEF+="$(git log --since='24 hours ago' --oneline --no-merges 2>/dev/null || echo 'No commits')\n\n"
done

echo -e "$BRIEF" | claude -p "Synthesize this multi-repo activity into a single brief. Follow proactive-brief format. Highlight cross-repo impacts."
```

## Memory Extract Pattern

Every brief is an opportunity to capture durable knowledge. After generating the brief, check:

| Signal | Action |
|---|---|
| Decision made | Write to project memory / decision log |
| Pattern discovered | Add to conventions / architecture docs |
| Bug root cause found | Add to known issues / post-mortem |
| External API behavior learned | Add to integration docs |

**Rule**: Only extract if the fact has signal ≥ 2 — it must have a title, a source, AND a 1-line purpose to be worth persisting.

Where to save extracted knowledge:
- `.claude/memory/` — if using Claude Code's memory system
- `docs/decisions/` — for decision records (ADRs)
- `CLAUDE.md` — for the single most important project context
- Wherever your team keeps durable docs

## Examples

### Morning Brief (solo developer)
```markdown
# Brief — 2026-04-02

## Needs Attention
- CI: `test-auth` failing since 8pm yesterday → flaky Redis mock, see commit a3f21b

## Changed
- Merged PR #42: rate limiting middleware (3 files, +180 lines)
- New issue #51: user reports timeout on /api/export (assigned to me)

## Upcoming
- Friday: v2.5 release tag deadline

## Learned
- Redis MULTI/EXEC doesn't support WATCH inside pipelines (hit this in rate limiter)
```

### Weekly Digest (team lead)
```markdown
# Brief — W14 2026

## Needs Attention
- 2 PRs open >3 days: #38 (backend) and #41 (frontend) — need review
- Sprint velocity: 18/24 points done, 6 carry over

## Changed
- Auth service migrated to JWT (merged Monday, 12 files)
- New staging environment provisioned (infra PR #15)
- Onboarded 1 new contributor (first PR merged Thursday)

## Learned
- JWT refresh token rotation needs explicit revocation on logout (missed in spec)
```

## Anti-Patterns

| Don't | Do |
|---|---|
| "The project is a web app built with..." | Skip — this is known context, not a delta |
| "No changes to the database schema" | Omit — empty sections are noise |
| "We should probably look into..." | Be specific: "X needs attention → do Y by [when]" |
| Paragraphs of explanation | Max 20 words. Link to a file if more is needed. |
| "Everything looks good" | If nothing needs attention, the brief is just Changed + Learned |
