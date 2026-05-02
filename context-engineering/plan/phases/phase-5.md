# Phase 5 — Distribution flywheel

> **Goal**: Get adoption among AI-native devs / staff engs / Karpathy-adjacent crowd who already want this. Compound the open-core release into ≥500 weekly active CE users + ≥3 inbound non-Syroco Anabasis pilots by month 6.
>
> **Effort**: S-M (ongoing) | **Status**: pending | **Trigger**: Phase 4 close (HN essay landed)
>
> **Source**: ROADMAP.md v3 § Phase 5 + multi-repo-spine.md § Phase 6 + methodology/onboarding-toc.md.

## Why this phase

Phases 0-4 build and release the engine + runtime + spec + reference skills. Phase 5 is **the funnel**. Without distribution, the open-core release is a tree falling in a forest.

Two parallel funnels:
1. **CE-as-skill funnel**: AI-native devs install CE alone (`pip install -e .`), use it for code-context / build-wiki workflows, hear about Anabasis
2. **Anabasis-runtime funnel**: ops-heavy companies see the YC essay / HN post, install the runtime, eventually call CE through it

The former feeds the latter. CE is the wedge.

## Deliverables

### 5.1 — Public essay: "10 patterns Claude Code uses to manage context" (S, 2-3 days)

Cheapest distribution lever. Off the existing `claw-code-context-engineering-analysis.md` work.

Structure:
- 10 concrete patterns (depth packing, symbol extraction, anti-hallucination filters, multi-hop, etc.)
- Each pattern: what / why / code example
- Closing tie: "you can run all of this with `pack` from the open-source `context-engineering` skill"

**Audience**: AI-native devs, staff engs, anyone running Claude Code or Cursor.

**Acceptance**: published to LinkedIn + GitHub gist + X. Track inbound CE installs in 2 weeks.

### 5.2 — 60-second screen-rec: bug-fix demo (S, 1 day)

Stack trace → `pack "users getting 401" --why` → fixed bug.

Show:
- The query (free-form English)
- The trace (`--why` output: 14 files at 5 depths, 95% budget)
- The output (depth-packed markdown)
- The paste into Claude → one-shot fix

**Acceptance**: 60-second clean recording; embedded in CE README, Anabasis landing page, HN essay.

### 5.3 — 60-second screen-rec: company-brain demo (S, 1 day)

`anabasis sources add notion` → `pack --wiki "what changed in our refund policy last quarter"` → entity page + provenance + answer.

Show:
- Connector wired (one command)
- Multi-source query
- Entity page with provenance trail
- Answer pulled from the wiki layer

**Acceptance**: 60-second clean recording; second demo for Phase 4 HN essay; differentiator vs Sourcegraph/Cody.

### 5.4 — MCP directory listings (S, 1 day each)

