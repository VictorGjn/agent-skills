# Scribe Plan v0.1 — full catalog, use cases, multi-POV audit

> **Companion to:** `SPEC.md`. The spec defines what a scribe IS; this plan defines WHICH scribes we want, in what order, for what user, and what we're risking by building them.
>
> **Drafted:** 2026-05-03. Author: Victor + Claude session. Status: draft for review, no code yet.

---

## 1. The frame

**Pain we're attacking.** The "company brain" lives in 17 disconnected systems today. Every founder/PM/CSM hand-stitches context across Granola + Slack + HubSpot + Linear + Notion + Gmail before every customer call, every PRD, every renewal. The cost is hours/week × every employee.

**The thesis.** N scribes → 1 brain. Each scribe pulls from one source, pushes to `wiki.add`. Brain entities (Anthony Veder, Theyr, Acme, Sinbad, "follow-route", "fleet-alerts") accumulate claims from every source automatically. Operators query the brain via `wiki.ask` and stop hand-stitching.

**Today's state.** Brain shipped (PRs #21–#28, on `main`). Spec for scribes shipped (this folder, `SPEC.md`). Zero scribes built. The next ~4 weeks decides whether this is a substrate or a product.

---

## 2. Scribe catalog (17 connectors → 17 scribes)

Sourced from `/context` — every MCP currently available to Claude Code that maps to a "company brain" data source.

| # | Scribe | Source MCP | Tier (default) | Cadence | Primary persona | Killer use case |
|---|---|---|---|---|---|---|
| 1 | **granola-scribe** | `mcp__granola__*` | T1 | nightly | Founder / CPO / CSM | "Brief me on $prospect before this call" |
| 2 | **slack-scribe** | `mcp__claude_ai_Slack__*` | T1 | hourly | CSM / All | "What's been said about $customer in the last week?" |
| 3 | **hubspot-scribe** | `mcp__claude_ai_HubSpot__* + mcp__syroco__hubspot__*` | T0 | hourly | Sales / CSM | "Show me every deal touching Acme last quarter" |
| 4 | **linear-scribe** | `mcp__claude_ai_Linear__* + mcp__syroco__linear__*` | T1 | 15-min | Eng / PM | "Why did we close $ticket last sprint?" |
| 5 | **notion-scribe** | `mcp__claude_ai_Notion__* + mcp__syroco__notion__*` | T0 | nightly | All | "Find the latest spec referencing $feature" |
| 6 | **gmail-scribe** | `mcp__claude_ai_Gmail__* + mcp__gmail__* + mcp__gmail-personal__*` | T1 | nightly | Founder / Sales | "What was the last thread with $contact about?" |
| 7 | **calendar-scribe** | `mcp__claude_ai_Google_Calendar__*` | T0 | 6-hourly | Founder / EA | "What's on my calendar today and what should I know?" |
| 8 | **drive-scribe** | `mcp__claude_ai_Google_Drive__* + mcp__syroco__google_drive__*` | T0 | nightly | All | "What's the most recent deck about $partner?" |
| 9 | **mixpanel-scribe** | `mcp__claude_ai_Mixpanel__*` | T0 | daily | PM / Growth | "What features did $customer use this month?" |
| 10 | **atlassian-scribe** (Jira+Confluence) | `mcp__claude_ai_Atlassian__* + mcp__atlassian__*` | T0 | hourly | Eng (if Jira) | "Find every ticket referencing $component" |
| 11 | **vercel-scribe** | `mcp__claude_ai_Vercel__*` | T0 | per-deploy | Eng / Ops | "Did $deploy break? Who shipped it?" |
| 12 | **figma-scribe** | `mcp__figma__*` | T0 | nightly | Design / PM | "Find the latest mockup for $feature" |
| 13 | **excalidraw-scribe** | `mcp__claude_ai_Excalidraw__*` | T0 | nightly | All (whiteboard) | "Find the diagram from last week's planning" |
| 14 | **pageindex-scribe** | `mcp__pageindex__*` | T0 | on-demand | All (long docs) | "Pull the relevant section from the 200-page RFP" |
| 15 | **mistral-scribe** | `mcp__syroco__mistral_ai__*` | T0 | daily | Internal | "What models did we batch-call this week?" |
| 16 | **n8n-bridge-scribe** | `mcp__n8n-mcp__*` | T0 | per-trigger | Ops | Generic catch-all for n8n-orchestrated upstream sources |
| 17 | **html-to-figma-scribe** | `mcp__claude_ai_html_to_figma__*` | T0 | on-demand | Design | "What did we import to Figma from $url last month?" |

