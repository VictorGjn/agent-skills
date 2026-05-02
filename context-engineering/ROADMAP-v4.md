# Context-Engineering Roadmap v4 — Engine for the Anabasis Company Brain

> **Goal**: Deliver context-engineering as **the engine that powers Anabasis's company brain** — indexer + Source ABC + EntityStore reference impl + depth-aware packer + multi-hop reasoning + MCP surface. Connectors stay in `syroco-product-ops`. CE is independently useful and Anabasis-conformant.
>
> **YC RFS framing satisfied**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."
>
> **Supersedes**: ROADMAP.md v3 (code-context-only scope).
> **Companion docs**: `plan/task_plan.md`, `plan/findings.md`, `plan/phases/*`, `plan/audits/*`.

---

## 0. What changed from v3

| v3 said | v4 says | Why |
|---|---|---|
| "Code-context tool only, NOT company-wide KG" | "**Engine** for indexer + Source ABC + EntityStore + packer; connectors live in syroco-product-ops" | Anabasis multi-repo-spine commits to CE as reference impl of EntityStore + find-links. The old framing structurally blocked this. |
| Phase 1 wiki schema "co-designed with Anabasis spec" — but spec/skill.md v0.1 is empty of EntityStore | CE Phase 1 **produces** the v0.2 EntityStore + SignalSource ABCs; spec documents them | Anabasis spec/skill.md § 7 explicitly says these are "coming in v0.2". CE drives the spec. |
| No incremental indexing, no git signals, no local embeddings, no LSP, no authority weighting | All five added at the right phase (LSP **dropped**; tree-sitter coverage pulled UP from Deferred instead) | Sourcegraph capability gap analysis + locked decisions (2026-05-01) |
| Headline: "Pack 40+ files at 5 depth levels" (depth packer) | Headline: "**The engine for building and querying a queryable, compounding company brain**" | CE is much more than a depth packer — depth packing is one of five capabilities. |
| Eval/benchmarks deferred to "Proving Layer" | Stays deferred + **internal regression eval** added to Phase 0 | Value-over-proof preference; regression ≠ benchmark |
| Bug-fix demo as hero use case | Bug-fix demo + **company-brain demo** | YC + HN essay both need both demos |

**Memory updated 2026-05-01**: `project_context_engineering_scope` now reads "engine, not tool". Scope discipline preserved by *where connector code lives*, not by what corpus types CE supports.

---

## 1. Locked decisions (2026-05-01)

1. CE = **engine** (indexer + Source ABC + EntityStore + packer); connectors stay in `syroco-product-ops`
2. CE Phase 1 produces **EntityStore reference impl**; Anabasis spec v0.2 documents it
3. **BGE-small** is the local embeddings default (kills `OPENAI_API_KEY` requirement)
4. **Drop LSP** from Phase 2; **expand tree-sitter to all 14 advertised languages** (pulled UP from Deferred to Phase 0)
5. Regression eval corpus = **CE itself + efficientship-backend + company-knowledge** (code/code/markdown coverage)
6. **Option (b) licensing posture**: spec Apache-2.0; CE engine + reference skills MIT; **runtime + hosted plane commercial closed**. The moat is operational, not source-code-secret. Cursor/Vercel/Snowflake model.

---

## 2. Sequencing

```
YC SPRINT (May 4)              POST-FUNDING (90-day window)               OPEN-CORE (Day 90+)
    │                               │                                          │
    ▼                               ▼                                          ▼
 Phase A ─► Phase 0 ─► Phase 0.5 ─► Phase 1 ─► Phase 2 ─► Phase 3 ─► Phase 4 ─► Phase 5
 Tie       Truth-up   Surface       Wiki +     pack       Anabasis    Open-core  Distribution
 (½ d)     +incr      +BGE          Source     --wiki     bootstrap   release    flywheel
           +AST       (3-5 d)       ABC +      (1-2 wk)   (post-fund) (release)  (ongoing)
           +auth      ↓             EntityStore                       │
           +eval      v1.0          (~2 wk)                           │
           (~1 wk)    surface                                         ↓
                                                              Spec v0.2 frozen
                                                              CE find-links ref impl
```

**v1.0 ship** = Phases 0 + 0.5 + 1 + 2.
**YC submission** unblocked by Phase A only.
**Post-funding** = Phases 3-5 paced by pilots, not calendar.

