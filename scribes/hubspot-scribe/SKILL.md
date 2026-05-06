---
name: hubspot-scribe
version: 0.1.0
description: |
  Reads Syroco's HubSpot CRM (companies, deals, contacts, activities)
  and emits events to the context-engineering brain via wiki.add MCP.
  Different shape from granola/slack scribes: consumes property diffs
  + activity logs, not chunked transcripts. Maps HubSpot company.domain
  to brain entity_hint; scope derived from HubSpot lifecycle stage.

  Third worked example of the scribe pattern. Replaces the HubSpot
  prioritization phase of `product-signals-pipeline` per the migration
  doc — but moved upstream: hubspot-scribe pushes raw deal context to
  the brain; consumer skills (fleet-radar-skill, opportunity-builder)
  apply the prioritization logic at delivery time.

triggers:
  - "ingest hubspot"
  - "pull crm into brain"
  - "hubspot scribe"
  - "MCP namespace: hubspot"
  - "deal stage changes into brain"
  - "syroco crm brain ingestion"

scribe:
  connector: hubspot
  mcp_required: ["claude_ai_HubSpot"]   # or syroco__hubspot via Pipedream
  mcp_alternative: ["syroco__hubspot"]   # operator picks one
  events_pushed_to: "wiki.add"
  quality_tiers: ["T0", "T1"]
  default_tier: "T0"                     # property-diff events are already structured
  cadence_supported: ["cron", "webhook"]  # webhook future, cron v0.1
  ce_event_schema: "1.0"
  half_life_key: "hubspot"               # NEW — must register in HALF_LIVES
  scopes_required:
    - "crm.objects.companies.read"
    - "crm.objects.contacts.read"
    - "crm.objects.deals.read"
    - "crm.objects.activities.read"
    - "crm.schemas.custom.read"          # to discover Vessel + Captain schemas
    - "crm.objects.custom.read"          # to read custom object instances

scribe_compatibility:
  ce_event_schema: "1.0"
  ce_min_version: "0.5.0"

scribe_syroco:
  brain_corpora_supported:
    - "company-brain/corpora/syroco-product-brain"
  default_brain_env_var: "CE_BRAIN_DIR"
  priority_pipeline: "default"             # which HubSpot pipeline to track
  lifecycle_to_scope:
    customer: "customer-expansion"
    evangelist: "customer-expansion"
    opportunity: "new-biz"
    salesqualifiedlead: "new-biz"
    marketingqualifiedlead: "new-biz"
    lead: "new-biz"
    subscriber: "default"
    other: "default"

owners:
  primary: "victor.grosjean@syro.co"
  fallback: "@andrew.cornish"     # sales is closest user
---

# hubspot-scribe (Syroco-flavored v0.1)

> Reads HubSpot company/deal/contact properties + activities, emits
> events to the brain. Different shape from transcript scribes: structured
> diff stream, not narrative extraction.

## Why this exists for Syroco

HubSpot holds the COMMERCIAL ground truth about every prospect and
customer — deal stage, amount, owner, last activity, lifecycle stage,
fleet-extension milestone tags. Today this lives only in HubSpot and
gets manually overlaid onto Slack + Granola signals during the Monday
Fleet Radar build.

After this scribe ships:
- Deal stage transitions land in the brain in real-time.
- `wiki.ask anthony-veder` returns "$deal moved to negotiation 2 weeks
  ago" alongside Granola call notes and Slack threads.
- Consumer skills (fleet-radar-skill) read the brain to apply the
  HubSpot-overlay logic, instead of re-fetching every Monday.
- Cross-source brief becomes one query: "what's the full Anthony Veder
  picture?" returns commercial + product + competitive context merged.

## What it does (T0 default)

Per cron run (hourly):

