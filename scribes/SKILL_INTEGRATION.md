# Scribes ↔ Skills Integration v0.1

> **Companion to:** `SPEC.md` (the contract) and `PLAN.md` (the prioritization).
> This doc connects scribes to the broader skills ecosystem and answers: *how does a new MCP connection automatically get the right scribe, and how does cross-scribe entity merge actually happen?*
>
> **Drafted:** 2026-05-03. Status: design sketch, no code.

---

## 1. The thesis

A scribe is **just a skill** with a particular shape. The skills ecosystem already has the primitives we need:

- `find-skills` discovers skills by capability.
- `skill-author` creates new skills.
- `skill-check` validates them.
- `coordinator-pattern` orchestrates multiple skills.
- `mcp__knowledge-graph-memory` provides a graph substrate for entity resolution.

**Cross-scribe entity merge is not a feature of CE. It is its own skill** that subscribes to the events log, detects same-entity hints across sources, and proposes (or applies) merges. Same shape as scribes — separate, replaceable, versioned.

```
   ┌──────────────────────────────────────────────────────────────┐
   │                    SKILLS ECOSYSTEM                          │
   │                                                              │
   │  find-skills  →  npx skills add <name>  →  installed skill   │
   │       ↑                                          ↓           │
   │       │                                          │           │
   │   discovery                                    runtime        │
   │       │                                          │           │
   └───────│──────────────────────────────────────────│───────────┘
           │                                          │
           │      [new MCP connected via Pipedream]   │
           │                  ↓                        │
   ┌───────┴──────┐    ┌─────────────┐    ┌──────────┴──────┐
   │ <X>-scribe   │    │ <X>-enricher│    │ <X>-acter       │
   │ (read MCP X, │    │ (query MCP X│    │ (write to MCP X │
   │  push events)│    │  for context│    │  on brain query)│
   └───────┬──────┘    └─────────────┘    └─────────────────┘
           ↓
   ┌──────────────┐    ┌──────────────────────────────┐
   │  CE.wiki.add │ ←  │  entity-resolver-skill       │
   │              │    │  (cross-scribe merge,        │
   │              │    │   uses kg-memory MCP)        │
   └──────┬───────┘    └──────────────────────────────┘
          ↓
   ┌──────────────┐
   │   wiki/      │
   └──────────────┘
```

---

## 2. The 3-skill family per connector — and the broader 3-LAYER model

For every MCP, three skill roles are useful. Most connectors ship just one (the scribe); high-value connectors graduate to all three.

| Role | Verb | Reads from | Writes to | Example: Granola |
|---|---|---|---|---|
| **scribe** | observe | upstream MCP | brain (`wiki.add`) | Pulls meetings → emits events |
| **enricher** | query-back | brain + upstream MCP | brain (`wiki.add`) | "Find every meeting mentioning $entity, extract more claims" |
| **acter** | write-out | brain (`wiki.ask`) + user request | upstream MCP | "Create a Granola note summarizing what the brain knows about $prospect before tomorrow's call" |

**Scribe is necessary; enricher is high-value; acter is the agentic loop closer.**

### The stable-vs-mutable cut (added 2026-05-03 after Victor's pushback)

There's a deeper architectural distinction that determines WHICH of the three roles a piece of logic belongs in:

- **Stable** = the fact doesn't change once captured. The voyage that was active when a captain sent a Slack message at 14:32 UTC is a frozen historical fact. The deal stage at that timestamp is a point-in-time snapshot. The author's role at message-time is recorded. Stable facts are extracted ONCE and cached on the event.
- **Mutable** = the interpretation can change. The 15-theme taxonomy might gain a 16th theme. The priority client list (KCC/Odfjell/Hafnia/LDC) might change. The LLM classifier can be retrained. Mutable interpretation is computed at QUERY time, never cached on the event.

