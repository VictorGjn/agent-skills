# Phase 1 audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Hit | Strength | Evidence |
|---|---|---|---|
| **Executable runtime** | ✅ | **Critical** | 1.9 freezes EntityStore + SignalSource ABCs as Anabasis spec v0.2. The runtime can now be specced *against published contracts* — closes the open-spec / closed-runtime gap that v0.1 leaves. |
| **Installs through** | ✅ | Strong | 1.1 `init_brain.py` is the bootstrap primitive: one command, full layout. 1.6 `wiki_init.py` populates from existing index — no manual entity authoring required. |
| **Human knowledge** | ✅ | **Critical** | Provenance schema (1.2) preserves human-curated source claims. Department Specs feed in via WorkspaceSource. RFCs, ADRs, decisions become first-class entity kinds. |
| **Connections** | ✅ | **Critical** | Source ABC (1.4) is the connection contract. Concrete connectors live in syroco-product-ops; CE imports zero connector libraries. The mock 3rd-party test fixture validates this discipline. |
| **AI** | ✅ | Strong | semantic_shift detector (1.5) is GAM-grade consolidation. wiki_init.py with `--concept-llm` (1.6) is Haiku-driven entity naming. Both depend on Phase 0.5 BGE backend. |
| **Skills that automate** | ✅ | Medium | Auditor (1.7) is itself a runnable skill; produces audit/proposals.md. Phase 2 stacks `wiki.audit` MCP tool on top. |
| **Company brain** | ✅ | **Critical** | Phase 1 IS the brain. Three-tier storage + provenance + Auditor = a queryable, compounding entity vault that survives connector churn (events/ append-only) and synthesizes only on drift (semantic_shift). |

**Score: 7/7 with 4 Critical pillars served.**

## Strongest pillars (Critical)

This is the **load-bearing phase** for the YC pitch. Four pillars hit Critical strength:
- **Executable runtime**: spec v0.2 ABCs make the runtime spec-able.
- **Human knowledge**: schema preserves human-authored claims with file:line provenance.
- **Connections**: Source ABC contract enables connectors without CE owning them.
- **Company brain**: the brain artifact becomes concrete, walkable, auditable.

If Phase 1 ships well, the YC narrative goes from "we have a methodology and a spec stub" to "we have the engine that powers the brain, and here's the brain we built on Syroco." Demo-able.

## Drift risks flagged

1. **Schema bikeshedding paralysis**: 1.2 wiki page schema has 11 frontmatter fields. Easy to debate field names for a week. **Mitigation**: lift schema verbatim from existing `company-knowledge` if compatible (per multi-repo-spine § "company-knowledge migration cost"). If incompatible, the migration cost shows up in 1.10 round-trip and forces a decision.

2. **EntityStore ABC creep**: spec v0.2 documentation (1.9) is a 1-day deliverable. Could balloon if we try to also freeze Routine ABC, scheduler semantics, conflict resolution. **Mitigation**: 1.9 explicitly only covers EntityStore + SignalSource. Routine ABC is Phase 4 (post-funding open-core).

3. **Source ABC abstraction-creep**: tempting to add `subscribe()` for streaming connectors, `delete()` for tombstones, `move()` for reorganization. Each is a real concern but post-Phase-1 work. **Mitigation**: ship 4 methods only (list_artifacts / fetch / metadata / emit_events). Connectors that need more compose at the connector layer.

4. **wiki_init.py's `--concept-llm` adds Anthropic dependency**: the existing CE feature uses Haiku for concept naming. Phase 1 makes it more visible. **Mitigation**: keep `--concept-llm` opt-in (existing pattern); document offline mode in SKILL.md. Default seeding works without ANTHROPIC_API_KEY.

5. **Auditor as "runs nightly" without a runtime**: 1.7 says Auditor runs scheduled. CE doesn't have a scheduler. **Mitigation**: ship Auditor as a skill that *can* be cron'd by Anabasis runtime (or `cron` directly); CE doesn't own scheduling.

6. **Round-trip test (1.10) gates Phase 1 close**: if company-knowledge schema diverges, Phase 1 can't close. **Mitigation**: identify divergence early (start 1.10 in week 1, not week 2). Decide: either company-knowledge migrates to CE schema, or CE schema absorbs company-knowledge variations.

## Cross-phase risks

- **Phase 2 dependency**: `pack --wiki` reads entity pages. If 1.2 schema is wrong, Phase 2 reads wrong shape. **Action**: schema validation tests (1.2) gate Phase 2 start.

- **Phase 3 dependency**: Anabasis bootstrap (Phase 3) calls `init_brain.py` + `wiki_init.py`. These must be stable before Phase 3 ships. **Action**: freeze 1.1 + 1.6 APIs at end of Phase 1; document in `find-links.md` v0.2.

- **Phase 4 dependency**: open-core release (Phase 4) ships the EntityStore + SignalSource ABCs as part of v0.2. Phase 1.9 produces them. **Action**: 1.9 is gating for Phase 4 — if 1.9 wobbles, Phase 4 slips.

## Connector ecosystem question (out of scope but worth flagging)

Phase 1 ships the Source ABC contract. The first 5 concrete connectors (Notion / HubSpot / Gmail / Granola / Slack) live in syroco-product-ops. The user will need to decide post-funding:
- Do those concrete connectors get extracted into a public Anabasis adapter library?
- Or do they stay private and Anabasis ships its own commercial adapter library?

**Recommendation**: defer. Phase 1 produces the contract; market response decides whether commodity adapter libraries are worth building publicly.

## What this audit does NOT cover

- Whether Phase 1 demos well in the YC video (it ships post-funding, so no — see Phase A audit)
- Whether the wiki schema covers every entity kind a customer might want (deliberately limited; 1.2's `kind` field is open-ended)
- Whether `events/<YYYY-MM-DD>.jsonl` daily rolling is the right cadence (could be hourly for high-volume connectors; revisit in Phase 3)

## Recommendation

**Phase 1 is the load-bearing phase.** Approve as-is. Sequence:
1. 1.1 + 1.2 (layout + schema) — week 1 day 1-2, gates everything else
2. 1.3 + 1.4 (events extractor + Source ABC) — week 1 day 3-5
3. 1.5 + 1.6 (semantic_shift verify + wiki_init) — week 2 day 1-2
4. 1.7 + 1.8 (Auditor + git signals) — week 2 day 3-4
5. 1.9 + 1.10 (spec v0.2 docs + round-trip test) — week 2 day 5

**Strongest risk**: 1.10 round-trip discovering company-knowledge schema mismatch. **Mitigation**: start 1.10 in week 1 in parallel, not as a final gate.

**This phase justifies the YC funding ask.** Without Phase 1, "we have an engine that powers the company brain" is vapor. With Phase 1, it's deployable code with a documented contract.
