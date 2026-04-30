# Example — Product department spec

A worked example of a Department Spec produced by `install-department`.
Reverse-engineered from the manual prior art at
[`syroco-product-ops/README.md`](https://github.com/syrocolab/syroco-product-ops/blob/main/README.md)
— what Victor Grosjean (Head of Product, Syroco) built by hand over
several months. The skill aims to produce something of equivalent
quality in 60-90 minutes.

This example is illustrative only. Real installs vary by company.

---

# Product — Department Spec

> **Head:** Victor Grosjean — Head of Product
> **Installed:** 2026-04-XX
> **Spec version:** 1.0
> **Source skill:** install-department v0.1.0

---

## 1. Tools

### Notion
- **Primary use:** Source of truth for every product artifact (signal, opportunity, solution, PRD, delivery)
- **Entities tracked:** Signals DB, Opportunities DB, Solutions DB, Deliveries DB, OKRs DB, Product Hub
- **Access:** rw
- **Volume:** ~2,400 signals, ~180 opportunities, ~60 solutions, ~40 deliveries
- **Cross-references:** Signal→Opportunity (relation), Solution→Opportunity (relation), Delivery→Solution (relation), Solution→PRD (sub-page)

### HubSpot
- **Primary use:** Connect product priorities to revenue (deals, ARR at stake per theme)
- **Entities tracked:** Companies, Deals
- **Access:** r
- **Volume:** Active deal pipeline + closed-won corpus
- **Cross-references:** Deal→Company, Deal→Theme (custom property)

### Linear
- **Primary use:** Engineering execution tracking; PRD → tickets → cycle delivery
- **Entities tracked:** Engineering team, Cycle boards, Project: Product OS
- **Access:** rw
- **Volume:** Per-cycle issue count
- **Cross-references:** Linear Issue ←→ Notion Delivery (via "Closes ENG-XXX" in PR body)

### GitHub
- **Primary use:** Code, dashboard hosting (GitHub Pages), release-notes archive
- **Entities tracked:** syrocolab/syroco-product-ops, syrocolab/company-knowledge, victorgjn/agent-skills
- **Access:** rw
- **Volume:** Daily commits, weekly PRs
- **Cross-references:** PR → Linear Issue (auto-close), PR → Notion Delivery (manual link)

### Slack
- **Primary use:** Release notifications, signal capture from CSM/Sales conversations
- **Entities tracked:** #release-notes, #product, #fleet-csm, #fleet-sales
- **Access:** rw (channel membership)
- **Volume:** Member counts only (per privacy floor)

### Granola
- **Primary use:** Meeting transcripts; signal source from client calls
- **Entities tracked:** Folders: Client Calls, Internal Reviews, Betting Tables
- **Access:** rw
- **Volume:** ~12 meetings/week

### Metabase
- **Primary use:** Embedded KPI charts in OKR slide of dashboard
- **Entities tracked:** Public dashboards referenced from `roadmap-data.json`
- **Access:** r
- **Volume:** N/A (rendered, not stored)

---

## 2. Roles

### Head of Product (Victor Grosjean)
- **Accountable for:** Decides what gets bet on, owns the PRD authoring, runs the betting table
- **Tools used:** All §1 tools
- **Reports to:** CEO
- **Backed up by:** No formal backup; PMs cover signal triage in Victor's absence

### Product Managers
- **Accountable for:** Owns Signal→Opportunity grouping, drafts Solution pitches
- **Tools used:** Notion, HubSpot (read), Granola, Linear (read)
- **Reports to:** Head of Product

### Engineering Leads (per lane)
- **Accountable for:** PRD → Delivery; Linear cycle planning; release readiness
- **Tools used:** Linear, GitHub, Notion (Deliveries)
- **Reports to:** CTO; dotted line to Head of Product on PRDs

### CSMs (signal contributors)
- **Accountable for:** Captures client pain as Signals; owns release-notes-sent confirmation per client
- **Tools used:** Notion (Signals only), Slack, Granola
- **Reports to:** Head of CSM (different department)

---

## 3. Cadence

| Name | Frequency | Attendees | Trigger | Output |
|---|---|---|---|---|
| Daily Sync (automated) | daily 07:00 CET | none (cron) | cron | Updated `roadmap-data.json` |
| Weekly Re-rank (automated) | Monday 08:00 CET | none (cron) | cron | Re-ranked theme priorities |
| Betting Table | weekly | Head of Product, PMs, CTO | calendar | Solutions → Bet status |
| Release Notes Review | event | Head of Product | Delivery moves to Released | Approved release notes draft |
| Sprint Planning | per cycle (2-week) | Engineering team | calendar | Linear cycle commits |
| OKR Review | quarterly | Leadership | calendar | OKR status update + new objectives |

---

## 4. Pipeline

```
Signal → Opportunity → Solution → PRD → Delivery → Release
```

### Stage 1 — Signal
- **What happens:** A client pain, request, or competitive observation is captured
- **Who works in this stage:** Anyone (PMs, CSMs, Sales, Support)
- **Artifact:** Notion Signal page (DB: Signals)
- **Moves forward when:** A PM identifies ≥3 signals on the same theme, creates an Opportunity
- **Owner of the move:** PM

### Stage 2 — Opportunity
- **What happens:** A validated problem worth solving, backed by signal patterns
- **Who works in this stage:** PM
- **Artifact:** Notion Opportunity page (DB: Opportunities)
- **Moves forward when:** PM completes discovery (interviews, data, competitive intel) and drafts a Solution
- **Owner of the move:** PM

### Stage 3 — Solution
- **What happens:** A 1-page Shape Up pitch (problem, approach, appetite, riskiest assumption)
- **Who works in this stage:** PM
- **Artifact:** Notion Solution page (DB: Solutions, Betting Status field)
- **Moves forward when:** Betting Table approves; Betting Status → Bet
- **Owner of the move:** Head of Product (calls the bet)

### Stage 4 — PRD
- **What happens:** Engineering-ready spec with acceptance criteria
- **Who works in this stage:** PM (drafts), Engineering Lead (reviews)
- **Artifact:** Notion sub-page of the Solution
- **Moves forward when:** Engineering Lead signs off; Delivery row created
- **Owner of the move:** Engineering Lead

### Stage 5 — Delivery
- **What happens:** Build, tracked in Linear and synced to Notion
- **Who works in this stage:** Engineering team
- **Artifact:** Linear cycle issues + Notion Delivery row
- **Moves forward when:** Status → Released in Notion (set on production deploy)
- **Owner of the move:** Engineering Lead

### Stage 6 — Release
- **What happens:** Release notes drafted from Solution + PRD, reviewed by Head of Product, sent to fleet clients
- **Who works in this stage:** Head of Product (reviews), CSMs (confirm receipt per client)
- **Artifact:** Notion Delivery row with `Release Notes Sent = true`, Slack post in #release-notes, mirrored to company-knowledge GitHub
- **Moves forward when:** N/A (terminal stage); next cycle begins for any new signals it generates

---

## 5. Taxonomy

- **Taxonomy name:** Themes
- **Classification:** Manual tag in Notion, with auto-rollup from 6 secondary categories to 9 dashboard themes
- **Categories:** 15 total themes; top 9 dashboard-visible (Fleet Monitoring & Alerts, Voyage Costing/ETA, Route Quality, Guidance Widgets, Prove Syroco Value, ECDIS & Route Export, Reporting/NR, Avoidance Zones, Connectivity); 6 secondary roll up to the top 9
- **Add/merge/retire rule:** New theme requires Head of Product approval; secondary themes auto-roll into nearest top-9 by category mapping
- **Conflict rule:** When a signal fits two themes, the higher-revenue theme wins (per weekly re-rank from HubSpot deal data)

---

## 6. Automations

| Name | Trigger | Action | Owner | Breakage symptom |
|---|---|---|---|---|
| Daily Sync | cron 07:00 CET | Pull Notion DBs, push `roadmap-data.json` to GitHub, redeploy dashboard | sync-agent (external repo) | Dashboard shows stale data; new signals don't appear until manually refreshed |
| Weekly Re-rank | cron Monday 08:00 CET | Analyze HubSpot deal movements, re-rank theme priorities | sync-agent | Theme order frozen; high-revenue movements not reflected |
| Release-notes drafting | webhook (Notion: Delivery → Released, Notes Sent = false) | Draft release notes from Solution + PRD; identify impacted clients from HubSpot; queue for Head of Product review | sync-agent | Releases ship; clients not notified |
| Feedback signal | end of Daily Sync | Detect new Released deliveries → create a Notion Signal asking PM to collect user reactions | sync-agent | Released features have no feedback loop |

**No release notes are sent without explicit Head of Product approval.**
The automation drafts and queues; humans send.

---

## 7. Metrics

### ARR at stake per theme
- **Source:** hubspot.deals (Deal Stage × Theme custom property)
- **Frequency reported:** Weekly (re-rank)
- **Healthy range:** N/A (informational, drives prioritization)
- **Action threshold:** Theme drops out of top 9 → review whether to deprioritize active opportunities under that theme

### Notes-sent rate
- **Source:** notion.deliveries (Released + Notes Sent flags)
- **Frequency reported:** Continuous (dashboard slide 7)
- **Healthy range:** 100%
- **Action threshold:** Any release > 7 days without notes sent → escalate to Head of Product

### Bottleneck column
- **Source:** notion (Workflow slide 6 logic — count items per pipeline stage)
- **Frequency reported:** Continuous (dashboard)
- **Healthy range:** No single stage > 2x the median count
- **Action threshold:** A stage hits 5+ items → flag as bottleneck on the dashboard with orange border

### Signal volume per theme
- **Source:** notion.signals (group by Theme)
- **Frequency reported:** Weekly
- **Healthy range:** N/A
- **Action threshold:** Theme signal count > 20 with no Opportunity created → PM review

---

## Annex — Unverified claims

*(none — everything in the spec is backed by a probed entity)*

---

## Notes for the runtime

- The Product department's pipeline (Signal → Opportunity → Solution → PRD → Delivery → Release) is the **canonical** Syroco pipeline. Other departments will have different pipelines (e.g., Sales: Lead → Qualified → Proposal → Closed). The runtime should not assume any one pipeline is universal.
- The 9 dashboard themes are department-internal taxonomy. Other departments will have different taxonomies. `find-links` (v0.2) is the skill that bridges them.
- Cron-driven automations (Daily Sync, Weekly Re-rank) live in the external `sync-agent` repo, not in the brain. The spec records their existence; the runtime does not invoke them.
