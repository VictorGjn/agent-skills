# Findings

Research notes, discoveries, and audit reference material.

## YC RFS Company Brain framing (canonical)

Anabasis pitch (locked, from Anabasis/pitch/1-paragraph.md and the user's verbal frame):

> **"The executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."**

Decompose:
- **Executable runtime** — not a library, not a SaaS dashboard. The thing fires.
- **Installs through** — the install path is the product narrative.
- **Human knowledge** — procedures, RFCs, decisions, internal docs (not just code).
- **Connections** — outbound MCP/API bindings to where company data already lives.
- **AI** — LLM-grade synthesis, semantic shift, multi-hop, concept naming.
- **Skills that automate** — agentskills.io-conformant skills, executed on schedule.
- **Company brain** — a queryable, compounding entity vault per customer.

CE's role inside this: **the engine.** Indexer + Source ABC + EntityStore + packer. Connectors plug in via Source ABC and live elsewhere.

## Anabasis spec v0.1 (current) — CORRECTED understanding

`Anabasis/spec/skill.md` says:
- A skill is conformant if `SKILL.md` passes SkillCheck Free v3.7.2 with no Critical issues.
- Optional fields runtimes use: `mcp_tools`, `entity_kinds`, `version`, `repository`, `license`, `transports`.

`Anabasis/spec/README.md` says (more recent than multi-repo-spine.md):
- **Reference skill #1 = `install-department`** (NOT CE). It captures human procedures into a Department Spec with 7 canonical sections (Tools / Roles / Cadence / Pipeline / Taxonomy / Automations / Metrics).
- v0.2 future reference skills: `find-links`, `audit-process`, `sota-search`, `refresh-department`.
- v0.2 also lands runtime ABCs: `Routine`, `EntityStore`, `SignalSource`, scheduler semantics, conflict resolution.

**CE's actual position**:
- **v0.1**: independently useful "adjacent skill" — current SKILL.md is correct.
- **v0.2**: planned reference impl of **`find-links`** (the cross-department retrieval skill).
- **Wiki schema** = v0.2 `EntityStore` reference impl, drives the spec.
- CE's job in the brain: index across the Department Specs that `install-department` produces, surface cross-department concepts via `wiki/<slug>.md`, retrieve via `pack --wiki`.

This is BETTER positioning than multi-repo-spine assumed. install-department → human knowledge capture. find-links (CE) → AI-grade retrieval/connection. Both serve the company brain.

CE's SKILL.md frontmatter today: `mcp_tools`, `version`, `repository`, `license`, `transports`. Already v0.1-conformant. Missing: `entity_kinds` (defer until Phase 1 wiki schema lands).

**Phase A correction**: ce-anabasis-tie.md plans to write `spec/reference-skills/find.md`. The right naming is `find-links.md` per spec/README. Make this a v0.2-stub (signals intent, doesn't bind contracts) so YC reviewers see the v0.2 trajectory without committing to a half-baked spec.

## Sourcegraph capability gaps absorbed into roadmap

| Gap | CE today | Slot |
|---|---|---|
| Incremental indexing | None — full re-parse | Phase 0 |
| Git signals (since/PR/diff/churn) | None | Phase 1 (alongside Source ABC) |
| Local embeddings | OpenAI-only | Phase 0.5 (BGE-small) |
| Authority / structural importance | KT priority only | Phase 0 (in-degree tiebreak + headline annotation) |
| Tree-sitter coverage all 14 langs | Silent fallback to regex for 11 of 14 | Phase 0 |

## Anabasis multi-repo spine (canonical)

```
Anabasis Spec (PUBLIC)              ← drives everything below
   │
   ├─► context-engineering          ← reference skill #1 (find) + EntityStore reference impl
   ├─► Anabasis Runtime             ← closed 90 days then Apache; hosted = paid
   └─► syroco-product-ops           ← internal Syroco brain (98 skills, 11 routines, 5 sources)
        └─► company-knowledge       ← Syroco-private entity vault
```

Three structural commitments:
1. CE Phase 1 wiki schema = Anabasis EntityStore ABC
2. CE Source ABC = Anabasis SignalSource ABC
3. CE ships under Anabasis as canonical "find" skill

## YC sprint critical-path note

- Phase A (½ day) MUST land before YC submission Mon May 4 8pm PT
- Phase 0 incremental indexing should NOT block YC; do it in parallel if time
- Demo on Fri May 1 needs the bug-fix demo OR a company-brain demo runnable — not both required

## Memory note

Updated `project_context_engineering_scope` from "code-context tool only" → "engine, not tool". Scope discipline preserved by **where connector code lives**, not by what corpus types CE supports.

## SKILL.md headline rewrite (2026-05-01, mid-session)

User course-correction: CE is not only a depth packer.

**Old headline**: "Pack 40+ files at 5 depth levels into any LLM context window."

**New headline**: "The engine for building and querying a queryable, compounding company brain — across code, human-curated knowledge, and connector streams."

Five tightly-coupled capabilities now lead the SKILL.md body:
1. Multi-source indexer (existing, Phase 0 hardened)
2. Source ABC (Phase 1 — the connector contract)
3. EntityStore (Phase 1 — three-tier brain)
4. Synthesizer + Auditor (Phase 1 — compounding)
5. Retrieval surface (Phases 0+0.5+2 — depth packer is one piece)

The depth packer is **one capability**, not the headline. Phases 1+2 deliverables now positioned as fulfillment of the engine framing rather than "scope creep."

This is what makes CE the v0.2 `find-links` reference impl — find-links operates on the EntityStore, not a flat file index.

## Licensing posture (2026-05-01, locked option b)

**Open primitives, closed orchestration.**
- **Spec** = Apache-2.0 open
- **CE engine** = **MIT public** (option b — reversing existing public PRs erodes credibility for no moat gain)
- **Reference skills** (`install-department`, `find-links`, `audit-process`, `sota-search`, `refresh-department`) = MIT public
- **Runtime** = commercial closed (operational complexity is the moat, not source-code-secrecy)
- **Hosted plane** = commercial SaaS

Market signal: Sourcegraph charges ~$16k/yr for indexer-packer functionality. We charge for the *runtime + hosted plane* — orchestration is harder to operate than the engine alone. The Cursor / Vercel / Snowflake model: closed orchestration on open primitives.

Canonical compass: `agent-skills/context-engineering/plan/COMPASS.md`.

Cascading reconciliation completed Sat 2026-05-01 across YC essays, methodology, spec README, multi-repo-spine, ce-anabasis-tie, find-links, ROADMAP-v4, SKILL.md, brand/landing-page, site/index.html, pitch/founder-video-script, pitch/1-paragraph, demo/storyboard. Remaining vestigial references in CE phase plans (phase-3.md, phase-4.md, phase-5.md, related audits, task_plan.md) are agent-facing and post-submission OK.

## Phase 0.1 audit (corrected manifest, 2026-05-01)

ROADMAP v3 § Phase 0 claimed 5 modules missing: `pack_context_lib.py`, `code_graph.py`, `index_workspace.py`, `mcp_server.py`, `embeddingResolver.ts`.

**Reality (verified by `wc -l scripts/*.py` + `find scripts/*.ts`)**:

| Module | Status | Lines |
|---|---|---|
| `pack_context_lib.py` | ✅ shipped | 569 |
| `code_graph.py` | ✅ shipped | 503 |
| `index_workspace.py` | ✅ shipped | 347 |
| `mcp_server.py` | ✅ shipped | 375 |
| `embeddingResolver.ts` | ✅ shipped | (TS file present at `scripts/embeddingResolver.ts`) |
| `pack_context.py` | ✅ shipped | 635 |
| `embed_resolve.py` | ✅ shipped | 482 |
| `ast_extract.py` | ✅ shipped | 490 |
| `concept_labeler.py` | ✅ shipped | 198 |
| `community_detect.py` | ✅ shipped | 203 |
| `index_github_repo.py` | ✅ shipped | 411 |
| `graphify_adapter.py` | ✅ shipped | 180 |
| `feature_map.py` | ✅ shipped | 859 |
| `visualize_graph.py` | ✅ shipped | 1035 |

**`requirements.txt` already exists** with pinned versions:
```
requests>=2.31,<3
tree-sitter==0.21.3
tree-sitter-languages==1.10.2
mcp[cli]>=1.0,<2
```

**`pyproject.toml` already exists** with project metadata, optional-dependency groups (`ast`, `mcp`, `all`), MIT license, version 0.3.0.

**Missing for Phase 0**:
- `sentence-transformers` not in requirements (needed for Phase 0.5.4 BGE)
- `wiki/` subdirectory in scripts/ (needed for Phase 1)
- Schema versioning fields (Phase 0.3)
- File lock (Phase 0.3)
- Cache relocation (Phase 0.2)
- Incremental indexing (Phase 0.5)
- Tree-sitter coverage audit (Phase 0.6 — verify which of 14 langs actually use TS vs regex)

**Phase 0.1 effort revised**: M (2 days) → **S (½ day)** — most work is verification + minor additions, not vendoring entire modules. Mostly: add `sentence-transformers` to requirements; add a `[project.optional-dependencies] embed = ["sentence-transformers>=2.7,<3"]` group; verify console scripts exposure decision.

**Updated Phase 0.1 deliverable for plan/phases/phase-0.md**: replace "Vendor missing modules" with "Verify all advertised modules + add `sentence-transformers` to optional deps + add `wiki/` subdir scaffolding".

## What's at stake (positioning)

1. **YC narrative coherence** — depth packer doesn't ladder to "engine that powers the company brain"
2. **Distribution funnel** — users searching for "company brain engine" or "build wiki from connectors" now find this skill
3. **Competitive framing** — vs Sourcegraph/Cody on engine-with-EntityStore terms (where CE wins on open-spec + depth packer + Anabasis tie), not on precise-xref terms (where CE loses)
4. **Phase 1+2 receivability** — wiki schema and pack --wiki are fulfilment, not scope creep
5. **Karpathy LLM-wiki adjacency** — already in ROADMAP § 0; now reflected in headline
