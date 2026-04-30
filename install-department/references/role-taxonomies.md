# Role taxonomies by function

Common role splits per function. Like
[`methodology-patterns.md`](./methodology-patterns.md), this is
heuristic — used by the synthesizer to flag implausible role lists, not
to constrain them.

## Convention

Each role entry uses the **accountability framing** from interview Q2:
"decides X" / "owns X" / "gates X". A role with no accountability is
not a role; it's a description.

## Marketing

| Role | Accountability |
|---|---|
| Head of Marketing / CMO | Decides brand voice, channel mix, marketing budget allocation |
| Demand Gen lead | Owns paid + lifecycle pipeline targets |
| Content lead | Owns editorial calendar + asset production |
| Product Marketing | Owns positioning, launches, sales enablement |
| Brand / Design | Gates visual identity, gates external creative |
| Marketing Ops | Owns MarTech stack, attribution, lead routing |
| Field / Events | Owns event calendar + sponsorships |

## Sales

| Role | Accountability |
|---|---|
| VP Sales / CRO | Decides territory, comp, hiring, gates revenue forecast |
| Sales Director / Manager | Owns team quota, deal coaching |
| Account Executive (AE) | Owns assigned accounts + closing motion |
| Sales Development Rep (SDR) / BDR | Owns top-of-funnel outreach + qualification |
| Sales Engineer / Solutions | Owns technical fit + demos |
| Sales Ops | Owns CRM hygiene, comp calculations, forecast methodology |

## Product

| Role | Accountability |
|---|---|
| Head of Product / CPO | Decides what gets bet on; owns the bet table |
| Group PM | Owns area roadmap |
| PM | Owns Signal→Opportunity grouping; drafts Solutions |
| Product Designer | Owns UX research + interaction design |
| Product Ops | Owns process, tooling, dashboards |
| User Researcher | Owns evidence collection, interview synthesis |

## Engineering

| Role | Accountability |
|---|---|
| CTO / VP Eng | Decides tech strategy, hiring, architecture review board |
| Engineering Manager | Owns team capacity, on-call, performance |
| Tech Lead | Owns architectural decisions for a domain |
| Senior / Staff Engineer | Owns cross-team technical initiatives |
| Engineer (IC) | Owns features in their lane |
| SRE / DevOps | Owns infra reliability, deploy pipeline |
| Security Engineer | Owns vuln triage, compliance signals |

## Customer Success

| Role | Accountability |
|---|---|
| VP CS / Head of CSM | Decides segmentation, playbooks, escalations |
| CS Manager | Owns team coaching, churn-risk reviews |
| Customer Success Manager (CSM) | Owns assigned book of business |
| Onboarding Specialist | Owns time-to-value for new accounts |
| Renewals Manager | Owns renewal motion + commercial conversations |
| Support / Implementation | Owns ticket resolution + setup work |

## Finance

| Role | Accountability |
|---|---|
| CFO | Decides capital allocation, gates board reporting |
| Controller | Owns books, monthly close, audit |
| FP&A | Owns forecast, budget, variance analysis |
| Accounting | Owns AR, AP, payroll, tax |
| Treasury | Owns cash management, banking, FX |
| Strategic Finance | Owns deal modeling, M&A analysis |

## People / HR

| Role | Accountability |
|---|---|
| CPO / Head of People | Decides comp philosophy, performance system, gates org changes |
| HR Business Partner | Owns assigned function's people matters |
| Recruiter | Owns hiring loop for assigned roles |
| People Ops | Owns HRIS, payroll integration, benefits administration |
| Learning & Development | Owns training, career frameworks |
| DEI lead | Owns DEI programs + reporting |

## Support / Operations

| Role | Accountability |
|---|---|
| Head of Support / Ops | Decides SLA targets, escalation policy |
| Support Lead / Manager | Owns shift coverage, training, escalations |
| Support Engineer (L1/L2/L3) | Owns assigned ticket queue at their tier |
| Operations Manager | Owns process documentation + tooling |
| QA / Reliability | Owns ticket trend analysis + root cause feedback to Eng |

## Smaller orgs

In <50-person companies, one person commonly holds 2–4 roles. The
interview should reflect this — list the *role* but use the same
*person* for all that they hold. The validator does not require unique
people per role.

In <10-person companies, the dept "head" may also be the only person in
the dept. That's fine. The spec captures the function as it is, not as
it would be in a larger org.

## Common implausibilities the synthesizer flags

- A function with no head — every department has a head, even if it's
  the CEO acting in that capacity
- A function with 10+ unique roles in <50-person company — likely
  conflated org chart with department membership
- A role with no accountability statement — likely a description, not a
  role
- Multiple "deciders" for the same accountability — exactly one role
  should be Accountable per topic (RACI's "A"); others are Consulted
  ("C") or Informed ("I")

The synthesizer does not block on these; it adds them to the
verification annex.
