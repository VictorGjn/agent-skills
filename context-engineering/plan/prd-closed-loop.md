# PRD: CE Phase 1 — Closed-loop wiki layer for the Anabasis brain

**Owner:** Victor Grosjean (CPO / lone PM)
**Stakeholders:** future Anabasis runtime; CE skill consumers (Claude Code, n8n cron routines, future agents)
**Date:** 2026-05-02
**Status:** Draft (pre-implementation review)
**Deadline:** Anabasis MVP onboarding lift-off — target ~6 weeks (calibrated for `~half day` per item × 4 items + integration buffer)
**Solution:** `~/.claude/plans/stateless-waddling-whisper.md` § P0.1 + P0.5 + P2.1 + P2.2 (the cannonball path)
**Opportunity:** Anabasis runtime's `find-links` reference skill — the closed loop that turns brain reads into brain writes back into reads
**Submarine:** internal CE engineering. Local PRD only; no Notion/Deliveries mirror per `feedback_submarine_mode`.

---

# PART 1 — Product

## 1. Problem

### The closed-loop gap

CE is read-only from any agent's perspective. A skill calls `pack_context`, gets back a depth-packed bundle, and that's it — nothing the skill subsequently does flows back into CE's substrate. Tomorrow's run sees yesterday's brain. For an open agent runtime that fires routines on a cadence, that means every cron invocation either:
- Re-derives everything from raw upstream sources (expensive, slow, redundant), or
- Silently drifts (skill emitted a finding nobody persisted; next skill re-discovers it).

Anabasis is being pitched as "Temporal for company-knowledge agents" — durable, stateful, replayable. Without a write-back surface in CE, that pitch is half-true: the orchestration is durable, the data is not.

### Who's affected

**Anabasis runtime** (~10 routines at MVP, scaling to 50+ at customer onboarding)
- Each routine wakes up expecting fresh state from prior runs of the same or different skills.
- Today: no shared write-back substrate; each skill builds its own ad-hoc persistence (Slack messages, GitHub issues, hand-rolled Notion writes).
- A `competitive-intel` routine flags a pricing change at Acme; tomorrow's `weekly-brief` routine has no way to read that finding from the brain. Loop is open.

**Skill authors** (Victor today; future open-source contributors via Anabasis spec)
- Want to compose skills that read CE state and write back canonical claims.
- Want a stable contract: "the events log is append-only, the wiki is consolidated daily, the audit surfaces contradictions."
- Today they'd have to build that themselves per skill.

