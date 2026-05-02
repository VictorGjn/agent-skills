# Final cross-phase audit — End-to-end YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Coverage matrix (all 8 phases)

| Phase | Executable | Installs | Human knowledge | Connections | AI | Skills automate | Company brain | Total |
|---|---|---|---|---|---|---|---|---|
| **A — Anabasis tie** | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 6+1 |
| **0 — Truth-up** | ✅ | ✅ | ◐ | ✅ | ✅ | ✅ | ✅ | 6+1 |
| **0.5 — Surface + BGE** | ✅ | **★** | ✅ | ◐ | ✅ | ✅ | ✅ | 6+1 |
| **1 — Wiki + Source ABC** | **★** | ✅ | **★** | **★** | ✅ | ✅ | **★** | 7 (4★) |
| **2 — pack --wiki + MCP** | **★** | ◐ | ✅ | ✅ | **★** | **★** | ✅ | 6+1 (3★) |
| **3 — Bootstrap** | **★** | **★** | **★** | **★** | ✅ | **★** | **★** | 7 (6★) |
| **4 — Open-core** | **★** | ✅ | ✅ | ◐ | ◐ | **★** | **★** | 5+2 (3★) |
| **5 — Distribution** | ✅ | **★** | ◐ | ✅ | ◐ | **★** | **★** | 5+2 (3★) |

Legend: ✅ = served (strong/medium); ◐ = indirect / inherited; **★** = Critical strength

## End-to-end narrative

Reading the phases as one story:

