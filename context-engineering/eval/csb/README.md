# CSB adapter — context-engineering MCP

Plugs CE in as a `BASELINE_MCP_TYPE=context-engineering` config alongside Sourcegraph / Augment / baseline. Mirrors the wiring `augment-local-direct` uses.

## Files

| File | Purpose |
|---|---|
| `.mcp.json.template` | Drop into `$CLAUDE_CONFIG_DIR/.mcp.json` inside the sandbox after substituting `{{CE_MCP_URL}}` + `{{CE_MCP_TOKEN}}` |
| `prompt-injection.md` | Text the harness prepends to the task description so the agent reaches for `ce_pack_context` (CSB report's gotcha — agents won't use new MCP tools spontaneously) |
| `setup_corpus.py` | Pre-task hook — calls `ce_index_github_repo` (or `ce_upload_corpus` for unreachable repos) and emits `CE_CORPUS_ID=<id>` for the harness to export to the agent shell |

## End-to-end flow

```
┌─────────────────┐
│ Harbor harness  │  task spec → repo + branch
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│ setup_corpus.py --repo o/n --branch main \      │
│   --mcp-url $CE_MCP_URL --token $CE_MCP_TOKEN   │
│ → POST ce_index_github_repo                      │
│ → prints CE_CORPUS_ID=gh-o-n-main                │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│ Harbor exports CE_CORPUS_ID + drops .mcp.json   │
│ in $CLAUDE_CONFIG_DIR with substituted vars     │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│ Agent reads injected prompt:                    │
│   "Call ce_pack_context with corpus_id=$CE_..." │
│ → ce_pack_context retrieves depth-packed bundle │
│ → agent works on the task                        │
└─────────────────────────────────────────────────┘
```

## Smoke test (1 task, local Docker)

After deploying CE MCP per `server-prod/README.md`:

```bash
export CE_MCP_URL=https://ce-mcp.vercel.app
export CE_MCP_TOKEN=$(cat ~/.config/ce-mcp/token)

# 1. Pre-register a task's corpus
eval $(python eval/csb/setup_corpus.py \
    --repo VictorGjn/agent-skills --branch main \
    --classification public \
    --mcp-url $CE_MCP_URL --token $CE_MCP_TOKEN)

# Now $CE_CORPUS_ID is set in the shell.

# 2. Substitute .mcp.json template
sed "s|{{CE_MCP_URL}}|$CE_MCP_URL|; s|{{CE_MCP_TOKEN}}|$CE_MCP_TOKEN|" \
    eval/csb/.mcp.json.template > /tmp/.mcp.json

# 3. Run a single CSB task with BASELINE_MCP_TYPE=context-engineering
#    (CSB harness specifics out of scope for this repo — see CSB docs)
```

## Hypothesis decision gates (from plan/codescalebench-bench-plan.md)

| H | Test | Pass | Fail action |
|---|---|---|---|
| H1 | C2 vs C1 file recall | ≥+10% | Drop semantic from default |
| H2-IR | C3 vs C2 top-K Jaccard < 0.7 on ≥30% | hold | Ship without MMR |
| H4 | C4 vs C0 reward delta on smoke | ≥+0.05 absolute | Cut depth packer |
| H7 | MMR lift shrinks as budget grows | hold | λ should auto-scale with budget |

## IR-only bench (Phase 2 of the bench plan, no Haiku)

`run_ir_bench.py` drives a config (C1–C4) over a directory of CSB-format tasks
and scores against `ground_truth.json`. Pure IR — no LLM inference, costs only
codestral embeds (~$3 total for 151 single-repo SDLC tasks).

```bash
python eval/csb/run_ir_bench.py \
    --tasks-dir ./csb/tasks/single-repo-sdlc \
    --mcp-url $CE_MCP_URL --token $CE_MCP_TOKEN \
    --config ce-keyword \
    --top-k 5 \
    --output runs/ir-keyword.jsonl
```

Available configs: `ce-keyword` (C1), `ce-codestral` (C2), `ce-codestral-mmr` (C3), `ce-shipping` (C4 = `mode=auto`, server picks).

Output JSONL has one record per task (retrieved paths, ground truth, recall/P@K/F1@K) plus a `_summary` record at the end. `ir_metrics.aggregate()` computes the means.

## What's not here

- The actual CSB harness (clone it from sourcegraph/codescalebench, then point its `BASELINE_MCP_TYPE` switch at this dir)
- Phase 7 Haiku bench run (Phase 1 of the plan: 20 SDLC tasks × {C0, C4, C5} × 100K = ~$18) — needs Victor's go-ahead

## Testing

The adapter has unit-level smoke tests in `tests/test_csb_adapter.py` — they verify the `.mcp.json.template` is well-formed JSON, the prompt-injection text mentions the canonical tool name, and `setup_corpus.py` produces parsable output for the harness.
