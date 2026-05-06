---
name: slack-scribe
version: 0.1.0
description: |
  Reads Syroco's Slack project channels (`#project-*`) and emits events
  to the context-engineering brain via wiki.add MCP. Scope-aware: each
  channel is mapped to a known client and lands in the right scope
  (customer-expansion / new-biz / partners). Filters operational chatter;
  keeps meeting recaps, alerts, blockers, competitive intel, fleet
  changes, and product feedback.

  Second worked example of the scribe pattern in `SPEC.md`. Replaces the
  Slack fetch phase of `product-signals-pipeline` per
  `MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md`.

triggers:
  - "ingest slack messages"
  - "pull slack into brain"
  - "slack scribe"
  - "MCP namespace: slack"
  - "scan project channels"
  - "syroco slack brain ingestion"

scribe:
  connector: slack
  mcp_required: ["claude_ai_Slack"]   # uses Claude.ai Slack MCP
  mcp_optional: []
  events_pushed_to: "wiki.add"
  quality_tiers: ["T0", "T1"]
  default_tier: "T1"
  cadence_supported: ["cron"]          # hourly preferred
  ce_event_schema: "1.0"
  half_life_key: "slack"               # NEW — must register in HALF_LIVES
  scopes_required: ["channels:history", "channels:read", "groups:history", "users:read"]

scribe_compatibility:
  ce_event_schema: "1.0"
  ce_min_version: "0.5.0"

scribe_syroco:
  brain_corpora_supported:
    - "company-brain/corpora/syroco-product-brain"
  default_brain_env_var: "CE_BRAIN_DIR"
  channel_pattern: "#project-*"
  monday_full_scan: true
  weekday_lookback_hours: 24
  monday_lookback_days: 7

owners:
  primary: "victor.grosjean@syro.co"
  fallback: "@gilles"
---

# slack-scribe (Syroco-flavored v0.1)

> Reads project channels, emits events keyed on Syroco client entities,
> lands them in the right scope, replaces the Slack fetch phase of
> product-signals-pipeline.

## Why this exists for Syroco

Syroco runs ~16 `#project-<client>` Slack channels (the priority client
list lives in `slack-channels.md`). CSMs post meeting recaps, captains
escalate alerts, pricing comparisons get debated. Today these signals
flow through `product-signals-pipeline`'s Monday scan, get categorized
into 15 themes, and land in Notion Signals.

After scribe ships:
- Slack signals land in the brain CONTINUOUSLY (hourly, not Monday-only).
- Same client = same wiki page (no per-source duplication).
- The Monday Fleet Radar consumer reads the brain instead of fetching.
- Pre-call brief consumer ("what's been said in #project-odfjell this
  week?") becomes a one-shot query.

## What it does (T1 default)

Per channel scan (hourly cron + Monday full scan):

1. Walk `#project-<client>` channels per the Syroco channel map (see
   §"Channel-to-entity mapping" below).
2. Fetch new messages since `last_processed_ts` (state file).
3. For threaded messages with >3 replies OR root message >200 chars:
   fetch the full thread.
4. Filter messages: skip operational chatter, keep signal-bearing posts
   (see §"What to extract" below).
5. For each signal-bearing message: emit one event with
   `entity_hint = <client-slug>` (from channel map),
   `scope = <client-scope>`, `claim = <message text or thread summary>`.
6. Cross-emit if competitor mentioned in text (same as granola-scribe).

## Channel-to-entity mapping (Syroco-specific, from
`product-signals-pipeline/references/slack-channels.md`)