**Notes**
- "Tier (default)" = quality tier per `SPEC.md` §"Quality tiers". Scribes can offer higher tiers; default is what ships first.
- Some sources have BOTH `mcp__claude_ai_*` AND `mcp__syroco__*` versions (HubSpot, Linear, Notion, Drive). One scribe per source — the scribe picks whichever auth path the operator has configured. The MCPs are interchangeable from the scribe's perspective.
- `gmail-personal` is intentionally separate: founder-personal mailbox should not bleed into the company brain. If a personal gmail-scribe runs, it pushes to a SEPARATE brain or a `personal` scope.

---

## 3. Use case matrix (persona × scribe)

Each cell = the question that becomes answerable when that scribe ships, for that persona. **Cells reading "—" mean the scribe doesn't unlock new value for that persona; not every scribe matters to every persona.**

| | Founder/CEO | CPO/PM | CSM/Account | Sales | Engineering | Design | Ops/Admin |
|---|---|---|---|---|---|---|---|
| **granola** | Pre-call brief; investor recall | Decision archaeology; PRD source | Account history | Customer-said-X | Tech-decision recall | — | — |
| **slack** | What's the team buzzing on? | Feature signal triangulation | Customer support narrative | Champion identification | Incident timeline | Design feedback threads | Audit trail of decisions |
| **hubspot** | Pipeline glance | Customer profile depth | Stage transitions | Stage transitions, deal value | — | — | Renewal calendar |
| **linear** | Eng velocity | Feature → ticket → ship traceability | "Did we ship $customer's ask?" | "When does $feature ship?" | Sprint review prep | Design ticket status | — |
| **notion** | Strategy doc retrieval | Spec / PRD / OKR retrieval | Onboarding doc lookup | Sales playbook lookup | Architecture doc retrieval | Design doc retrieval | Policy doc retrieval |
| **gmail** | Investor thread recall | External feedback recall | Customer thread history | Lead conversation thread | Vendor comms | Feedback threads | Vendor renewals |
| **calendar** | Day brief | — | Day brief | Day brief, prospect-rotation | — | — | Coverage planning |
| **drive** | Deck recall | Spec doc PDF retrieval | Customer-shared docs | Pitch-deck history | Diagram archive | Asset history | Policy archive |
| **mixpanel** | Top-line product usage | Feature adoption signal | Customer health by usage | Customer expansion signal | — | UX flow signal | — |
| **atlassian** | — | — | — | — | Ticket archaeology (if Jira) | — | Compliance ticket trail |
| **vercel** | Ship velocity | Release cadence | Outage timeline | — | Deploy history, rollback prep | — | Cost/usage tracking |
| **figma** | — | Mockup-to-spec linkage | — | — | Design handoff history | Design version history | — |
| **excalidraw** | — | Whiteboard-to-decision | — | — | Architecture diagram | — | — |
| **pageindex** | Long-doc Q&A | Long-doc Q&A | RFP retrieval | RFP retrieval | Standard-spec lookup | Brand guidelines | Compliance doc retrieval |
| **mistral** | — | — | — | — | Model-call cost tracking | — | LLM spend audit |
| **n8n-bridge** | Generic | Generic | Generic | Generic | Generic | Generic | Generic |
| **html-to-figma** | — | — | — | — | — | Web-to-design import history | — |

**The reading.** Top-3 scribes by persona-coverage:
1. **slack-scribe** — touches every persona except design
2. **notion-scribe** — touches every persona
3. **granola-scribe** — touches every customer-facing persona

These three should ship first.

---

## 4. CPO audit

> *Hat: senior product manager. Lens: value, moat, prioritization, risk of feature-bloat.*

### 4.1 What's the wedge?

The brain itself isn't the wedge. **Everyone has a brain.** Notion has a brain. Glean has a brain. Slack AI has a brain. ChatGPT-with-MCP has a brain.

The wedge is **multi-source merge with provenance**. When granola-scribe says "Anthony Veder uses Napa" and 4 weeks later hubspot-scribe says "Anthony Veder deal moved to negotiation," the brain merges those onto ONE entity page and the operator sees the timeline. **Nobody else does this end-to-end with citations.**

### 4.2 Killer demo

Single screen, single query, five sources merged:

