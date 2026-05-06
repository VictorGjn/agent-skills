---
name: voyage-context-enricher
version: 0.1.0
description: |
  First enricher skill for Syroco's company brain. Subscribes to events
  from granola-scribe, slack-scribe, hubspot-scribe (and any future
  customer-touching scribe). For events whose entity is a customer with
  active voyages, looks up the voyage context at the event's timestamp:
  voyage_id, vessel, route, voyage_phase, deal_state_at_time,
  speaker_role. Emits enriched events into a parallel enrichment log;
  consumer skills read both and join.

  This is the keystone of the 3-layer model in `SKILL_INTEGRATION.md`
  §2: a captain signal in Slack is barely a signal without knowing
  which voyage they were referring to. This enricher closes that gap.

triggers:
  - "enrich voyage context"
  - "link captain signals to voyages"
  - "join slack with voyage data"
  - "voyage-context-enricher"
  - "syroco brain enrichment"

enricher:
  consumes_events_from:
    - "granola-scribe"
    - "slack-scribe"
    - "hubspot-scribe"
    - "gmail-scribe"           # when shipped
    - "notion-scribe"          # when shipped
  emits_to: "enrichments/<YYYY-MM-DD>.jsonl"  # parallel to events/, same brain
  upstream_apis:
    - "claude_ai_HubSpot or syroco__hubspot (custom objects: Vessels + Captains; standard: Deals + Companies)"
    # NOTE: efficientship-backend was the WRONG source per Victor 2026-05-03.
    # Vessel/captain context lives in HubSpot custom objects (associated to
    # companies). voyage-id-at-time (dynamic voyage state) is deferred to
    # v0.2 — when needed, comes from efficientship-backend voyage history,
    # not the static context this enricher cares about.
  scopes_required:
    - "crm.objects.companies.read"
    - "crm.objects.deals.read"
    - "crm.schemas.custom.read"           # to read custom object schemas
    - "crm.objects.custom.read"           # to read Vessel + Captain instances
  cadence_supported: ["cron"]      # webhook future
  default_cadence: "hourly"
  ce_event_schema: "1.0"

enricher_compatibility:
  ce_event_schema: "1.0"
  ce_min_version: "0.5.0"

enricher_syroco:
  brain_corpora_supported:
    - "company-brain/corpora/syroco-product-brain"
  hubspot_custom_objects:
    vessels: "vessel"             # HubSpot custom object schema name
    captains: "captain"           # HubSpot custom object schema name
  customer_scope_filter:
    - "customer-expansion"
    - "new-biz"
    - "partners"
  skip_scopes:
    - "internal-product"
    - "internal-eng"
    - "internal-leadership"
    - "mentoring"
    - "competitors"
    - "default"

owners:
  primary: "victor.grosjean@syro.co"
  fallback: "@arnaud.zago"  # eng lead, owns efficientship-backend

---

# voyage-context-enricher (Syroco-flavored v0.1)

> Reads scribe events, looks up the voyage / deal / speaker context
> they didn't have at extract time, emits enriched events. Stable
> facts only — no interpretation.

## Why this exists

Syroco's customer signals fragment across three timelines:
1. **Slack thread** ("captain reports ETA off by 4h") — has a timestamp + author + channel
2. **Voyage system** — has the actual voyage that was running when the captain spoke
3. **HubSpot** — has the deal stage / fleet-extension milestone at that moment

A captain signal without the voyage is half a signal. A deal-stage move
without a Granola call within ±48h is missing the "why." Today these
get hand-stitched in CSM heads.

This enricher does the joining ONCE per event, at ingestion time,
freezing the cross-source link as a stable fact. Every consumer
downstream (fleet-radar-skill, opportunity-builder-skill, pre-call
brief) gets the linkage for free.

## What it does (per cron tick)

1. Read events emitted since `last_processed_at` from the brain's
   `events/<date>.jsonl` files.
2. Filter to events in `enricher_syroco.customer_scope_filter` scopes
   (skip internal / competitor / default events — no vessel/deal
   context to add).
