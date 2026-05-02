# Phase 3 audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Hit | Strength | Evidence |
|---|---|---|---|
| **Executable runtime** | ✅ | **Critical** | The 5-day bootstrap turns the methodology from prose into 6 commands. Day 5 ends with `anabasis schedule` running cron — the runtime is firing. |
| **Installs through** | ✅ | **Critical** | Phase 3 IS the install. Day 0-5 is the install path. `npx @anabasis/init` is the entry point; everything else composes. |
| **Human knowledge** | ✅ | **Critical** | Day 1 entities extract from Department Specs (output of install-department) + existing markdown vault. Day 2 connectors ingest from Notion / etc. — where humans curate. |
| **Connections** | ✅ | **Critical** | Day 2 OAuth flow + connector wiring. Day 3 skill composition. Each connector implements CE's Source ABC. |
| **AI** | ✅ | Medium | Inherits Phase 1+2 AI (semantic_shift, multi-hop, lens). No new AI surface — Phase 3 is integration, not algorithm. |
| **Skills that automate** | ✅ | **Critical** | Day 3 + Day 4 = the skill catalog + routine cron. By Day 5 the brain runs unattended. |
| **Company brain** | ✅ | **Critical** | End-of-Day-5 = working company brain with cron. Promise delivered, not promised. |

**Score: 7/7 with 6 Critical pillars.**

## Strongest phase by RFS alignment

Phase 3 is the **highest-RFS-density phase**. Six pillars at Critical strength because the YC pitch promises a 5-day bootstrap and Phase 3 IS that 5-day bootstrap.

Phases 0-2 build the engine; Phase 3 turns it into a product.

## Drift risks flagged

1. **Cross-team coordination cost**: Phase 3 deliverables span CE (this skill) + Anabasis runtime (closed) + syroco-product-ops (private connectors). Three repos, three teams, one customer experience. **Mitigation**: ce-anabasis-tie.md established the API contract; now multi-repo-spine.md holds the integration plan. Schedule weekly syncs post-funding.

2. **Customer profile divergence**: 3.2 specifies 3 customer profiles (no vault, existing vault, markdown-heavy). Real customers may differ — what about repo-only with no docs? **Mitigation**: ship 3 profiles for v1; iterate based on first 5 customers' actual shape.

3. **5-day timeline marketing risk**: every "5 days to install" promise in B2B SaaS history has slipped. **Mitigation**: Anabasis bootstrap services package (3.8) is sold as $25-50k specifically because the first cohort is high-touch. Day 1-5 is the *target*, with paid hand-holding for outliers.

4. **Connector library extraction question**: post-funding, who owns NotionSource / HubSpotSource / etc.? Stay private (Syroco-only)? Extract to public Anabasis adapter library? Sell as commercial Anabasis SaaS? **Mitigation**: defer the answer. Phase 3 ships syroco-product-ops connectors privately first; market response decides extraction strategy.

5. **`init_brain.py` API stability under TypeScript wrapper**: 3.1 calls Python from `npx`. Python errors must surface cleanly through the JS layer. **Mitigation**: `init_brain.py` returns structured JSON on failure, not just exit code; npx layer parses + presents.

## Cross-phase risks

- **Phase 4 dependency**: Phase 4's open-core release ships the Anabasis runtime. If Phase 3 reveals the runtime needs new ABCs (e.g., `BootstrapStep`, `RoutineStep`), they must land in spec v0.2 before Phase 4. **Action**: Phase 3 includes a "spec gap audit" — every runtime call that doesn't map to a documented ABC gets logged for Phase 4.

- **Phase 5 dependency**: Phase 5's HN demo recorded post-Phase-3 (real 5-day bootstrap). If Phase 3 ships unstable, Phase 5 demo fails. **Action**: Phase 3 stability gate = 3 internal customer-style runs (CE team simulates Profile A/B/C) before Phase 4 ships.

## What this audit does NOT cover

- Whether install-department produces useful Department Specs out of the box (separate skill, separate audit)
- Whether OAuth flows handle every customer's identity provider (engineering detail, not RFS concern)
- Pricing / packaging of bootstrap services (sales/CSM concern, methodology already captures it)
- Anabasis runtime internals (closed source, no public spec until Phase 4)

## Recommendation

**Phase 3 is post-funding integration work.** Approve as-is. Sequence:
1. Week 1 post-funding: 3.1 + 3.2 (init + entities extract) — composes Phase 1+2
2. Week 2: 3.3 + 3.4 (sources + skills) — composes syroco-product-ops connectors
3. Week 3: 3.5 + 3.6 (routines + loop closure) — first end-to-end customer run
4. Week 4: 3.7 + 3.8 (demo recording + bootstrap services package) — sales materials

**Strongest risk**: cross-team coordination. **Mitigation**: weekly sync between CE / Anabasis runtime / syroco-product-ops post-funding.

**Phase 3 is where Anabasis becomes deployable.** Without it, the YC pitch is theater. With it, customers actually install.
