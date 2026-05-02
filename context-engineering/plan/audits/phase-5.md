# Phase 5 audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Hit | Strength | Evidence |
|---|---|---|---|
| Executable runtime | ✅ | Medium | Hosted MCP endpoint = runtime-as-a-service. Free tier seeds the funnel; paid tier feeds Anabasis hosted plane. |
| **Installs through** | ✅ | **Critical** | Phase 5 IS distribution. Cursor snippet + MCP directory listings + hosted endpoint reduce install friction to zero for evaluators. |
| Human knowledge | ⚠️ | Indirect | Essay teaches devs what CE does. Doesn't directly add new human-knowledge ingestion. |
| Connections | ✅ | Medium | Hosted endpoint expands the connection surface (CE-as-MCP for any harness). Community Source PRs grow the connector ecosystem. |
| AI | ⚠️ | Indirect | No new AI surface. Phase 5 is distribution, not algorithm. |
| **Skills that automate** | ✅ | **Critical** | Phase 5 = skills find their consumers. Distribution = adoption = compounding. The open-core flywheel kicks in. |
| **Company brain** | ✅ | **Critical** | External companies build their own brains using the open stack. Proof of category, not just product. ≥3 inbound non-Syroco pilots = the category exists. |

**Score: 5/7 strong + 2 indirect.** Three pillars at Critical strength.

## Audience alignment

ROADMAP § Distribution explicitly targets **AI-native devs / staff engs / Karpathy-adjacent crowd / Obsidian users**, NOT enterprise.

Phase 5 deliverables hit this audience:
- 5.1 essay → AI-native devs reading dev.to / HN / X
- 5.2 + 5.3 demos → Anyone who clicks a 60-sec Loom
- 5.4 MCP directories → Cursor / Cline / Zed / Claude Code users
- 5.5 Cursor snippet → Cursor users specifically
- 5.6 hosted endpoint → developers who want zero-install evaluation

**Phase 5 deliberately does NOT target**:
- Enterprise SaaS buyers (different cycle, different sales motion)
- Notion/Coda/Linear marketing audiences (different audience)
- Sales-led growth (RFS is product-led)

This audience choice is explicitly correct per ROADMAP § Non-goals: "Not chasing enterprise. AI-native solo builders + staff engs who already use Obsidian are the audience. Enterprise wants Sourcegraph + SOC2 — different game."

## Drift risks flagged

1. **Essay-quality variance**: 5.1 essay quality dictates funnel performance. A weak essay doesn't recover. **Mitigation**: review by 2 trusted operators / ex-YC founders before publication (same pattern as YC essays per 5-day-sprint.md). Iterate on title pre-publication.

2. **MCP directory churn**: directories are pre-launch / pre-stabilized; submission processes change. **Mitigation**: prioritize Smithery + PulseMCP (mature) over Anthropic's directory (TBD timing). Re-submit when directories stabilize.

3. **Cursor `.cursorrules` syntax churn**: Cursor changes their config format quarterly. **Mitigation**: ship 5.5 as a documented snippet, not a binding API. Update when Cursor changes.

4. **Hosted MCP endpoint capacity**: 100 packs/day per IP works for individual evaluators; a dev using CE in a tight loop hits the limit fast. **Mitigation**: clear messaging on free vs paid tier. Paid tier seeds Anabasis hosted cloud revenue.

5. **Kill-criteria measurement gaming**: 5.8 tracks weekly active users. Easy to game with bot scripts. **Mitigation**: instrument multiple signals (telemetry, GitHub stars, pip download, Cursor snippet copies). Triangulate.

6. **Naming decision (5.7) reversal**: if `context-engineering` proves un-googleable, the essay (5.1) won't hit search. **Mitigation**: lock naming BEFORE 5.1 publishes. Recommend keep `context-engineering` repo + add `find-links` as Anabasis spec name (already in find-links.md). Cross-reference in essay.

## Cross-phase risks

- **Phase 4 dependency**: Phase 5 starts when Phase 4 (HN essay landed) closes. Phase 5 timing is therefore funded by Phase 4 momentum. **Mitigation**: Phase 5.1-5.3 prepared in advance; ready to drop within 1 week of Phase 4 close.

- **Phase 1+2 stability**: external installs hit edge cases not covered by internal regression eval. **Action**: Phase 5 GitHub Actions CI runs CE against fresh-checkout VMs (Linux + macOS + Windows) to catch install bugs.

## Kill-criteria realism

ROADMAP v3 sets explicit kill criteria. Phase 5.8 measures them.

| Criterion | Realistic? |
|---|---|
| ≥500 weekly active CE users by month 6 | **Aggressive but achievable** — needs HN front page + 1-2 viral threads |
| ≥3 inbound non-Syroco Anabasis pilots by month 6 | **Aggressive** — Anabasis hosted cloud needs to be live + onboarding self-serve |
| Average outlinks per page increasing on showcase wiki | **Achievable** — depends on whether anyone uses the wiki layer (Phase 1+2 receivability) |
| No Anthropic native persistent memory + adoption < 500 WAU | **Existential** — if Anthropic ships this, CE's value compresses to "the engine, not the product" — exactly the Anabasis pivot we already made |

These are *honest* kill criteria. Phase 5 measures, doesn't massage.

## What this audit does NOT cover

- Anabasis hosted cloud pricing post-Phase-5 (not CE concern)
- Whether community PRs (5.9) get merged at the right cadence (operations, not RFS)
- Whether kill criterion #4 (Anthropic ships native memory) actually fires (out of our control)

## Recommendation

**Phase 5 is the funnel.** Approve as-is. Sequence:
1. Pre-essay (1-2 weeks before Phase 4 essay drop): record demos (5.2, 5.3); pre-flight install (5.6); naming locked (5.7)
2. Essay drop week: publish essay (5.1); submit to MCP directories (5.4); ship Cursor snippet (5.5)
3. +2 weeks: hosted MCP free tier stable (5.6); kill-criteria dashboard live (5.8)
4. +1 month: assess weekly active users; iterate

**Strongest risk**: essay quality. **Mitigation**: 2-reviewer pre-publication review.

**Phase 5 is where the YC bet pays off or doesn't.** ≥500 WAU + ≥3 inbound pilots = the bet works. Less = honest re-evaluation.
