# Scribe Spec v0.1

> **Status:** draft, 2026-05-03. First worked example (granola-scribe) deferred.

## Why this exists

The `context-engineering` (CE) brain exposes one push-shape ingest button: `wiki.add` on the MCP server. Anything that wants to feed the brain calls that button.

**Scribes** are the family of small, single-purpose helpers that read one upstream source (Granola, Slack, Notion, HubSpot, Linear, Gmail, Pipedream-bridged thing N+1, etc.) and press the button.

CE stays corpus-agnostic. Scribes do the source-specific work. The two are independently versioned, deployed, and replaced.

```
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │  granola-mcp │    │   slack-mcp  │    │  hubspot-mcp │  ← N upstream MCPs
   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
          ▼                   ▼                   ▼
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │granola-scribe│    │ slack-scribe │    │hubspot-scribe│  ← N scribes
   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
          │  presses [add]    │  presses [add]    │  presses [add]
          └────────────┬──────┴────────────┬──────┘
                       ▼                   ▼
              ┌──────────────────────────────────┐
              │     CE.wiki.add (push-shape)     │  ← 1 brain
              │  → events/<date>.jsonl           │
              │  → wiki_init consolidates        │
              │  → audit flags                   │
              └──────────────────────────────────┘
```

The `wiki.add` button already exists (`mcp_server.py:wiki_add`). The brain uses `EventStreamSource` (push-shape Source) under it. Scribes just call the button — no plugin contract, no engine modifications.

## What a scribe is (the minimum)

A scribe is a runnable thing that:

1. **Reads** from one upstream source (typically via the corresponding MCP).
2. **Extracts** zero or more claims per artifact (meeting / message / issue / page / email).
3. **Pushes** each claim as an event to `wiki.add`.
4. **Tracks** what it has already pushed so re-runs are idempotent.

Anything else is implementation choice.

## Scribe contract

### Naming

`<connector>-scribe`. The connector name matches the MCP namespace (e.g. `granola` from `mcp__granola__*`). Reserved scribes:

| Scribe | Source | Notional cadence |
|---|---|---|
| `granola-scribe` | Granola meeting transcripts | nightly |
| `slack-scribe` | Slack channel messages | hourly |
| `hubspot-scribe` | HubSpot CRM updates | hourly or webhook |
| `linear-scribe` | Linear issues + comments | hourly |
| `notion-scribe` | Notion page changes | nightly |
| `gmail-scribe` | Gmail threads matching a label | nightly |
| `granola-folder-scribe` | One Granola folder (scope-narrow variant) | nightly |
| `pipedream-bridge-scribe` | Generic catch-all for Pipedream-mediated MCPs | per-source |

### Event schema (output)

Every event a scribe emits MUST satisfy CE's event contract (`scripts/wiki/events.py`):

```json
{
  "schema_version": "1.0",
  "ts": 1714742400,
  "source_type": "<connector>",
  "source_ref": "<stable upstream ref>",
  "file_id": "<connector>-<artifact-id>",
  "claim": "<1–3 sentences>",
  "entity_hint": "<owning entity slug | null>",
  "embedding_id": null
}
```

Required keys: `source_type`, `source_ref`, `file_id`, `claim`. The scribe MUST set these. `ts`, `entity_hint`, `embedding_id` are optional but strongly encouraged.

Rules:
- `source_type` MUST be the scribe's connector name (`granola`, `slack`, …) — and MUST be registered in `freshness_policy.HALF_LIVES` before the scribe ships, otherwise every event from this scribe falls through to the 60-day default.
- `source_ref` MUST be stable across re-runs (so audit can de-duplicate citations).
- `file_id` is the cross-run dedup key — see *Idempotency* below.
- `entity_hint` is what the librarian uses to group events into pages. Without it, events are silently dropped (`wiki_init.consolidate()`). **Setting a sensible hint is the scribe's hardest job.**

### Pushing events

Scribes push via `wiki.add` MCP, not by writing the events log directly:

```jsonc
// MCP call
{
  "tool": "wiki.add",
  "arguments": {
    "events": [
      { "source_type": "granola", "source_ref": "...", "file_id": "...",
        "claim": "...", "entity_hint": "..." }
    ],
    "brain": "/path/to/brain"  // optional, falls back to CE_BRAIN_DIR env
  }
}
```