**End consumers of the brain** (Victor's ops use cases; future CSM personas)
- Ask "what's the current Acme pricing tier?" — want the latest known, with provenance, with staleness markers, with prior-version chain visible if asked.
- Today: only the file-level retrieval CE provides; no entity-level state.

### Three failure modes when we don't ship

1. **Re-derivation cost** — every routine re-reads raw sources, re-clusters, re-ranks. Multiply by 10–50 routines; eats agent-runtime budget linearly.
2. **Contradiction accumulation** — without `supersedes:` / `superseded_by:` chains, a brain populated by multiple skills over weeks holds both "Acme price = $5k" (March) and "Acme price = $7k" (April) as equally current. Queries return mush.
3. **Anabasis pitch credibility gap** — the YC narrative leans on "durable orchestration." If the data layer Anabasis orchestrates is itself volatile / stateless / contradictory, the pitch breaks at the first reviewer demo question.

## 2. Context reminder

### What already exists in CE (post merges of PR #14, #16, #17, #18)

| Layer | Status | What it does | What it lacks |
|---|---|---|---|
| Events log primitives | ✅ shipped (PR #10) | `scripts/wiki/events.py` writes `events/<date>.jsonl`; `semantic_shift.py` detects when events drift far enough from current entity centroids | No producer feeding events from skills (only WorkspaceSource as a one-shot seed) |
| Wiki schema spec | ✅ landed in `plan/phases/phase-1.md` (PR #14, #16) | Canonical `wiki/<slug>.md` frontmatter shape — `id, kind, scope, sources[], confidence, links_in/out, centroid_embedding`; slug collision rule; schema migration policy | No implementation (`wiki_init.py`, `validate_page.py` planned but not coded) |
| Source ABC | 🧪 specced in `plan/phases/phase-1.md` §1.4 | Abstract base class shape with `WorkspaceSource` + `GithubRepoSource` concrete | No `EventStreamSource` (write-back), no `GraphifyWikiSource` (consume graphify's wiki output) |
| Auditor | ❌ specced in §1.6, not implemented | Will propose splits / merges / contradictions / dead-link cleanups in `audit/proposals.md` | Nothing exists — entire module to build |
| Decision-continuity primitive | ❌ specced in MoSCoW (handoffs) | `supersedes:` / `superseded_by:` / `valid_until:` on `kind: decision` pages | Schema fields not yet in `phase-1.md` §1.2 (need addition); no Auditor rule |
| Freshness markers | ❌ same | `freshness_score`, `last_verified_at` on entity frontmatter | Not yet specced in schema |
| Namespace / scope | 🧪 schema field added (PR #16) | `scope: <corpus_id>` on entity frontmatter | No `wiki.ask --scope` filter implementation in `mcp_server.py` |
| MCP wiki tools | ❌ specced (`plan/phases/phase-2.md` §2.4) | `wiki.{ask, add, audit, export, closure}` shape committed to spec | No implementations in `scripts/mcp_server.py` |

### Prior decisions

| Date | Source | Decision |
|---|---|---|
| 2026-05-01 | `plan/phases/phase-1.md` §1.2.1 | Wiki = refusal+rebuild < 10k entities; events = forward-migrate from day one. Migration cost on the side that can't be rebuilt. |
| 2026-05-02 | PR #16 §1.2.0 | Hybrid via `GraphifyWikiSource` (4th `Source` subclass): consume graphify's `--wiki` as input, re-emit in CE's richer schema. |
| 2026-05-02 | PR #16 §1.2 schema | Slug collision rule: `used_slugs` set with lowercased keys for case-insensitive filesystems; numeric suffix on collision. |
| 2026-05-02 | PR #16 §3.0.1 SPEC-mcp | `next_tool_suggestions` field on every tool response. Bake into MCP wiki tools from day one. |
| 2026-05-02 | Plan Step 1 (Tier B) | `unresolved_alias` edge kind shipped (PR #17): broken-alias signal must be reflected somewhere queryable; Auditor is the natural surface. |

### What was tried elsewhere

- **graphify v0.4** (`safishamsi/graphify`) — generates `--wiki` output but not entity-level. No supersession, no freshness, no event log. Their wiki is a static snapshot per build, not a closed loop.
- **CRG** (`tirth8205/code-review-graph`) — durable graph in SQLite, but no entity store, no semantic-shift consolidation, no Auditor.
- **CocoIndex** (André Lindenberg's framing) — has the "agent outputs become source data" loop conceptually but not as a stable wire contract that any agent runtime can call.
- **Internal Syroco workflows** — Slack messages + Notion pages + ad-hoc skill state. The fragmentation Anabasis is supposed to fix.

The closed-loop wiki layer is the gap nobody else has filled with a stable agent-callable contract.

## 3. Chosen solution

### Core philosophy

Events are primary truth (append-only, never mutated, source-cited per claim). Wiki entity pages are the consolidated, queryable surface — derivable from events, periodically rebuilt or migrated. The Auditor is the contradiction-detection + drift-flagging layer that runs as a routine. The MCP `wiki.*` verbs are the runtime-callable contract. The `EventStreamSource` is the loop closer: any skill can emit events back into the brain via the same Source ABC that workspace/github sources use.

### Non-goals (V1)

> **NOT in this release.** Pressure to add any of these during build routes back to this PRD.
>
> - **Cross-corpus reasoning.** A query in scope `competitive-intel` cannot reference entities in `lead-qual`. One scope per query (per `wiki.ask --scope=…`).
> - **Implicit contradiction detection** (semantic-similarity-based). Auditor v1 only flags **explicit** `superseded_by:` chains. Detecting "X says A, Y says NOT A" without a marker is v2 research.
> - **Freshness scoring against ground truth.** `freshness_score` is heuristic (last_verified_at + source-type half-life) — calibrating against actual decision drift is open work.
> - **Real-time write coalescing.** `EventStreamSource` writes append to `events/<today>.jsonl`; consolidation runs on a cadence (semantic-shift trigger), not synchronously per-write. Read-after-write within the same minute may not show the new event.
> - **Schema migration tooling at scale.** Below 10k entities we use refusal+rebuild for wiki; events forward-migrate. The actual `migrate_v1_to_v2.py` etc. only get written when first schema bump happens.
> - **Wiki page content quality / LLM-assisted authorship.** Events extract structured claims; wiki consolidation is mechanical (cluster events → render frontmatter + body skeleton). LLM-assisted prose is v2.

### Scope (MoSCoW)

#### Must have — blocks "closed-loop demo" milestone

- **M1.** `EventStreamSource` (P0.1) — 3rd `Source` subclass; agents emit `EventStreamSource.emit_events([{ts, source_type, source_ref, claim, embedding?, entity_hint?}])` from a cron-driven context; events land in `events/<today>.jsonl` (append-only); `semantic_shift.py` picks them up on next consolidation tick.
- **M2.** `wiki_init.py` seeder (Phase 1.5) — reads events log + existing wiki; writes/updates `wiki/<slug>.md` per consolidated entity; idempotent (re-running with same input produces same output).
- **M3.** `validate_page.py` (Phase 0.3 / scaffold from Tier B) — schema-version refusal: errors out with remediation message on mismatch ("Run `wiki_init.py --rebuild`").
- **M4.** `audit.py` (Phase 1.6) — Auditor routine that emits `audit/proposals.md`. v1 rules: stale `kind: decision` flagged when `superseded_by:` chain exists and current entities still link to the superseded predecessor; entities whose computed freshness (see M6) sits below 0.3 AND whose `last_verified_at` is older than N days; entities sharing a slug-collision footnote.
- **M5.** Decision continuity schema fields (P2.1) — `supersedes:` (id), `superseded_by:` (id), `valid_until:` (date) added to `wiki/<slug>.md` frontmatter spec for `kind: decision`. Auditor rule per M4.
- **M6.** Freshness markers (P2.2) — **stored field**: `last_verified_at: <iso8601>` (skill-emitted on every event/refresh that touches the entity). **Computed-on-read field**: `freshness_score: 0.0–1.0` (derived at query time from `last_verified_at` + a per-source-type half-life policy declared in `scripts/wiki/freshness_policy.py`). Stored vs computed split is deliberate: avoids write-back to entity files just to age them, keeps `events/<date>.jsonl` immutability, makes the policy tuneable without rewriting historical pages. Auditor and `wiki.ask` callers receive the computed score; the entity file stores `last_verified_at` only.
- **M7.** `wiki.ask --scope=<corpus_id>` MCP filter (P0.5 impl) — implements the namespace primitive landed in PR #16 schema. Default scope = `default`; multi-source loops MUST set scope on writes.

#### Should have — fast follow

- **S1.** `GraphifyWikiSource` (P0.2) — 4th `Source` subclass; consumes `graphify-out/wiki/` if present and re-emits in CE schema; preserves user choice to run graphify upstream without competing. **Demoted from Must after Wave-0 gating analysis**: the closed-loop demo only needs M1 + M2-M7 to close (skill emits → wiki refresh → Auditor flags); graphify-as-input is real-world ergonomics for users who already run graphify, not a gate on the demo. Slips to Wave 1 (real-routine integration).
- **S2.** `wiki.add(source_ref, claims[])` MCP verb implementation — the runtime-facing alias to `EventStreamSource.emit_events`. M1 ships the underlying capability; this exposes it via MCP for non-Python consumers.
- **S3.** `wiki.audit` MCP verb implementation — surfaces `audit/proposals.md` content to runtime callers. Same delta as S2 for the Auditor.
- **S4.** Telemetry events: `entity.consolidated`, `entity.superseded`, `audit.flagged`, `freshness.expired`. Stdout JSONL per `SPEC-mcp.md` §9 telemetry pattern.
- **S5.** `wiki.export obsidian` impl — the "your data is yours" promise. Generates an Obsidian-readable vault from the brain.

#### Could have — backlog

- **C1.** `EventStreamSource` rate-limiting / backpressure (defer until a real skill emits >100 events/sec).
- **C2.** Auditor scheduled-run integration with cron-style trigger (currently invoked manually).
- **C3.** `wiki.ask --multi-hop N` (Phase 2.2) — query-rooted multi-hop traversal. Specced; not in V1 of closed-loop.
- **C4.** `wiki.closure(entity_id)` impl — entity-rooted blast-radius closure (Phase 2.4). Specced and reserved in `SPEC-mcp.md` §10; impl is Phase 2 work.

#### Won't have — this release

- **W1.** Cross-corpus / cross-scope queries. One scope per query call. Multi-scope joining is v2.
- **W2.** Implicit contradiction detection via semantic similarity. Only `superseded_by:` chains flagged in v1.
- **W3.** Custom Auditor rules pluggable from skills. v1 Auditor rules are a fixed set in `audit.py`.
- **W4.** Real-time stream-of-events to subscribers. v1 is poll-based via `wiki.audit`.
- **W5.** Schema migration tooling beyond `validate_page.py` refusal. Forward-migrate events as needed; rebuild wiki when schema rev.
- **W6.** LLM-authored wiki page content. Mechanical consolidation only.
- **W7.** Hyperedge consumption from graphify v2 (TODO tag in `graphify_adapter.py` covers the deferral).

### User stories (8)

- **US1.** As a **competitive-intel skill**, I want to emit a `claim: "Acme pricing tier raised from $5k to $7k"` event with `source_type: web, source_ref: "acme.com/pricing", scope: competitive-intel`, so that **tomorrow's `weekly-brief` skill reads the updated price as current state**.
- **US2.** As a **lead-qualification skill**, I want to mark a `lead-acme-2026q1` decision as `superseded_by: lead-acme-2026q2`, so that **future `wiki.ask` queries about Acme show the v2 decision and flag any entity still referencing v1 as stale**.
- **US3.** As an **Anabasis routine**, I want to call `wiki.ask --scope=competitive-intel "Acme pricing tier"` and receive only competitive-intel-scoped entities, so that **lead-qualification entities don't bleed into competitive analysis**.
- **US4.** As **Victor reviewing the brain**, I want `audit/proposals.md` to surface decisions that have been silently superseded but are still referenced by current entities, so that **I can decide whether to update the references or revoke the supersession**.
- **US5.** As **Victor reviewing the brain**, I want pages with `freshness_score < 0.3` and `last_verified_at` past 30 days to surface in `audit/proposals.md`, so that **stale competitive-intel and lead-status entries get triaged before they leak into a customer-facing brief**.
- **US6.** As a **skill author building on top of CE**, I want `EventStreamSource` to be a stable contract documented in `phase-1.md` §1.4, so that **I can write skills that cleanly emit events without coupling to internal CE implementation**.
- **US7.** As a **graphify user with a TS monorepo**, I want CE to consume `graphify-out/wiki/` via `GraphifyWikiSource` and re-emit in CE's richer schema, so that **I can run graphify upstream and still get CE's supersession + freshness without duplicate setup**.
- **US8.** As a **CE maintainer**, I want `validate_page.py` to refuse loading any entity page whose `schema_version` doesn't match the current CE release, with a clear remediation message, so that **schema bumps don't silently corrupt downstream queries**.

### Alternatives considered

| Option | Rejected because |
|---|---|
| **Skip EventStreamSource; let skills write directly to `wiki/<slug>.md`** | Bypasses the events log (primary truth). No append-only audit trail. Each skill reinvents persistence semantics. |
| **Use a real graph database (Neo4j) for entity store** | Already rejected per `ROADMAP-v4.md` §7 non-goals: markdown + frontmatter + `[[wiki-links]]` IS the graph store. Cypher-via-MCP is more surface than agents need. |
| **Implicit contradiction detection in v1 Auditor** | Open research problem (semantic-similarity-based contradiction is not solved). Ship explicit `superseded_by:` chains v1; lift to implicit in v2 once we have ground-truth data. |
| **Synchronous write-then-consolidate** (every `EventStreamSource.emit` rebuilds wiki) | Catastrophic cost at >1 event/sec. Async semantic-shift consolidation already shipped (PR #10) is the right architecture. |
| **Single global namespace (no `scope` field)** | Multi-source loops would bleed into each other; competitive-intel entities would surface in lead-qualification queries. Engineering would chase "why is Acme pricing in our auth docs" forever. |
| **Pluggable Auditor rules from skills** (W3) | Premature. Fixed rule set in v1 keeps the audit surface predictable. Once 3+ rule patterns exist, generalize. |

## 4. Design reference

CLI + MCP feature, no UI. The "design" surface is:
- The **markdown layout** of `wiki/<slug>.md` and `audit/proposals.md` (already specced in PR #16 — `plan/phases/phase-1.md` §1.2)
- The **MCP tool catalog** (already specced in `plan/phases/phase-2.md` §2.4 + `SPEC-mcp.md` §3 / §10)
- The **CLI invocation** of `wiki_init.py`, `validate_page.py`, and `audit.py`

References:
- `plan/phases/phase-1.md` §1.2 (schema), §1.2.1 (migration policy), §1.4 (Source ABC), §1.5 (wiki_init.py spec), §1.6 (Auditor spec)
- `plan/phases/phase-2.md` §2.4 (MCP wiki.* tools)
- `SPEC-mcp.md` §3.0.1 (`next_tool_suggestions` field), §10 reservations

## 5. Front-end surfaces

> No UI. CLI + MCP only. Skipping per template `## 5 Front-end` adapt-rule.

## 6. Acceptance criteria (Given / When / Then)

### Must pass — V1 (closed-loop demo gate)

- [ ] **AC1.** Given a brain with 5 existing entities, when a skill calls `EventStreamSource.emit_events([{ts, source_type='manual', source_ref='test', claim='X is Y', entity_hint='entity_a'}])`, then `events/<today>.jsonl` contains the new event line within 100ms.
- [ ] **AC2.** Given accumulated events whose centroid drifts >0.4 from `entity_a.centroid_embedding`, when `semantic_shift.py` next runs, then `wiki_init.py` regenerates `wiki/entity_a.md` with the updated body and frontmatter.
- [ ] **AC3.** Given a `wiki/decision-acme-pricing-v1.md` with `superseded_by: dec_v2_id`, and a current `wiki/lead-acme.md` linking to `[[decision-acme-pricing-v1]]`, when `audit.py` runs, then `audit/proposals.md` lists "lead-acme references superseded decision-acme-pricing-v1" under a "Stale references" heading.
- [ ] **AC4.** Given an entity page with `last_verified_at: 2026-04-01` and a `web` source-type half-life of 30 days declared in `freshness_policy.py`, and current date 2026-05-15 (44 days later), when `audit.py` runs, then `audit/proposals.md` lists the page under "Freshness expired" (computed `freshness_score` < 0.3 — never read from the page; always derived).
- [ ] **AC5.** Given a brain with entities scoped `default`, `competitive-intel`, and `lead-qual`, when an Anabasis routine calls `wiki.ask --scope=competitive-intel "Acme pricing"`, then only competitive-intel-scoped entities appear in the response. No `lead-qual` or `default` entities leak in.
- [ ] **AC6.** Given a `wiki/<slug>.md` with `schema_version: "1.0"` (or any earlier value) and current CE expecting `1.1`, when `validate_page.py` is invoked, then the script exits non-zero with stderr text "Run `python3 scripts/wiki/wiki_init.py --rebuild` to regenerate from events log."
- [ ] **AC7.** Given two entities with titles `"Data Processing"` and `"data processing"`, when `wiki_init.py` writes both, then files are `data-processing.md` and `data-processing-2.md` with distinct `id`s, and `_index.md` footnote records the collision.
- [ ] **AC8.** Given a routine emits 10 events via `EventStreamSource` then immediately calls `wiki.ask`, the response **may** not yet reflect the events (consolidation is async). Documented in `next_tool_suggestions` hint: "events emitted; wiki refresh on next semantic-shift trigger (~5 min)."

### Should pass — V1.1 (post-demo polish, includes Wave 1 items)

- [ ] **AC9.** Given `graphify-out/wiki/` exists with 3 entity pages, when `GraphifyWikiSource.list_artifacts()` is called, then it returns those 3 paths and `emit_events()` produces CE-schema-compliant events. *(S1 — Wave 1)*
- [ ] **AC10.** Given an Anabasis routine calls `wiki.add(source_ref, claims)` via MCP, then events are appended identically to `EventStreamSource.emit_events` (parity).
- [ ] **AC11.** Given an Anabasis routine calls `wiki.audit`, the response is the current `audit/proposals.md` content as markdown.
- [ ] **AC12.** Given any of {`entity.consolidated`, `entity.superseded`, `audit.flagged`, `freshness.expired`} occurs, a JSONL telemetry event is emitted to stdout per `SPEC-mcp.md` §9.
- [ ] **AC13.** Given a brain with 50 entities, when `wiki.export obsidian` is called, an Obsidian-compatible vault zip is returned with `[[wiki-links]]` intact and graph view loadable.

## 7. Success metrics

### North Star

> **Closed-loop tick latency** — from `EventStreamSource.emit_events` call to the relevant entity's `wiki/<slug>.md` reflecting the new claim, end-to-end, **≤ 5 minutes p95** by Anabasis MVP demo (target ~6 weeks from start). This is the minute-bar that lets Anabasis routines compose: a `competitive-intel` routine writing at 09:00 must be readable by a `weekly-brief` routine at 09:05.

### Supporting metrics (adapted HEART for an internal substrate)

| Dimension | Metric | Today | V1 target | How we measure |
|---|---|---|---|---|
| Happiness | Skill-author onboarding cost: minutes to write a "hello world" skill that emits + reads events | n/a (no path) | ≤ 30 min | Time Victor + 1 invited tester from "read the spec" to "first round-trip event" |
| Engagement | Routines actively writing back: # of distinct skills calling `EventStreamSource` per week | 0 | ≥ 3 by week 4 post-demo | grep `events/<date>.jsonl` for distinct `source_type` values, unique-by-week |
| Adoption | % of Anabasis routines using `wiki.ask` (vs raw `pack_context`) for entity-shaped queries | n/a | ≥ 50% by week 6 | Telemetry counter on tool calls, weekly report |
| Task success | Auditor proposal acceptance rate: % of `audit/proposals.md` entries Victor accepts vs rejects | n/a | ≥ 60% (signal: rules are useful, not noisy) | Manual log; if <40%, tune rule thresholds |

### Qualitative sign-off gates (named humans)

- [ ] **Victor** sign-off after walkthrough of a real loop: a `competitive-intel` routine emits a price change → 5 minutes later `weekly-brief` reads the updated price → Auditor flags the prior price as superseded.
- [ ] **Victor** sign-off after seeing `audit/proposals.md` for a 1-week-aged brain produce ≥ 1 useful flag without ≥ 3 noise flags.
- [ ] **One external skill author** (TBD: Animesh? a YC mentor?) walks through the spec in `phase-1.md` §1.4 and writes a skill in ≤ 1 hour without asking for help. If they can't, the spec is the bug.

### Rollout plan (user-facing phasing)

| Wave | Date | Users | Scope |
|---|---|---|---|
| **Wave 0** (closed-loop demo) | ~Week 4 from start | Victor only, on a synthetic 50-entity brain | M1–M7 working end-to-end on a fixture corpus; AC1–AC8 pass |
| **Wave 1** (real-routine integration) | ~Week 5 | Victor + 1 real routine (`product-signals-pipeline` per memory) | S1 `GraphifyWikiSource` ships; real cron routine emits events; brain accumulates; Auditor surfaces real flags; AC9 passes |
| **Wave 2** (Anabasis MVP-eligible) | ~Week 6 | Anabasis runtime calls `wiki.ask` / `wiki.add` / `wiki.audit` via MCP | S2–S5 shipped; telemetry emitting; AC10–AC13 pass; ready for YC demo |

### What a win looks like (one sentence)

A `competitive-intel` cron at 09:00 emits "Acme raised pricing to $7k from $5k" via `EventStreamSource`; a `weekly-brief` cron at 09:05 calls `wiki.ask --scope=competitive-intel "Acme pricing"` and gets back a depth-packed bundle whose Acme entity reads `tier: $7k (superseded $5k from 2026-04 per acme.com/pricing)` with the supersession chain visible — without a human touching anything.

---

# PART 2 — Technical sketch

> Capabilities, not files. The EM (Victor wearing the EM hat for this batch) owns the implementation choices below.

## 8. Front-end sketch

> No UI. Surfaces are (a) the markdown layout of `wiki/<slug>.md` and `audit/proposals.md`, both specced in PR #16, and (b) the MCP tool catalog specced in `phase-2.md` §2.4 and `SPEC-mcp.md`. Nothing new to design.

## 9. Back-end sketch

**Philosophy.** Build on top of what PRs #14 / #16 / #17 already merged. Zero new MCP tools beyond what `phase-2.md` §2.4 already commits us to. Zero new Source ABC fields — the existing `Source.list_artifacts / fetch / metadata / emit_events` shape is what `EventStreamSource` and `GraphifyWikiSource` implement.

**Capabilities required**

1. **A new `Source` subclass that lets agents push events instead of pulling from a workspace.** Same ABC, different direction of dataflow. The append target (events/) is the existing one.
2. **A consolidation runner** that turns events log + existing entities into refreshed `wiki/<slug>.md` pages. Already half-built (`semantic_shift.py` shipped); needs a wrapper that produces the `wiki/<slug>.md` output rather than just detecting drift.
3. **A schema validator that errors on version mismatch.** Stateless; reads frontmatter, compares to a constant, writes a remediation message. Trivial.
4. **An Auditor with a fixed set of rules** that walks the wiki + events and writes proposals. Rules are: stale-supersession-reference, expired-freshness, slug-collision-near-miss. Each rule produces a markdown bullet in `audit/proposals.md`.
5. **A `scope` filter on `wiki.ask`** — schema field already in entity frontmatter (PR #16); MCP plumbing reads `--scope` arg and filters the entity-resolution stage upstream of the existing `pack --wiki` pipeline.

**What the EM owns.** Source ABC discipline (don't leak workspace concepts into EventStreamSource); consolidation idempotency (re-running with same events produces same wiki); Auditor rule registry shape (so v2 pluggable rules don't require rewrite); telemetry event schemas (per `SPEC-mcp.md` §9 conventions).

## 10. Core / data sketch

```
┌──────────────────────┐
│  EXISTING:           │
│  events/<date>.jsonl │  ◄── append-only; events.py + semantic_shift.py shipped
└──────────┬───────────┘
           │
           │ semantic-shift trigger
           ▼
┌──────────────────────┐         ┌──────────────────────┐
│  NEW:                │         │  NEW:                │
│  wiki_init.py (M2)   │ ◄────── │  EventStreamSource   │  ◄── M1
│  (consolidate)       │         │  (skills emit here)  │
└──────────┬───────────┘         └──────────────────────┘
           │
           │ writes
           ▼
┌──────────────────────┐
│  EXISTING SCHEMA:    │
│  wiki/<slug>.md      │  ◄── schema in PR #16
│  + scope: <id>       │  ◄── M7 filter reads here
│  + supersedes/       │  ◄── M5 fields
│    superseded_by/    │
│    valid_until       │
│  + last_verified_at  │  ◄── M6 stored; freshness_score
│                      │       is COMPUTED on read from
│                      │       freshness_policy.py
└──────────┬───────────┘
           │
           │ Auditor walks
           ▼
┌──────────────────────┐
│  NEW:                │
│  audit/proposals.md  │  ◄── M4 audit.py emits here
│  + telemetry stdout  │  ◄── S4
└──────────────────────┘

Plus (deferred to Wave 1 per S1 demotion):
┌──────────────────────┐
│  NEW (S1):           │
│  GraphifyWikiSource  │  ◄── reads graphify-out/wiki/, re-emits in CE schema
└──────────────────────┘  ◄── 4th Source subclass alongside Workspace/GitHub/Event

Plus:
┌──────────────────────┐
│  NEW (M3):           │
│  validate_page.py    │  ◄── refuses on schema_version mismatch
└──────────────────────┘
```

## 11. Phases, risks, open questions

### Implementation phasing (engineering-facing)

| Phase | Window | Deliverable |
|---|---|---|
| **P1** Schema additions | Day 1 (~half day) | M5 + M6 schema fields land in `plan/phases/phase-1.md` §1.2 (doc-only PR). `freshness_policy.py` half-life table per source-type committed. Slug-collision tested via existing fixture. |
| **P2** EventStreamSource | Days 2-3 | M1 (`EventStreamSource`) implementation. Round-trip test against fixture. (`GraphifyWikiSource` deferred to Wave 1 per S1 demotion.) |
| **P3** Validate + seed | Days 4-5 | M2 (`wiki_init.py`) + M3 (`validate_page.py`). Idempotency test: re-run produces same output. Refusal test: bumped schema rejected. |
| **P4** Auditor | Days 6-9 | M4 (`audit.py`) — three rules (stale-supersession, freshness-expired, collision-near-miss). Freshness rule reads `freshness_policy.py` half-life and computes score on the fly from `last_verified_at`. Each rule has a unit test. |
| **P5** MCP plumbing | Days 10-12 | M7 (`wiki.ask --scope`) + S2 (`wiki.add`) + S3 (`wiki.audit`) MCP tool implementations in `scripts/mcp_server.py`. Telemetry events S4 wired. |
| **P6** End-to-end demo | Days 13-14 | Real routine emits events → wiki refreshes → Auditor flags. Wave 0 sign-off. |
| **P7** Wave 1 (post-demo) | Days 15-17 | S1 `GraphifyWikiSource` lands; first real cron routine on the brain; AC9 passes. |

Total: ~17 working days = ~3.5 calendar weeks at full focus, more realistically 5–6 weeks given other priorities. Lines up with the rollout plan above.

### Technical risks

| Risk | Mitigation |
|---|---|
| **Consolidation semantics drift between dev and prod** — `wiki_init.py` running on dev fixtures produces different output than production cron-driven runs | M2 idempotency requirement makes this testable: write a fixture, run twice, byte-compare. CI lint would catch divergence. |
| **Auditor rules too noisy → Victor ignores `audit/proposals.md`** | Wave 0 success metric explicitly requires "≥ 60% acceptance rate." If <40% on the demo week, tune thresholds before Wave 1. |
| **EventStreamSource called from a long-running cron leaks file handles** | Pure append-write per call; no persistent state. Test by emitting 1000 events in a tight loop, watch handle count. |
| **Schema migration mid-Phase-1** (someone adds a new field to entity frontmatter while implementation is in flight) | Lock the schema at the start of P1 (PR with §1.2 fields landed before any code). Any change during P2-P5 freezes implementation, requires re-coordination. |
| **`semantic_shift.py` thresholds wrong for skill-emitted events** (events from `EventStreamSource` may be sparser/denser than from `WorkspaceSource`) | Wave 1 measures real-routine behavior. If consolidation triggers too rarely, lower threshold or add a wall-clock fallback (consolidate every N minutes regardless of drift). |
| **GraphifyWikiSource format brittle to graphify version drift** | Pin graphify version in CE's `requirements.txt`/`pyproject.toml` for the Wave 0 corpus; document tested versions in `SKILL.md`. v0.4.x format only; v0.5+ revisit. |
| **`scope` filter false-negatives on entities with `scope: default`** | M7 acceptance criterion explicitly tests `wiki.ask` with no `--scope` arg returns default-scoped entities only (not all entities). One unit test per scope behavior. |

### Open questions

- [ ] **Q1.** Auditor rule list extension — do we ship "missing centroid_embedding" and "broken `[[wiki-link]]`" rules in v1 or defer? Owner: Victor — decide before P4 starts.
- [ ] **Q2.** `EventStreamSource` is documented in `phase-1.md` §1.4 today as a 3rd subclass — but the Source ABC was conceived as pull-shaped (`list_artifacts → fetch → metadata → emit_events`). Push-shaped (skill calls `emit_events` directly) is technically a different control flow even if the output shape matches. Confirm the ABC accommodates both (likely: yes, since `emit_events` is the only method that matters for the events log).
- [ ] **Q3.** Should the Auditor be a CLI script, an MCP tool, or both? `wiki.audit` MCP verb is in §2.4; `audit.py` CLI entry is implied. Lean: both, with CLI being the primary cron-driven runner and MCP being a thin reader of the latest `audit/proposals.md`.

**Resolved during PR #19 review (Codex bot):**
- ~~Q4 freshness storage model~~ — **DECIDED** (M6): `last_verified_at` is the only stored field; `freshness_score` is computed on read from `last_verified_at` + per-source-type half-life policy in `scripts/wiki/freshness_policy.py`. Avoids write-back to entity files just to age them.
- ~~Q5 GraphifyWikiSource gating~~ — **DECIDED**: demoted from Must to Should (S1), slips to Wave 1. Closed-loop demo only needs M1 + M2–M7.

---

## Appendix — source evidence

### Plan
- `~/.claude/plans/stateless-waddling-whisper.md` — full priority table; this PRD covers items P0.1, P0.2 (schema decision already shipped in PR #16), P0.5 (impl), P2.1, P2.2.

### Specs (already merged on main)
- `Repos/agent-skills/context-engineering/plan/phases/phase-1.md` (§1.2 schema, §1.2.1 migration, §1.4 Source ABC, §1.5 wiki_init.py spec, §1.6 Auditor spec)
- `Repos/agent-skills/context-engineering/plan/phases/phase-2.md` (§2.4 MCP wiki.* tool catalog)
- `Repos/agent-skills/context-engineering/SPEC-mcp.md` (§3.0.1 next_tool_suggestions; §10 wiki.closure reservation)
- `Repos/agent-skills/context-engineering/ACADEMIC.md` (Wu et al. arXiv:2604.12285 event-graph + topic-network + semantic-shift consolidation pattern)

### Strategic context
- `Repos/anabasis/plan/multi-repo-spine.md` — CE = Anabasis "find-links" reference skill #1
- `~/.claude/handoffs/ce_external_signals_consolidated.md` — CocoIndex / Graphify / CRG synthesis that motivated the cannonball items

### Memory citations
- `project_anabasis` — runtime is "Temporal for company-knowledge agents"
- `project_context_engineering_scope` — CE is the engine; connectors stay in syroco-product-ops; wiki schema corpus-agnostic
- `reference_cloud_routine_constraints` — single-agent cloud routines; per-entity flush pattern
- `reference_gam_paper` — arXiv:2604.12285 event-graph + topic-network + semantic-shift consolidation
- `feedback_value_over_proof` — defer benchmarks; prioritize compounding/distribution work
- `feedback_submarine_mode` — internal CE tooling stays local; this PRD is not pushed to Notion

### Prior PRs that built up the substrate
- PR #10 (events.py + semantic_shift.py shipped)
- PR #14 (SPEC v1.0-rc1 + phase-1/2 docs landed)
- PR #15 (TS resolver, prerequisite for clean code-corpus indexing)
- PR #16 (3-signal reconciliation: §1.2.0 hybrid, §1.2.1 migration policy, slug rule, scope field, decision-continuity field references, MCP `next_tool_suggestions`, wiki.closure §10)
- PR #17 (Tier B: index_version, validity guard, unresolved_alias, _VENDORED_FROM, hyperedge TODO)
- PR #18 (TF-IDF hub damping — read-side improvement consumed by `wiki.ask` once it ships)