3. For each event:
   a. **Speaker → captain lookup** (HubSpot Captain custom object):
      if `source_type=slack` and the event has a Slack `author` user_id,
      query HubSpot custom-object `captain` filtered by
      `slack_user_id=<author>`. Hit → `speaker_role: "captain"`,
      `captain_record_id`, plus the Captain's associated Vessel and
      Company. Miss → fall through to CSM list (local config) →
      `speaker_role: "csm"` if matched, else `"unknown"`.
   b. **Vessel context** (HubSpot Vessel custom object): if the
      Captain lookup returned a Vessel association, OR if the event
      mentions a vessel name / IMO in text, fetch the Vessel record:
      `vessel_name`, `vessel_imo`, `vessel_type`, associated `company_id`.
      Cache vessel records by `vessel_record_id` for 24h (vessel
      attributes don't change often).
   c. **Deal-state lookup** (HubSpot Deals + property history): for
      the entity's HubSpot Company, find the most recent deal modified
      AT-OR-BEFORE the event timestamp. Capture `deal_id`,
      `deal_stage`, `deal_amount`, `fleet_extension_milestone`,
      `competitor_in_consideration` (Syroco custom property).
      Cache by `(company_id, day_bucket)` for 24h.
4. Build enrichment record (see schema below).
5. Append to `enrichments/<today>.jsonl` (parallel to `events/`).
6. Update state file `last_processed_at`.

**Note (2026-05-03 correction):** earlier draft of this enricher
proposed querying `efficientship-backend` for voyage data. That was
wrong — vessel/captain context lives in HubSpot custom objects
attached to companies. This enricher is HubSpot-only at v0.1.

If a future use case demands "which voyage was active at timestamp T"
(dynamic voyage history, not static vessel/captain context), THAT
lookup goes to efficientship-backend and lives in a separate
v0.2 enricher (`voyage-history-enricher`). For v0.1, vessel + captain
+ deal-state is enough cross-source linkage.

## Enrichment record schema

Lives in `enrichments/<YYYY-MM-DD>.jsonl`, JSONL one record per line.
Each record references the event it enriches by `event_ref`:

```json
{
  "schema_version": "1.0",
  "ts": 1714464000,
  "enricher": "voyage-context-enricher",
  "enricher_version": "0.1.0",
  "event_ref": "slack://project-odfjell/1714464000.123456",
  "entity_hint": "odfjell",

  "speaker": {
    "user_id": "U123ABC",
    "speaker_role": "captain",
    "captain_record_id": "hubspot-captain-789",
    "captain_name": "Captain X",
    "source": "hubspot.custom.captain.slack_user_id"
  },

  "vessel": {
    "vessel_record_id": "hubspot-vessel-456",
    "vessel_name": "MV Atlantic Star",
    "vessel_imo": "9876543",
    "vessel_type": "gas-tanker",
    "company_id": "hubspot-company-123",
    "source": "hubspot.custom.vessel"
  },

  "deal_state_at_time": {
    "deal_id": "12345",
    "deal_stage": "negotiate",
    "deal_amount": 120000,
    "fleet_extension_milestone": "Q3-2026-pilot-extension",
    "competitor_in_consideration": "Napa",
    "captured_at": "2026-04-30T15:30:00Z",
    "source": "hubspot.deals.history"
  },

  "lookup_metadata": {
    "captain_lookup_latency_ms": 142,
    "vessel_lookup_latency_ms": 87,
    "deal_lookup_latency_ms": 156,
    "cache_hits": ["vessel"],
    "cache_misses": ["captain", "deal_state"],
    "errors": []
  }
}
```

Any of `speaker`, `vessel`, `deal_state_at_time` may be `null` if the
lookup failed or wasn't applicable. `lookup_metadata.errors[]` records
why.

**Note**: `voyage` (dynamic voyage history) is NOT in v0.1 — it's
deferred to a v0.2 sibling enricher (`voyage-history-enricher`) that
reads from efficientship-backend. v0.1 captures the static structure
(captain → vessel → company) which is enough for most consumer
queries.

## Lookup config (Syroco-specific)

### Captain identification — HubSpot Captain custom object

The Captain object (HubSpot custom object schema name: `captain`) is
the source of truth. Required properties on each Captain record:

| Property | Type | Purpose |
|---|---|---|
| `name` | string | Display name |
| `slack_user_id` | string | The U-prefixed Slack ID — primary lookup key from this enricher |
| `email` | string | Secondary lookup key (matches Granola participant emails) |
| `associations.vessel` | association | The vessel this captain commands |
| `associations.company` | association | The shipping company that employs them (denormalized for lookup speed) |
| `assignment_start` | date | When they took this assignment |
| `assignment_end` | date / null | If they've rotated off |

**Lookup pattern**: `search_crm_objects(object_type='captain',
filter={slack_user_id: <U123ABC>, assignment_end: null})`. Returns one
or zero records.

**Onboarding workflow**: when a new captain joins a customer's
project Slack channel, CSM creates the HubSpot Captain record with
their Slack `user_id`. This replaces the manual roster TOML proposed
in the earlier draft.

### CSM identification

CSM identification stays local config (CSMs aren't HubSpot custom
objects):

```toml
# ~/.claude/scribes/voyage-context-enricher/csm_roster.toml
[csms]
gilles_tabart = "U07GTABCD"
apolline_berge = "U08APOL12"
amelie_lestienne = "U09AMEL34"
```

### Vessel identification — HubSpot Vessel custom object

The Vessel object (HubSpot custom object schema name: `vessel`) is the
source of truth. Required properties on each Vessel record:

| Property | Type | Purpose |
|---|---|---|
| `vessel_name` | string | Display name (e.g. "MV Atlantic Star") |
| `vessel_imo` | string | IMO number — secondary lookup |
| `vessel_type` | string | gas-tanker / bulk-carrier / container / etc. |
| `associations.company` | association | The owning shipping company |
| `associations.captain` | association | Current captain (mirrors Captain.associations.vessel) |
| `current_status` | string | active / drydock / decommissioned |

**Lookup pattern**: by `vessel_record_id` (preferred, captured from
Captain association) or by `(company_id, vessel_name)` text search
(fallback when text mentions a vessel without a Captain link).

### Customer → company mapping (mirrors granola/slack/hubspot aliases)

Imported from the shared aliases file (long-term:
`~/.claude/scribes/_shared/aliases.toml`). For v0.1, embedded copy
synced manually. Maps `entity_hint` (e.g. `odfjell`) → HubSpot
`company_id`.

### Priority client list

Imported from
`~/.claude/skills/product-signals-pipeline/references/client-scoring.md`.
Used to flag `is_priority_client: true` on enrichments — picked up by
fleet-radar-skill at delivery time.

## Configuration

### Environment variables

```bash
# Required
export CE_BRAIN_DIR=~/Repos/company-brain/corpora/syroco-product-brain
export HUBSPOT_ACCESS_TOKEN=<token>     # custom objects + standard objects

# Optional
export VOYAGE_ENRICHER_LOOKBACK_HOURS=2      # cron tick window
export VOYAGE_ENRICHER_CACHE_TTL_VESSEL=86400  # 24h (vessel attributes stable)
export VOYAGE_ENRICHER_CACHE_TTL_CAPTAIN=3600  # 1h (assignments can change)
export VOYAGE_ENRICHER_CACHE_TTL_DEAL=86400    # 24h
export VOYAGE_ENRICHER_DRY_RUN=0
```

### State file

`~/.claude/scribes/voyage-context-enricher/state.json`:

```json
{
  "last_processed_at": "2026-04-30T15:30:00Z",
  "last_processed_event_ref": "slack://project-odfjell/1714464000.123456",
  "version": "0.1.0",
  "stats": {
    "events_seen_total": 12345,
    "enrichments_emitted_total": 8901,
    "skipped_no_customer_scope": 3000,
    "skipped_no_captain_match": 222,
    "skipped_no_vessel_match": 100
  }
}
```

### Cache

`~/.claude/scribes/voyage-context-enricher/cache.json` — vessel
records TTL 24h, captain records TTL 1h, deal lookups TTL 24h.
In-memory primary; disk for cron-restart resilience.

## Run

```bash
# Hourly cron (default — incremental)
npx skills run voyage-context-enricher

# Backfill last 30 days
npx skills run voyage-context-enricher -- --since 2026-04-01

# Single entity (debugging)
npx skills run voyage-context-enricher -- --entity odfjell --since 2026-04-28

# Dry run
npx skills run voyage-context-enricher -- --dry-run
```

## Use cases this unlocks

| Persona | Question that becomes one-shot answerable |
|---|---|
| **Andrew (Sales)** | "Did Anthony Veder's deal stall after the captain raised the ETA issue?" — joins slack-scribe captain signal + hubspot-scribe deal-stage move via voyage timestamp |
| **Gilles (CSM)** | "Which Odfjell voyages had captain complaints?" — every captain signal from `#project-odfjell` is now `voyage_id`-tagged; group by voyage |
| **Apolline (CSM)** | "Pre-call brief: KCC's Captain Y last 3 voyages and the issues raised on each" — voyages × captain signals × HubSpot deal context, all merged |
| **Sinbad (Product)** | "Which voyages had ETA misses + which themes did the captain hit?" — voyage_id lets fleet-radar-skill aggregate by voyage instead of by client |
| **Florent (Strategy)** | "Voyages where Napa was mentioned by the captain" — competitor enricher (separate skill) + voyage enricher join |
| **Fleet Radar** | Every priority-client signal arrives pre-tagged with voyage + deal context. Monday digest builds a signal-per-voyage view trivially |

## Integration with consumer skills

### fleet-radar-skill (consumer, Phase 2 of migration doc)

Reads BOTH `events/<date>.jsonl` (raw scribe output) AND
`enrichments/<date>.jsonl` (this enricher's output). Joins on
`event_ref`. Then:

- Applies 15-theme classification (mutable, consumer-side)
- Applies priority overrides per `client-scoring.md` using
  `is_priority_client` flag from enrichment
- Builds Monday digest: signals grouped by client → grouped by voyage →
  with deal-state context

Without this enricher, fleet-radar-skill would have to do the voyage
join itself on every Monday — wasteful, expensive, not stable.

### opportunity-builder-skill (consumer)

Uses `deal_state_at_time` to detect:
- "Captain raised ETA issue → deal stalled at negotiate stage 2 weeks
  later" — opportunity for proactive engagement
- "Multiple voyages had on-time deliveries → opportunity to expand
  fleet coverage" — data point for renewal pitch

### Pre-call brief skill (consumer, future)

`brief @anthony-veder before tomorrow 10am call` →
- Pulls every event for `entity=anthony-veder` since last call
- Joins enrichments → shows "voyage VOY-X completed on-time, voyage
  VOY-Y delayed by 4h, deal currently at negotiate"
- Critical context the founder couldn't reconstruct in 5 minutes
  pre-call

## Quality tiers — Syroco-specific

| Tier | When to use | Cost |
|---|---|---|
| **T0** (default) | Voyage lookup + deal lookup + speaker role. Rule-based. | $0 (just API calls; voyage system + HubSpot, no LLM) |
| **T1** | T0 + LLM summarization of voyage's relevance to the event ("the captain's ETA complaint was during voyage X which had Y constraint"). Useful for pre-call briefs. | ~$0.005/enrichment × ~50/day = $0.25/day |
| **T2** (future) | Cross-event temporal pattern: "captain complained on voyages X, Y, Z, all had similar weather conditions" — proper voyage-cluster analysis | Quarterly batch |

Default config is **$0/month** (just API calls). LLM tier opt-in.

## Failure modes & graceful degradation

| Failure | Behavior |
|---|---|
| HubSpot unreachable | Skip all lookups; emit enrichment with all fields `null`, log error in `lookup_metadata.errors`. Re-run will retry on next cron tick. |
| HubSpot rate-limited | Skip deal lookup; emit enrichment with `deal_state_at_time: null` but keep captain/vessel if cached. |
| Captain custom object misses for `slack_user_id` | `speaker_role: "unknown"`, log telemetry `captain.miss` with the unknown user_id. CSM reviews; creates HubSpot Captain record if it's a real captain. |
| Vessel custom object missing on Captain association | `vessel: null`, log telemetry `vessel.miss`. Means captain record was created without a vessel link — CSM fixes in HubSpot. |
| Customer not in alias table | Skip enrichment entirely; emit telemetry `alias.miss` so operator adds it. |
| Captain has rotated off (`assignment_end != null`) | Use the captain record but flag `speaker_role: "former_captain"`. Important: their old signals shouldn't suddenly become unattributable when they leave. |
| Event already enriched | Idempotent: re-runs check enrichment log; skip. |

The enrichment log being separate from the events log means a failed
enricher run doesn't corrupt event data. Worst case: enrichments are
behind events. Consumers tolerate missing enrichments (fall back to
unenriched event display).

## Anti-patterns to avoid

- **Don't store enrichment IN the events log.** Events are append-only
  and immutable. Enrichments are stored in a parallel log so they can
  be regenerated (e.g., when the captain roster updates).
- **Don't enrich every event.** Filter by scope first
  (`customer-expansion`, `new-biz`, `partners`). Skipping internal
  events saves 50%+ of lookup cost.
- **Don't query the voyage system per event.** The cache (1h TTL on
  voyage data) collapses N captain messages from the same minute into
  one lookup. Without the cache, an active client + 50 messages/day =
  50 redundant API calls.
- **Don't fail loudly on roster miss.** New captains arrive every
  month; the enricher should degrade gracefully (`speaker_role:
  "unknown"`) and let CSM update the roster offline.
- **Don't try to be a consumer.** This enricher does NOT classify
  themes, score priority, or render output. Stable enrichment only.

## Open questions

| # | Question | Owner |
|---|---|---|
| ~~V1~~ | ~~efficientship-backend voyage endpoint~~ | **DROPPED 2026-05-03** — vessel/captain context is in HubSpot custom objects, not efficientship-backend. Voyage history (dynamic) is a v0.2 separate enricher |
| V2 | Captain custom-object `slack_user_id` field — does it already exist on the schema? If not, ~5min HubSpot admin add. CSM team backfills for active captains | Gilles + HubSpot admin |
| V3 | When a captain rotates off (`assignment_end` set), the enricher caches keep them findable for stale lookups. 1h TTL on captain records is short enough that rotation propagates within an hour. Acceptable? | Implementation |
| V4 | HubSpot deal "as-of" lookup — HubSpot's API doesn't natively return "stage as of timestamp X." We have property-history APIs but they're per-deal pagination-heavy. Cache aggressively + stale-tolerance of 1h | Implementation |
| V5 | When a Slack message is in a `_with-total` channel (Hafnia/Socatra/Oceonix), enrichment for the named client OR TotalEnergies (master contract)? Recommend: named client. TotalEnergies enrichment is a separate cross-emit | slack-scribe + this |
| V6 | Multi-vessel customers (Odfjell has 90+ vessels) — when a captain says "ETA off" without naming the vessel, the captain → vessel association on the HubSpot Captain record gives us the answer. Resolved by HubSpot data model | — |
| V7 | Should the enricher run BEFORE or AFTER scribes? Today's spec: AFTER (consumes their events). Alternative: scribes call this enricher inline at emit-time. Recommend: AFTER — keeps scribes simple | Architecture |
| V8 | Email matching — Granola transcripts have participant emails (e.g. `lsopar@anthonyveder.com`). Should we look up Captain custom object by email too, not just Slack `user_id`? Yes — secondary lookup key per the schema spec | Implementation |
| V9 | Vessel-name extraction from text — when a captain says "MV Atlantic Star is reporting issues" without us knowing it's their vessel, do we text-match against HubSpot Vessel records by name? Recommend: yes, fall-back when Captain lookup misses | Implementation |

## What's NOT in v0.1

- LLM summarization (T1 tier deferred).
- Cross-voyage pattern detection (T2 tier deferred).
- Real-time webhook mode (cron only).
- Voyage forecast lookahead ("voyage X is heading into bad weather, captain might raise ETA concern soon"). That's predictive — out of scope, belongs in a forecasting consumer.
- Vessel-by-name disambiguation (multiple vessels with similar names).
- Acter sibling (e.g. "auto-create Linear ticket when captain signal correlates with deal stall") — that's a separate skill.

## Relationship to the spec

- `agent-skills/scribes/SPEC.md` — defines event schema; enrichments
  follow the same field conventions but live in a separate log.
- `agent-skills/scribes/SKILL_INTEGRATION.md` §2 — defines the 3-skill
  family (scribe / enricher / acter); this is the first enricher.
- `agent-skills/scribes/PLAN.md` §7 Tier 1 — added 2026-05-03 after
  Victor's pushback on the categorization placement question.
- `agent-skills/scribes/MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md`
  Phase 2 — fleet-radar-skill (consumer) reads enrichments from this
  skill.

## Composition with other enrichers

When all 5 enrichers from `SKILL_INTEGRATION.md` §2 are deployed, the
order matters:

```
captain-signal-enricher           → speaker_role
voyage-context-enricher (this)    → voyage_id, vessel, voyage_phase, deal_state
deal-state-enricher               → deal_id, deal_stage, deal_amount (delegated to voyage-context-enricher v0.1; split out at v0.2 if scoped enough)
competitor-mention-enricher       → competitors_mentioned[]
fleet-radar-eligibility-enricher  → priority client flag, theme candidates
```

For v0.1, voyage-context-enricher does both speaker_role AND voyage
AND deal_state in one pass. Splitting per the canonical 5-enricher
list is a v0.2 refactor — only worth the split if independent
versioning becomes valuable.

## Owner contact

Author: Victor Grosjean. Eng owner for voyage-system endpoint: Arnaud.
First-pass review: Gilles (CSM).
Issues: `agent-skills` repo with label `enricher:voyage-context`.