```
> wiki.ask "Anthony Veder"

# anthony-veder
- 25-vessel North Sea/Baltic gas tanker fleet  _(via granola, 2026-04-24)_
- Currently uses Napa, dissatisfied  _(via granola, 2026-04-24)_
- Niche needs: ice conditions, controllable pitch, coastal weather  _(via granola, 2026-04-24)_
- HubSpot deal: $120k ARR, stage=negotiation  _(via hubspot, 2026-04-30)_
- Last email: "thanks, will discuss internally"  _(via gmail, 2026-04-25)_
- Mentioned in #sales 7×, last 2026-04-29  _(via slack, 2026-04-29)_
- LinkedIn-cited as reference for Christiania Shipping prospect  _(via slack, 2026-04-30)_
```

That's the demo. Three scribes (granola, hubspot, slack) make it real. Five make it impressive.

### 4.3 Prioritization

**Ship-order signal: persona-coverage × cadence × tier-feasibility.**

| Tier | Scribe | Why |
|---|---|---|
| **T1 (next 2 weeks)** | granola | Founder pain is loudest; demo unlocks here |
| | slack | Highest persona-coverage |
| | hubspot | Sales/CSM is the fastest revenue tie |
| **T2 (weeks 3–4)** | linear | Eng triage; PRD-to-ship traceability |
| | notion | Doc retrieval; everyone uses Notion |
| | gmail | External thread context |
| **T3 (month 2+)** | calendar, drive, mixpanel, atlassian, vercel | Long tail; build-on-demand |
| **Won't (now)** | figma, excalidraw, pageindex, mistral, n8n-bridge, html-to-figma | Niche; build only if a real user complains |

### 4.4 Anti-patterns to avoid

- **Building 17 thin scribes.** Better to have 3 deep T2-quality scribes than 17 T0 scribes that just dump titles.
- **Skipping `entity_hint` work.** Without good hints, every scribe drops events on the floor. The entity-hint heuristic per source is THE work — not the MCP wiring.
- **Treating the brain as the product.** The brain is substrate. The scribes are the moat. Product surface is `wiki.ask` UX (slash command, one-click in Granola, Slack bot, etc.) — that's the next layer up after scribes.
- **Building "the perfect scribe."** Ship T0 fast, upgrade to T1/T2 when a user asks for it. Start with title-only.

### 4.5 What the spec deferred and shouldn't

- **Cross-scribe entity merge** — when granola says `acme` and hubspot says `acme-corp-1234`, they need to merge. Today's `entity_hint` is per-scribe; cross-scribe needs an alias resolver. **This will be the #1 user complaint within 7 days of shipping >1 scribe.**
- **Pinned entity list** — operator should be able to say "track Anthony Veder, Theyr, Jupi, Christiania" and have all scribes prefer those hints.
- **Brain UX** — `wiki.ask` is a CLI today. Founders want a slash command in Granola/Slack, or a one-click "brief me" in Calendar.

---

## 5. CTO audit

> *Hat: technical founder. Lens: auth, rate limits, PII, reliability, storage, privacy, ops.*

### 5.1 Auth surface

17 scribes × N MCPs = a sprawling auth surface. Each scribe needs:
- Upstream MCP connection (handled by Pipedream / Syroco Connect for org-wide; per-user OAuth for personal).
- CE brain access (file path; or `wiki.add` MCP token).

**Risks:**
- **Token sprawl.** 17 scribes × 1 token each × N users. Rotating any one token without breaking others requires per-scribe config isolation.
- **Scope creep.** A scribe with `slack:read` is fine; a scribe accidentally given `slack:write` is a phishing vector. Scribe SCRIBE.toml MUST declare minimum-required scopes; runtime MUST enforce.
- **Service accounts vs per-user OAuth.** Service account = simpler ops, scope-explosion risk. Per-user OAuth = scope minimization, ops complexity. Recommendation: **per-user OAuth for read scribes; service accounts only for shared write paths (`wiki.add`).**

### 5.2 Rate limits and backpressure

Top 5 risk MCPs:
- **Slack search** — 100 req/min, more aggressive on enterprise. slack-scribe with hourly cron walking 50 channels = 50 req/run = fine. But a backfill (`--since 2026-01-01`) on hundreds of channels = bursty, will throttle.
- **HubSpot search** — 100 req/10s burst, 250k/day. Fine until a portal grows.
- **Linear** — generous but 100 req/min cap. linear-scribe at 15-min cadence over 5 teams = trivial.
- **Granola** — unknown public limit. Anecdotal: query_granola_meetings is slow (~5–15s per call).
- **Gmail** — 250 quota/sec/user; backfill on 30-day mailbox is fine, lifetime backfill needs batching.

