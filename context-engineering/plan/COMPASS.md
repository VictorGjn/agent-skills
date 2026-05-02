# The Compass — what we are building

**Read this first, every time.** Re-read at every phase transition.

## End state

A customer types `anabasis init` on Monday and has a working company brain on Friday. The brain queries across their code, docs, Notion, HubSpot, Slack, Gmail. Skills run on cron. The customer reviews `audit/proposals.md` weekly and the brain compounds.

## What ships at end-of-runway (Day 90 from funding)

| Layer | License | Where | What's there |
|---|---|---|---|
| **Anabasis spec** | Apache-2.0 (open) | `github.com/VictorGjn/anabasis/spec/` | Skill ABC, EntityStore + SignalSource + Routine ABCs, reference-skill specs |
| **Anabasis runtime** | Commercial (closed) | `github.com/VictorGjn/anabasis/runtime/` (private) | Scheduler, MCP routing, conflict resolution, observability, anabasis CLI, npx init |
| **Anabasis hosted plane** | Commercial SaaS | `mcp.anabasis.tech` + multi-tenant cloud | Managed runtime, SSO, audit, SLA, dashboard |
| **CE engine** | **MIT (open)** | `github.com/victorgjn/agent-skills/context-engineering/` | Indexer + Source ABC + EntityStore + Synthesizer + Auditor + Packer + MCP server |
| **Reference skills** | MIT (open) | `github.com/victorgjn/agent-skills/{install-department,context-engineering,audit-process,sota-search,refresh-department}/` | The 5 reference skill impls |
| **Connector library** | TBD (likely commercial, lives in Anabasis adapter library) | TBD repo | NotionSource, HubSpotSource, GmailSource, GranolaSource, SlackSource — concrete Source ABC subclasses |
| **Syroco internal brain** | Private | `syrocolab/syroco-product-ops` + `syrocolab/company-knowledge` | Pilot dogfood; the proof |

## The compass principles

1. **The moat is the runtime + hosted plane.** Not source-code-secrecy. Sourcegraph charges $16k/yr for indexer-packer because *operating* it is hard, not because the algorithm is secret. Anabasis's moat is identical in shape — orchestration + hosted ops, not closed engine.

2. **CE stays MIT.** Public engine is the wedge. Reversing public PRs erodes credibility; the moat doesn't depend on it.

3. **Connectors live elsewhere.** Source ABC is the contract. CE imports zero connector libraries. Notion/HubSpot/Gmail/Granola/Slack code lives in syroco-product-ops first, then likely Anabasis commercial adapter library.

4. **Spec drives implementation, not the reverse.** CE Phase 1 produces the v0.2 EntityStore + SignalSource ABCs that the spec then *documents*. The spec is what's published; the engine is the reference impl.

5. **install-department is reference skill #1, not CE.** install-department captures human knowledge into Department Specs. CE (find-links) retrieves across them. Two halves of the operating loop.

6. **Independent usefulness is invariant.** A user can `pip install -e .` from CE alone and get value. Anabasis adds orchestration on top. Breaking this invariant breaks the wedge.

7. **Value over proof.** Defer benchmarks/baselines until value lands and usage data exists. Internal regression eval (golden-set) is for non-regression, not external claims.

8. **No source-specific code in CE.** Wiki schema is corpus-agnostic. Code, Department Specs, Notion pages, HubSpot deals all become entity pages with provenance. The engine doesn't care; connectors do the parsing.

## Today (2026-05-01)

Pre-YC submission Mon May 4 8pm PT. Engineering work runs in parallel.

## What this document is NOT

- Not the marketing pitch (that's `Repos/anabasis/pitch/1-paragraph.md`)
- Not the methodology (that's `Repos/anabasis/methodology/onboarding-toc.md`)
- Not the spec contracts (that's `Repos/anabasis/spec/`)
- Not the per-phase plan (that's `agent-skills/context-engineering/plan/phases/`)

This is **the compass**. Read on every phase transition. If a deliverable in any phase doesn't serve the compass, cut it.
