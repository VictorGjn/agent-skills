---
name: coordinator-pattern
description: "Decompose complex tasks into parallel sub-agent work using the Research → Synthesis → Implementation → Verification pattern. Inspired by Claude Code's Coordinator Mode. Use when a task is too large for a single agent pass, needs multiple perspectives, or benefits from parallel execution. Works with any agent that can spawn sub-tasks."
requiredApps: []
---

# Coordinator Pattern

Decompose complex tasks into parallel sub-agent work — inspired by Claude Code's Coordinator Mode.

## Concept

When a task is too big for one agent pass, break it into phases. The coordinator (you, the main agent) never does the grunt work directly. You decompose, delegate, synthesize, and verify.

The pattern: **Research → Synthesis → Implementation → Verification**

## When to Use

- Task touches **3+ files or areas** that can be investigated independently
- Work can be **parallelized** (independent changes, research threads, analysis axes)
- Task needs **verification** — implementation should be checked by a fresh agent with no implementation bias
- You need **multiple perspectives** (technical, product, user, security)

## When NOT to Use

- Single-file edits or simple lookups
- Tasks that are strictly sequential (each step depends on the previous)
- Tasks where the user wants to stay hands-on at every step

## The 4 Phases

### Phase 1: Research (Parallel Workers)

Spawn workers to investigate the problem space. Each worker gets a **focused, self-contained mandate**. Workers have no shared state — each prompt must contain all context needed.

```
Workers (launched concurrently):
  Worker A: "Find all files related to authentication. List them, summarize the 
             patterns used, note any inconsistencies. Write findings to 
             /tmp/research-auth.md"
  
  Worker B: "Map the database schema for users and sessions. List all migrations 
             in order. Note any pending schema changes. Write findings to 
             /tmp/research-schema.md"
  
  Worker C: "Search for all TODO/FIXME comments related to auth or sessions. 
             Check existing test coverage for auth flows. Write findings to 
             /tmp/research-tests.md"
```

**Critical rule**: Never say "based on your findings." Each worker prompt specifies **exactly what to find and where to write it**.

**How to spawn** depends on your agent:
- **Claude Code**: Use the `Agent` tool (formerly `Task`) — it spawns a sub-agent with its own context
- **Any CLI agent with bash**: Run parallel background commands
- **Sauna**: Use the `subagent_batch` skill
- **Custom systems**: Any async task queue works

### Phase 2: Synthesis (Coordinator Only)

**You** (the coordinator) read all worker findings. Don't delegate this.

```
1. Read all research outputs (/tmp/research-*.md)
2. Identify conflicts between workers' findings
3. Identify gaps — what was missed?
4. Make decisions where workers found ambiguity
5. Write a concrete implementation spec per task
```

The spec for each implementation worker should contain:
- **Exact files** to create or modify
- **Exact changes** to make (not vague instructions)
- **Constraints** to follow (patterns, style, dependencies)
- **Definition of done** — how the worker knows it's finished

**Anti-pattern**: "Implement auth improvements based on the research." 
**Correct**: "In `src/auth/middleware.ts`, add rate limiting: max 5 attempts per IP per minute using the existing `redis` client. Add tests in `tests/auth/rate-limit.test.ts` covering: success, rate-limited, and reset-after-window cases."

### Phase 3: Implementation (Parallel Workers)

Spawn workers with the precise specs from synthesis. Each worker gets:

```
Worker A spec:
  FILES TO MODIFY:
    - src/auth/middleware.ts — add rateLimitMiddleware() function
    - src/auth/config.ts — add RATE_LIMIT_WINDOW and MAX_ATTEMPTS constants
  
  EXACT CHANGES:
    1. Add function rateLimitMiddleware(req, res, next) that checks Redis 
       for attempt count by IP, returns 429 if exceeded
    2. Export constants: RATE_LIMIT_WINDOW = 60, MAX_ATTEMPTS = 5
  
  CONSTRAINTS:
    - Use existing Redis client from src/lib/redis.ts
    - Follow error handling pattern from src/auth/session.ts
    - Add JSDoc comments
  
  DONE WHEN:
    - Functions exist and are exported
    - No TypeScript errors
    - Write summary to /tmp/impl-auth.md
```