---

## 3. Phase summary

| # | Phase | Effort | Key deliverables | RFS pillars hit |
|---|---|---|---|---|
| **A** | Anabasis tie | ½ day | spec README/skill exist; `find-links.md` v0.2-draft stub; CE SKILL.md trajectory paragraph | 6 strong + 1 indirect |
| **0** | Truth-up + incremental + tree-sitter + authority + regression eval | M (~1 wk) | Vendor missing modules; pinned deps; cache relocation; schema versioning; atomic writes; relations cap fix; incremental indexing; tree-sitter to 14 langs; authority signals; RRF; regression eval; MCP HTTP hardening | 6 strong + 1 indirect |
| **0.5** | Surface collapse + telemetry + BGE local embeddings | S (3-5 d) | One-verb `pack`; 3 user flags; `--why` trace; **BGE-small default**; per-call telemetry; activation metric; off-topic-guard headline; `/pack` slash commands | 6 strong + 1 indirect (Installs through = **Critical**) |
| **1** | Wiki schema + Source ABC + git signals + EntityStore reference impl | M (~2 wk) | Three-tier brain layout (`raw/`+`events/`+`wiki/`); wiki schema with full provenance; events extractor; Source ABC + 2 concrete classes; semantic_shift verify; `wiki_init.py`; Auditor; git signals (`--since`, `--pr`, `--diff`, churn); spec v0.2 EntityStore + SignalSource docs; round-trip test | 7/7 (4 Critical) |
| **2** | `pack --wiki` + multi-hop + lens + MCP wiki tools | M (1-2 wk) | `pack --wiki` mode; `--multi-hop N` reasoning paths; query-as-lens reranking; MCP `wiki.{ask,add,audit,export}`; anti-hallucination filters extended; find-links v0.2 freeze; CE tagged | 6 strong + 1 indirect (3 Critical) |
| **3** | Anabasis 5-day bootstrap | M (post-fund) | Day 0 `npx @anabasis/init`; Day 1 entities extract; Day 2 `sources add`; Day 3 `skills add`; Day 4 routine YAML; Day 5 loop closure; bootstrap services package | 7/7 (6 Critical) |
| **4** | v1.0 commercial release + spec v0.2 freeze | S (release wk) | Spec v0.2 freeze (Apache-2.0); CE tagged + MIT (option b); reference skills MIT; **runtime stays commercial closed**; hosted MCP endpoint v1; HN launch essay framed as "we built and shipped the engine + runtime" not "we open-sourced it"; pre-release first-run pre-flight | 5 strong + 2 indirect (3 Critical) |
| **5** | Distribution flywheel | S-M (ongoing) | Public essay; bug-fix demo + company-brain demo; MCP directory listings; Cursor `.cursorrules`; hosted MCP free tier; naming decision; kill-criteria dashboard; community PR review budget | 5 strong + 2 indirect (3 Critical) |

**Total RFS coverage**: every phase serves ≥5 of 7 pillars; Phases 1, 3 hit 7/7 with 4-6 Critical strengths.

---

## 4. What CE does (revised headline)

> **The engine for building and querying a queryable, compounding company brain — across code, human-curated knowledge, and connector streams.**

Five tightly-coupled capabilities ship as one skill:

1. **Multi-source indexer** — AST (14 languages via tree-sitter) + markdown heading trees, schema-versioned cache, incremental re-indexing
2. **Source ABC** — the contract connectors implement to feed events into the brain. CE ships `WorkspaceSource` + `GithubRepoSource` only; Notion / HubSpot / Gmail / Granola adapters live elsewhere (Anabasis spec calls this `SignalSource`)
3. **EntityStore** — three-tier brain layer (`raw/` + `events/` append-only + `wiki/<slug>.md` consolidated entity pages with full provenance). Reference impl of Anabasis spec `EntityStore` ABC
4. **Synthesizer + Auditor** — GAM-grade semantic-shift detector; `wiki_init.py` one-shot seeder; Auditor proposes splits/merges/contradictions/dead links
5. **Retrieval surface** — depth-aware packer (5 levels, 95% budget utilization) + multi-hop reasoning + query-as-lens reranking + RRF fusion + authority signals + anti-hallucination filters + knowledge-type priority

