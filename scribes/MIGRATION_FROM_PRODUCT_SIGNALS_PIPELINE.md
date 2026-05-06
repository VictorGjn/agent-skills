# Migration: product-signals-pipeline → scribes + consumer skills

> **Drafted:** 2026-05-03. Status: design. The existing
> `product-signals-pipeline` skill is monolithic (fetch + categorize +
> prioritize + deliver). Scribes architecture decomposes this into a
> producer layer (scribes) + substrate (CE brain) + consumer skills.

## TL;DR

Today's `product-signals-pipeline` splits into:

| Phase today | Replaced by |
|---|---|
| ❶ Fetch from 5 sources | `granola-scribe`, `slack-scribe`, `gmail-scribe` (OnWatch label), `notion-scribe`, `atlassian-scribe` (Jira HELP) |
| ❷ Categorize into 15 themes | `fleet-radar-skill` consumer (themes applied at delivery time) |
| ❸ Prioritize via HubSpot | `fleet-radar-skill` + `opportunity-builder-skill` (consume `hubspot-scribe` events from brain) |
| ❹ Deliver Fleet Radar (Monday) | `fleet-radar-skill` |
| ❺ Deliver Opportunities (Notion) | `opportunity-builder-skill` |

**Scribes are neutral producers.** They emit raw entity-tagged events.
**Consumer skills are opinionated.** They apply themes, prioritization,
formatting, and delivery.

The 15 canonical signal themes (currently at
`~/.claude/skills/product-signals-pipeline/references/themes.md`)
**stay where they are**. Consumer skills import them. Scribes ignore
them entirely.

## Why decompose

1. **Reuse.** Today's product-signals-pipeline is bound to its 5 fetch
   sources and 2 delivery formats. Scribes are reusable across
   consumers (the Anthony Veder pre-call brief consumer and the
   Monday Fleet Radar consumer both read the same brain entities; both
   benefit when granola-scribe ships).
2. **Independent versioning.** Adding HubSpot deal property X today
   requires a product-signals-pipeline release. With scribes,
   hubspot-scribe ships independently and consumers automatically
   benefit.
3. **Cross-source merge.** Today's pipeline can't easily say
   "Anthony Veder says X in Granola and Y in HubSpot — what's the full
   picture?" because each source produces independent Notion rows. The
   brain merges per entity by design.
4. **Consumer optionality.** The brain becomes the substrate for many
   consumer flavors — Fleet Radar (Monday), Opportunity (continuous),
   pre-call brief (on-demand), competitor digest (weekly), etc. Today's
   monolith is locked into 2 consumers.

## What stays the same

- **The 15 canonical themes** — used by `fleet-radar-skill`. Same
  taxonomy, same priority overrides.
- **The Notion Signals DB and Opportunities DB** — `opportunity-builder-skill`
  posts to them. Format stays.
- **Monday digest cadence and format** — `fleet-radar-skill` delivers
  Monday morning. `proactive-brief` skill is the weekday daily.
- **HubSpot overlay logic** — same fleet-extension milestone tagging
  (Anthony Veder = no-extension, KCC/Odfjell/Hafnia/LDC = priority).

## What changes

| Thing | Today | Tomorrow |
|---|---|---|
| Where signals come from | Direct API calls per Monday run | scribes pushed events to brain over the past N days |
| Where themes are applied | At fetch time (per signal) | At delivery time (per consumer) |
| Where HubSpot context is overlaid | At categorize time | At consumer time (consumer reads hubspot-scribe events from brain) |
| Where freshness decays | Not modeled | CE freshness_policy (per source-type half-life) |
| Cross-source merge ("what does Granola + HubSpot + Slack say about $entity?") | Manual stitching | Automatic at brain query time |
| Adding a new source | Edit product-signals-pipeline | Ship new scribe; consumers don't change |
| Adding a new delivery format | Edit product-signals-pipeline | Ship new consumer skill; scribes don't change |
| Backfill 90 days | Re-run pipeline | Re-run each scribe with `--since` |

## Step-by-step migration

### Phase 1 — scribe parity (weeks 1–2 post-YC)

Ship the scribes that cover product-signals-pipeline's 5 sources:

| Today's source | Replacement scribe | Notes |
|---|---|---|
| OnWatch email (Gmail label) | `gmail-scribe` (with `--label onwatch` filter) | Per memory `reference_onwatch_briefings`: HTML format started ~Oct 2025, strip base64 to shrink 15MB → 260KB |
| Granola meetings | `granola-scribe` | Already drafted in this folder |
| Notion meetings | `notion-scribe` | Reads from the Meetings DB per `~/.claude/skills/notion-meetings-db/` |
| Jira HELP | `atlassian-scribe` (with `--project HELP` filter) | Note: per memory `reference_syroco_tracker`, Linear is the active tracker; Jira HELP is legacy |
| Slack (Monday-only project channels) | `slack-scribe` | Drafted alongside this doc |

