# Scribes

> **Multi-source ingestion for the company brain.** N scribes → 1 brain, via the existing `wiki.add` MCP push contract. CE stays corpus-agnostic; each scribe handles one source.

## What's here

### Strategic / spec layer

| Document | Purpose | Audience |
|---|---|---|
| **`SPEC.md`** | The contract every scribe respects. Naming, event schema, idempotency, cadence, quality tiers, entity-hint heuristics, telemetry, SCRIBE.toml metadata. | Scribe authors, runtime/Pipedream implementers |
| **`PLAN.md`** | Catalog of all 17 scribes from connected MCPs. Use-case matrix (persona × scribe). Multi-POV audit (CPO, CTO, ADMIN). Reconciled prioritization (Tier 1 / 2 / 3 / Won't ship). Open questions. | Founder, CPO, CTO, ops |
| **`SKILL_INTEGRATION.md`** | How scribes plug into the broader skills ecosystem. The 3-skill family per connector (scribe / enricher / acter). Cross-scribe entity-resolver as its own skill. Discovery via `find-skills`. Composition with `coordinator-pattern`, `using-superpowers`, `proactive-brief`, etc. | Skill authors, runtime owners |
| **`GENERALIST-COMPONENTS.md`** | Cross-connector primitives: 2 scribes (`unstructured-text-scribe`, `live-web-data-scribe`) + 4 enrichers (`dataset-envelope-filter`, `unit-normalizer`, `jargon-detector`, `eli5-rewriter`). First worked example: `board-deck-from-brain-dump`. | Skill authors building intake/enrichment |
| **`MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md`** | How scribes + future consumer skills replace the existing `product-signals-pipeline` monolith. Producer/substrate/consumer separation. Migration phases + parallel-run plan. | Product-signals-pipeline owner, ops |

### Tier-1 scribe drafts (Syroco-tailored, no code yet)

| Folder | What it ingests / enriches | Layer | Default tier | Status |
|---|---|---|---|---|
| **`granola-scribe/`** | Granola meeting transcripts | 1 (raw) | T1 | Spec locked (5 Syroco questions answered 2026-05-03) |
| **`slack-scribe/`** | `#project-*` channels (16 channels mapped) | 1 (raw) | T1 | Spec drafted with real Syroco channel map |
| **`hubspot-scribe/`** | Companies, deals, contacts, activities | 1 (raw) | T0 | Spec drafted with Syroco lifecycle + property tracking |
| **`voyage-context-enricher/`** | Reads scribe events; adds voyage_id + deal_state_at_time + speaker_role from upstream APIs | 2 (enrichment) | T0 | Spec drafted; first enricher, keystone of 3-layer model |

## Reading order

1. **CPO / non-engineer** → `PLAN.md` only. Skip the other two.
2. **Skill author** → `SPEC.md` first, then `SKILL_INTEGRATION.md`.
3. **Architect / runtime owner** → all three, in catalog order.
4. **CTO / ops review** → `PLAN.md` §5 + §6, then `SPEC.md` §"Operational concerns".

## The one-paragraph version

The `context-engineering` brain already exposes a push-shape ingest contract (`wiki.add` MCP). Scribes are the small, single-purpose helpers that read upstream sources (Granola, Slack, HubSpot, Linear, Notion, Gmail, …) and call that button. Each scribe is a standalone skill installed via `npx skills add <connector>-scribe`, discovered via `find-skills` matching on the MCP namespace, and configured via per-scribe SKILL.toml metadata. Cross-scribe entity merge ("acme = acme-corp = acme-inc") is its own separate skill that uses `mcp__knowledge-graph-memory` as substrate. The runtime layer (Pipedream, Syroco Connect) is responsible for matching connected MCPs to scribe skills and prompting installation — out of scope for the scribes themselves.

## Status

**2026-05-03**: spec + plan + skill-integration drafted. Zero scribes built. Tier-1 ship target post-YC: `granola-scribe`, `slack-scribe`, `hubspot-scribe`, `entity-resolver` skill (week 1–2).

## Related

- `agent-skills/context-engineering/` — the brain (substrate)
- `agent-skills/context-engineering/plan/prd-closed-loop.md` — closed-loop PRD; brain side
- `mcp__knowledge-graph-memory` — entity-resolver substrate
- `~/.claude/skills/find-skills/` — discovery surface
- `~/.claude/skills/skill-author/` — for creating new scribe skills
