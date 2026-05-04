# CSB prompt injection — context-engineering MCP

This text is prepended to the task description fed to the agent under the
`context-engineering` baseline config. Without it, agents won't reach for
new MCP tools spontaneously (CSB's own gotcha — verified in their report).

Mirror the structure of `augment-local-direct`'s injection so the harness
treats CE as a peer of Augment, not a custom path.

---

## INJECTION TEXT (verbatim, no f-strings — the harness substitutes nothing here)

```
You have access to the `context-engineering` MCP server. It indexes the
codebase you are working on and packs depth-graded context into a token
budget. Use it BEFORE reading individual files.

Workflow for retrieval:

1. Call `ce_pack_context` with:
   - `query`: a natural-language description of what you need (e.g. the
     task statement, paraphrased to focus on the technical surface)
   - `corpus_id`: the corpus pre-registered for this task (see env var
     `CE_CORPUS_ID` in your shell — the harness sets it)
   - `budget`: 100000  (sweep variable; harness may override)
   - `mode`: "auto"  (let the server pick keyword vs semantic)
   - `task`: one of {"fix", "review", "explain", "build", "document",
     "research"} — pick the closest match to the work type

   The response includes a packed markdown bundle plus a `files[]` list
   with paths and depths. Use this as your primary context.

2. If a specific file you need is missing or rendered too shallow,
   fall back to `ce_find_relevant_files` with the same query — it returns
   ranked paths only — then `cat`/`read` the ones you actually need.

3. Do NOT call `ce_pack_context` repeatedly with similar queries. The
   pack already includes adjacent files at lower depths; re-querying
   rarely helps and burns the bench budget.

The corpus has already been indexed against the current commit of this
repo. You do not need to call `ce_index_github_repo` or
`ce_upload_corpus` — those are for setup, not retrieval.
```

---

## Notes for harness operators

- The harness MUST set `CE_CORPUS_ID` in the agent shell before task start.
  Convention: `gh-{owner}-{repo}-{branch}` slugified (matches CE's server-derived id).
- If pre-task indexing failed, the harness should skip CE for that task and
  log it as `setup_failed` rather than running with no context.
- Per SPEC § 3.1, multi-corpus is opt-in via `corpus_ids[]`. CSB single-repo
  tasks always use `corpus_id` (single string).
- The agent should NOT be told the underlying retrieval is keyword-only in v1
  — that biases their queries. `mode: auto` is the deliberately abstract dial.