After Phase 1, run BOTH old pipeline AND new scribes for a week. Compare
outputs. Confirm parity on a couple of priority clients (KCC, Odfjell).

### Phase 2 — consumer skills (weeks 3–4)

Two consumer skills replace categorize / prioritize / deliver:

**`fleet-radar-skill`** (Monday morning)
- Reads brain via `wiki.ask` for `customer-expansion` + `new-biz` scopes
- Applies 15 themes (LLM-classify each new claim from past 7 days)
- Overlays HubSpot deal context (read hubspot-scribe events for same entities)
- Generates Monday digest with priority overrides per
  `~/.claude/skills/product-signals-pipeline/references/themes.md` §"Priority override rules"
- Posts to Notion Signals DB + Slack #digest

**`opportunity-builder-skill`** (continuous, weekly summary)
- Reads brain for entities with N+ recent claims
- Groups claims into opportunity candidates
- Posts to Notion Opportunities DB
- Cross-references existing opportunities (don't duplicate)

### Phase 3 — sunset (week 5+)

After 2 weeks of parallel-run with parity confirmed:
- Mark product-signals-pipeline as DEPRECATED in its SKILL.md (same pattern as
  `onwatch-signal-scan`)
- Disable the cron routine
- Keep the skill installed for 1 month as fallback
- Then archive

## Open questions

| # | Question | Owner |
|---|---|---|
| M1 | The OnWatch HTML→entity-event extraction has Syroco-specific shape. Does it belong in `gmail-scribe` (generic) or in a Syroco-specific `onwatch-scribe` (or both — onwatch-scribe extends gmail-scribe)? | Architecture |
| M2 | The 15 themes file is in `product-signals-pipeline` skill. After sunset, where does it live? Ship a `syroco-themes` reference skill? Or absorb into `fleet-radar-skill`? | Long-term ownership |
| M3 | HubSpot prioritization logic ("priority client = KCC/Odfjell/Hafnia/LDC") is hardcoded today. Lives in `client-scoring.md` reference. After scribes, this becomes consumer-specific config. Where does it live? | Long-term ownership |
| M4 | Backfill plan: re-run scribes for past 90 days, then verify the new brain matches Notion Signals from the same period. Tooling? | Implementation |
| M5 | Slack channel-discovery list (16 channels in `slack-channels.md`) — does slack-scribe walk the same list, or does it discover dynamically? Recommend: same list as v0.1, add discovery in v0.2 | slack-scribe author |

## What this migration is NOT

- **Not a rewrite of the 15 themes.** Same taxonomy, just applied at a different stage.
- **Not a rewrite of the HubSpot overlay logic.** Same priority-override rules, applied at consumer time.
- **Not a rewrite of the Notion deliverables.** Same Signals DB and Opportunities DB; consumer skills post to them.
- **Not a Big Bang.** Run both old and new in parallel for 2 weeks before sunset.

## Reference: today's product-signals-pipeline shape

For migrators who didn't write it:

```
Mon-Fri 05 UTC cloud routine + local skill
  ├── Phase 0: signal extraction (5 sources, last 24h on weekdays, last 7d on Monday)
  ├── Phase 1: dedup + entity-tag against client map
  ├── Phase 2: categorize into 15 themes
  ├── Phase 3: prioritize via HubSpot deals + override rules
  └── Phase 4: deliver
        ├── Notion Signals DB rows
        ├── Mon-only: Fleet Extension Radar digest (Slack #digest)
        └── Linked Opportunities (Notion Opportunities DB)
```

After migration:

```
Continuous, scribe-driven
  ├── granola-scribe  (event-stream)
  ├── slack-scribe    (event-stream, hourly)
  ├── hubspot-scribe  (event-stream, hourly)
  ├── gmail-scribe    (event-stream, daily)
  ├── notion-scribe   (event-stream, daily)
  └── atlassian-scribe (event-stream, hourly, low priority)
                ↓
        [CE brain]
                ↓
  Mon 06 UTC: fleet-radar-skill         (replaces categorize+prioritize+Monday-deliver)
  Continuous: opportunity-builder-skill  (replaces categorize+opportunity-deliver)
  On-demand:  brief-skill                (NEW capability — pre-call brief)
  On-demand:  competitor-digest-skill    (NEW capability — weekly competitive landscape)
```

The new column gains capabilities (pre-call brief, competitor digest) for free
as a side effect of decomposition. That's the wedge.
