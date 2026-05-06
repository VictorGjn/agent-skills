---
name: granola-scribe
version: 0.1.0
description: |
  Reads Syroco's Granola meeting transcripts and emits events to the
  context-engineering brain via wiki.add MCP. Ingests customer calls,
  prospect calls, partner syncs, and internal meetings; assigns
  entity_hint based on Syroco's known company list; segregates by scope
  (customers / prospects / partners / internal-product / internal-eng /
  competitors).

  First worked example of the scribe pattern in `agent-skills/scribes/SPEC.md`.
  Tailored to Syroco's actual MCP setup, meeting patterns, and brain
  corpora.

triggers:
  - "ingest granola meetings"
  - "pull granola into brain"
  - "granola scribe"
  - "MCP namespace: granola"
  - "what was discussed with $company"
  - "brief me on granola transcripts for $entity"
  - "syroco meeting brain ingestion"

scribe:
  connector: granola
  mcp_required: ["granola"]
  mcp_optional: []
  events_pushed_to: "wiki.add"
  quality_tiers: ["T0", "T1"]
  default_tier: "T1"
  cadence_supported: ["on-demand", "cron"]
  ce_event_schema: "1.0"
  half_life_key: "granola"
  scopes_required: ["granola:read"]

scribe_compatibility:
  ce_event_schema: "1.0"
  ce_min_version: "0.5.0"  # post PR #28 (M3 atomic batch + F1 wiki.ask validation)

scribe_syroco:
  brain_corpora_supported:
    - "company-brain/corpora/syroco-product-brain"   # the main brain
    - "company-brain/corpora/yc-sus26-brain"          # YC corpus (subset)
  default_brain_env_var: "CE_BRAIN_DIR"
  internal_email_domain: "syro.co"
  external_email_drop_list: []  # explicit ignore list for spammy domains

owners:
  primary: "victor.grosjean@syro.co"
  fallback: "@gilles"
---

# granola-scribe (Syroco-flavored v0.1)

> The first scribe in the family. Reads Granola meetings, emits events keyed on
> Syroco entities, lands them in the company brain on the right scope.

## Why this exists for Syroco

You ship 30+ Granola-recorded meetings per month — customer calls, prospect
calls, partner syncs, internal product/eng standups. Today every entity
(Anthony Veder, Theyr, Odfjell, Follow Route initiative, Fleet Alerts) lives
only in the Granola transcript that captured it. Cross-meeting recall is
hand-stitching: Victor opens 5 transcripts before each customer call.

The scribe ends that. After install, every meeting becomes events on the
brain, every external company gets a wiki page accumulating claims across
calls, and `wiki.ask "anthony veder"` returns the consolidated history.

## What it does (T1 default)

Per meeting:

1. Compute `entity_hint`:
   - **External email domain → Syroco-canonical slug** via the alias table
     (see §"Entity heuristics for Syroco" below).
   - **Internal-only meeting** → slugified title with Syroco-specific
     normalization (Product Planning / Sync / Bi-Monthly all map to
     `product-planning`; Stand-Up * → `engineering-standup`; etc.).
2. Compute `scope` (Syroco-locked vocabulary):
   - **`new-biz`** — Sales-led prospect calls, new-business pursuit
     (Anthony Veder, Christiania, Jupi as of 2026-05-03)
   - **`customer-expansion`** — CSM-led calls into existing customers,
     expansion / renewal / health (Odfjell, KCC). Heuristic: note-creator
     is a CSM (Gilles, Apolline, Amélie) AND external entity is a known
     customer
   - **`partners`** — partner syncs (Theyr, Wordware, Anthropic). See
     §"Theyr-as-partner-and-Jibe-source" below for the dual-emit rule
   - **`competitors`** — events tagged from competitor mentions
   - **`internal-product`** — product planning / iteration / CSM-product syncs
   - **`internal-eng`** — standups, eng decisions
   - **`internal-leadership`** — all-hands, exec, strategy
   - **`mentoring`** — intern mentoring, "Suivi stage *" (per Q3 lock-in)
   - **`default`** — fallback only when nothing else matches

   **Single-external rule:** Syroco meetings are typically single-external
   in practice (per audit of last 30 days). When >1 external company is
   present, emit events tagged for EACH external entity independently —
   no primary/secondary distinction (Q4 dropped as not-useful per
   2026-05-03 lock-in).
