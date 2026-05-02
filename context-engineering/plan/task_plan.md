# CE Unified Roadmap Execution Plan

> **Goal**: Deliver context-engineering as the **engine** that powers Anabasis's company brain — indexer + Source ABC + EntityStore reference impl + depth-aware packer + MCP surface. Connectors stay in `syroco-product-ops`.
>
> **YC RFS framing to satisfy**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Sources

- `agent-skills/context-engineering/ROADMAP.md` (current v3, code-context-only scope)
- `Repos/anabasis/plan/multi-repo-spine.md` (cross-repo architecture)
- `Repos/anabasis/plan/ce-anabasis-tie.md` (Phase A, ½-day tie)
- `Repos/anabasis/spec/skill.md` (Anabasis spec v0.1)
- `Repos/anabasis/plan/5-day-sprint.md` (YC submission Mon May 4 8pm PT)

## Locked decisions (2026-05-01)

1. CE = **engine**; connectors stay in `syroco-product-ops`
2. CE Phase 1 produces **EntityStore reference impl**; Anabasis spec v0.2 documents it
3. **BGE-small** is the local embeddings default (kills `OPENAI_API_KEY` requirement)
4. **Drop LSP** from Phase 2; **expand tree-sitter to all 14 advertised languages** (pulled UP from Deferred to Phase 0)
5. Regression eval corpus = **CE itself + efficientship-backend + company-knowledge** (code/code/markdown coverage)

## Phases

| # | Phase | Effort | Status | Spec | Audit |
|---|---|---|---|---|---|
| A | Anabasis tie | ½ d | pending | [phases/phase-A.md](phases/phase-A.md) | [audits/phase-A.md](audits/phase-A.md) |
| 0 | Truth-up + incremental + tree-sitter + authority + regression eval | M (~1 wk) | pending | [phases/phase-0.md](phases/phase-0.md) | [audits/phase-0.md](audits/phase-0.md) |
| 0.5 | Surface collapse + telemetry + BGE | S (3-5 d) | pending | [phases/phase-0-5.md](phases/phase-0-5.md) | [audits/phase-0-5.md](audits/phase-0-5.md) |
| 1 | Wiki schema + Source ABC + git signals + EntityStore reference impl | M (~2 wk) | pending | [phases/phase-1.md](phases/phase-1.md) | [audits/phase-1.md](audits/phase-1.md) |
| 2 | `pack --wiki` + multi-hop + lens + MCP wiki tools | M (1-2 wk) | pending | [phases/phase-2.md](phases/phase-2.md) | [audits/phase-2.md](audits/phase-2.md) |
| 3 | Anabasis 5-day bootstrap | M (post-funding) | pending | [phases/phase-3.md](phases/phase-3.md) | [audits/phase-3.md](audits/phase-3.md) |
| 4 | Open-core release + spec v0.2 | S | pending | [phases/phase-4.md](phases/phase-4.md) | [audits/phase-4.md](audits/phase-4.md) |
| 5 | Distribution flywheel | S-M (ongoing) | pending | [phases/phase-5.md](phases/phase-5.md) | [audits/phase-5.md](audits/phase-5.md) |

## YC RFS alignment criteria (used in every audit)

Each phase must answer at least one:
1. **Executable** — does it advance "runtime that runs", not "library to read"?
2. **Installs** — does it lower the friction of getting CE in someone's environment?
3. **Human knowledge** — does it ingest/preserve human-curated knowledge (procedures, decisions, RFCs)?
4. **Connections** — does it expose or consume Source ABC bindings to outside systems?
5. **AI** — does it leverage LLM-grade resolution (semantic, multi-hop, lens, concept naming)?
6. **Skills that automate** — does it ship/strengthen skills that other Anabasis runtimes can call?
7. **Company brain** — does it move us closer to a queryable, compounding entity vault?

A phase that hits 0 of 7 is suspect.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---|---|

## Sequencing

```
Phase A ─► Phase 0 ─► Phase 0.5 ─► Phase 1 ─► Phase 2 ─► Phase 3 ─► Phase 4 ─► Phase 5
 (½ d)     (~1 wk)    (3-5 d)      (~2 wk)    (1-2 wk)   (post-fund) (release) (ongoing)
   │
   └─► YC submission (Mon May 4)
```

Phase A blocks YC submission. Phase 0+0.5+1+2 = the v1.0 ship. Phases 3-5 paced by funding/pilots.

## Cross-references

- v3 ROADMAP: `../ROADMAP.md` — superseded by ROADMAP-v4.md after final synthesis
- Anabasis spine: `~/Repos/anabasis/plan/multi-repo-spine.md`
- Findings (research/audit notes): `findings.md`
- Session log: `progress.md`
