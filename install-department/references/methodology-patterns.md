# Methodology patterns by function

Common cadence + pipeline patterns observed in working departments,
indexed by function. The interview prompts (
[`interview-prompts.md`](./interview-prompts.md)) ask open questions;
this reference is what the synthesizer (`scripts/synthesize.py`) checks
against to flag implausible answers ("you said you have no recurring
review and 8 reports — that's unusual; reconsider Cadence?").

These are patterns, not requirements. A department that genuinely does
something different is fine — the synthesizer flags but doesn't reject.

## Marketing

**Typical pipeline**
```
Topic → Brief → Asset → Distribution → Measurement
```

**Typical cadence**
- Daily: content slot triage
- Weekly: editorial review
- Monthly: campaign retro
- Quarterly: brand/strategy review

**Typical taxonomy**
- Funnel stage (TOFU / MOFU / BOFU)
- Channel (web / email / paid / social / events)
- Persona

**Common automations**
- Newsletter scheduling
- UTM enrichment on inbound traffic
- Lead scoring sync to CRM

**Common metrics**
- MQLs / SQLs per channel
- Cost per acquisition
- Content engagement (unique readers, time-on-page)
- Pipeline-influenced revenue

## Sales

**Typical pipeline**
```
Lead → Qualified → Discovery → Proposal → Negotiation → Closed Won/Lost
```

**Typical cadence**
- Daily: pipeline standup
- Weekly: forecast call
- Monthly: territory review
- Quarterly: comp / quota check

**Typical taxonomy**
- Deal source (inbound / outbound / referral / event)
- Account tier (enterprise / mid-market / SMB)
- Vertical / segment

**Common automations**
- Lead routing rules
- Sequence automation (Outreach, SalesLoft, Apollo)
- Deal-stage-change → Slack notification

**Common metrics**
- Pipeline coverage (X× quota)
- Win rate by source
- Average deal size
- Sales cycle length

## Product

See the worked example in [`example-product-spec.md`](./example-product-spec.md).
The Syroco Product department's full Department Spec is canonical for
this function — Signal → Opportunity → Solution → PRD → Delivery →
Release pipeline, theme-based taxonomy, betting-table cadence.

## Engineering

**Typical pipeline**
```
Backlog → Sprint → In Progress → Review → Done → Deployed
```

**Typical cadence**
- Daily: standup
- Per sprint (1-2 weeks): planning + retro
- Per release: cycle review
- Quarterly: tech-debt review / capacity planning

**Typical taxonomy**
- Issue type (bug / feature / chore / spike)
- Priority (P0 / P1 / P2)
- Service / domain / surface area
- Lane (frontend / backend / infra / mobile)

**Common automations**
- CI/CD pipelines
- Auto-merge on green checks
- Issue triage labels via bot
- On-call rotation scheduling

**Common metrics**
- Cycle time (issue created → deployed)
- DORA metrics (deploy frequency, lead time, MTTR, change failure rate)
- Bug escape rate
- Sprint commit ratio

## Customer Success / CSM

**Typical pipeline**
```
Onboarding → Adoption → Retention → Expansion → (Renewal / Churn)
```

**Typical cadence**
- Weekly: account health review
- Monthly: QBR (quarterly business review) per top tier
- Quarterly: portfolio review

**Typical taxonomy**
- Account tier
- Health status (green / yellow / red)
- Lifecycle stage

**Common automations**
- Health-score recalculation
- Churn-risk alerts (drop in usage, missed payments, NPS detractor)
- Renewal reminders to AMs

**Common metrics**
- NRR (net revenue retention)
- Churn rate
- NPS / CSAT
- Adoption depth (% of features used)

## Finance

**Typical pipeline**
```
Transaction → Booked → Reconciled → Reported → Forecast updated
```

**Typical cadence**
- Daily: cash position
- Weekly: AR aging
- Monthly: close + flash report
- Quarterly: board reporting + reforecast
- Annually: audit + budget

**Typical taxonomy**
- Account (chart of accounts)
- Cost center / department
- Currency
- Period (month / quarter / year)

**Common automations**
- Bank feed sync
- Invoice-to-deal matching
- AP approval workflows
- Reporting dashboard refresh

**Common metrics**
- Cash runway
- Burn rate
- Revenue (booked vs recognized)
- Gross / net margin
- DSO / DPO

## People / HR

**Typical pipeline**
```
Sourcing → Screen → Interview → Offer → Onboard → Active → Offboard
```

**Typical cadence**
- Weekly: hiring loop sync
- Monthly: headcount + comp review
- Quarterly: performance / engagement
- Annually: comp planning + reviews

**Typical taxonomy**
- Department / function
- Level (IC / manager / director / VP)
- Location / employment type

**Common automations**
- ATS integrations
- Onboarding checklist automation
- PTO tracking
- Anniversary reminders

**Common metrics**
- Time-to-hire
- Offer-accept rate
- Attrition (regrettable vs total)
- eNPS / engagement score

## Support / Operations

**Typical pipeline**
```
Ticket → Triaged → In Progress → Resolved → Verified → Closed
```

**Typical cadence**
- Hourly: queue check (depending on SLA)
- Daily: shift handoff
- Weekly: backlog review
- Monthly: trend / themes review

**Typical taxonomy**
- Severity (P1 / P2 / P3 / P4)
- Channel (email / chat / phone / portal)
- Product area
- Root cause category

**Common automations**
- SLA timers + alerts
- Auto-assignment by topic
- Macro/template responses
- Escalation rules

**Common metrics**
- First response time
- Resolution time
- CSAT per ticket
- Backlog age

## How the synthesizer uses this

When the head's interview answers diverge significantly from the
patterns above (e.g., a Sales department with no pipeline stages, an
Engineering department with no CI), the synthesizer adds a low-priority
flag to the `??-needs-verification.md` annex. The head can dismiss the
flag at validation time — the patterns are heuristics, not requirements.
