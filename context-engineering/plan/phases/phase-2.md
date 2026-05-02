# Phase 2 — `pack --wiki` + multi-hop + lens + MCP wiki tools

> **Goal**: Make CE's retrieval surface read entity pages first, source files second. This is what turns CE from "depth-packer" into "the find-links skill on top of an EntityStore." Ships the MCP tools the Anabasis runtime calls.
>
> **Effort**: M (1-2 weeks) | **Status**: pending | **Blocks**: Phase 3 (bootstrap), v0.2 spec freeze
>
> **Source**: ROADMAP.md v3 § Phase 3a + multi-repo-spine.md § Phase 3 + Animesh "data changes with context" (lens, multi-hop).

## Why this phase

Phase 1 built the brain (events/ + wiki/ + audit/). Phase 2 makes it queryable. Without Phase 2, the brain is a static artifact a human reads with Obsidian. With Phase 2, it's a runtime-queryable surface other skills compose against.

The pitch deck says find-links operates over the brain. Phase 2 ships find-links.

## Deliverables

### 2.1 — `pack --wiki` mode (M, 2-3 days)

Resolves entity pages **first**, source files **second**. Within budget:
1. Resolve entities via semantic + graph traversal of `[[wiki-links]]`
2. Pack matching entity pages at **Full** depth (each entity = one wiki/<slug>.md)
3. Pull underlying source files cited in the entity's `sources[]` at **Detail / Summary / Mention** depths
4. Demote any source file that doesn't appear in any matching entity's `sources[]`

**Output structure**:
```
<!-- depth-packed [wiki] query="401 on refresh tokens" budget=8000 used=~7600 entities=3 source_files=8 -->

## Entity pages (3 at Full)
### auth-middleware (ent_a4f3)
(complete entity page including frontmatter + body + See also + Provenance)

### token-store (ent_b7c1)
(complete entity page)

### session-policy (ent_c5d6)
(complete entity page)

## Source files (5 at Detail, 3 at Mention)
### src/auth/middleware.ts
(headings + first paragraphs)

...
```

**Acceptance**:
- `pack --wiki "401 on refresh tokens"` returns ≥1 entity page at Full + supporting source files
- Budget utilization ≥ 80%
- Each cited source file traces to at least one entity's `sources[]`

### 2.2 — `pack --multi-hop N` (M, 2 days) **[Animesh]**

Traces N-hop reasoning paths through `[[wiki-links]]`. Returns each hop with the relation kind.

```bash
pack "how does 401 propagate to compliance reporting" --wiki --multi-hop 3
```

**Algorithm**:
1. Resolve entry entity (semantic match on query)
2. BFS through `[[wiki-links]]` up to N hops, scoring each hop by edge type weight (uses existing 17-relation-kind weighting from `code_graph.py`)
3. Cap at top-K paths by total path score
4. Render each path as a `## Reasoning path` block:
   ```
   ## Reasoning path 1 (score 0.78)
   auth-middleware → [depends on] → token-store → [implements] → session-policy → [references] → 2026-Q1-compliance-ADR
   ```

**Why this matters for the company brain**: bug-fix queries need 1 hop ("which file"). Compliance / impact queries need 3+ hops ("what's the cascading effect of changing X"). Sourcegraph's Deep Search has a Lua evaluator for this; CE has structured graph traversal — different shape, similar power.

**Acceptance**:
- `pack --wiki --multi-hop 3` returns at least one Reasoning path block
- Path score is monotonic in path length (longer paths ranked lower at equal edge weight)
- Cycle detection prevents infinite loops

### 2.3 — Query-as-lens re-ranking (S, 1 day) **[Animesh]**

The same entity page surfaces different `[[See also]]` priorities depending on query.

**Implementation**:
1. After matching an entity to query, compute query embedding
2. Re-rank the entity's `links_out[]` by cosine similarity of (query embedding, neighbor entity centroid_embedding)
3. Return top-K reranked neighbors instead of the static order

**Example**:
- Query "401 bug" against `auth-middleware` entity → reranks `[[token-store]]` first (concrete cause)
- Query "GDPR retention" against `auth-middleware` entity → reranks `[[2026-Q1-compliance-ADR]]` first (policy concern)
- Query "token rotation policy" against `auth-middleware` entity → reranks `[[session-policy]]` first

Same entity, three different lenses, three different orderings.