**Recommendation:** every scribe MUST implement exponential backoff with jitter on 429. State-file dedup (`SPEC.md` §Idempotency) handles re-runs. Webhook scribes are post-v0.1.

### 5.3 PII and the brain-as-honeypot problem

Meeting transcripts contain customer names, deal values, salaries, candidate evaluations. Slack DMs contain personnel discussions. HubSpot has contact phone numbers. **The brain becomes a centralized PII store the moment 2 scribes ship.**

Concrete obligations:
- **Encryption at rest.** brain/ on disk MUST be on an encrypted volume. Not optional.
- **Access control.** `wiki.ask` MUST authenticate the caller and gate scope. Today's `wiki.ask` accepts a `scope` arg with no auth — fine for local dev, breaks the moment it's exposed over network MCP.
- **Right-to-be-forgotten (GDPR).** Events log is append-only. Need a tombstone pattern: `delete_event` writes a tombstone JSONL row that suppresses matching events at read time. Wiki regenerates without the tombstoned events on next consolidation.
- **Audit logging.** Who queried `wiki.ask` for entity `<contact>`? Today's telemetry covers tool calls; need to extend to query-target tracking for sensitive scopes.
- **Scope-level encryption.** `personal` scope SHOULD be encrypted with a separate key from `default`. Multi-key isn't in CE; it's a follow-up.

### 5.4 Reliability and ops

