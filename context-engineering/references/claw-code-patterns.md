# Claw Code Patterns — Context Engineering Reference

> Extracted from [instructkr/claw-code](https://github.com/instructkr/claw-code), a clean-room
> Python + Rust rewrite of an AI coding agent harness.

## 1. Layered Context Assembly

Context is **assembled**, not static. Each turn constructs a composite from:

```
System Init Message          ← trust-gated
  + Workspace Context        ← file counts, archive presence
  + Setup Report             ← Python version, platform, prefetch results
  + Command/Tool Registries  ← loaded from JSON snapshots
  + Routed Matches           ← fuzzy token scoring selects relevant tools
  + Permission Denials       ← deny-lists filter available tools
  + Session History          ← transcript with sliding window compaction
  + User Prompt              ← the actual message
```

## 2. Permission = Visibility

Denied tools are **removed from context**, not just discouraged.
What the model can't see, it can't hallucinate about.

## 3. Fuzzy Prompt Routing

Not all tools are shown every turn. The runtime tokenizes the user prompt
and scores each tool by overlap. Only relevant tools enter context.

## 4. Token Budget Dual-Stop

Two stop conditions: `max_turns` + `max_budget_tokens` with projected usage.

## 5. Transcript Compaction

Sliding window keeps last N turns. Combined with depth-packing.

## 6. Streaming Event Audit

`message_start → command_match → tool_match → permission_denial → message_delta → message_stop`

## 7-10. Prefetch DAG, Execution Registry, Session Persistence, Trust-Gated Init

See transfer map below.

---

## Transfer Map

| Pattern | → modular-crew | → modular-patchbay | → skill |
|---------|---------------|-------------------|---------|
| Permission = Visibility | permissionFilter.ts | PermissionGate.ts | ref |
| Fuzzy Prompt Routing | — | PromptRouter.ts | ref |
| Budget Dual-Stop | budgetGuard.ts | — | ref |
| Transcript Compaction | — | TranscriptCompaction.ts | ref |
| Event Stream | eventStream.ts | — | ref |
