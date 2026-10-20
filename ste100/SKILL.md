---
name: ste100
description: "Write answers, explanations, docs, commit and PR text in ASD-STE100 Simplified Technical English (Issue 8): short sentences, active voice, simple tenses, one word per meaning. Use when the user asks for STE, STE100, Simplified Technical English, or 'explain it like an aircraft manual', or wants unambiguous technical prose. Do NOT use to rewrite code, identifiers, or quoted text."
requiredApps: []
---

# ASD-STE100

Write in ASD-STE100 Simplified Technical English. This follows [Andrej Karpathy's tip](https://x.com/karpathy/status/2105819303471976479): ask the LLM to explain in ASD-STE100.

Read [rules.md](./rules.md) and obey every rule in it.

## Always-on mode (Claude Code plugin)

The folder is also a Claude Code plugin. Its hooks inject `rules.md` at session start and a one-line reminder on each prompt:

```bash
claude --plugin-dir ./ste100          # one session
```

To keep it on, add this folder to a plugin marketplace and install `ste100`. If your settings set a non-English `language`, remove it. The rules require English answers.

## Limits

- The rules come from ASD-STE100 Issue 8, Part 1 (writing rules). This skill does not include the STE dictionary (about 900 approved words), which has copyright. Get the full standard from [asd-ste100.org](https://www.asd-ste100.org).
- The hooks only add the rules to the context. Nothing checks the output.
