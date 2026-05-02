# Phase 0 audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Hit | Strength | Evidence |
|---|---|---|---|
| **Executable runtime** | ✅ | Strong | 0.5 incremental indexing — under cron-fired routines (Anabasis runtime model), full re-parse is fatal. Without 0.5, every routine pays a 30-sec amortization cost; with it, sub-second. This is the difference between a runtime that *runs* and one that *re-bootstraps*. |
| **Installs through** | ✅ | Strong | 0.2 cache relocation + pinned deps — install becomes `pip install -e .` with deterministic outputs. 0.11 MCP hardening makes the install safe to expose to other Anabasis components. |
| **Human knowledge** | ⚠️ | Indirect | 0.6 tree-sitter coverage indirectly serves human knowledge (precise extraction from human-edited code). But Phase 0 doesn't ingest human procedures — that's Phase 1's `events/` + Source ABC. |
| **Connections** | ✅ | Medium | 0.11 MCP hardening = the find skill is a safe network-exposable connector. 0.4 stable file_id makes cross-source joins work in monorepos. |
| **AI** | ✅ | Strong | 0.8 RRF fusion replaces magic-number linear blend; 0.9 authority signals add structural ranking; both improve baseline retrieval before LLM call. |
| **Skills that automate** | ✅ | Strong | 0.10 regression eval is the meta-deliverable — ensures the skill stays usable as Phase 1-5 stack on top. Without it, the open-core release in Phase 4 would ship unvalidated. |
| **Company brain** | ✅ | Medium | 0.3 schema versioning + atomic writes = the brain doesn't corrupt under concurrent connector cron. 0.7 relations cap fix removes 59% silent data loss in graph mode. Foundational, not direct. |

**Score: 6/7 strong + 1 indirect.**

## Drift risks flagged

1. **Scope creep into Sourcegraph clone**: 0.5 incremental + 0.6 tree-sitter coverage + 0.9 authority signals could cascade into "let's add LSP" / "let's add precise xref" / "let's add cross-repo". **Mitigation**: locked decision #4 explicitly drops LSP from this phase. Authority signals stop at in-degree on existing graph — no precise call resolution.

2. **Regression eval becomes Proving Layer**: 0.10 internal regression risks turning into the deferred public benchmark by accident. **Mitigation**: explicit "internal only, NOT public" framing in the spec. Different audience (own GitHub Action) vs the deferred (HN essay).

3. **0.6 tree-sitter coverage estimate**: SKILL.md flagged 11 of 14 silent regex per ROADMAP; the actual count needs verification. If it's 11, this is S-M; if it's 14 (already coherent), this drops to a no-op verification task. **Mitigation**: phase 0.6 deliverable starts with audit step, not implementation.

4. **0.10 corpora include company-knowledge** (Syroco-private). The eval JSON output cannot be checked into a public repo. **Mitigation**: keep eval results in `cache/eval/` (gitignored); CI publishes only delta vs baseline (e.g., "+0.03 weighted recall") — never raw results.

## Cross-phase risks

- **Phase 0.5 dependency**: BGE local embeddings need 0.2's pinned `sentence-transformers` to be in requirements. If 0.2 ships first, 0.5 can't import. Sequence: 0.2 lands before 0.5 begins.
- **Phase 1 dependency**: events/ writes need 0.3's atomic + lock primitives. Sequence: 0.3 before any Phase 1 events/ work.

## What this audit does NOT cover

- Whether 0.10's golden-set queries are *good* — that's a content-quality concern, not architecture
- Whether the YC demo on Fri May 1 needs Phase 0 done first (it doesn't — demo runs on current state with caveats)
- Whether company-knowledge is the right third corpus (the user confirmed; out of scope for audit)

## Recommendation

**Phase 0 is well-scoped and serves all 7 RFS pillars.** Approve as-is. Sequence:
1. 0.1 + 0.2 (vendor + pin) — half day, unblocks everything
2. 0.3 + 0.4 + 0.7 — schema + atomicity + relations cap (1 day, the foundations)
3. 0.5 incremental + 0.6 tree-sitter + 0.9 authority (parallel where possible, 2-3 days)
4. 0.8 RRF + 0.10 regression eval + 0.11 MCP harden + 0.12 headlines (1-2 days)

Total ~1 week, matches ROADMAP estimate.

**Risk to flag to user**: 0.10 regression eval is the deliverable that takes longest to *land* but is most necessary for confidence in Phase 1+. Don't cut it.
