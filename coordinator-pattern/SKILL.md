---
name: coordinator-pattern
description: "Decompose complex tasks into parallel sub-agent work using the Research → Synthesis → Implementation → Verification pattern from Claude Code's Coordinator Mode. Use when a task is too large for a single agent pass, needs multiple perspectives, or benefits from parallel execution."
requiredApps: []
---

# Coordinator Pattern

Decompose complex tasks into parallel sub-agent work — inspired by Claude Code's Coordinator Mode.

## When to Use

- Task requires **3+ distinct investigation areas** before action
- Work can be **parallelized** (independent file changes, research threads, analysis axes)
- Task needs **verification** after implementation
- You need **multiple perspectives** (e.g., technical + product + user)

## When NOT to Use

- Single-file changes or simple lookups
- Tasks that are inherently sequential
- When the user wants to stay hands-on at every step

## The 4-Phase Pattern

### Phase 1: Research (Parallel)

Spawn workers to investigate the problem space concurrently. Each worker gets a focused mandate.

```
Workers (parallel):
  Worker A: "Investigate the current state of X — list all files, summarize structure"
  Worker B: "Find all references to Y — check tests, docs, and configs"
  Worker C: "Research external context Z — check docs, standards, prior art"

Rule: Each worker prompt must be SELF-CONTAINED.
      Never say "based on your findings" — specify exactly what to look for.
```

**Using subagent_batch:**

```typescript
import { spawnAgent } from "/var/workspace/skills/global/subagent_batch/scripts/spawn-subagent.ts";

const researchTasks = [
  {
    description: "Investigate codebase structure for feature X",
    prompt: `You are a research agent. Your task:
1. Search for all files related to [topic]
2. Read the top 5 most relevant files
3. Summarize: what exists, what's missing, key patterns
4. Save findings to session/research-worker-a.md

Success criteria: Complete file inventory + pattern summary saved to disk.`,
  },
  {
    description: "Analyze dependencies and test coverage",
    prompt: `You are a research agent. Your task:
1. Find all tests related to [topic]
2. Check dependency graph for [module]
3. Identify gaps in coverage
4. Save findings to session/research-worker-b.md

Success criteria: Dependency map + coverage gaps saved to disk.`,
  },
];

researchTasks.forEach(t => spawnAgent({ ...t, type: "general" }));
```

### Phase 2: Synthesis (Coordinator)

**YOU** read all worker findings. Don't delegate this — the coordinator must synthesize.

```
Action: Read session/research-worker-a.md, session/research-worker-b.md, ...
Action: Identify conflicts, gaps, and key decisions
Action: Craft specific implementation specs per worker
Output: Implementation plan with exact specs per task

Key rule: "Read the actual findings and specify exactly what to do."
          Do NOT say "implement based on research" — be explicit.
```

### Phase 3: Implementation (Parallel)

Spawn workers with precise specs from synthesis. Each worker gets:
- Exact files to modify
- Exact changes to make
- Constraints and patterns to follow

```typescript
const implTasks = [
  {
    description: "Implement component A per spec",
    prompt: `You are an implementation agent. Your spec:

FILES TO MODIFY:
- src/feature/component.ts — add [specific function]
- src/feature/types.ts — add [specific type]

EXACT CHANGES:
1. In component.ts, add function processWidget() that [exact behavior]
2. In types.ts, add WidgetConfig type with fields: [list fields]

CONSTRAINTS:
- Follow existing patterns in src/feature/
- Keep functions under 50 lines
- Add JSDoc comments

Save result to session/impl-worker-a.md with list of files changed.`,
  },
  // ... more workers
];

implTasks.forEach(t => spawnAgent({ ...t, type: "general" }));
```

### Phase 4: Verification (Parallel)

Spawn verification workers that check implementation against specs.

```typescript
const verifyTasks = [
  {
    description: "Verify implementation matches spec",
    prompt: `You are a verification agent. Check:

1. Read the implementation spec: [paste or reference spec]
2. Read the changed files: [list files]
3. Verify each spec point is correctly implemented
4. Run any available tests
5. Check for regressions in related files

Report format in session/verify-result.md:
- PASS/FAIL per spec point
- Any issues found
- Suggested fixes if FAIL`,
  },
];

verifyTasks.forEach(t => spawnAgent({ ...t, type: "general" }));
```

## Shared Scratchpad Pattern

Workers need a way to share discovered knowledge. Use session files as a scratchpad:

```
session/
  coordinator/
    plan.md              ← The coordinator's synthesis
    research-worker-a.md ← Worker A findings
    research-worker-b.md ← Worker B findings
    impl-worker-a.md     ← Worker A implementation log
    verify-result.md     ← Verification report
```

## Parallelism Rules (from Claude Code)

1. **Launch independent workers concurrently** — don't serialize work that can run simultaneously
2. **Each worker is isolated** — no shared state, no references to other workers' context
3. **Coordinator reads everything** — only the coordinator has the full picture
4. **Be explicit in specs** — workers should never need to guess or infer
5. **Verify independently** — verification workers should not trust implementation workers' self-reports

## Example: Feature Implementation

```
User: "Add dark mode support to the dashboard"

Phase 1 - Research (3 workers):
  → Worker A: Scan all CSS/theme files, map current color system
  → Worker B: Check component library for theme support
  → Worker C: Research dark mode best practices for the framework

Phase 2 - Synthesis (coordinator):
  → Read all findings
  → Decide: CSS custom properties approach, 12 components need changes
  → Write spec per component group

Phase 3 - Implementation (2 workers):
  → Worker A: Theme system + CSS custom properties + toggle component
  → Worker B: Update 12 components to use theme tokens

Phase 4 - Verification (1 worker):
  → Check all components render correctly in both modes
  → Check contrast ratios meet WCAG AA
  → Check no hardcoded colors remain
```

## Scaling Guide

| Task Complexity | Research Workers | Impl Workers | Verify Workers |
|:-:|:-:|:-:|:-:|
| Small (1-3 files) | 1-2 | 1 | 1 |
| Medium (5-15 files) | 2-3 | 2-3 | 1-2 |
| Large (15+ files) | 3-5 | 3-5 | 2-3 |

**Max recommended**: 5 workers per phase. Beyond that, coordination overhead exceeds parallelism gains.
