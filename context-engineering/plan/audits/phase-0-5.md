# Phase 0.5 audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Hit | Strength | Evidence |
|---|---|---|---|
| **Executable runtime** | ✅ | Medium | One-verb `pack` + `/pack` slash command makes CE a callable primitive in any agent loop. Telemetry confirms calls land. |
| **Installs through** | ✅ | **Critical** | 0.5.4 BGE local embeddings is the install-friction killer. Without it, "anabasis init" requires a cloud key handoff that breaks the "runtime in your environment, our key is yours" pitch. With it, the install is genuinely self-contained. |
| **Human knowledge** | ✅ | Medium | 0.5.3 `--why` flag exposes the resolution trace — humans inspect what the brain found, build trust. 0.5.7 off-topic guard headline tells humans "we won't make stuff up." |
| **Connections** | ⚠️ | Indirect | Phase 0.5 doesn't add new connectors. But 0.5.8 slash commands turn CE into a primitive other connectors can compose against. |
| **AI** | ✅ | Strong | 0.5.4 BGE = AI grade retrieval without API keys; 0.5.1 auto-mode = the AI-classified query routing. |
| **Skills that automate** | ✅ | Strong | 0.5.8 `/pack` is the consumer surface for any other skill that wants to retrieve. Without it, every consumer rolls its own subprocess invocation. |
| **Company brain** | ✅ | Medium | 0.5.5 telemetry is the *first* compounding signal — which queries get packed, which budgets get hit. Foundation for Phase 1's semantic-shift detection. |

**Score: 6/7 strong + 1 indirect.**

## Strongest pillar

**Installs through** is uniquely served by this phase. No other phase materially changes install friction. If Phase 0.5 didn't ship 0.5.4 BGE, the YC pitch's "runtime in your environment" promise would have a footnote: "*requires OpenAI API key for AI features." That footnote contradicts the open-core narrative.

This is the asymmetric value of Phase 0.5 — small surface area, high leverage on the YC story.

## Drift risks flagged

1. **Surface collapse undoes Sourcegraph parity**: hiding 17 relations + 6 task presets behind `--advanced` makes CE *look* simpler than Sourcegraph but power-user behavior is unchanged. **Mitigation**: defaults must be sensible — auto-mode should pick the right relations weights for typical queries. If defaults are bad, sophisticated users will revert and complain. **Action**: regression eval (Phase 0.10) must include "advanced flag combinations" to catch regression in expert workflows.

2. **BGE-small precision vs OpenAI**: BGE is competitive but not equivalent. On conceptual / fuzzy queries, OpenAI text-embedding-3-small may outperform. **Mitigation**: 0.5.4 keeps OpenAI as opt-in. Document in SKILL.md when to switch backends.

3. **Telemetry as data exfil concern**: even metadata-only `usage.jsonl` could leak query patterns to a snooper with cache access. **Mitigation**: keep `usage.jsonl` under `${XDG_CACHE_HOME}/context-engineering/`, not in skill dir. Add `--no-telemetry` flag for users who want to disable.

4. **Slash commands depend on Claude Code-specific path**: 0.5.8 hardcodes `~/Repos/agent-skills/context-engineering/scripts/pack_context.py`. Other harnesses (Cursor, Cline) need different wiring. **Mitigation**: ship `/pack` as a Claude Code-specific addon; document `.cursorrules` snippet for Cursor in Phase 5.

## Cross-phase risks

- **Phase 1 dependency**: 0.5.5 telemetry is consumed by Phase 1's semantic-shift detector (drift threshold). If `usage.jsonl` schema changes between Phase 0.5 and Phase 1, semantic_shift.py breaks. **Action**: freeze `usage.jsonl` schema in Phase 0.5, version it.

- **Phase 4 dependency**: 0.5.6 activation metric is the kill-criterion measurement at month 6 (≥500 weekly active users). If 0.5.6 ships with bugs, the kill criteria can't be evaluated. **Action**: 0.5.6 includes a manual test in regression eval.

## What this audit does NOT cover

- Whether BGE-small is the right model (vs. nomic-embed, jina-embeddings) — settled by user decision
- Whether `/pack-why` is more useful than `/pack --why` as separate command — settled in spec, can revisit
- Whether telemetry should be opt-in vs opt-out — recommend opt-out (default on, easy to disable)

## Recommendation

**Phase 0.5 is well-scoped and disproportionately serves "Installs through".** Approve as-is.

**Sequence inside the phase**:
1. 0.5.4 BGE backend (the longest-lead item) — start day 1
2. 0.5.1 + 0.5.2 + 0.5.3 (one-verb + 3 flags + --why) — depends on 0.5.4 for `auto` mode picking semantic
3. 0.5.5 + 0.5.6 (telemetry + activation) — independent, can parallel
4. 0.5.7 (headline polish) + 0.5.8 (slash commands) — finishing touches

**Risk to flag to user**: 0.5.4 BGE adds a 134MB download on first run. If the user tests on a slow connection, the demo-day experience suffers. Mitigation: pre-download the model in the Anabasis bootstrap (`anabasis init` step). Cross-reference in Phase 3 spec.
