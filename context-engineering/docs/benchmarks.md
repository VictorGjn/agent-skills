# CE benchmarks — demonstration run

**Status:** Demonstration only, not a full benchmark suite. Per CE's `value_over_proof` discipline (defer baselines until usage data exists), this doc ships **reproducible commands and numbers from a small reference set** — enough to neutralize external "X% context reduction" framing without committing to a benchmarking treadmill.

> If you want an apples-to-apples test against [Context Signals MCP](./vs-context-signals.md)'s 79–95% on Cal.com TRPC and Trigger.dev Core, run the commands below against those repos. The methodology is documented; the numbers are reproducible.

## What `pack` does (recap)

CE's `pack` packs N source files into a token budget at **5 graded depths** (Full / Detail / Summary / Headlines / Mention). Most-relevant files at full content; less-relevant compressed down to a one-line mention. Target: ~95% budget utilization.

This is a **different operation** from "serve the symbol map" (Context Signals) or "return one section by anchor" (lat.md). Direct number comparison is apples-to-pears unless the same query is run through each tool.

## Methodology

For each (repo, query) pair:

1. Index the repo: `python -m scripts.index_workspace <repo>` → `cache/workspace-index.json`
2. Compute total tokens: sum of `tree.totalTokens` across all indexed files (naive "include everything" baseline).
3. Run `pack`: `python scripts/pack_context.py "<query>" --budget <B> --mode keyword`
4. Output token count: `len(output) / 4` (CE's own estimate).
5. Reduction: `(1 - pack_tokens / total_tokens) × 100`.

`--mode keyword` is used for reproducibility (no embeddings dependency). `--mode semantic` typically yields tighter packs at the same budget — re-run with `OPENAI_API_KEY` set to compare.

Hardware: Windows 11, Python 3.14, no GPU. Numbers are wall-clock single-run.

## Reference: CE itself (51 files, 21,249 tokens)

A small repo — useful for quick verification but understates reduction (every file is "relevant" to most CE-internal queries).

| Query | Budget | Pack output | Time | Reduction |
|---|---:|---:|---:|---:|
| `users getting 401 on refresh tokens` | 8,000 | ~11,494 | 122ms | **45.9%** |
| `how does the wiki link parser work` | 4,000 | ~5,928 | 123ms | **72.1%** |
| `audit broken refs in pre-commit` | 8,000 | ~12,626 | 117ms | **40.6%** |

Smaller repos compress less because every file is plausibly relevant. The interesting numbers are on larger repos.

## How to run on Cal.com TRPC / Trigger.dev / your repo

```bash
# Pick a target repo
git clone https://github.com/calcom/cal.com /tmp/cal.com
cd /path/to/agent-skills/context-engineering

# Index
python -m scripts.index_workspace /tmp/cal.com

# Pick a query that maps to an actual user task
python scripts/pack_context.py \
    "where does the booking flow handle timezone conversion" \
    --budget 8000 --mode keyword

# Compare against full-content baseline
python -c "
import json
idx = json.load(open('cache/workspace-index.json'))
print(f'baseline: {sum(f.get(\"tokens\", 0) for f in idx[\"files\"]):,} tokens across {len(idx[\"files\"])} files')
"
```

The expected pattern: **larger repos show higher reduction** because more files are off-topic and demoted to Mention or excluded. CE's `pack` doesn't claim a single-percentage figure — the number depends on repo size, query specificity, and budget. The framing CE pitches is *"40+ files at 5 depth levels into your budget instead of 2-3 fully"* — depth-grading, not strict reduction.

## How CE's numbers differ from Context Signals'

[Context Signals MCP](./vs-context-signals.md)'s published 79–95% numbers come from serving the **symbol map only** — file paths, function/class names, line numbers — without any source code. That's a different output shape from `pack`, which returns actual content at varying depths.

A fair head-to-head:

- **Symbol-map operation**: run CE's `code_index.py` and dump `cache/code_index.json`. That JSON's size vs raw repo size is comparable to Context Signals' framing.
- **Context-pack operation**: run `pack` at a budget. Numbers above.

If the goal is "give the agent enough context to answer at the smallest token cost," neither single number is the right metric — answer quality at fixed budget is. That's the eval harness's job (see `scripts/eval/`), and it's deferred per `value_over_proof` until real usage signals which budget / mode wins per task class.

## What ships with the eval harness

`scripts/eval/` already contains the full evaluator infrastructure used internally:

- `run_harness.py` — multi-mode multi-query runner
- `judge_llm.py` — LLM-as-judge for answer quality at fixed pack
- `cases/` — golden-set queries with expected answers
- `diff_runs.py` — cross-version regression detection

These are not run as part of the demo benchmarks above. When CE goes through a quality-driven release (post-Phase 5), the harness produces the numbers. Until then: the table above + reproducible commands are what's published.