Submit CE + Anabasis runtime to:
- Anthropic's MCP directory (when public)
- [Smithery](https://smithery.ai) — already accepts MCP submissions
- PulseMCP — community directory
- Composio, Cequence — gateway directories

**CE deliverable**: ensure MCP server fields (manifest, transports, examples) are pristine for directory submission.

**Acceptance**: CE listed in ≥3 directories within 2 weeks of Phase 4 close.

### 5.5 — Cursor `.cursorrules` snippet (S, ½ day)

Cursor users install CE via `.cursorrules` in their workspace:

```
# .cursorrules
# Use context-engineering skill for codebase queries
on file_change:
  if ".context-engineering/index" not exists:
    run: python3 ~/.cursor/skills/context-engineering/scripts/index_workspace.py .

slash_command /pack:
  run: python3 ~/.cursor/skills/context-engineering/scripts/pack_context.py "$ARGUMENTS"
```

**Acceptance**: snippet documented in CE README + Cursor MCP directory listing.

### 5.6 — Hosted MCP endpoint (Phase 4.6 carry-over)

Free tier: `mcp.anabasis.tech/find-links`. 100 packs/day, no persistent index.

Paid tier (announced post-Phase-5): persistent index, higher rate limits, multi-tenant. Funnel into Anabasis hosted cloud.

**Acceptance**: free tier live; paid tier opt-in within 4 weeks.

### 5.7 — Naming decision (S, 30 min)

The recurring question from ROADMAP § 6: keep `context-engineering` (precise, accurate) or rename to `repo-context` / `code-context` / `find-links` (concrete, googleable)?

**Per CPO review**: defer until before public essay (Phase 5.1). Decide now.

**Recommendation**: keep `context-engineering` as the GitHub repo + pip package. **Add** `find-links` as the skill name in Anabasis spec (already done — find-links.md). Two names for the same thing — Karpathy-precise + Anabasis-canonical.

**Acceptance**: SKILL.md frontmatter + repo path + spec all coherent.

### 5.8 — Kill-criteria measurement (S, ongoing)

ROADMAP v3 lists kill criteria for the bet:
- Anthropic ships native Claude Code persistent project memory + CE adoption < 500 weekly active users
- After 6 months, average outlinks per page is flat or declining on the showcase wiki
- No external installs from Karpathy-adjacent / AI-engineering audience by month 4

**Phase 5 deliverable**: track these monthly. Phase 0.5.5 telemetry + GitHub stars + npm/pip download counts feed the dashboard.

If 6-month review hits any kill criterion, shut down. Per CPO review.

**Acceptance**: dashboard live; monthly review cadence established.

### 5.9 — Open-source companion repos (M, ongoing)

Once CE downloads stabilize, accept community PRs:
- New Source classes contributed by the community (RSS, S3, Postgres, etc.) — but **only if they implement Source ABC cleanly + tests pass**
- New `--task` presets for community-specific workflows
- Translations of SKILL.md
- New language tree-sitter parsers as they mature

**CE deliverable**: maintainer time budget (~4 hours/week) for community PR review.

**Acceptance**: ≥5 community PRs merged within 6 months of Phase 5 start.

## Acceptance criteria (phase-level)

- [ ] Public essay published
- [ ] Both 60-sec demos recorded + embedded
- [ ] CE in ≥3 MCP directories
- [ ] Cursor `.cursorrules` snippet documented
- [ ] Hosted MCP free tier live
- [ ] Naming decision locked
- [ ] Kill-criteria dashboard tracking
- [ ] ≥500 weekly active CE users by month 6 (or kill)
- [ ] ≥3 inbound non-Syroco Anabasis pilots by month 6 (or escalate)

## Dependencies

- ✅ Phase 4 = open-core release + spec v0.2 = the marketing peg
- ⚠️ Anthropic / Cursor / OpenAI MCP directory acceptance (out of CE control)
- ⚠️ Karpathy-adjacent audience attention (out of CE control)
- ⚠️ Anabasis runtime stable enough for non-Syroco pilots

## What this phase does NOT do

- Not a paid marketing campaign (organic distribution per RFS audience)
- Not enterprise sales (different audience; methodology explicitly "not chasing enterprise")
- Not feature work (CE is feature-complete at v0.2; iterate based on usage)
- Not localization beyond community PRs

## YC RFS alignment (preview)

| Pillar | Phase 5 contribution |
|---|---|
| Executable runtime | Hosted MCP endpoint = runtime as a service |
| Installs | Cursor snippet + MCP directory = install via discovery, not manual |
| Human knowledge | Essay teaches devs what CE actually does (positioning fix) |
| Connections | Hosted endpoint + community Source classes = expanded connection surface |
| AI | (inherited — no new AI surface) |
| **Skills that automate** | **Phase 5 = skills find their consumers. Distribution = adoption = compounding.** |
| **Company brain** | **External companies build their own brains using the open stack. Proof of category, not just product.** |

7/7 served. Phase 5 closes the loop: open-source runtime + open spec + reference skills + paid hosted plane + community contributions = a self-sustaining ecosystem.