Plus an MCP server, CLI + slash commands, visualizations, and an internal regression eval harness.

---

## 5. Cross-references

- **Spec contract**: `Repos/anabasis/spec/skill.md` (v0.1) + `spec/runtime/{entity-store,signal-source,routine}.md` (v0.2 — Phase 1.9 + Phase 4.2)
- **Reference skills**: `spec/reference-skills/install-department.md` (v0.1) + `spec/reference-skills/find-links.md` (v0.2-draft → v0.2 at Phase 4)
- **Multi-repo spine**: `Repos/anabasis/plan/multi-repo-spine.md`
- **YC sprint**: `Repos/anabasis/plan/5-day-sprint.md` (deadline 2026-05-04 8pm PT)
- **Phase A details**: `plan/phases/phase-A.md` + audit `plan/audits/phase-A.md`
- **Phase 0 details**: `plan/phases/phase-0.md` + audit `plan/audits/phase-0.md`
- ... (every phase has spec + audit pair)

---

## 6. Open questions to settle

These survived from v3 + new from v4:

1. **Bootstrap corpus for showcase wiki**: code repo (Syroco) vs product knowledge (`company-knowledge`) vs personal research? **Recommend**: company-knowledge as showcase, code-repo as dogfood README example.
2. **CE versioning post-Phase-4**: keep CE on independent semver (0.4.0+) or align with Anabasis spec (0.2.0)? **Recommend**: independent semver + `anabasis_spec_compat: v0.2` frontmatter field.
3. **Connector library extraction post-Phase-4**: Notion/HubSpot/Gmail stay private (syroco-product-ops only) or extract to public Anabasis adapter library? **Recommend**: defer to first 5 customer profiles.
4. **company-knowledge migration cost (Phase 1.10)**: lossy auto-migrate vs hand-write top-30 entities? **Recommend**: round-trip test (1.10) decides — start in Phase 1 week 1, not as a final gate.
5. **Naming (Phase 5.7)**: `context-engineering` (precise) vs `find-links` (Anabasis-canonical)? **Recommend**: keep both — repo + pip = `context-engineering`; spec name = `find-links`.

---

## 7. Non-goals (unchanged from v3)

- Not building a new graph DB. Markdown + frontmatter + `[[wiki-links]]` is the graph.
- Not replacing the packer. The packer is the retrieval engine; the wiki layer is its long-term memory.
- Not a chat product. Wiki is the artifact; agents and humans both read it.
- **Not chasing enterprise**. AI-native solo builders + staff engs who already use Obsidian are the audience.
- **Not contorting CE for maritime/Syroco use cases**. Treat Syroco angle as recruiting + credibility halo.
- **Not absorbing connector libraries**. Connectors stay in syroco-product-ops or future Anabasis adapter library. CE imports zero connector code.

---

## 8. Deferred — Proving Layer

Same as v3 § 8 (BM25 baselines, ablation matrix, public Cursor/Cody benchmark, AST-chunk embeddings, FAISS, NLI contradiction detection, etc.). **Trigger to pull back**: ≥3 wikis ≥100 pages each, OR public claim challenged, OR ≥100 weekly active users, OR contributor needs tuning data.

**New deferred (post-Sourcegraph analysis)**:
- LSP-based precise xref (Sourcegraph parity)
- Iterative refine pack loop (Deep Search analog)
- LLM-generated symbol summaries on index (Sourcegraph smart hover analog)

---

## 9. Kill criteria (unchanged from v3, measured in Phase 5.8)

Shut this bet down at 6-month review if any of:
- Anthropic ships native Claude Code persistent project memory + CE adoption < 500 weekly active users
- Average outlinks per page is flat or declining on the showcase wiki — compounding thesis empirically false
- No external installs from Karpathy-adjacent / AI-engineering audience by month 4 — narrative wedge didn't land

---

## 10. The principle

> **Phase 0 is "stop wrong answers / make it run."**
> **Phase 0.5 is "make people *want* to run it."**
> **Phase 1 is "make it the engine."**
> **Phase 2 is "make the engine queryable from the runtime."**
> **Phase 3 is "make it install in 5 days."**
> **Phase 4 is "ship v1.0 commercial — closed runtime + hosted plane on open MIT engine + open spec."**
> **Phase 5 is "make adoption compound."**

Proving comes after value lands, not before.
