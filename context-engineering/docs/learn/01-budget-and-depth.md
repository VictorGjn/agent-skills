# Step 1 — Budget and depth

> **Molecule: composition.** A packed answer is not a single chunk. It's five depth levels composed against one budget.

## What you'll do

Re-run the same query at three different budgets and watch the depth distribution shift.

## Commands

```bash
python3 scripts/pack_context.py "authentication middleware" --budget 4000  --why
python3 scripts/pack_context.py "authentication middleware" --budget 8000  --why
python3 scripts/pack_context.py "authentication middleware" --budget 12000 --why
```

The `--why` trace prints, among other things:

```
- **Budget**: 11,840 / 12,000 tokens (98.7%) on 18 files
```

## The five depth levels

| Depth | What gets rendered | When the packer picks it |
|-------|--------------------|---------------------------|
| Full | Entire file content | Highest scoring file(s), budget permitting |
| Detail | Headings + first paragraph per section | Strong matches that don't fit Full |
| Summary | Headings + one-line summaries | Mid-confidence matches |
| Headlines | Heading list only | Tangentially relevant |
| Mention | `path (87 tok)` one-liner | Possibly relevant, cheap to keep |

The packer descends through depths greedily, holding the budget close to 95% usage. A larger budget *does not* just append more files at Mention — it promotes existing files up the depth ladder.

## Concept

This is the difference between a fixed-chunk-size RAG and a depth-aware packer. A vanilla RAG asks "which 8 chunks?". The packer asks "given 12,000 tokens, what's the best mix of resolutions across the candidates?".

In practice this means:

- Small budgets favour breadth via Mention/Headlines
- Large budgets favour depth via Full/Detail
- The relevance ranking is the same; only the rendering changes

## Override the rendering

You can force a profile:

```bash
# Quality mode: fewer files, deeper rendering
python3 scripts/pack_context.py "auth middleware" --budget 8000 --quality

# Topic filter: drop off-topic matches before packing (anti-hallucination)
python3 scripts/pack_context.py "auth middleware" --budget 8000 --topic-filter
```

`--quality` caps candidates at 15. `--topic-filter` runs an off-topic filter before packing — useful in noisy knowledge corpora where keyword matches aren't always semantic matches.

## Try it

Run the same query at the three budgets above. Diff the file counts and the depth distribution. The depth ladder is the molecule — once you internalize it, every later step in this ladder just changes *which candidates* feed the same packing logic.

## Next

[Step 2 — Index a workspace](02-index-a-workspace.md). The cell layer: the packer's persistent memory of what's in the corpus.