**Phase A**: Tell YC reviewers honestly what CE is and where it slots in (find-links v0.2 reference impl, not pretending to be reference skill #1 which is install-department).

**Phase 0**: Make CE match its README. No silent corruption, no 59% relations loss, no fake 14-language coverage. Add the honesty primitive: regression eval that catches future drift.

**Phase 0.5**: One verb. BGE-small kills the OpenAI key requirement. The "install" promise becomes credible — you can run CE end-to-end in a fresh environment with no cloud accounts.

**Phase 1**: Build the brain. Three-tier storage. Source ABC with the discipline that connectors live elsewhere. Auditor for compounding without rewrite-on-every-write. EntityStore reference impl that becomes Anabasis spec v0.2.

**Phase 2**: Make the brain queryable from the runtime. `wiki.ask` is the find-links primary call. Multi-hop reasoning, lens reranking. find-links contract freezes.

**Phase 3** (post-funding): The 5-day bootstrap turns methodology into 6 commands. CE primitives (Phase 1+2) compose with Anabasis runtime + syroco-product-ops connectors. This is the YC pitch made operational.

**Phase 4**: Runtime opens. Spec v0.2 frozen. HN essay drops. The Temporal/HashiCorp 2017 moment.

**Phase 5**: Distribution funnel. CE-as-skill funnels into Anabasis-runtime adoption. Kill criteria measured honestly.

## Pillars served by phase distribution

```
Pillar              Phase A 0  0.5 1   2   3   4   5
─────────────────────────────────────────────────────
Executable          ◐  ✅ ✅  ★   ★   ★   ★   ✅
Installs through    ✅ ✅ ★   ✅  ◐   ★   ✅  ★
Human knowledge     ✅ ◐ ✅  ★   ✅  ★   ✅  ◐
Connections         ✅ ✅ ◐  ★   ✅  ★   ◐   ✅
AI                  ✅ ✅ ✅  ✅  ★   ✅  ◐   ◐
Skills automate     ✅ ✅ ✅  ✅  ★   ★   ★   ★
Company brain       ✅ ✅ ✅  ★   ✅  ★   ★   ★
```

**Each pillar served Critical at least once across phases**:
- Executable: Phase 1, 2, 3, 4 critical
- Installs through: Phase 0.5, 3, 5 critical
- Human knowledge: Phase 1, 3 critical
- Connections: Phase 1, 3 critical
- AI: Phase 2 critical
- Skills automate: Phase 2, 3, 4, 5 critical
- Company brain: Phase 1, 3, 4, 5 critical

**No pillar is orphaned.** No pillar is over-stuffed. Each gets a phase that hits Critical strength.

## End-to-end coherence checks

### 1. Does the YC RFS framing hold from A→5?

**Yes.** Reading the phases linearly, the pitch "executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain" maps to:
- "executable runtime" → Phase 1's EntityStore + Phase 2's MCP `wiki.*` + Phase 3's `anabasis schedule` + Phase 4's open-source runtime
- "installs" → Phase 0.5's BGE + Phase 3's 5-day bootstrap + Phase 5's hosted MCP
- "through human knowledge" → Phase 1's wiki schema with provenance + Phase 3's Department Spec ingestion
- "connections" → Phase 1's Source ABC + Phase 3's `sources add`
- "AI" → Phase 0.5's BGE + Phase 2's multi-hop + lens
- "skills that automate" → Phase 2's MCP `wiki.*` + Phase 4's five reference skills + Phase 5's distribution
- "company brain" → Phase 1's three-tier storage + Phase 3's working bootstrap + Phase 4's three-customer proof

### 2. Is there scope creep?

**No** — locked decisions enforce discipline. Specifically:
- LSP **dropped** from Phase 2 (decision #4)
- Connectors **stay in syroco-product-ops** (decision #1, memory updated)
- Public benchmarks **stay deferred** (Proving Layer)
- Notion/Gmail/HubSpot adapters **out of scope** (project_context_engineering_scope memory)

### 3. Are dependencies sequenced correctly?

**Yes** — each phase's dependencies upstream are met:
- Phase 0 depends on nothing (vendor + pin)
- Phase 0.5 depends on Phase 0.2 (`sentence-transformers` pinned) ✓
- Phase 1 depends on Phase 0.3 (atomic writes) + Phase 0.5 BGE (semantic_shift cosine) ✓
- Phase 2 depends on Phase 1 wiki schema + Phase 1.4 Source ABC ✓
- Phase 3 depends on Phase 1+2 = CE v1.0 ✓
- Phase 4 depends on Phase 3 (3 pilots running) ✓
- Phase 5 depends on Phase 4 close (HN essay) ✓

### 4. Does the unified roadmap reconcile the three input plans?

| Input plan | How v4 reconciles |
|---|---|
| **CE ROADMAP v3** (code-context only) | Superseded by engine framing. v3 phases mapped 1:1 to v4 phases plus 4 Sourcegraph-gap additions. |
| **multi-repo-spine.md** (CE = canonical "find" skill) | Updated: CE = v0.2 `find-links` reference impl (more accurate per spec/README evolution). install-department is reference skill #1, not CE. |
| **ce-anabasis-tie.md** (4 artifacts, ½ day) | Phase A executes 3 of 4 already-shipped + 1 new (find-links.md stub). Coherent with current spec state. |

### 5. Is the depth-packer-vs-engine framing consistent?

**Yes after SKILL.md rewrite (mid-session).** The headline now leads with "engine for building and querying a queryable, compounding company brain." Depth packing is one of five capabilities. ROADMAP-v4 § 4 + every phase audit reflect this.

## Drift risks across the whole roadmap

1. **YC submission deadline (May 4) vs Phase 0 work**: Phase A is ½ day; Phase 0 is ~1 week. **Mitigation**: Phase A unblocks YC; Phase 0 starts post-submission. Don't conflate.

2. **Cross-team coordination post-funding**: Phases 3-5 require CE + Anabasis runtime + syroco-product-ops to coordinate. **Mitigation**: weekly sync; integration test gates per phase audit recommendations.

3. **Spec v0.2 freeze quality**: 3 customers + 6 months internal use = 9 months of data. Small N for a freeze. **Mitigation**: v0.3 deprecation window + alias support per spec/skill.md § Stability commitment.

4. **CE versioning post-Phase-4**: 0.3.0 → ?. Locked decision deferred. **Action item**: decide before Phase 4.

5. **Connector library extraction**: stays in syroco-product-ops or extracts? Locked decision deferred. **Action item**: decide before Phase 4.

## What this audit confirms

- **Engine framing holds** end-to-end. Every phase reinforces "CE = engine for the company brain."
- **Scope discipline preserved**: connectors out of CE, LSP deferred, benchmarks deferred.
- **YC RFS pitch is supported by deployable phases**, not vapor. Phase 1+2+3 produce the demo; Phase 4 produces the proof.
- **Independent usefulness preserved**: every phase keeps "CE works without Anabasis installed" as an invariant.

## Recommendation

**Approve the unified roadmap.** Ship Phase A by Mon May 4. Begin Phase 0 immediately after YC submission (or in parallel post-submission per critical path). Phase 0 + 0.5 + 1 + 2 = v1.0 = pre-funding ship.

**Strongest single risk**: cross-team coordination at Phase 3 (post-funding). Mitigate with weekly syncs from Day 1 of funding.

**Strongest single asset**: Phase 1 — load-bearing for the YC pitch, 4 Critical RFS pillars served, produces the EntityStore + SignalSource ABCs that drive Anabasis spec v0.2.

The roadmap is **demonstrably aligned with YC RFS company-brain framing across all 8 phases**, with no pillar orphaned, no phase below 5/7 RFS coverage, and 3 phases at 7/7 with 4-6 Critical strengths each.