This produces a 3-layer pipeline (cleaner than the 3-role family above when you're reasoning about WHAT-goes-WHERE):

```
LAYER 1 — Scribe (raw, source-specific)
  Extract what the source SAID. Don't interpret.

LAYER 2 — Enricher (stable cross-source linkage OR
          pure function with timestamped assumptions)
  Add facts that are TRUE at message-time and frozen forever:
  speaker_role, voyage_id, deal_state_at_time, vessel, GPS, etc.
  Cross-source — joins data from one scribe with point-in-time
  data from another (or upstream APIs).
  ALSO: deterministic transforms whose assumption set is recorded
  on the output (e.g. unit conversions: "643 EUR/t VLSFO_eq → EUR/t fuel"
  with assumptions {VLSFO energy density, GHG intensity, FuelEU target}
  frozen at compute-time). Re-deriving with new assumptions = a new
  event, not a retroactive rewrite. Witness: board-deck-from-brain-dump
  unit-normalizer (2026-05-06).

LAYER 3 — Consumer (mutable interpretation)
  Apply taxonomies, scoring rules, format. Interpretation owners
  whose definitions can change. Theme classification, priority
  scoring, opportunity grouping, brief format.
```

### Why this matters: the "captain signal → voyage" question

A captain posts in `#project-odfjell` at 14:32 UTC: "ETA was off by 4 hours on the last voyage." The brain should know which voyage they meant.

Three wrong places to do the linkage, and one right one:

- ❌ **Inside slack-scribe**: forces slack-scribe to know about voyages, HubSpot, the captain roster. Coupling explosion.
- ❌ **Inside fleet-radar-skill (consumer)**: every Monday digest run re-queries the voyage system for every captain message ever ingested. Wasteful — the voyage that was active is FROZEN, doesn't need re-lookup.
- ❌ **Inside the brain itself (CE)**: would require CE to learn upstream APIs. Engine bloat.
- ✅ **Inside `voyage-context-enricher` (a separate enricher skill)**: subscribes to events from slack-scribe, granola-scribe, gmail-scribe; for any event whose source has a captain or vessel context, queries the voyage system at message-time and adds `voyage_id`, `vessel`, `deal_state_at_time` to the event. Stable facts. Captured once, forever.

Then fleet-radar-skill (consumer) reads enriched events with the linkage already done. It applies mutable interpretation (theme classification, priority weighting) but doesn't touch the stable cross-source facts.

### Concrete enricher candidates for Syroco

| Enricher | Reads from | Looks up | Adds to events |
|---|---|---|---|
| **captain-signal-enricher** | slack-scribe events from `#project-*` channels | Captain roster (config) | `speaker_role: "captain" \| "csm" \| "ops" \| "shore"` |
| **voyage-context-enricher** | slack-scribe + granola-scribe events from customer entities | Voyage system / efficientship-backend at message-time | `voyage_id`, `vessel`, `route`, `voyage_phase: "in-progress" \| "completed" \| "planning"` |
| **deal-state-enricher** | any event for an entity that has a HubSpot company | HubSpot at message-time | `deal_id`, `deal_stage`, `deal_amount`, `fleet_extension_milestone` |
| **competitor-mention-enricher** | any event with text content | Tier-1 competitor list (Napa, Kongsberg, etc.) | `competitors_mentioned[]` |
| **fleet-radar-eligibility-enricher** | any customer-expansion / new-biz event | priority client list (`client-scoring.md`) | `priority: "high" \| "normal"`, `is_priority_client: bool` |

Each enricher is independently versioned, owned, and replaceable. They all consume events and emit enriched events (or update existing events via the event log's append-and-supersede pattern).

For Tier-1 ship: 3 scribes (granola, slack, hubspot) + 1 enricher (`voyage-context-enricher` — answers Victor's captain→voyage question). Acters wait.

---

## 3. Cross-scribe entity-resolver skill

### Problem

Scribes produce `entity_hint` per source. Different sources name the same entity differently:

| Source | Hint produced |
|---|---|
| granola-scribe | `anthony-veder` |
| hubspot-scribe | `anthonyveder.com` |
| slack-scribe | `av` (channel slang) |
| linear-scribe | `customer-12-anthony-veder` |

Today's CE consolidates by exact-string `entity_hint`, so these become 4 separate wiki pages. The brain looks fragmented to the operator.

### Design: `entity-resolver-skill`

A separate skill that:

1. **Subscribes** to the events log (tail mode) and to `wiki/_index.md`.
2. **Builds** an entity graph using `mcp__knowledge-graph-memory` — each canonical entity is a node, each scribe-emitted hint is an alias relation.
3. **Detects** new aliases via:
   - Substring/levenshtein similarity (`anthony-veder` ≈ `anthonyveder.com`).
   - Email domain canonicalization (`anthony.veder.com` → company slug).
   - LLM-assisted (T2): "are these the same entity?" prompt for borderline cases.
4. **Proposes** merges by writing to `audit/proposals.md` under a "Cross-source entity merges" section.
5. **Applies** approved merges by either:
   - Writing rule entries to `~/.claude/scribes/aliases.toml` (every scribe consults this BEFORE emitting a hint).
   - OR rewriting events log via tombstone+replay (heavier; only when corruption is detected).

### Why a separate skill, not in CE

- **CE stays corpus-agnostic.** Same reason scribes are separate.
- **The resolver is the most LLM-heavy piece.** Putting LLM cost inside the engine couples engine deployment to LLM availability.
- **Resolver versioning is independent.** Improving entity-resolution shouldn't require a CE deploy.
- **Multiple resolvers can coexist.** v0.1 = string-similarity rule-based. v0.2 = `mcp__knowledge-graph-memory`-backed graph queries. v0.3 = LLM-assisted. Operator picks one or chains them.

### kg-memory as substrate

`mcp__knowledge-graph-memory` provides:
- `create_entities` / `create_relations` — graph build.
- `search_nodes` / `open_nodes` — alias lookup.
- `add_observations` — non-canonical claims attached to canonical entities.

This is exactly the resolver's data shape. **Don't reinvent it; use it.**

The flow:
1. Scribe emits event with `entity_hint = "anthonyveder.com"`.
2. Resolver queries kg-memory: `search_nodes "anthonyveder"`.
3. If match exists with canonical name `anthony-veder`, resolver maps the hint and the event lands on `wiki/anthony-veder.md`.
4. If no match, resolver creates a new canonical entity `anthonyveder.com` AND queues the event for later cross-link review.

### What kg-memory is NOT

It's not a replacement for CE's wiki. The wiki is the OPERATOR-FACING surface (markdown, scope filtering, freshness, supersession). kg-memory is the RESOLVER'S INTERNAL graph. They serve different reads.

---

## 4. Discovery flow: MCP connect → scribe install

### Today's gap

When a user connects a new MCP via Pipedream / Syroco Connect, nothing happens. The user has to know that `<connector>-scribe` exists and run `npx skills add <connector>-scribe` manually.

### Proposed flow using `find-skills`

```
[Pipedream / Syroco Connect detects new MCP connection: X]
       ↓
[Runtime calls: find-skills "scribe for MCP X" or
                find-skills "<X>-scribe"]
       ↓
[find-skills returns matching scribe skills, ranked]
       ↓
[Runtime presents to user: "Install <X>-scribe to feed
 your brain from <X>?"]
       ↓
[User confirms → npx skills add <X>-scribe]
       ↓
[Scribe configures itself from MCP auth + CE_BRAIN_DIR env]
       ↓
[First run on next cron tick]
```

**The runtime layer is responsible for the trigger.** This spec just defines:
1. The naming convention (`<connector>-scribe` matching MCP namespace).
2. The required `find-skills`-discoverable metadata in the scribe's `SKILL.md`.

### Required SKILL.md frontmatter (in addition to standard skill fields)

```yaml
---
name: granola-scribe
description: |
  Reads Granola meeting transcripts and emits events to the
  context-engineering brain via wiki.add MCP.

  Matches MCP namespace: granola
  Required scopes: granola:read

scribe:
  connector: granola              # MCP namespace match key
  mcp_required: ["granola"]       # MCPs this scribe needs connected
  mcp_optional: []                # MCPs that enrich behavior if present
  events_pushed_to: "wiki.add"
  quality_tiers: ["T0", "T1"]
  default_tier: "T0"
  cadence_supported: ["on-demand", "cron"]
  ce_event_schema: "1.0"          # CE event-schema version this scribe targets
  half_life_key: "granola"        # must be registered in CE.HALF_LIVES

triggers:
  - "ingest granola meetings"
  - "pull granola into brain"
  - "granola scribe"
  - "MCP namespace: granola"      # used by find-skills auto-match
---
```

The `triggers[]` array is what `find-skills` already uses to match user intent → skill. Adding "MCP namespace: <X>" trigger lines lets the runtime auto-discover when MCP X connects.

### Composition with `find-skills`

`find-skills` already exists. Don't replace it. Just write scribes' SKILL.md so it finds them:

```
> find-skills "I just connected the Granola MCP"

Match: granola-scribe (95% confidence)
  Reason: trigger "MCP namespace: granola" matched
  Description: Reads Granola meeting transcripts...
  Install: npx skills add granola-scribe
```

This is zero new infrastructure. The `find-skills` skill + good triggers in scribe SKILL.md = auto-discovery.

---

## 5. Composition with other skills in the ecosystem

Scribes DO NOT live alone. They compose with:

### `coordinator-pattern`
Orchestrates parallel scribe runs. Tier-1 backfill (granola + slack + hubspot all running for the first time) is a coordinator-pattern job:

```
coordinator
  ├── granola-scribe --backfill --since 2026-01-01
  ├── slack-scribe --backfill --since 2026-01-01
  └── hubspot-scribe --backfill --since 2026-01-01
        ↓
   wait for all → trigger entity-resolver
        ↓
   wait → trigger CE.audit
        ↓
   report ready
```

### `agent-patterns`
For deciding the right shape when designing a new scribe. Push vs pull, single-pass vs multi-pass, sync vs async — `agent-patterns` is the prep skill before `skill-author`.

### `skill-author` and `skill-check`
For creating and validating new scribe skills. Every scribe is a skill, so creation flow goes through `skill-author`. SKILL.md MUST pass `skill-check`.

### `using-superpowers`
The meta-skill that routes the right skill to the right task. Once 5+ scribes are installed, `using-superpowers` is what picks "for ingesting yesterday's customer call, use granola-scribe (T1)" without the operator naming the skill.

### `prompt-craft`
For T2 LLM-extraction prompts. Each scribe's claim-extraction prompt is a craft job; this is where it lives.

### `knowledge-synthesis`
For combining query results across scribes. Once scribes feed the brain, queries like "what's our position with Anthony Veder across granola/slack/hubspot/linear/notion?" go through `knowledge-synthesis` to dedupe and cite.

### `proactive-brief`
Produces the operator-facing daily brief. Reads from CE.wiki + recent events. Pairs naturally with calendar-scribe for "what should I know before today's calls?"

### `pack` and `pack-why`
The CE-side query surface. Operators use `pack` against the brain; scribes don't call this — they only call `wiki.add`.

---

## 6. The fully composed picture

```
┌──────────────────────────────────────────────────────────────┐
│                    User connects MCP X                       │
└────────────────────────────────┬─────────────────────────────┘
                                 ↓
                  ┌──────────────────────────────┐
                  │  Pipedream / runtime detects │
                  └──────────────┬───────────────┘
                                 ↓
                  ┌──────────────────────────────┐
                  │  find-skills "MCP X scribe"  │
                  └──────────────┬───────────────┘
                                 ↓
                  ┌──────────────────────────────┐
                  │  npx skills add <X>-scribe   │
                  └──────────────┬───────────────┘
                                 ↓
        ┌────────────────────────┼────────────────────────┐
        ↓                        ↓                        ↓
┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ <X>-scribe   │    │ entity-resolver  │    │ proactive-brief  │
│ (cron / on-  │    │ skill (continuous │    │ skill (daily)    │
│  demand)     │    │  graph build)    │    │                  │
└──────┬───────┘    └────────┬─────────┘    └────────┬─────────┘
       │ wiki.add            │ kg-memory             │ pack
       ↓                     ↓                       ↓
┌──────────────────────────────────────────────────────────────┐
│                  CE Brain (wiki/, events/)                   │
│  + audit (3 rules) + freshness + scope                       │
└──────┬───────────────────────────────────────────────────────┘
       ↑
       │ wiki.ask / pack / pack-why
       │
┌──────┴───────┐
│ User query   │
│ ("brief me   │
│  on Acme")   │
└──────────────┘
```

Every box is a separate skill or a separate piece of CE. Each can be replaced, upgraded, or removed without touching the others. **That's the wedge — composition, not monolith.**

---

## 7. What to actually ship (the skill-shaped Tier-1)

Refines `PLAN.md` §7 prioritization with the skill-ecosystem framing:

### Week 1 (post-YC)

1. **`granola-scribe` skill**
   - SKILL.md with discovery metadata (trigger array including "MCP namespace: granola")
   - T0 first run: one event per meeting, claim = title + first-280 chars
   - State-file dedup
   - Unit tests + skill-check passing
   - Local install via `npx skills add` works
2. **`slack-scribe` skill**
   - Same shape. T1 default (per-thread chunking).
3. **`hubspot-scribe` skill**
   - Same shape. T0 default (property-diff events).

### Week 2

4. **`entity-resolver` skill**
   - Subscribes to events log
   - Uses kg-memory for graph build
   - Writes proposals to `audit/proposals.md` under "Cross-source entity merges"
   - Apply mode: writes `aliases.toml` consumed by all scribes
5. **Update SPEC.md** with the alias config contract — every scribe MUST consult `aliases.toml` before emitting `entity_hint`.

### Week 3

6. **`linear-scribe`, `notion-scribe`, `gmail-scribe`** — same shape, copy-paste from earlier scribes.
7. **`granola-enricher` skill** — first enricher, validates the enricher pattern. "Given a brain entity, query Granola for related transcripts and extract more claims."

### Week 4

8. **First acter:** `granola-acter` — "Given a brain entity and an upcoming meeting, create a Granola pre-meeting note with brain claims."
9. **Long-tail scribes** — calendar, drive, mixpanel, atlassian, vercel, etc.

---

## 8. Open questions

| # | Question | Owner |
|---|---|---|
| 1 | Does `find-skills` already support "MCP namespace" triggers, or do we need to extend it? | runtime |
| 2 | Where does `aliases.toml` live? Per-user (`~/.claude/scribes/`)? Per-brain (`brain/scribes/aliases.toml`)? | CE + scribes |
| 3 | Does `mcp__knowledge-graph-memory` persist across sessions, or is it ephemeral? Affects entity-resolver design | runtime / kg-memory |
| 4 | Should the entity-resolver run as a continuous skill (subscribed to events) or batch (run after each scribe completes)? | architecture |
| 5 | Pipedream side: is there an existing hook for "MCP connected" events that we can subscribe to, or do we need to poll? | runtime |

---

## 9. Anti-patterns

- **Building scribes as Python scripts inside CE.** Already addressed in PLAN. Reinforced here: scribes are SKILLS, installed via npx, discoverable via find-skills.
- **Putting entity resolution inside CE.** Same anti-pattern. Resolver is its own skill, uses kg-memory.
- **Building a custom skill registry.** `npx skills` and `find-skills` exist. Use them.
- **Building a custom trigger system for MCP connection.** That's runtime work (Pipedream / `/skills` infra). Not in the SPEC.
- **Bundling enricher / acter into the scribe skill.** Three roles, three skills. Composability matters.
- **Skipping the SKILL.md trigger array.** Without it, find-skills can't auto-match. Operators don't know your scribe exists.

---

## 10. The summary slide

- **Scribe = skill with a specific shape.** Naming: `<connector>-scribe`. Discovery: `find-skills` via SKILL.md triggers. Install: `npx skills add`.
- **Cross-scribe merge = its own skill** (`entity-resolver`). Uses `mcp__knowledge-graph-memory` as substrate. Proposes via `audit/proposals.md`. Applies via `aliases.toml`.
- **Three skill roles per high-value connector**: scribe (read), enricher (query-back), acter (write-out). Most connectors only need scribe v1.
- **Composition with existing skills**: `coordinator-pattern` orchestrates, `using-superpowers` routes, `proactive-brief` consumes, `knowledge-synthesis` answers cross-source queries.
- **No new infrastructure.** Skills + npx + find-skills + kg-memory + CE = full architecture. We just need to write the right SKILL.md files.

---

## Related artifacts

- `agent-skills/scribes/SPEC.md` — scribe contract
- `agent-skills/scribes/PLAN.md` — prioritization + multi-POV audit
- `~/.claude/skills/find-skills/` — existing discovery skill
- `~/.claude/skills/skill-author/` — for creating new scribes
- `~/.claude/skills/skill-check/` — for validating SKILL.md
- `~/.claude/skills/coordinator-pattern/` — for orchestrating parallel scribe runs
- `~/.claude/skills/using-superpowers/` — meta-router
- `mcp__knowledge-graph-memory` — entity-resolver substrate
- `agent-skills/context-engineering/` — the brain