1. **Companies**: query `crm.objects.companies.search` for companies
   modified since `last_processed_at` (state file). For each:
   - Map `domain` → entity_hint via aliases (or fall back to slugified
     company name).
   - Determine scope from `lifecyclestage` per the scope map.
   - Diff key properties against last snapshot (cached locally). Emit
     one event per CHANGED property: `claim = "[Property] changed from
     X to Y"`.
2. **Deals**: query `crm.objects.deals.search` for deals modified.
   For each:
   - Map deal's primary company → entity_hint.
   - Diff: stage transitions, amount changes, close date changes, owner
     changes, custom-property changes (Syroco-specific: fleet-extension
     milestone, vessel count).
   - Emit one event per change with `claim` describing the diff.
3. **Contacts**: query `crm.objects.contacts.search`. Emit only on:
   - New contact added (with company association)
   - Contact role/title change
   - Contact owner reassignment
4. **Activities**: query `engagements.search` for new calls/emails/notes
   since last run. Emit one event per activity with the contact +
   company association.
5. **Vessels (custom object)**: query
   `crm.objects.custom.<vessel_schema>.search`. For each modified vessel:
   - `entity_hint = "<vessel_imo>"` OR `"<company-slug>-<vessel-slug>"`
     (when IMO unavailable). One vessel = one brain entity.
   - `scope = customer-expansion` (vessels belong to existing customers).
   - Diff: vessel name changes, status (drydock/active), captain
     reassignment, IMO updates.
   - Cross-emit: also append vessel info to the OWNING COMPANY's brain
     page so `wiki.ask odfjell` returns vessel list.
6. **Captains (custom object)**: query
   `crm.objects.custom.<captain_schema>.search`. For each modified
   captain:
   - `entity_hint = "<company-slug>-<captain-slug>"` (e.g. `odfjell-captain-x`).
   - `scope = customer-expansion`.
   - Diff: name changes, slack_user_id additions/changes, vessel
     reassignment, assignment_start / assignment_end.
   - Cross-emit: append captain info to OWNING COMPANY page AND to the
     associated VESSEL page.

Push as a single batched `wiki.add` call (atomic per `SPEC.md`).

**Note (2026-05-03)**: items 5 and 6 (Vessels and Captains as custom
objects) are first-class for Syroco. They give the brain structural
ground truth: "Odfjell owns 90 vessels, captain X commands MV Atlantic
Star, Q3 deal targets fleet-extension milestone Y." This data is
consumed by `voyage-context-enricher` to do the captain-signal →
vessel/company linkage.

## Entity heuristics for Syroco

### HubSpot domain → brain slug (aliases)

```toml
# hubspot-scribe/aliases.toml — embedded for v0.1; entity-resolver
# skill takes ownership later.

# Maps HubSpot company.domain → brain entity_hint.
# Mirrors granola-scribe/aliases.toml so the brain entities merge.

["anthonyveder.com"]
slug = "anthony-veder"

["christianiashipping.com"]
slug = "christiania-shipping"

["jupi.co"]
slug = "jupi"

["odfjell.com"]
slug = "odfjell"

["theyr.com"]
slug = "theyr"

["wordware.ai"]
slug = "wordware"

# Add Syroco priority clients (from slack-channels.md priority list)
["hafnia.com"]
slug = "hafnia"
secondary_entity = "totalenergies"  # mirrors slack-scribe with-total rule

["ldc.com"]
slug = "ldc"

["kcc.com"]
slug = "kcc"

# (Verify exact domain — KCC may use klaveness.com or kcc-eu.com)

["geogas.com"]
slug = "geogas"

["knutsen.no"]
slug = "knutsen"

["uhl.de"]
slug = "uhl"

["oldendorff.com"]
slug = "oldendorff"
```

**Domain-to-slug consistency rule:** every external entity in
hubspot-scribe MUST share its slug with the granola-scribe and
slack-scribe alias tables. Otherwise the brain accumulates duplicate
pages. Recommend: extract aliases.toml to a shared file
`~/.claude/scribes/_shared/aliases.toml` and have all 3 scribes import
it. (Out of v0.1 scope; manual sync for now.)