3. Split transcript into ~30-word turns by `Me:` / `Them:` markers.
4. For each turn whose first sentence ends in a commitment verb (`will`,
   `won't`, `we use`, `we don't`, `we need`, `we're trialing`, etc.) OR
   contains a competitor name (`napa`, `theyr`, `kongsberg`, etc.): emit
   one event with that turn as the claim.
5. Plus one **summary event** per meeting at title-tier (always emitted).

Push as a single batched `wiki.add` call (atomic per meeting per `SPEC.md`
§Idempotency).

## Entity heuristics for Syroco

### External canonical slugs (from your last 30 days of meetings)

```toml
# granola-scribe/aliases.toml — consumed by the entity-resolver skill OR
# inlined here as the scribe's lookup table for v0.1.

["anthonyveder.com"]
slug = "anthony-veder"
default_scope = "new-biz"             # sales-led prospect

["christianiashipping.com"]
slug = "christiania-shipping"
default_scope = "new-biz"

["jupi.co"]
slug = "jupi"
default_scope = "new-biz"

["odfjell.com"]
slug = "odfjell"
default_scope = "customer-expansion"  # existing customer; CSM-led

# KCC isn't in the last-30-day meetings but is a known pilot
# ["klaveness.com" or "kcc.com"]
# slug = "kcc"
# default_scope = "customer-expansion"

["theyr.com"]
slug = "theyr"
default_scope = "partners"
also_emit_to = "jibe"                 # see Theyr-as-partner-and-Jibe-source rule

["wordware.ai"]
slug = "wordware"
default_scope = "partners"

["anthropic.com"]
slug = "anthropic"
default_scope = "partners"

# Add as new external companies appear:
# ["<domain>"]
# slug = "<canonical>"
# default_scope = "<scope>"
# also_emit_to = "<additional-entity>"  # optional cross-emit
```

**Note**: `default_scope` is the FALLBACK. Actual scope at emit-time can
override based on note-creator (CSM-led vs sales-led — see §1 rule).

The entity-resolver skill (per `SKILL_INTEGRATION.md` §3) eventually owns
this; v0.1 ships with it inlined.

### Internal title normalizations

| Title pattern | entity_hint | scope |
|---|---|---|
| `Product Planning *` / `Product Sync` / `Product Bi-Monthly` / `Product weekly *` | `product-planning` | internal-product |
| `Stand-up Tech` / `Stand-Up - Squad *` | `engineering-standup` | internal-eng |
| `Weekly All-Hands` | `weekly-all-hands` | internal-leadership |
| `Follow route *` / `Force Follow *` | `follow-route` | internal-product |
| `CP terms*` / `Kick-Off CP Terms` | `cp-terms` | internal-product |
| `Weather Iteration *` | `weather-iteration` | internal-product |
| `Sales pitch *` | `sales-pitch` | internal-leadership |
| `1:1 * x Victor` / `Yves / Victor` / `Victor / *` | (filtered — see §1:1 filter rule below) | conditional |
| `CSM/Onwatch * Product` | `csm-onwatch-product` | internal-product |
| `Suivi stage *` | `mentoring-<intern-slug>` | **mentoring** |
| anything else internal | slugified title | default |

### 1:1 filter rule (per Q5 lock-in)

Internal 1:1s are NOT fully ingested. They contain too much personal /
relational chitchat that pollutes the brain. Filter rule:

1. Skip the meeting entirely UNLESS the transcript contains at least one of:
   - Mention of a known external entity (any slug from `aliases.toml`)
   - Mention of a known internal initiative (`follow-route`, `cp-terms`,
     `fleet-alerts`, `jibe`, `weather-iteration`, etc.)
   - Decision verbs: "we should", "let's ship", "we'll prioritize", "we'll
     drop", "we decide"
   - Opportunity verbs: "opportunity", "deal", "expansion", "renewal",
     "new client"
   - Issue verbs: "blocker", "stuck", "pain point", "broken"

   **Note (N1 resolution):** The 15 canonical signal themes from
   `~/.claude/skills/product-signals-pipeline/references/themes.md` are
   NOT applied here. Themes are consumer-side concerns (applied by
   `fleet-radar-skill` at delivery time). The scribe stays neutral. See
   `MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md` for the rationale.
2. If passes filter → emit ONLY the matching turns as events. Skip
   non-matching turns. Skip the summary event entirely.
