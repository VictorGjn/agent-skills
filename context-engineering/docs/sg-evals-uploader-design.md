# sg-evals/* reachability — design + operator playbook

**Status**: design only. Implementation deferred to when sg-evals org access is sorted.
**Phase**: v1.2 P4.4
**Goal**: lift the bench's reachable subset from the projected ~50/70 (post P1+P2 merge) to ~66/70 by handling the 17 sg-evals/* private fork snapshots that the prod bench token can't read. The remaining 4 unreachable tasks reference `torvalds/linux@v3.7.6 / v4.1.15 / v5.6.7 / v5.6-rc2` (git TAG refs, not branch heads) and need a separate indexer fix to reach the full 70/70 (out of P4.4 scope).

## Why these 17 tasks fail the bench

`sg-evals/*` repos are private fork snapshots used by the CodeScaleBench evaluation suite (e.g. `sg-evals/django--674eda1c`, `sg-evals/pytorch--5811a8d7`). The prod indexer's `GITHUB_TOKEN` doesn't have read access to that org, so `ce_index_github_repo` returns `SOURCE_FORBIDDEN` for every sg-evals task.

This is org-side access, not a code problem. The 17 repos (verified against `eval/csb/runs/index-2026-05-06.jsonl`'s SOURCE_FORBIDDEN cluster):

```
sg-evals/aspnetcore--87525573
sg-evals/cal.com--4b99072b
sg-evals/django--674eda1c
sg-evals/envoy--v1.33.0
sg-evals/Ghost--b43bfc85
sg-evals/kafka--3.8.0
sg-evals/kubernetes--11602f08
sg-evals/pytorch--5811a8d7
sg-evals/pytorch--863edc78
sg-evals/pytorch--cbe1a35d
sg-evals/pytorch--ca246612
sg-evals/pytorch--d18007a1
sg-evals/servo--be6a2f99
sg-evals/TensorRT-LLM--b98f3fca
sg-evals/terraform--v1.10.3
sg-evals/vscode--138f619c
sg-evals/vscode--1.96.0
```

## Two paths

### Path A — GitHub App with org install (preferred long-term)

1. Create a GitHub App at `github.com/settings/apps/new` with `Contents: Read` permission.
2. Install on the sg-evals org (requires org-admin approval).
3. Add `GH_APP_ID`, `GH_APP_PRIVATE_KEY`, `GH_APP_INSTALLATION_ID` to Vercel env.
4. Add `cryptography` to `server-prod/requirements.txt` (or use `pyjwt[crypto]`).
5. Patch `_lib/tools/index_github_repo.py:_resolve_github_token()` to do JWT exchange:
   - Build JWT signed with private key (RS256, claims: iss=app_id, iat, exp).
   - POST `/app/installations/{id}/access_tokens` → installation token.
   - Cache token in-process; refresh when expires_at - 5min < now.
6. Re-run bench — sg-evals tasks now resolve.

**Cost**: 1 dependency (~15MB compiled wheel), ~50 lines of code, org-admin approval.

**Benefit**: also gets you 15K req/hr (vs 5K classic PAT) — dissolves the secondary rate-limit pain that bit this session's 4-way bench burst.

### Path B — Local clone + ce_upload_corpus (short-term workaround)

Operator clones each sg-evals repo to local disk (using their own GitHub credentials), then runs an uploader script that builds a corpus from each local clone and POSTs via the existing `ce_upload_corpus` MCP tool. No prod changes; no org-app coordination.

**Constraint**: operator must have personal read access to the sg-evals org.

#### Steps

1. **Clone the 17 repos** (operator's own credentials):
   ```bash
   mkdir -p ~/sg-evals && cd ~/sg-evals
   for repo in $(cat repos.txt); do
     name=$(echo $repo | cut -d/ -f2)
     git clone --depth=1 https://github.com/$repo.git $name
   done
   ```

2. **Run the uploader** (script not yet implemented):
   ```bash
   python eval/csb/upload_local_corpora.py \
     --clones-dir ~/sg-evals \
     --tasks-dir eval/csb/converted-tasks \
     --mcp-url https://ce-mcp-prod.vercel.app \
     --token-file ~/.claude/handoffs/secrets/ce_mcp_bootstrap_token.txt
   ```

#### Uploader design (~150 lines)

```python
# eval/csb/upload_local_corpora.py — sketch

def main():
    1. Walk converted-tasks/, find every spec.json with repo='sg-evals/*'.
    2. For each unique sg-evals repo, locate its clone under --clones-dir.
       - Owner from spec.json ('sg-evals'), name extracted from repo string.
       - clone_dir = clones_dir / name
       - Skip + warn if clone_dir doesn't exist.
    3. For each clone:
       a. Walk filesystem with the same INDEXABLE_EXTENSIONS / SKIP_PATTERNS
          / MAX_FILE_SIZE / source-first sort / cap as the GH-API indexer.
          (Reuse should_index() + sort_key + cap logic from
           server-prod/_lib/vendor/index_github_repo.py.)
       b. For each surviving file:
          - Read content, compute contentHash (md5 of bytes, first 12 hex).
          - Build a minimal tree: {text: <full-content>, title: <filename>}.
            (No AST extraction needed for IR-only bench.)
       c. Compute commit_sha over (path, contentHash) pairs (same fn as the
          GH-API indexer for parity).
    4. Build the ce_upload_corpus payload:
       {
         source: {type: "github_repo", uri: "https://github.com/sg-evals/<name>", branch: "main"},
         corpus_id: spec.corpus_id,        # match what the bench expects
         data_classification: "public",
         embedding: {provider: "none", model: "n/a", dims: 0},
         file_count: len(files),
         commit_sha: ...,
         files: [...]
       }
    5. POST to MCP /api/mcp tools/call name=ce_upload_corpus.
    6. Per-repo result logged to upload-log.jsonl.
```

#### Why this isn't shipped

- Without operator clones in hand, no way to test end-to-end.
- Walking the local fs with the same indexer logic requires either
  duplicating ~100 lines or extracting a `walk_filesystem` helper from
  the vendored indexer (small refactor, deferred).
- Embeddings: the local-uploader path produces keyword-only corpora
  (embedding.dims=0). C2/C3/C4 configs degrade to keyword scoring on
  these tasks. Fine for unblocking H1 (semantic > keyword) on the
  reachable subset that DOES embed; sg-evals tasks contribute to H2-IR
  diversity but not to the H1 mean if they're keyword-only.

  Workaround: client-compute Mistral embeddings during the upload step
  (use `embed_batch` from `server-prod/_lib/embed.py` on the local
  Python). Adds ~$3 / 18 corpora at 300 files each. Not done by default
  in the design above; add `--with-embeddings` flag if needed.

## Recommendation

Path A (GH App) once feasible. Path B (local clone + uploader) as a workable bridge if operator can clone. Either way, sg-evals reachability lifts the bench from ~50/70 to ~66/70. The remaining 4 unreachable tasks reference `torvalds/linux` git TAG refs (`v3.7.6`, `v4.1.15`, `v5.6.7`, `v5.6-rc2`) which the indexer's branch-fetch path doesn't currently handle; closing that gap is a separate triage to reach the full 70/70.

## Tracking

- `.planning/v1.2/task_plan.md` § P4 — P4.4 marked as design-shipped, implementation gated on org access.
- `~/.claude/handoffs/ce_v1_2_milestone.md` — full v1.2 milestone summary including this gap.
