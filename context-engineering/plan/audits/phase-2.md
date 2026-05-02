# Phase 2 audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Hit | Strength | Evidence |
|---|---|---|---|
| **Executable runtime** | ✅ | **Critical** | 2.4 MCP `wiki.{ask,add,audit,export}` is the runtime↔skill contract. Stable tool names that survive into v0.2 freeze (2.6). The runtime can fire find-links against the brain through these calls. |
| Installs | ⚠️ | Indirect | No new install primitives. Phase 2 inherits Phase 1's `init_brain.py`. |
| **Human knowledge** | ✅ | Strong | 2.3 query-as-lens preserves human-authored entity ordering as fallback (won't override curator's intent without strong AI signal). 2.5 anti-hallucination filters preserve human trust. |
| **Connections** | ✅ | Strong | 2.4 `wiki.add` is the inbound emit-back contract. Connectors push events through this; CE doesn't import them. Closes the loop on Phase 1's Source ABC. |
| **AI** | ✅ | **Critical** | 2.2 multi-hop reasoning + 2.3 query-as-lens = AI-grade brain traversal. The Sourcegraph Deep Search analog without the Lua sandbox dependency. |
| **Skills that automate** | ✅ | **Critical** | 2.4 MCP tools are *the* surface every other skill composes against. `wiki.ask` becomes find-links's headline call; install-department, audit-process, sota-search all stack on top. |
| **Company brain** | ✅ | Strong | 2.4 `wiki.export` makes the brain portable (Obsidian / JSON-LD / static HTML) — closes "your data is yours" pitch promise. 2.1 `pack --wiki` makes it queryable. |

**Score: 6/7 strong + 1 indirect.** Three pillars at Critical strength.

## Strongest pillars

Phase 2 is the **runtime contract** phase. Three Critical pillars:
- **Executable runtime**: contract surface frozen.
- **AI**: multi-hop + lens elevate retrieval beyond Phase 0.5's auto-mode.
- **Skills that automate**: MCP tools are the composable primitives.

Phase 1 made the brain real; Phase 2 makes it runnable. Together they're the v1.0 ship.

## Drift risks flagged

1. **MCP tool naming bikeshed**: `wiki.ask` vs `find_links.query` vs `brain.search` — all defensible. **Mitigation**: lock `wiki.*` namespace because it's already in ROADMAP v3 and matches the wiki layer. Document the choice in find-links.md v0.2 § 3.1.

2. **`wiki.export` formats sprawl**: Obsidian, JSON-LD, static HTML, Markdown archive, GraphML, Mermaid... **Mitigation**: ship 3 formats only (obsidian, json-ld, static-html). Others are user-extension via `wiki.export --format custom --plugin <path>`.

3. **Multi-hop blow-up**: `--multi-hop 5` on a 1000-entity brain = expensive. **Mitigation**: cap at N=3 by default; `--multi-hop N` with N>3 prints a warning. Score-pruning (2.2 algorithm) bounds expansion.

4. **Lens reranking false confidence**: query embedding might surface a tangentially-related neighbor over the curator's primary link. **Mitigation**: lens reranking is a *resort*, not a rewrite — the curator's order is the prior. Lens only reorders if cosine difference exceeds a threshold (default 0.15).

5. **Anti-hallucination filters extended to wiki carry false-negative risk**: low-confidence entity pages might be exactly what the user needs (early-stage concepts). **Mitigation**: 2.5 injects warning marker, doesn't filter out. User sees the warning + content together.

6. **find-links v0.2 freeze locks contract before Anabasis runtime sees real load**: 2.6 freezes contracts based on CE behavior, not customer behavior. **Mitigation**: this is the *open spec, closed runtime* model. Customer feedback iterates the runtime; spec stays stable until v0.3 with a deprecation window.

## Cross-phase risks

- **Phase 3 dependency**: Anabasis bootstrap (Phase 3) demos `wiki.ask` end-to-end. If 2.4 ships unstable, Phase 3 demo breaks. **Action**: 2.4 stability gate = MCP introspection passes + 24-hour soak test.

- **Phase 4 dependency**: open-core release (Phase 4) freezes the v0.2 spec. 2.6 produces the v0.2 docs. If spec docs miss any v0.2 ABC behavior CE relies on, Phase 4 ships incomplete. **Action**: 2.6 includes a "spec coverage check" — every behavior CE relies on must be either documented in spec or explicitly out-of-spec.

## Demo dependency

The bug-fix demo and the company-brain demo (both planned for YC + open-core launch) require Phase 2:
- Bug-fix: `pack "users getting 401"` → 30 second context → fix
- Company-brain: `pack --wiki "what changed in our refund policy last quarter"` → entity page + provenance → answer

If Phase 2 doesn't ship, both demos run on Phase 0.5's keyword/semantic surface — usable but doesn't show the wiki layer. **The wiki layer is the differentiation vs Sourcegraph / Cursor / Cody.**

## What this audit does NOT cover

- Whether 2.4's MCP tool shapes are compatible with Cursor's MCP server discovery (assumed yes; verify in Phase 5)
- Whether `wiki.export obsidian` produces a vault that survives Obsidian's plugin updates (out of scope; ship a known-good baseline)
- Whether multi-hop paths render legibly for ≥5 hops (capped at 3 by default per drift mitigation)

## Recommendation

**Phase 2 is the v1.0-completing phase.** Approve as-is. Sequence:
1. 2.1 + 2.5 (pack --wiki + anti-hallucination filters) — week 1, foundation
2. 2.2 + 2.3 (multi-hop + lens) — week 1-2, AI surface
3. 2.4 (MCP wiki.* tools) — week 2, runtime contract
4. 2.6 (find-links v0.2 freeze + tag CE v0.2.0) — week 2 day 5

**Strongest risk**: 2.4's MCP shapes leaking into the closed Anabasis runtime before v0.2 spec freezes. **Mitigation**: write the runtime against a mock `wiki.*` MCP server first (CE's MCP server itself); only swap to runtime-internal calls after 2.6.

**Phase 1 + Phase 2 = v1.0 ship.** Together they answer the YC question "what does the engine actually do?" with a 30-second working demo.