```toml
# slack-scribe/channel_map.toml — embedded for v0.1; entity-resolver
# skill takes ownership later.

# Priority clients (full scan + heightened lookback)
[channels."#project-odfjell"]
slack_id = "C07RDLUHA64"
entity_hint = "odfjell"
scope = "customer-expansion"
priority = "high"

[channels."#project-hafnia-with-total"]
slack_id = "C09FB0FPV7S"
entity_hint = "hafnia"
scope = "customer-expansion"
priority = "high"
secondary_entity = "totalenergies"   # see §with-total rule

[channels."#project-ldc"]
slack_id = "C08K7F63XJ8"
entity_hint = "ldc"
scope = "customer-expansion"
priority = "high"

[channels."#project-kcc"]
slack_id = "C0946SU39ND"
entity_hint = "kcc"
scope = "customer-expansion"
priority = "high"

[channels."#project-geogas"]
slack_id = "C066K5AA7CY"
entity_hint = "geogas"
scope = "customer-expansion"
priority = "high"

[channels."#project-knutsen"]
slack_id = "C06BW8SU02C"
entity_hint = "knutsen"
scope = "customer-expansion"
priority = "high"

[channels."#project-uhl"]
slack_id = "C06MTEQKQG4"
entity_hint = "uhl"
scope = "customer-expansion"
priority = "high"

[channels."#project-oldendorff"]
slack_id = "C07USEEP9AL"
entity_hint = "oldendorff"
scope = "customer-expansion"
priority = "high"

# Secondary clients (regular cron, no heightened scrutiny)
[channels."#project-nirint"]
slack_id = "C0960BMDTQ8"
entity_hint = "nirint"
scope = "customer-expansion"
priority = "normal"

[channels."#project-euroafrica"]
slack_id = "C094S7TA05T"
entity_hint = "euroafrica"
scope = "customer-expansion"
priority = "normal"

[channels."#project-marfret"]
slack_id = "C04NW0JJT4Y"
entity_hint = "marfret"
scope = "customer-expansion"
priority = "normal"

[channels."#project-socatra_with-total"]
slack_id = "C06L7FBN1BJ"
entity_hint = "socatra"
scope = "customer-expansion"
priority = "normal"
secondary_entity = "totalenergies"

[channels."#project-olympic"]
slack_id = "C093RAU07C1"
entity_hint = "olympic"
scope = "customer-expansion"
priority = "normal"

[channels."#project-sun_enterprises"]
slack_id = "C0939KZ2LN9"
entity_hint = "sun-enterprises"
scope = "customer-expansion"
priority = "normal"

[channels."#projet-tgs"]
slack_id = "C092T4T7D7V"
entity_hint = "tgs"
scope = "customer-expansion"
priority = "normal"

[channels."#project-oceonix-with-total"]
slack_id = "C0AEZ84SFCG"
entity_hint = "oceonix"
scope = "customer-expansion"
priority = "normal"
secondary_entity = "totalenergies"

# New channel discovery: log to telemetry, prompt operator to add
[discovery]
pattern = "#project-*"
auto_add = false   # require explicit operator confirmation
```

### `with-total` co-managed rule (per slack-channels.md §"Attribution rules")

Hafnia / Socatra / Oceonix are co-managed with TotalEnergies. Master
contract is with TotalEnergies.

- **Default**: emit event with `entity_hint = <named client>` (hafnia,
  socatra, oceonix) and a SECOND event with
  `entity_hint = "totalenergies"`.
- **If message explicitly names TotalEnergies (pool points, budget,
  commercial decision)**: emit primary event with
  `entity_hint = "totalenergies"`, secondary event tagged for the
  operational client.

This dual-emit ensures `wiki.ask totalenergies` returns the full
contractual picture; `wiki.ask hafnia` returns operational picture.

## What to extract (signal-bearing message detection)

Skip messages that match operational-chatter patterns. Emit events for
messages matching one of:

### 1. Meeting recaps
Pattern: posted by a CSM after a client call. Often threaded to a
calendar link. Detect: presence of `calendar.google.com` or `cal.com`
links + author is in the CSM list (Gilles, Apolline, Amélie).

### 2. Alerts / blockers
Pattern: outages, captain escalations, deployment issues, ETA misses.
Detect keywords (case-insensitive): `outage`, `down`, `escalation`,
`escalated`, `urgent`, `blocker`, `production issue`, `eta miss`,
`captain reports`, `not working`, `broken`, `failing`.

### 3. Competitive intel
Pattern: mentions of competitors, RFP decisions, pricing comparisons.
Detect: any tier-1 competitor name (`napa`, `kongsberg`, `dnv eco
insight`, `wartsila fleet`, `oneocean`, `chartco`) — case-insensitive.

### 4. Fleet changes
Pattern: new vessels added, decommissioned, onboarding status. Detect:
`new vessel`, `decommissioned`, `onboarded`, `fleet expansion`,
`new ship`, `delisted`, `swap to`, `replacement`.

