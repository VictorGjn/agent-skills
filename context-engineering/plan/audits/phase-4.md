# Phase 4 audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Hit | Strength | Evidence |
|---|---|---|---|
| **Executable runtime** | ✅ | **Critical** | Runtime open-sources. Spec v0.2 freezes EntityStore + SignalSource + Routine ABCs. The runtime that *executes* the brain is now public + auditable + forkable. |
| **Installs through** | ✅ | Strong | Hosted MCP endpoint = zero-install evaluation. Self-host install path documented. Pre-flight pre-essay catches install-blocking bugs. |
| Human knowledge | ✅ | Medium | Five reference skills (`install-department`, `find-links`, `audit-process`, `sota-search`, `refresh-department`) extend the human-knowledge surface. Inherits Phase 1 schema. |
| Connections | ⚠️ | Indirect | No new connector contracts. Phase 1 Source ABC stable. Connector library extraction still TBD. |
| AI | ⚠️ | Indirect | No new AI surface. Phase 1+2 stack stable. |
| **Skills that automate** | ✅ | **Critical** | Five reference skills at v0.2 = the ecosystem. The pitch goes from "install-department + find-links" to a real skill library — and it's open. |
| **Company brain** | ✅ | **Critical** | Three customer brains running in production. The proof phase. Numbers from these brains ground the HN essay. |

**Score: 5/7 strong + 2 indirect.** Three pillars at Critical.

## Open-core moat preserved

The four-layer business model from methodology/onboarding-toc.md:

| Layer | Status post-Phase-4 |
|---|---|
| Spec | Open (Apache-2.0) — Phase 4 freezes v0.2 |
| Methodology | Open — already public |
| Reference skills | Open (CE = MIT, others = Apache-2.0 typically) — five skills shipped |
| Runtime | Open (Apache-2.0 after BUSL-1.1 expires) — Phase 4 flips |
| Hosted orchestration cloud | Closed (paid SaaS) — the durable revenue plane |
| Bootstrap services | Paid — converging to automated by Q4 2026 |

Phase 4 transitions runtime from closed → open. The paid plane stays. Moat = hosted orchestration cloud (multi-tenant, SSO, audit, SLA), not source-code secrecy.

This is the **HashiCorp / Terraform 2014→2017 playbook** explicitly cited in the methodology. Phase 4 is the 2017 moment.

## Drift risks flagged

1. **Open-source runtime + closed connector library tension**: post-Phase-4, runtime is open but Notion/HubSpot/etc. connectors live in syroco-product-ops (private). External users get the runtime but can't easily wire connectors. **Mitigation**: extract first 5 connectors as public Anabasis adapter library at Phase 4 OR shortly after. Decision still pending — flag in Phase 4 deliverables.

2. **HN essay timing**: drop too early (before 3 pilots) and the proof is weak; drop too late (after runtime open-sources quietly) and the launch moment is wasted. **Mitigation**: gate essay on pilot #3 acceptance (Day 90 OR pilot #3, whichever later, per methodology).

3. **CE versioning conflict**: SKILL.md version is 0.3.0; spec is at v0.1 (going to v0.2). What does CE bump to? **Mitigation**: keep CE on independent semver (0.4.0 or 1.0.0 at this milestone); add `anabasis_spec_compat: v0.2` to frontmatter. Decision in Phase 4.3.

4. **Spec v0.2 freeze locks ABCs after only 3 customers**: small N, big freeze. **Mitigation**: 3 customers + 6-month internal Syroco use ≈ 9 months of data. v0.3 deprecation window for any v0.2 mistake.

5. **Five reference skills span >1 repo**: install-department + find-links live in agent-skills; audit-process / sota-search / refresh-department might live elsewhere. **Mitigation**: consolidate under `agent-skills/` if feasible; document multi-repo if not.

## Cross-phase risks

- **Phase 5 dependency**: Phase 5 distribution requires Phase 4's open-core release as the marketing peg. If Phase 4 slips, Phase 5 marketing slips. **Action**: Phase 4 + Phase 5 coordinate calendar. Essay drop = Phase 4 close = Phase 5 start.

- **Customer escape risk**: post-open-source, a savvy customer could fork the runtime + run it themselves to avoid hosted cloud. **Mitigation**: hosted plane offers value beyond the OSS runtime (multi-tenancy, observability, SLA) — not just convenience.

## What this audit does NOT cover

- Whether the HN essay actually lands (marketing execution)
- Whether the three pilots happen on schedule (sales/CSM execution)
- Internal Anabasis runtime architecture (closed source, no public spec to audit)
- Pricing model post-open-source (TBD per methodology)

## Recommendation

**Phase 4 is execution work, not invention.** Approve as-is. Sequence:
1. Day 80 (post-funding): pre-flight CE first-run experience (4.5)
2. Day 85: spec v0.2 freeze (4.2) — drop drafts
3. Day 88: CE tag + release (4.3)
4. Day 90: runtime open-source flip (4.1) + HN essay (4.4)
5. Day 91+: hosted MCP endpoint stabilizes (4.6)

**Strongest risk**: connector library extraction question. **Mitigation**: decide before Phase 4 starts (post-funding, pre-Day-80).

Phase 4 turns Anabasis from a startup into an open-source category-defining project. The category being "company-knowledge agent orchestration runtime" — Temporal of its segment.