`wiki.add` is atomic per batch (post PR #28 M3 fix): either all events in the batch land, or none do. Scribes should batch in groups of ≤100 events for memory + latency reasons.

### Idempotency

Re-running a scribe MUST NOT duplicate events. **CE does not deduplicate events downstream** — `wiki.add` / `EventStreamSource.emit_events` appends every event it receives, and `wiki_init.consolidate()` only groups by `entity_hint` (not by `file_id`). Source-side dedup is the scribe's responsibility.

Two valid implementations:

1. **State-file dedup** (preferred for unbounded streams): scribe persists a `state.json` with the last processed upstream cursor (`last_processed_at`, `last_meeting_id`, etc.) and only fetches newer artifacts on re-run. Recommended default.
2. **Content-hash dedup with source-side filtering** (for bounded universes): scribe enumerates the full source each run, computes `file_id = sha256(<connector-id>:<content-hash>)`, then **reads the existing events log and skips any `file_id` already emitted** before calling `wiki.add`. Without that read-before-emit step, re-runs WILL bloat entity pages with duplicate claims.

State files live at `~/.claude/scribes/<scribe-name>/state.json` by default; configurable.

### Configuration

Scribes accept config via (in priority order):

1. CLI flags (`--brain`, `--since`, `--scope`, `--dry-run`).
2. Environment variables (`CE_BRAIN_DIR`, `<CONNECTOR>_TOKEN`, `<CONNECTOR>_WORKSPACE`).
3. A per-scribe config file at `~/.claude/scribes/<scribe-name>/config.toml`.

Auth tokens travel via env var, never in the config file. Pipedream-mediated MCP auth handles the upstream side; scribes just need the MCP endpoint.

### Cadence

Three patterns, scribe declares which it supports:

| Cadence | When to use | How it runs |
|---|---|---|
| **On-demand** | Ad-hoc backfills, one-shot ingestion | `python ingest.py --since 2026-04-01` |
| **Cron** | Daily / hourly batch refresh | `/schedule` invocation, or external cron |
| **Webhook** | Real-time (Slack messages, HubSpot updates) | Pipedream webhook → scribe HTTP endpoint → push |

Most scribes start at on-demand, graduate to cron, and only go webhook when latency matters. Webhook mode is its own can of worms (re-delivery, ordering, replay) and is out of scope for v0.1.

## Quality tiers (claim extraction)

The scribe's hardest decision is *what counts as a claim*. Four tiers, scribe declares its tier in metadata:

| Tier | Behavior | Cost | Quality |
|---|---|---|---|
| **T0 — title-only** | One event per artifact. claim = title or first paragraph. | Zero | Low — finds entities, misses claims |
| **T1 — rule-based** | Multiple events per artifact via deterministic chunking (paragraph splits, speaker turns, regex on commitment language). | Zero | Medium — finds explicit decisions, misses inferences |
| **T2 — LLM-extracted** | Each artifact → LLM call → structured claims (JSON list of `{claim, confidence, kind, entities[]}`). | $0.001–0.05 per artifact | High — finds inferences, real decisions |
| **T3 — LLM + cross-artifact consolidation** | T2 plus a second pass that detects supersession ("the v2 pricing replaces v1"), entity dedup, contradiction. | $$ per artifact | Best — produces decision-continuity chains directly |

A scribe MAY support multiple tiers and let the operator choose via `--quality T0|T1|T2|T3`. Default tier should be the cheapest one that produces useful output for the source — T1 for transcripts, T0 for stand-ups, T2+ for high-stakes customer calls.

### v0.2 extension — focus-driven extraction (pull-shape only)

Push-shape scribes always extract everything (corpus growth is the goal). Pull-shape components (Normalizer / Fetcher per the 7-noun taxonomy in `Anabasis/plan/board-deck-discovery-mining.md`) MAY accept a `--focus` parameter to scope extraction at the consumer's request:

```
--focus claims | decisions | numbers | open-questions | entities
```

Crossed with quality tiers, this gives a (tier × focus) cost matrix. The consumer states intent; the component runs only the LLM work needed to satisfy it. Push-shape scribes ignore `--focus` (or fail loudly when given one — operator confusion is worse than wasted CPU).

Witness: `unstructured-text-normalizer` in `board-deck-from-brain-dump` (2026-05-06).

## entity_hint heuristics

Without `entity_hint`, events drop on the floor at `wiki_init.consolidate()`. Each scribe must define its hint policy. Recommended defaults:

| Source | Hint heuristic |
|---|---|
| Granola | First non-`syro.co` participant email domain → company slug. Internal-only meeting → slugified title. |
| Slack | Channel name → topic slug. Thread-rooted message → first 5 words slugified. |
| HubSpot | Company record → `<company>-<id>`. Contact-only event → `<contact>-<id>`. |
| Linear | Project + issue title → `<project>-<issue-slug>`. |
| Notion | Page title → slug. Database row → `<db-name>-<row-id>`. |
| Gmail | Sender domain → company slug for external; subject line for internal. |

Hints SHOULD be lowercase-kebab-case for filesystem-safe slugs. Hints MAY collide across scribes (e.g. `acme` from HubSpot AND from Granola) — that's a feature, the librarian merges them onto one page.

## Telemetry

Every scribe SHOULD emit JSONL telemetry on stderr (matches CE's pattern in `mcp_server.py:_emit_telemetry`):

```jsonl
{"event": "scribe.run.start", "scribe": "granola-scribe", "since": "2026-04-01"}
{"event": "scribe.artifact.processed", "scribe": "granola-scribe", "artifact_id": "...", "claims": 3}
{"event": "scribe.run.end", "scribe": "granola-scribe", "artifacts": 34, "events": 48, "duration_ms": 12340}
```

Operators tail telemetry to verify "the scribe ran and produced N events" without reading the brain.

## Auto-discovery (out of spec scope)

The runtime hosting the scribes (Pipedream, a local launcher, Claude Code's `/skills` infra, etc.) is responsible for:

- Listing connected MCPs.
- Querying a scribe registry — `npx skills search <connector>-scribe` against `victorgjn/agent-skills` and `syrocolab/company-knowledge`.
- Suggesting installation (`npx skills add <scribe-name>`).
- Wiring auth tokens through.

**This spec does not prescribe the discovery mechanism.** Scribes only need to:

1. Be installable via the standard `npx skills add` flow.
2. Declare their connector name (= MCP namespace) in skill metadata so the runtime can match.
3. Document required env vars + config in the skill README.

The runtime layer is someone else's problem. This spec defines the contract a scribe respects so that any runtime can host it.

### Skill metadata required for discovery

Every scribe ships with a `SCRIBE.toml` or equivalent block in the skill manifest:

```toml
[scribe]
name = "granola-scribe"
connector = "granola"             # matches MCP namespace mcp__granola__*
mcp_required = ["granola"]        # MCP namespaces this scribe needs
events_pushed_to = "wiki.add"     # CE MCP tool
quality_tiers = ["T0", "T1"]      # tiers this scribe implements
default_tier = "T0"
cadence_supported = ["on-demand", "cron"]
half_life_key = "granola"         # must be registered in CE.HALF_LIVES
```

Discovery layers read this block to confirm the scribe matches the connected MCP.

## Worked example: granola-scribe (T0)

**Inputs:**
- `mcp__granola__list_meetings(time_range)` → list of meetings
- `mcp__granola__get_meeting_transcript(meeting_id)` → transcript blob

**Per meeting:**
1. Determine `entity_hint`:
   - Filter participants to non-`syro.co` domains.
   - If any: take first → slugify domain (`anthonyveder.com` → `anthony-veder`).
   - Else: slugify title (`Product Planning (Delivery & Prio)` → `product-planning-delivery-prio`).
2. Determine `scope`:
   - External entity (any non-`syro.co` participant) → `leads-and-customers`.
   - Internal-only → `default`.
3. Build event:
   ```json
   {
     "source_type": "granola",
     "source_ref": "granola://meeting/<id>",
     "file_id": "granola-<id>",
     "claim": "<title>. <participants>. <first-280-chars-of-transcript>",
     "entity_hint": "<computed>",
     "ts": <meeting_date_epoch>
   }
   ```
4. Push via `wiki.add` (batch of ≤100).

**State:** `state.json` tracks `last_meeting_processed_at`. Re-runs only fetch meetings newer than that.

**Limitations of T0:** every meeting becomes one event. "Anthony Veder uses Napa" is buried inside the claim. Useful for entity discovery (you can `wiki.ask anthony-veder` and see all calls), insufficient for fact retrieval ("which prospects use Napa?" needs T2+).

**Path to T1:** split transcript by `Me:` / `Them:` turns; one event per ≥30-word turn whose first sentence ends with a commitment verb (`will`, `won't`, `we use`, `we don't`). Same `entity_hint`, finer-grained claims.

**Path to T2:** feed each transcript to an LLM with a JSON-schema prompt. ~200 calls × $0.01 = $2 to backfill 30 days. Negligible compared to founder-time saved.

## Worked example sketches (deferred)

### slack-scribe

- Source: `mcp__claude_ai_Slack__slack_search_*` + `slack_read_thread`.
- Per channel: scribe walks recent threads.
- `entity_hint`: thread topic (first 5 non-stopword tokens of the root message) OR channel name for short messages.
- `scope`: per-channel mapping (e.g. `#sales` → `leads-and-customers`, `#general` → `default`).
- Cadence: hourly cron.
- Tier: T1 (split per thread, one event per substantive message).

### hubspot-scribe

- Source: `mcp__claude_ai_HubSpot__search_crm_objects` filtered to recently-modified.
- Per company/deal/contact updated since last run: emit one event with the changed properties.
- `entity_hint`: `<company-domain>` for company-level events, `<deal-id>` for deal-level.
- `scope`: `customers` (default).
- Cadence: hourly cron OR webhook (HubSpot supports it).
- Tier: T0 (the property diffs ARE the claims; no extraction needed).

### linear-scribe

- Source: `mcp__claude_ai_Linear__list_issues` filtered to recently-updated.
- Per issue: emit events for issue body, comments, status transitions.
- `entity_hint`: `<team>-<issue-slug>` OR project-level when issue is part of a project.
- `scope`: `engineering` (or `product` for PM-led teams).
- Cadence: every 15 min (development pace).
- Tier: T1 (issue body is structured; comments are conversational).

## Operational concerns

### Backpressure

`wiki.add` is fast (atomic JSONL append) but downstream `wiki_init` consolidates can be slow on large brains. Scribes SHOULD batch in groups of ≤100 events. Multiple scribes running in parallel SHOULD NOT block each other — events log is append-only and writers don't lock readers.

### Failure modes

| Failure | Behavior |
|---|---|
| Upstream MCP unavailable | Skip this run, emit telemetry, exit non-zero. Cron retries on schedule. |
| One artifact fails to parse | Skip artifact, emit warning telemetry, continue. |
| `wiki.add` rejects batch | Atomic batch — none landed. Emit telemetry with `appended_before_error=0`, retry the batch on next run (state file unchanged). |
| Auth expired | Hard-fail with operator-facing error. Don't silently skip. |

### Versioning

Scribes have their own semver, independent of CE. A scribe MUST NOT emit events that violate CE's current `EVENT_SCHEMA_VERSION`. When CE bumps the event schema (rare; events forward-migrate per `phase-1.md` §1.2.1), all scribes need a coordinated bump.

A scribe SHOULD declare the CE event schema version it targets:

```toml
[scribe.compatibility]
ce_event_schema = "1.0"
```

## Open questions (v0.1 → v0.2)

- **Multi-tenancy:** one operator runs N scribes against ONE brain. What about N brains (per-customer scope segregation)? Current spec assumes one brain per scribe instance.
- **Replay-on-recover:** if a scribe crashes mid-batch and resumes, do we replay the whole batch or trust state-file dedup? Current spec assumes state-file dedup is canonical.
- **Cross-scribe entity merge:** if granola-scribe and hubspot-scribe both produce `acme` events, how do we ensure they land on the same wiki page? Current answer: shared `entity_hint`. Future: explicit entity-id resolution.
- **Streaming vs batch:** webhook scribes need a small HTTP server. Where does it live (Pipedream-side? scribe-side? a shared receiver)? Out of v0.1 scope.
- **Skill registry conventions:** `npx skills search` is the assumed surface, but the registry format isn't standardized yet. Coordinate with `victorgjn/agent-skills` + `syrocolab/company-knowledge` README updates.

## Reference: minimal scribe directory layout

```
granola-scribe/
├── SCRIBE.toml              # discovery metadata (above)
├── README.md                # how to run, env vars, config
├── ingest.py                # main entry point
├── claim_extraction/
│   ├── t0_title_only.py
│   └── t1_rule_based.py
├── tests/
│   ├── test_entity_hint.py
│   └── test_idempotency.py
└── state/                   # gitignored — operator's state lives here
    └── .gitkeep
```

`ingest.py --help` SHOULD print the supported flags; `ingest.py --dry-run` SHOULD output the events it would push without calling `wiki.add`.

## What this spec is NOT

- **Not a runtime spec.** Pipedream / `/schedule` / cron / etc. host scribes. The spec defines what scribes look like; the runtime layer is independent.
- **Not a registry spec.** `npx skills` and the hosting repos already define skill packaging.
- **Not a CE engine change.** CE doesn't need to know about scribes. It already exposes `wiki.add`. Scribes plug in over the existing surface.

## Related artifacts

- `context-engineering/scripts/wiki/events.py` — event schema authority
- `context-engineering/scripts/wiki/source_adapter.py:EventStreamSource` — push-shape Source the scribe ultimately writes through
- `context-engineering/scripts/mcp_server.py:wiki_add` — MCP tool scribes call
- `context-engineering/plan/phases/phase-1.md` §1.2 — wiki page schema downstream consumers
- `context-engineering/plan/prd-closed-loop.md` — closed-loop PRD; AC1 is the scribe contract from the brain's side