3. `entity_hint` for emitted events:
   - If turn mentions an external entity → that entity's slug
   - If turn mentions an initiative → that initiative's slug
   - Otherwise → `1on1-<other-person-slug>` in `internal-leadership` scope
4. **Default behavior: drop the meeting silently** unless the filter passes.

### Theyr-as-partner-and-Jibe-source rule (per Q2 lock-in)

Theyr is a partner today, but the `jibe` project's purpose is to replace
Theyr's role in the stack. Every Theyr pain point is a Jibe requirement.
Special handling for Theyr-meeting events:

1. Always emit primary event with `entity_hint = "theyr"`,
   `scope = "partners"` (per default_scope in aliases).
2. **Additionally** scan claim text for pain-point markers:
   - "we can't" / "doesn't support" / "limited to" / "only works with"
   - "missing" / "lacks" / "no way to" / "would need"
   - "frustrating" / "issue" / "problem" / "broken"
   - "manual workaround" / "manually"
3. If pain-point markers detected → emit a SECOND event with
   `entity_hint = "jibe"`, `scope = "internal-product"`, claim prefixed
   with `"[Theyr pain → Jibe req]"`.

Result: the Jibe entity page accumulates a list of replacement requirements
across every Theyr sync. Operator queries `wiki.ask jibe` to see the full
parts list of what Jibe needs to deliver.

### Competitor-mention dual-emit (already in v0.1)

When a transcript mentions Napa / Kongsberg / DNV ECO Insight / Wartsila
Fleet / OneOcean, emit an additional event in `competitors` scope. Same
shape as the Theyr→Jibe rule, just for external competitors.

### Competitor recognition

Tier-1 competitors recognized in transcript text (case-insensitive):
- `Napa` — weather routing / digital twin / performance — emit competitor event
- `Kongsberg` — vessel performance — emit competitor event
- `DNV ECO Insight` — performance + class — emit competitor event
- `Wartsila Fleet` — performance + maintenance — emit competitor event
- `OneOcean` (formerly ChartCo) — voyage planning — emit competitor event

When detected, emit an additional event with `entity_hint = "<competitor-slug>"`
and `scope = "competitors"`. The same transcript can produce events for both
the customer entity AND every competitor it mentions.

## Event schema example

For meeting "Syroco / Anthony Veder" (2026-04-24):

```json
[
  {
    "schema_version": "1.0",
    "ts": 1714128000,
    "source_type": "granola",
    "source_ref": "granola://meeting/f5a6d76a-5a80-4c35-8e48-8733a152dbb4",
    "file_id": "granola-f5a6d76a",
    "claim": "Anthony Veder ran a Syroco / Anthony Veder call (Victor + Andrew + Chloe + Lsopar). 25-vessel North Sea/Baltic gas tanker fleet; currently uses Napa, dissatisfied; interested in digital twin for hull performance and contractual margin understanding; niche needs include controllable pitch propellers and ice conditions.",
    "entity_hint": "anthony-veder",
    "scope": "prospects"
  },
  {
    "schema_version": "1.0",
    "ts": 1714128300,
    "source_type": "granola",
    "source_ref": "granola://meeting/f5a6d76a-5a80-4c35-8e48-8733a152dbb4#turn-12",
    "file_id": "granola-f5a6d76a-t12",
    "claim": "Anthony Veder uses Napa for digital logbook + MRV validation; trialed Napa's performance tool, found it not satisfactory; in discussion with Napa about a new tool.",
    "entity_hint": "anthony-veder",
    "scope": "prospects"
  },
  {
    "schema_version": "1.0",
    "ts": 1714128300,
    "source_type": "granola",
    "source_ref": "granola://meeting/f5a6d76a-5a80-4c35-8e48-8733a152dbb4#turn-12",
    "file_id": "granola-f5a6d76a-t12-napa",
    "claim": "Napa (competitor): Anthony Veder reports Napa's performance tool was not satisfactory; Napa is working on a new model.",
    "entity_hint": "napa",
    "scope": "competitors"
  },
  {
    "schema_version": "1.0",
    "ts": 1714128600,
    "source_type": "granola",
    "source_ref": "granola://meeting/f5a6d76a-5a80-4c35-8e48-8733a152dbb4#turn-25",
    "file_id": "granola-f5a6d76a-t25",
    "claim": "Anthony Veder operates 25 vessels in North Sea/Baltic; gas tanker fleet uses shaft generator with fixed RPM (legacy variable frequency drive constraint); ~9 vessels have shaft power sensors but no fuel sensors.",
    "entity_hint": "anthony-veder",
    "scope": "prospects"
  },
  {
    "schema_version": "1.0",
    "ts": 1714128900,
    "source_type": "granola",
    "source_ref": "granola://meeting/f5a6d76a-5a80-4c35-8e48-8733a152dbb4#turn-43",
    "file_id": "granola-f5a6d76a-t43",
    "claim": "Anthony Veder asks Syroco for spectral resolution detail on weather data, controllable pitch propeller modeling capability, and ice condition data exclusion. Andrew (Syroco) commits to share slides and follow up on CPP modeling with the Naval team.",
    "entity_hint": "anthony-veder",
    "scope": "prospects"
  }
]
```