### 5. Product feedback (direct quotes)
Pattern: client quotes about features, pain points, wishes. Detect:
quoted text (lines starting with `>` or in code blocks) + signal verbs
(`captain says`, `they want`, `they need`, `they don't like`,
`they prefer`, `client asked`, `feedback was`).

### Skip these
- "FYI" without further content
- Calendar reminders (auto-posted)
- Pure operational logistics ("standup at 10am tomorrow")
- Bot messages from CI/CD, monitoring (unless they're outage alerts —
  see #2)
- Single-emoji reactions / acknowledgments

## Event schema example

For a hypothetical `#project-odfjell` thread on 2026-04-30:

```
[Gilles] @here Just got off a call with Michelle. She's pushing for the
fleet alert system to land before May 12 — they need it for the CP
Terms cutover. Also asking when we'll have weather provider choice
(she mentioned ECMWF specifically). She raised a concern about Napa's
hull performance numbers being more conservative than ours, wants us
to validate against their methodology.
```

Emitted events:

```json
[
  {
    "schema_version": "1.0",
    "ts": 1714464000,
    "source_type": "slack",
    "source_ref": "slack://project-odfjell/1714464000.123456",
    "file_id": "slack-C07RDLUHA64-1714464000",
    "claim": "[Odfjell, via Michelle] Pushing for fleet alert system before May 12 (CP Terms cutover dependency). Asking re weather provider choice (ECMWF specifically). Concerned re Napa's hull performance numbers more conservative than ours — wants validation against their methodology.",
    "entity_hint": "odfjell",
    "scope": "customer-expansion"
  },
  {
    "schema_version": "1.0",
    "ts": 1714464000,
    "source_type": "slack",
    "source_ref": "slack://project-odfjell/1714464000.123456#fleet-alert",
    "file_id": "slack-C07RDLUHA64-1714464000-fleet-alert",
    "claim": "[Odfjell deadline] Fleet alert system needed before May 12 for CP Terms cutover.",
    "entity_hint": "fleet-alert-system",
    "scope": "internal-product"
  },
  {
    "schema_version": "1.0",
    "ts": 1714464000,
    "source_type": "slack",
    "source_ref": "slack://project-odfjell/1714464000.123456#napa-mention",
    "file_id": "slack-C07RDLUHA64-1714464000-napa",
    "claim": "Napa (competitor) — Odfjell flags Napa's hull performance methodology as more conservative than Syroco's; client wants validation against Napa's approach.",
    "entity_hint": "napa",
    "scope": "competitors"
  }
]
```

3 events from one Slack message at T1: the primary (odfjell), one
internal initiative cross-emit (fleet-alert-system), one competitor
cross-emit (napa).

## Configuration

### Environment variables

```bash
# Required
export CE_BRAIN_DIR=~/Repos/company-brain/corpora/syroco-product-brain

# Optional
export SLACK_SCRIBE_TIER=T1                  # T0 | T1
export SLACK_SCRIBE_LOOKBACK_HOURS=24        # weekday cron
export SLACK_SCRIBE_MONDAY_LOOKBACK_DAYS=7   # Monday full scan
export SLACK_SCRIBE_DRY_RUN=0
```

### State file

`~/.claude/scribes/slack-scribe/state.json`:

```json
{
  "channels": {
    "C07RDLUHA64": { "last_ts": "1714464000.123456" },
    "C09FB0FPV7S": { "last_ts": "1714463500.456789" }
  },
  "version": "0.1.0"
}
```

### Channel map

`~/.claude/scribes/slack-scribe/channel_map.toml` — see above. Operator
edits to add new channels.

## Run

```bash
# Hourly cron (default — incremental)
npx skills run slack-scribe

# Monday full scan (7-day lookback)
npx skills run slack-scribe -- --full-scan

# First-time backfill (90 days, all channels)
npx skills run slack-scribe -- --since 2026-02-01

# Single channel
npx skills run slack-scribe -- --channel C07RDLUHA64
```

## Use cases this unlocks for Syroco

| Persona | Question that becomes one-shot answerable |
|---|---|
| **Gilles (CSM)** | "What's been said about Odfjell in the last 7 days?" — combines all `#project-odfjell` threads |
| **Apolline (CSM)** | "Action items from KCC's last calls" — Slack + Granola merged |
| **Andrew (Sales)** | "Which clients mention Napa?" — cross-channel scan |
| **Florent (Strategy)** | "Where is TotalEnergies surfacing this week?" — `wiki.ask totalenergies` returns Hafnia + Socatra + Oceonix activity |
| **Sinbad (Product)** | "What blockers were filed in the last 24h?" — alert-pattern matches |
| **Victor (Founder)** | "Pre-call brief on Hafnia" — pulls Slack thread + Granola transcripts + HubSpot context |

## Integration with the rest of the brain

- **Replaces**: `product-signals-pipeline` Slack fetch phase per
  `MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md` Phase 1.
- **Cooperates with**:
  - granola-scribe (same client = same wiki page; meeting recap in
    Granola + Slack thread merge cleanly)
  - hubspot-scribe (deal context overlays Slack signals when consumer
    skill scores)
  - fleet-radar-skill (consumer; reads brain Monday morning, applies 15
    themes, posts digest)
  - opportunity-builder-skill (consumer; groups claims into opportunities)

## Quality tiers — Syroco-specific

| Tier | When to use | Cost |
|---|---|---|
| **T0** | First install, demos. One event per signal-matching message, claim = first 280 chars | $0 |
| **T1** (default) | All hourly runs. Per-thread chunking, per-message extraction, competitor + initiative cross-emit | $0 (rule-based) |
| **T2** (manual) | Backfill on a high-stakes client (KCC renewal, Odfjell expansion). LLM-classifies thread purpose, extracts atomic claims | ~$0.005/thread × ~100 threads/client = $0.50/backfill |
| **T3** (deferred) | Cross-channel pattern detection ("3 different clients flagged Napa this week") | Quarterly batch |

Total weekly cost at default config: **$0**. T2 spike on a single
client backfill: ~$0.50.

## Anti-patterns to avoid

- **Don't ingest #general or #random.** Operational chatter pollutes
  the brain. Only `#project-*` channels in the v0.1 scope.
- **Don't ingest DMs.** PII risk + low signal/noise. Out of scope.
- **Don't skip the with-total dual-emit.** Otherwise `wiki.ask
  totalenergies` returns nothing despite TotalEnergies being the
  master-contract holder for 3 channels.
- **Don't deduplicate across channels via `entity_hint`.** Same client
  posts in `#project-odfjell` AND someone @-mentions Odfjell in
  `#project-knutsen` — both are valid signals; the brain accumulates
  both (`source_ref` differs).

## Open questions

| # | Question | Owner |
|---|---|---|
| S1 | The CSM list (Gilles, Apolline, Amélie) is hardcoded for the meeting-recap detector. After org changes, this drifts. Sync from a `roles.toml` config? | Implementation |
| S2 | Slack search rate limits — how aggressive is hourly cron at scale? Need backpressure / `--max-rps` flag | Architecture |
| S3 | Should we ingest internal channels too (`#engineering`, `#product`, `#sales`)? Today: only `#project-*`. Internal channels are 1:1-style filter (per granola-scribe Q5 rule)? | Victor |
| S4 | Thread-resolution depth: a 50-message thread on a CP Terms blocker — emit 50 events or one consolidated event? T1 = consolidated; T2 = per-message. | Architecture |
| S5 | When a `#project-*` channel is created for a NEW prospect (not yet a customer), does it auto-rescope to `new-biz`? Or stay `customer-expansion` since it's a `#project-*` channel? Today: hardcoded to `customer-expansion` per channel_map; operator overrides. | Sales + CSM |

## What's NOT in v0.1

- DMs, #general, internal channels (out of scope).
- Real-time webhook mode (cron only).
- Auto-add of newly-discovered `#project-*` channels (operator confirms).
- T3 cross-channel pattern detection (Phase 2).
- Slack reactions as signal (e.g. 🚨 reaction on a message).

## Relationship to the spec

- `agent-skills/scribes/SPEC.md` §"Worked example sketches" → slack-scribe
  sketch was deferred; this is the elaboration.
- `agent-skills/scribes/PLAN.md` §7 Tier 1 — slack-scribe is item #2.
- `agent-skills/scribes/MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md`
  Phase 1 — slack-scribe replaces the Slack fetch phase.

## Owner contact

Author: Victor Grosjean. Reviewer: Gilles Tabart.
Issues: `agent-skills` repo with label `scribe:slack`.