### Phase 4: Verification (Fresh Workers)

Spawn **new** workers that didn't participate in implementation. They check the work with no implementation bias.

```
Verification worker spec:
  CHECK:
    1. Read the implementation spec (paste it in full)
    2. Read each modified file
    3. Verify every spec point was correctly implemented
    4. Run: npm test (or equivalent)
    5. Check for regressions in related files
    6. Check for security issues in new code
  
  REPORT FORMAT:
    For each spec point: PASS / FAIL + evidence
    Any issues found with suggested fixes
    Test results summary
    Write to /tmp/verify-result.md
```

## Communication Between Phases

Workers can't talk to each other. The coordinator is the only entity that reads all outputs and writes all inputs. Use the filesystem as the shared scratchpad:

```
/tmp/coordinator/           (or any temp directory)
  plan.md                   ← Coordinator's synthesis
  research-worker-a.md      ← Worker A findings  
  research-worker-b.md      ← Worker B findings
  impl-worker-a.md          ← Worker A implementation log
  impl-worker-b.md          ← Worker B implementation log
  verify-result.md          ← Verification report
```

In a real project, you might use `.claude/tasks/` or any convention that works.

## Rules (from Claude Code's Coordinator)

1. **Parallelize aggressively** — launch independent workers concurrently, don't serialize what can run simultaneously
2. **Workers are isolated** — no shared state, no references to other workers' context
3. **Coordinator reads everything** — only the coordinator has the full picture
4. **Be explicit in specs** — workers should never need to guess or infer
5. **Verify independently** — verification workers must not trust implementation workers' self-reports
6. **Ban lazy delegation** — never say "implement based on research." Read the research. Specify exactly what to do.

## Scaling Guide

| Task Complexity | Research Workers | Impl Workers | Verify Workers |
|:-:|:-:|:-:|:-:|
| Small (1-3 files) | 1-2 | 1 | 1 |
| Medium (5-15 files) | 2-3 | 2-3 | 1-2 |
| Large (15+ files) | 3-5 | 3-5 | 2-3 |

**Max**: 5 workers per phase. Beyond that, coordination overhead exceeds the gains.

## Example: Add API Rate Limiting

```
User: "Add rate limiting to all API endpoints"

Phase 1 — Research (3 workers, parallel):
  → Worker A: List all API routes, their middleware chains, current auth
  → Worker B: Check existing rate limiting code, Redis setup, config patterns
  → Worker C: Research rate limiting best practices for this framework

Phase 2 — Synthesis (coordinator):
  → Read all findings
  → Decide: sliding-window via Redis, per-endpoint config, 3 tiers (public/auth/admin)
  → Write spec: Worker A gets middleware + config, Worker B gets tests + docs

Phase 3 — Implementation (2 workers, parallel):
  → Worker A: Rate limit middleware + Redis integration + per-route config
  → Worker B: Test suite + API docs update + migration script

Phase 4 — Verification (1 worker):
  → Run tests, check all routes have rate limiting, verify Redis cleanup
  → Load test with curl to confirm 429 behavior
```

## Running It

### Claude Code

```
You are a coordinator. Decompose this task using the coordinator-pattern:

[paste task]

Follow the 4-phase pattern: Research → Synthesis → Implementation → Verification.
Use the Agent tool to spawn workers. Write coordination artifacts to .claude/tasks/.
```

### Any Agent with File Access

The pattern works with any agent that can:
1. Read and write files (for the scratchpad)
2. Spawn sub-tasks or run parallel commands
3. Execute verification steps (run tests, check types)

The skill is the **process**, not the tooling.