5 events from one meeting at T1. T0 would emit just the first one
(summary only).

## Configuration

### Environment variables

```bash
# Required
export CE_BRAIN_DIR=~/Repos/company-brain/corpora/syroco-product-brain

# Optional
export GRANOLA_SCRIBE_TIER=T1       # T0 | T1; default T1
export GRANOLA_SCRIBE_SINCE="2026-04-01"  # ISO date for backfill; default = state file
export GRANOLA_SCRIBE_DRY_RUN=0     # 1 = print events, don't push
export GRANOLA_SCRIBE_MAX_COST=0    # USD cap for T2 (T1 is free); 0 = no T2
```

### State file

`~/.claude/scribes/granola-scribe/state.json`:

```json
{
  "last_processed_meeting_id": "db41ae52-c20f-4cd1-9834-423a02ee8055",
  "last_processed_at": "2026-04-30T15:30:00Z",
  "version": "0.1.0"
}
```

### Aliases file

`~/.claude/scribes/granola-scribe/aliases.toml` — see §"Entity heuristics" above.

## Run

```bash
# First-time backfill (3 months, T1)
npx skills run granola-scribe -- --since 2026-02-01 --tier T1

# Incremental (cron / on-demand)
npx skills run granola-scribe

# Dry run (see what events would land, don't push)
npx skills run granola-scribe -- --dry-run

# Verbose
npx skills run granola-scribe -- --verbose
```

## Use cases this unlocks for Syroco

| Persona | Question that becomes one-shot answerable |
|---|---|
| **Victor (Founder/CPO)** | "Brief me on Anthony Veder before tomorrow's follow-up call" — pulls 4+ meetings of context, surfaces the 3 things they care about, last commitment from each side |
| **Andrew (Sales)** | "Which prospects mention Napa as their current vendor?" — lists Anthony Veder + any others |
| **Gilles (CSM)** | "What did we last discuss with Odfjell?" — Michelle Loeffler thread + status updates |
| **Apolline (CSM)** | "Action items from Michelle's last call" — surfaces commitments still open |
| **Chloe (PM)** | "What's our current position on controllable pitch propeller modeling?" — cross-meeting tech-decision recall |
| **Sinbad (Product)** | "What questions did prospects raise about weather resolution?" — pattern across calls |
| **Florent (Strategy)** | "Competitive landscape this month" — every competitor mention from every transcript |

## Integration with Syroco's existing pipelines

- **`product-signals-pipeline`** (Mon–Fri 05 UTC): being **decomposed**
  into scribes (this file is one of them) + future consumer skills
  (`fleet-radar-skill`, `opportunity-builder-skill`). Migration plan in
  `MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md`. Run BOTH for 2 weeks
  parallel before sunset.
- **YC SUS26 brain corpus** (`company-brain/corpora/yc-sus26-brain/`):
  scope-filtered subset can feed the YC corpus via `--brain-corpus
  yc-sus26-brain`.
- **Slack `/brain` bot** (planned, not yet built): bot calls `wiki.ask` —
  granola-scribe is what makes Anthony Veder return non-empty.
- **Onwatch signal scan** — DEPRECATED per memory; product-signals-pipeline
  superseded it. granola-scribe complements (different source, same brain).

## Quality tiers — Syroco-specific recommendation