**17 scribes × 24 cron-runs/day × 365 days = 148k invocations/year.** Per-scribe failure rate of 0.1% = 148 failures/year = 1 every 2.5 days. This is fine if:
- Each scribe is independently scheduled (one bad scribe doesn't block others).
- Failure telemetry is operator-visible (something's wrong → operator sees it).
- Auto-restart / next-cron-tick recovery is the default.

Recommendation: **scribes run via Pipedream cron OR Claude Code's `/schedule`, never via a shared launcher.** Isolation is cheap; coupled failure modes are expensive.

### 5.5 Storage

Per-scribe per-day event volume estimates (back-of-envelope for a Syroco-sized org):

| Scribe | Events/day | Annual rows |
|---|---|---|
| granola | 5–10 | ~3k |
| slack | 100–500 | ~150k |
| hubspot | 50–200 | ~50k |
| linear | 50–200 | ~50k |
| notion | 20–100 | ~25k |
| gmail | 50–200 | ~50k |
| calendar | 5–10 | ~3k |
| (long tail × 10) | 100 total | ~30k |
| **Total** | ~400–1300/day | **~360k/year** |

`events/<date>.jsonl` at avg 500 bytes/event = ~180MB/year. Trivial.

`wiki/<slug>.md` grows with entity count, not event count. ~5k–10k entities for a 50-person org × ~10kB/page = ~50–100MB. Trivial.

**No archival/compaction needed at v1.** Become a problem only at >100k entities or 7-figure event count. CTO worry list does NOT include storage.

### 5.6 What the spec deferred and shouldn't

- **Auth scope declarations in SCRIBE.toml.** SPEC mentions `mcp_required` but doesn't declare scopes. Need explicit `scopes_required = ["slack:read", "slack:channels:list"]`. Adds runtime enforcement.
- **Brain hosting model.** SPEC assumes local brain. Real deployments need a hosted option — either Anabasis-hosted, customer-hosted, or hybrid. **This is the #1 enterprise blocker.**
- **Webhook mode.** Punted to v0.2 in SPEC. CSM use cases (Slack support thread → instant brain update) need it. Not blocker for first-3-scribe ship.

---

## 6. ADMIN audit

> *Hat: head of ops / IT / security. Lens: install, auth, cost, onboarding, offboarding, audit.*

### 6.1 Install + uninstall lifecycle

Today's install path: `npx skills add granola-scribe`. That's fine for one founder. Doesn't scale to a team.

**Org-rollout questions:**
- **Who decides which scribes the org installs?** Default-set per role? Self-service per user?
- **How is config propagated?** Per-user `~/.claude/scribes/<name>/config.toml`? Org-wide `/etc/scribes/`? Pipedream UI?
- **How is uninstall surfaced?** `npx skills remove` exists; does it also clean up cron entries / state files / tokens?

Recommendation: **first ship is single-user. Org-rollout is a v0.2 problem; design for single-user, NOT against it.**

### 6.2 Cost allocation

T0/T1 scribes: free (no LLM).

T2/T3 scribes: ~$0.001–$0.05 per artifact extracted. For granola at T2 with 30 meetings/month/user:
- $0.01/meeting × 30 = **$0.30/user/month for granola alone.**
- Add slack at T1 (rule-based, free) + hubspot at T0 (free) = same.
- 50-person org × granola T2 = $15/month. **Negligible.**

For comparison: Notion AI is $10/user/month. Glean is $40+/user/month. Operating cost for the brain is structurally ~10× cheaper than alternatives.

**Where the cost reality bites:** if every scribe goes T2, and one scribe iterates over a 5k-thread Slack archive backfill, that's 5k LLM calls × $0.005 = $25. One backfill. Per user. **Backfills MUST be explicit and confirmed; cron-driven incremental runs MUST stay cheap.**

Recommendation: scribes MUST default to incremental mode. Backfills require an explicit `--backfill` flag AND a `--max-cost USD` guardrail. Refuse to run without one.

### 6.3 Onboarding

New employee joins. They need:
1. Brain access (read scope = their team's scope).
2. Their personal scribes installed (granola for their meetings, slack for their channels).
3. Their auth tokens wired (their granola, their slack, their gmail).

**Operator effort to onboard one person:** if each scribe takes 5 min × 5 scribes = 25 min/person. For a 10-person/month hire pace = 4 hours/month. Acceptable.

**If this hits 100-person/month:** automation becomes mandatory. Pipedream's auto-discovery (out of SPEC scope, per `SPEC.md` §"Auto-discovery") solves this — when employee X connects Granola, runtime suggests granola-scribe. Per-scribe install = one click.

### 6.4 Offboarding

Employee leaves. Their:
- Future contributions stop (their MCPs disconnect → their scribes can't pull → they error).
- Past contributions remain (events they've ingested are in the log, on entity pages).

**The hard question:** should past contributions be deleted? Most orgs keep them (Slack messages stay after employee leaves). But GDPR right-to-be-forgotten can apply for some claims. Tombstone pattern (CTO §5.3) handles selective deletion.

Recommendation: **default = retain on offboarding; provide tombstone CLI for selective deletion if requested.** Document this in scribe install docs.

### 6.5 Audit trail

Two distinct audit needs:

**Read audit:** who asked the brain about whom? `wiki.ask` calls today are telemetered (PR #28 M2 fix) but only with scope, not with query content. For PII-containing scopes (HR, customer-DMs), MUST log the full query target.

**Write audit:** which scribe pushed which event? Today's events have `source_type` + `source_ref` — the WRITE audit trail is built into the schema by design. Operator can ask "show me every event from `slack-scribe` since 2026-04-01" and grep the events log. ✅

### 6.6 Backup / DR

Brain = events.jsonl + wiki/. Both are flat files.

**Backup is trivial:**
- `git init brain/` per CTO audit recommendation (file already noted in `~/.claude/handoffs/ce_closed_loop_session_synthesis.md` as CTO block #2).
- Daily push to a private GitHub repo per user.
- One-command restore: `git clone brain.git`.

**DR scenario:** scribes' state files are on the same disk. One disk failure = lose state. State files are recreatable from upstream — re-running scribes from scratch produces the same events (idempotent), idempotent dedup eats duplicates. Worst case: one expensive backfill. Tolerable.

### 6.7 Multi-tenant

Single brain per user is v0.1. Inevitable expansion:
- **One brain per company** — already supported via `scope` (`competitive-intel`, `customers`, `engineering`, etc.).
- **One brain per team** — needed for compliance-sensitive teams (HR, legal).
- **Cross-tenant queries** — federation problem. Out of v0.1 scope. Likely never; better answer is "merge the brains."

### 6.8 What the spec deferred and shouldn't

- **Cost guardrails** (`--max-cost`, backfill confirmation). Cheap to add to the SPEC; expensive if a customer's first run pulls $400 of LLM calls before they realize.
- **Tombstone protocol** for GDPR. Not needed for friend-of-the-firm pilots; needed before any EU customer.
- **Brain hosting story** (CTO §5.6). Belongs in `PLAN.md` as a roadmap item; not the SPEC.

---

## 7. Reconciled prioritization

### Tier 1 (next 14 days, post-YC)

**Scribes (Layer 1 — raw extraction):**
1. **granola-scribe v0.1** — T1 default, drafted in `granola-scribe/SKILL.md`.
2. **slack-scribe v0.1** — T1 default, drafted in `slack-scribe/SKILL.md`.
3. **hubspot-scribe v0.1** — T0 default, drafted in `hubspot-scribe/SKILL.md`.

**Enrichers (Layer 2 — stable cross-source linkage, added 2026-05-03):**
4. **voyage-context-enricher v0.1** — answers "captain in Slack → which voyage was active?" by querying voyage system at message-time. Cross-emits `voyage_id`, `vessel`, `voyage_phase` onto events from any scribe touching customer entities. Reasoning: captain signals are useless without knowing which voyage they were about.
5. **captain-signal-enricher v0.1** — tags events from `#project-*` channels with `speaker_role` (captain / csm / ops / shore) using a maintained roster. Stable enrichment, drives downstream priority weighting.

**Cross-cutting CE work:**
6. **Cross-scribe entity merge** (CPO §4.5 #1). Add an alias resolver: `~/.claude/scribes/aliases.toml` maps multiple `entity_hint` strings to one canonical slug. Fixes the inevitable "acme vs acme-corp" merge problem before it happens.
7. **Pinned entity list** (CPO §4.5 #2). Operator declares high-value entities; scribes prefer those hints over freeform.
8. **Tombstone protocol** (CTO §5.3, ADMIN §6.4). Skeleton even before any EU customer; won't slow ship.

### Tier 2 (days 15–30)

7. linear-scribe v0.1 — T1.
8. notion-scribe v0.1 — T0.
9. gmail-scribe v0.1 — T1.
10. **Brain UX layer:** slash command in Granola ("brief me on this meeting's external participants"); Slack bot (`/brain $entity`); Calendar prep brief (autoposted to first calendar event of the day).

### Tier 3 (days 31–60)

11. calendar-scribe.
12. drive-scribe.
13. mixpanel-scribe.
14. atlassian-scribe.
15. vercel-scribe.
16. n8n-bridge-scribe (generic catch-all enables "any-future-source" without a new scribe).
17. **Hosted brain option** (CTO §5.6). Anabasis-hosted brain = enterprise unlock. Out of solo-founder scope; in-scope for the spinout.

### Won't ship (now)

- figma-scribe, excalidraw-scribe, pageindex-scribe, mistral-scribe, html-to-figma-scribe — niche, build only if a user asks.
- **Webhook mode for any scribe** — cron is fine for 95% of use cases. Webhook is post-v1.

---

## 8. Open questions

| # | Question | Owner | Latest-acceptable answer |
|---|---|---|---|
| 1 | Brain hosting model (local-only vs hosted vs hybrid) | CTO + CPO | Before first paying customer |
| 2 | Pricing model for T2/T3 LLM scribes (per-user, per-event, included) | CPO + ADMIN | Before first paying customer |
| 3 | Auto-discovery + auto-install spec (Pipedream side) | Pipedream / runtime owner | Day 60 |
| 4 | Cross-scribe entity merge: rule-based aliases vs LLM-resolved canonicalization | CPO | Day 21 (when 3 scribes ship) |
| 5 | Per-scope encryption keys (HR, legal scopes) | CTO | Before any compliance-regulated customer |
| 6 | Tombstone semantics — what counts as "delete" (event tombstone? entity-page delete? both?) | CTO | Day 30 |
| 7 | Wiki UX surface (CLI? Slack bot? Granola plugin? web UI?) | CPO | Day 21 (after first 3 scribes ship) |

---

## 9. What this plan is NOT

- **Not a code plan.** This is the strategic + ops audit. Code plan is per-scribe (one PR per scribe).
- **Not the SPEC.** SPEC defines the contract; this plan defines the prioritization.
- **Not a YC submission.** YC is tomorrow. This plan is the post-YC build-out roadmap.

---

## Related artifacts

- `agent-skills/scribes/SPEC.md` — the contract every scribe respects
- `agent-skills/context-engineering/plan/prd-closed-loop.md` — closed-loop PRD; the brain side
- `agent-skills/context-engineering/plan/wave-0-demo.md` — Wave 0 sign-off; substrate is real
- `~/.claude/handoffs/ce_closed_loop_session_synthesis.md` — 5-POV audit of the brain itself
- `~/.claude/projects/C--Users-victo/memory/project_anabasis.md` — Anabasis = "Temporal for company-knowledge agents"; scribes are how `find-links` actually finds links
