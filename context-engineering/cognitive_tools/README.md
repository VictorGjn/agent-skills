# Cognitive tools

**Status: experimental.** No measured uplift yet.

Reasoning scaffolds that wrap a packed context with structured prompting before the LLM sees it. Adapted from [davidkimai/Context-Engineering](https://github.com/davidkimai/Context-Engineering)'s `cognitive-tools/cognitive-templates/` (MIT-licensed). The IBM Zurich result that paper cites — "cognitive tools 26.7% → 43.3% on AIME2024" — is the kind of measurable lift this directory could verify; we have not run that bench.

## What this is

Two markdown templates, each scoped to one of the existing `--task` presets:

| Template | Maps to `--task` | Source template (davidkimai) | Adaptation |
|----------|------------------|-------------------------------|------------|
| [`fix.md`](./fix.md) | `fix` | `cognitive-templates/verification.md` (Solution Verification) | `{{problem}}/{{solution}}` → `{query}/{packed_context}` |
| [`explain.md`](./explain.md) | `explain` | `cognitive-templates/understanding.md` (Question Analysis) | `{{question}}` → `{query}` |

## How it plugs in

`pack_context.py` gains a `--cognitive-tool=<name>` flag. When set, the script:

1. Looks up `cognitive_tools/<name>.md`.
2. Splits the template at the `{packed_context}` placeholder into a prefix and suffix.
3. Renders: `<prefix>\n<packed depth-output>\n<suffix>`.
4. Stdout is one stream — the LLM caller sees the scaffold *around* the packed content.

The flag is pure-additive. Without it, the packer's behaviour is unchanged.

## Attribution

Templates are derivative works of [davidkimai/Context-Engineering](https://github.com/davidkimai/Context-Engineering) (MIT). Original prompt text is preserved in the body of each template; the placeholder syntax was changed from `{{double-brace}}` to single-brace `{name}` to match the existing `pack_context` conventions.

The upstream MIT license — including the original copyright notice — is reproduced verbatim in [`LICENSE-davidkimai`](./LICENSE-davidkimai) per the MIT terms.

## Why this is experimental

Per the skill's own value-over-proof posture (see `feedback_value_over_proof` in user memory), we ship the scaffolds without claiming a measured improvement. The eval harness lives at `scripts/eval/`; running these scaffolds through it is a separate piece of work, deliberately deferred.

## Adding a new tool

1. Drop a markdown file in this directory.
2. Use `{query}` and `{packed_context}` placeholders. Optionally `{depth_levels}` for a per-level summary.
3. Call `pack_context.py --cognitive-tool=<filename-without-md>`.

No code change needed for new templates.