### Scope derivation (lifecycle stage → brain scope)

Per the `scribe_syroco.lifecycle_to_scope` map in frontmatter:

| HubSpot lifecycle | Brain scope |
|---|---|
| `customer`, `evangelist` | `customer-expansion` |
| `opportunity`, `salesqualifiedlead`, `marketingqualifiedlead`, `lead` | `new-biz` |
| `subscriber`, `other`, missing | `default` |

This is intentionally simpler than granola/slack scribes' note-creator-
based discrimination. HubSpot lifecycle stage is authoritative for
"is this a prospect or a customer?"

### Properties tracked (Syroco-specific defaults)

```toml
# hubspot-scribe/tracked_properties.toml

[companies]
diff_track = [
  "name", "domain", "industry", "country",
  "lifecyclestage", "hs_lead_status",
  "numberofemployees",
  # Syroco custom properties
  "vessel_count", "fleet_size",
  "fleet_extension_milestone",
  "current_voyage_optimization_vendor",   # competitor tracking
]

[deals]
diff_track = [
  "dealname", "dealstage", "amount", "closedate", "hubspot_owner_id",
  # Syroco custom properties
  "fleet_extension_milestone_target",
  "vessel_count_in_scope",
  "estimated_arr",
  "competitor_in_consideration",          # competitor tracking
]

[contacts]
diff_track = [
  "firstname", "lastname", "email", "jobtitle",
  "hubspot_owner_id",                     # owner reassignment
  "lifecyclestage",
]

[custom_objects.vessels]
schema_name = "vessel"
diff_track = [
  "vessel_name", "vessel_imo", "vessel_type",
  "current_status",                       # active / drydock / decommissioned
  "associations.company",                 # who owns it
  "associations.captain",                 # current captain
]

[custom_objects.captains]
schema_name = "captain"
diff_track = [
  "name", "email",
  "slack_user_id",                        # primary key for voyage-context-enricher
  "associations.vessel",
  "associations.company",
  "assignment_start", "assignment_end",
]
```

## Event schema example

Suppose Anthony Veder's deal moves from `qualifiedtobuy` to `negotiate`
on 2026-04-30, amount goes from $80k to $120k:

```json
[
  {
    "schema_version": "1.0",
    "ts": 1714464000,
    "source_type": "hubspot",
    "source_ref": "hubspot://deal/12345#dealstage",
    "file_id": "hubspot-deal-12345-dealstage-1714464000",
    "claim": "[HubSpot deal] Anthony Veder primary deal moved from `qualifiedtobuy` to `negotiate` (Q2 2026 fleet performance pilot).",
    "entity_hint": "anthony-veder",
    "scope": "new-biz"
  },
  {
    "schema_version": "1.0",
    "ts": 1714464000,
    "source_type": "hubspot",
    "source_ref": "hubspot://deal/12345#amount",
    "file_id": "hubspot-deal-12345-amount-1714464000",
    "claim": "[HubSpot deal] Anthony Veder primary deal amount changed from $80,000 to $120,000.",
    "entity_hint": "anthony-veder",
    "scope": "new-biz"
  }
]
```

When a deal closes, the consumer skill (fleet-radar-skill) can detect
"deal closed-won this week" by querying brain entities with recent
HubSpot stage-transition events. No per-Monday re-fetch.

## Configuration

### Environment variables

```bash
# Required
export CE_BRAIN_DIR=~/Repos/company-brain/corpora/syroco-product-brain
export HUBSPOT_ACCESS_TOKEN=<token>      # or via Syroco Connect

# Optional
export HUBSPOT_SCRIBE_TIER=T0            # T0 default; T1 = + activity logs
export HUBSPOT_SCRIBE_LOOKBACK_HOURS=2   # cron tick window; default 2x cadence
export HUBSPOT_SCRIBE_PIPELINE=default   # which HubSpot pipeline to track
export HUBSPOT_SCRIBE_DRY_RUN=0
```