**Acceptance**:
- 3 different lens queries on the same entity produce 3 different `[[See also]]` orderings
- Lens reranking respects edge weights (won't promote a `co_located` neighbor over an `extends` neighbor without strong cosine signal)

### 2.4 — MCP `wiki.{ask,add,audit,export}` tools (M, 2 days)

Extend `mcp_server.py` with four new tools:

| Tool | Args | Returns |
|---|---|---|
| `wiki.ask` | `query`, `budget`, `lens?`, `multi_hop?` | depth-packed markdown |
| `wiki.add` | `source_ref`, `claims[]` | events appended |
| `wiki.audit` | (none) | `audit/proposals.md` content |
| `wiki.export` | `format` (obsidian / json-ld / static-html) | archive bytes |

These are the MCP calls Anabasis runtime makes. Stable names; same shapes survive into v0.2 spec freeze.

**`wiki.ask`** is the find-links primary surface — wraps `pack --wiki` with optional lens + multi-hop.

**`wiki.add`** is the runtime's emit-back surface — when a connector produces new claims, runtime calls `wiki.add(source_ref, claims)` to append events.

**`wiki.audit`** returns the Auditor's current proposals queue.

**`wiki.export`** lets users take the brain elsewhere (Obsidian vault, JSON-LD for graph DBs, static HTML for sharing). Critical for the open-core "your data is yours" promise.

**Acceptance**:
- All 4 tools registered in MCP introspection
- `wiki.ask` over a 50-entity brain returns ≤ 8000 token output in <2s
- `wiki.export obsidian` produces a vault that opens in Obsidian with graph view + backlinks
- MCP HTTP hardening (Phase 0.11) applies — auth required by default

### 2.5 — Anti-hallucination filters extended to wiki (S, ½ day)

Topic / section / confidence filters from current packer extended to entity pages:
- **Topic filter**: entity title + body must overlap ≥ 25% with query terms (else cosine fallback)
- **Confidence filter**: entity pages with `confidence < 0.5` get a `<!-- low-confidence: see audit/proposals.md -->` injected at top of pack output
- **Source confidence**: when a cited source has stale `ts` (older than 30 days vs current HEAD), inject staleness warning

**Acceptance**: low-confidence entity pages rendered include the warning marker; audit/proposals.md surfaces them for human review.

### 2.6 — Update `find-links.md` from `v0.2-draft` → `v0.2` (S, ½ day)

After Phase 2 ships, the find-links contract is no longer a draft. Update `Repos/anabasis/spec/reference-skills/find-links.md`:
- Drop `-draft` suffix
- Resolve all "open questions" with implementation answers (or explicit "deferred to v0.3")
- Tag CE repo as `v0.2.0`
- Update `Repos/anabasis/spec/README.md` to reflect v0.2 ship

**Acceptance**: spec/README links to `find-links.md` (v0.2, no draft); CE Git tag matches.

## Acceptance criteria (phase-level)

- [ ] All 6 deliverables shipped
- [ ] Phase 0 + 0.5 + 1 regression eval still passes
- [ ] Bug-fix demo runs end-to-end in 30 seconds (the Phase 3 demo dependency)
- [ ] Company-brain demo runs (anabasis init → connector → wiki.ask → 30 sec answer)
- [ ] MCP `wiki.ask` callable from Claude Code, Cursor, Cline as a slash command equivalent
- [ ] find-links.md → v0.2 (no draft suffix)
- [ ] CE tagged v0.2.0

## Dependencies

- ✅ Phase 1 wiki schema (entities to pack against)
- ✅ Phase 1 Source ABC (claim ingestion to populate entities)
- ✅ Phase 1 EntityStore + SignalSource ABCs documented (spec v0.2 contract)
- ⚠️ Anabasis runtime calls these MCP tools — runtime side is closed (private), schedule with Anabasis team

## What this phase does NOT do

- No connectors (lives in syroco-product-ops)
- No Anabasis runtime (closed for 90 days)
- No NLI contradiction classification (deferred to Proving Layer)
- No precise xref / LSP (deferred per locked decision #4)
- No public benchmark vs Cody / Cursor (deferred to Proving Layer)

## YC RFS alignment (preview)

| Pillar | Phase 2 contribution |
|---|---|
| **Executable runtime** | **`wiki.ask` is the find-links primary call. Anabasis runtime fires it. v0.2 contract frozen.** |
| Installs | (no new install primitives — Phase 1 covers) |
| Human knowledge | Lens reranking respects human-authored entity ordering as fallback |
| Connections | `wiki.add` is the inbound contract for connector emit-back |
| **AI** | **Multi-hop reasoning paths + query-as-lens reranking = AI-grade traversal of the brain. Sourcegraph Deep Search analog without the Lua eval.** |
| **Skills that automate** | **MCP `wiki.{ask,add,audit,export}` is the surface every other skill composes against.** |
| Company brain | Brain becomes runtime-queryable; `wiki.export` makes it portable (Obsidian / JSON-LD / static) |

7/7 served, with **Executable runtime + AI + Skills that automate** at full strength. Phase 2 is the "make it runnable" phase that follows Phase 1's "make it real."