| Tier | When to use | Cost (Syroco volume) |
|---|---|---|
| **T0** | First install, demos, internal meetings (low signal/noise) | $0 |
| **T1** (default) | All external meetings, all initiative meetings | $0 (rule-based) |
| **T2** (manual) | High-stakes calls — Anthony Veder closing call, KCC renewal call, board prep | ~$0.05/meeting × ~10/month = $0.50/month |
| **T3** (deferred) | Cross-meeting consolidation (decision-continuity for "what did we tell Anthony Veder when"?) | ~$0.10/entity × ~30 entities = $3 / quarterly run |

Total monthly cost at default config: **$0** (T1 is rule-based). T2 spikes
are bounded by `GRANOLA_SCRIBE_MAX_COST`.

## Anti-patterns to avoid

- **Don't ingest external meetings into `default` scope.** PII leak risk.
  Default scope is broad-read; customer/prospect data MUST be scoped.
- **Don't emit events with `entity_hint = null`.** They get silently dropped
  by `wiki_init.consolidate()`. Always assign — fall back to slugified
  title for internal-only.
- **Don't run T2 without `--max-cost`.** A 3-month backfill at T2 over 100
  meetings is ~$5; over 500 backfilled meetings is ~$25. Confirmable, not
  catastrophic — but always confirmable.
- **Don't skip the alias table.** "anthonyveder.com" vs "anthony-veder" vs
  "AV" all need to map to one slug or you'll have 3 wiki pages for one
  prospect.

## Open questions specific to Syroco — RESOLVED 2026-05-03

| # | Question | Resolution |
|---|---|---|
| 1 | CSM-led vs sales-led split | **Locked**: CSM-led on existing customer → `customer-expansion`. Sales-led on prospect → `new-biz`. Note-creator role is the discriminator |
| 2 | Theyr scope | **Locked**: `partners` primary, but every pain-point claim also emits a secondary event to `jibe` (since the Jibe project will replace Theyr). Pain-point detection is rule-based — see Theyr-as-partner-and-Jibe-source rule above |
| 3 | Mentoring scope | **Locked**: New scope `mentoring`. "Suivi stage *" meetings emit there |
| 4 | Multi-external joint calls | **Dropped**: not a real Syroco use case. Each external entity gets independent events; no primary/secondary distinction |
| 5 | Personal 1:1 ingestion | **Locked**: filter aggressively. Ingest ONLY turns containing external entities, known initiatives, or signal/opportunity verbs. Drop the meeting entirely if no filter match. See "1:1 filter rule" above |

## Newly opened questions (post-2026-05-03)

| # | Question | Owner |
|---|---|---|
| ~~N1~~ | ~~The 15 canonical signal themes from `product-signals-pipeline` need to be extracted into a shared list this scribe can reference~~ | **RESOLVED 2026-05-03**: themes are consumer-side. They stay at `~/.claude/skills/product-signals-pipeline/references/themes.md` and are imported by `fleet-radar-skill` when that ships. Scribes don't categorize. See `MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md`. |
| N2 | When a Theyr meeting has BOTH a pain point (→ jibe event) AND a feature delivery confirmation (→ partners-only event), the dual-emit rule emits the jibe event for the wrong claim. Need claim-level pain detection, not meeting-level | Implementation |
| N3 | KCC isn't in the last-30-day Granola archive (no recent meetings) — does the scribe need a manual seed for entities not in the meeting history? | Architecture |

## What's NOT in v0.1

- T3 cross-meeting consolidation (Phase 2 of scribe roadmap).
- Webhook mode (cron only for v0.1).
- Multi-tenant brains (single CE_BRAIN_DIR).
- Auto-discovery via runtime — when the Granola MCP connects, the runtime
  layer (Pipedream / Syroco Connect) is responsible for suggesting this
  scribe via `find-skills`. Not the scribe's job.
- Enricher / acter siblings — those are separate skills (granola-enricher,
  granola-acter) shipped later per `SKILL_INTEGRATION.md` §2.

## Relationship to the spec

This SKILL.md is the worked example referenced in:

- `agent-skills/scribes/SPEC.md` §"Worked example: granola-scribe (T0)" —
  this is the T1 elaboration, with Syroco-specific entity heuristics.
- `agent-skills/scribes/PLAN.md` §7 Tier 1 — granola-scribe is item #1.
- `agent-skills/scribes/SKILL_INTEGRATION.md` §4 — the SKILL.md frontmatter
  example is taken from this file.

## Owner contact

Author: Victor Grosjean (`victor.grosjean@syro.co`).
First-pass reviewer: Gilles Tabart.
Issues: file in `agent-skills` repo with label `scribe:granola`.