### State file

`~/.claude/scribes/hubspot-scribe/state.json`:

```json
{
  "last_processed_at": "2026-04-30T15:30:00Z",
  "snapshots": {
    "companies": "<sha256 of last full snapshot for diff>",
    "deals": "<sha256>",
    "contacts": "<sha256>"
  },
  "version": "0.1.0"
}
```

### Aliases file

`~/.claude/scribes/hubspot-scribe/aliases.toml` — see above. Should mirror
granola/slack aliases. (Long-term: shared `_shared/aliases.toml` consumed
by all scribes; the entity-resolver skill maintains it.)

### Tracked properties

`~/.claude/scribes/hubspot-scribe/tracked_properties.toml` — see above.

## Run

```bash
# Cron (hourly default)
npx skills run hubspot-scribe

# First-time backfill (last 90 days of activity + current property snapshots)
npx skills run hubspot-scribe -- --since 2026-02-01

# Limit to one pipeline
npx skills run hubspot-scribe -- --pipeline "Syroco Sales 2026"

# Dry run
npx skills run hubspot-scribe -- --dry-run
```

## Use cases this unlocks for Syroco

| Persona | Question that becomes one-shot answerable |
|---|---|
| **Andrew (Sales)** | "What deals moved stage this week?" — one brain query |
| **Victor (Founder)** | "Pre-call brief on Anthony Veder" — Granola + Slack + HubSpot stage + amount in one pack |
| **Gilles (CSM)** | "Which customers had no HubSpot activity in 30 days?" — health check |
| **Florent (Strategy)** | "Pipeline by competitor-in-consideration" — surfaces deals where Napa is the incumbent |
| **Apolline (CSM)** | "Did Michelle's last call result in a deal stage move?" — Granola transcript + HubSpot diff merged |
| **All CSMs** | "Show me Odfjell's fleet" — vessels custom-object pages cross-linked to the company entity |
| **Voyage-context-enricher** | Captain → vessel → company resolution from a single Slack `user_id` (queries Captain custom object by `slack_user_id`) — the keystone link this scribe enables |
| **Fleet Radar** | Monday digest reads brain for deal context AND vessel context AND captain context, no re-fetch |

## Integration with the rest of the brain

- **Replaces**: HubSpot prioritization phase of
  `product-signals-pipeline`. But moved upstream — scribe pushes raw
  deal context; consumers (fleet-radar-skill, opportunity-builder)
  apply the priority-override logic per `client-scoring.md`.
- **Cooperates with**:
  - granola-scribe (same client merges; brain page accumulates calls +
    deal stage)
  - slack-scribe (Slack signals correlate with HubSpot deal moves;
    "captain raised X in Slack → deal stalled in HubSpot 1 week later"
    becomes detectable)
  - fleet-radar-skill (consumer; reads brain Monday morning, applies
    `client-scoring.md` priority overrides at consumer time)

## Quality tiers — Syroco-specific

| Tier | When to use | Cost |
|---|---|---|
| **T0** (default) | Property diff + activity logs only. Each diff = one event. Rule-based. | $0 |
| **T1** | T0 + LLM-summarize multi-property changes into one semantic claim ("$deal moved stage AND amount AND close date in same update → consolidated narrative") | ~$0.001/diff × ~50 diffs/day = $0.05/day = $1.50/month |
| **T2** (manual) | Backfill mode: LLM-summarize the full deal history for a high-stakes client (e.g. Anthony Veder closing call prep) | ~$0.05/deal × 5 deals = $0.25 |
| **T3** (deferred) | Cross-deal pattern detection ("3 deals stalled at negotiation in last 30 days; common competitor: Napa") | Quarterly batch |

Total monthly cost at default config: **$0**.

## Anti-patterns to avoid

- **Don't emit on every property change.** Some properties are noise
  (e.g. `hs_last_modified_date` updates on every API call). The
  `tracked_properties.toml` is the explicit allowlist. Don't widen
  without thought.
- **Don't ingest the entire activity log on backfill.** A 10-year
  HubSpot history would emit 100k+ events. Use `--since` aggressively;
  default is 90 days.
- **Don't forget the `lifecyclestage` → scope mapping.** A "subscriber"
  HubSpot record landing in `customer-expansion` scope is a PII bleed.
- **Don't bypass the alias table.** Without it, "Anthony Veder Holdings
  N.V." (HubSpot legal name) and "anthony-veder" (slack/granola slug)
  become two pages.
- **Don't run T1+ during the first week.** Stabilize T0 first; LLM
  costs compound when you don't yet trust the diff stream.

## Open questions

| # | Question | Owner |
|---|---|---|
| H1 | Multiple deals per company — emit one event per deal-stage move, or aggregate? Current: one per deal-stage move (preserves per-deal granularity) | Architecture |
| H2 | HubSpot custom properties may evolve. The `tracked_properties.toml` needs maintenance. Sync from a HubSpot meta-query? | Implementation |
| H3 | Webhook mode is supported by HubSpot. Worth pulling in for v0.1, or stick with cron? Recommend cron-first; webhook is v0.2 | Architecture |
| H4 | KCC's actual HubSpot domain — verify (klaveness.com vs kcc-eu.com vs kcc.com) before backfill | Andrew |
| H5 | The `client-scoring.md` priority override list (KCC, Odfjell, Hafnia, LDC) lives in `product-signals-pipeline` — after migration, where does it go? Recommend: stays where it is, fleet-radar-skill imports it | Long-term ownership |
| H6 | Activities (calls/emails/notes) often contain the same content as Granola transcripts (when a call is logged in HubSpot AND captured in Granola). Risk: duplicate events. Dedup heuristic? Recommend: emit both, let entity-resolver skill dedupe at consumer time | Implementation |
| H7 | Vessel custom-object schema name — confirm exact HubSpot identifier ("vessel" assumed). Check via `mcp__claude_ai_HubSpot__get_properties` on a vessel record | HubSpot admin |
| H8 | Captain custom-object schema name + `slack_user_id` field existence. If field doesn't exist, ~5min HubSpot admin add. CSM team backfills | Gilles + HubSpot admin |
| H9 | Vessel-as-entity OR vessel-as-property-of-company? Today's spec: vessel-as-entity (one wiki page per vessel). Pro: rich vessel-specific brain. Con: 90+ Odfjell vessels = 90+ pages. Recommend: keep vessel-as-entity for v0.1; merge to one page per company in v0.2 if it gets noisy | Victor |

## What's NOT in v0.1

- Webhook mode (HubSpot supports it; cron only for v0.1).
- Custom-object support (Syroco may have custom CRM objects — out of
  scope until requested).
- HubSpot Marketing data (campaigns, emails, forms) — separate scribe
  if needed (`hubspot-marketing-scribe`).
- LLM activity-extraction (T2+) for v0.1.

## Relationship to the spec

- `agent-skills/scribes/SPEC.md` §"Worked example sketches" → hubspot
  sketch was deferred; this is the elaboration.
- `agent-skills/scribes/PLAN.md` §7 Tier 1 — hubspot-scribe is item #3.
- `agent-skills/scribes/MIGRATION_FROM_PRODUCT_SIGNALS_PIPELINE.md`
  Phase 1 + Phase 2 — hubspot-scribe upstream + fleet-radar-skill
  consumer downstream replaces the prioritization phase.

## Owner contact

Author: Victor Grosjean. Reviewer: Andrew Cornish (Sales).
Issues: `agent-skills` repo with label `scribe:hubspot`.
