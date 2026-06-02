---
name: entity-review
description: "Review JSON entities pushed to an EntityStore corpus (a git branch, a PR, or an explicit file set) against the LATEST entity.schema.json AND the EntityStore vision guardrails — schema validity, provenance honesty, anti-authority PROHIBITED-field enforcement, navigation reference-anchor discipline, scribe/enricher role boundaries + lazy-materialization, volatile-not-frozen integration fields, identity-as-revisable (v6 identity_assertions), referential integrity, and corroboration thinness. Diff-scoped and vision-aware — complements (does not replace) cb_engine `wiki_audit`, which is corpus-wide. Use when reviewing a company-brain PR/branch that adds or changes entity JSON, gating a backfill/scribe output before merge, or wiring a CI check on entity changes. Do NOT use for code review, schema design, or non-entity JSON."
version: 0.1.0
---

# entity-review

A **diff-scoped, vision-aware reviewer** for EntityStore corpora. It answers one
question well: *"Do the entities this branch/PR pushes stay valid against the
latest schema AND on-track with the brain's design philosophy?"*

It is the codified, repeatable version of the manual review done on company-brain
PR #8 (vessel/captain/navigation backfill, 2026-06-02).

## When to use

- Reviewing a `company-brain` PR or branch that adds/changes entity JSON.
- Gating a **scribe / backfill output** before merge (HubSpot, Notion, Gmail…).
- A pre-merge / CI check on entity changes.

Do NOT use for code review, schema authoring, or non-entity JSON.

## Design stance (why it exists)

`cb_engine.wiki_audit` already audits the **whole corpus** (contradictions, dead
links, freshness, orphans, schema-invalid). This skill is different on two axes:

1. **Diff-scoped** — it reviews only the entities a change *pushes*, so a 2-file
   PR gets a 2-file review, not a 2,593-entity dump.
2. **Vision-aware** — it enforces the design philosophy that a JSON-schema cannot
   express on its own. Crucially, the guardrails are **sourced from the schema's
   own `PROHIBITED FIELDS:` annotations** wherever possible, so the reviewer stays
   schema-agnostic and never drifts from the schema of record.

## How to run

```bash
python scripts/review_entities.py \
  --repo   /path/to/company-brain \
  --corpus corpora/syroco-commercial \
  --base   feat/hubspot-vessel-backfill \   # what to diff against (default: origin/main)
  --head   HEAD                              # the pushed state (default: HEAD)
# or review a GitHub PR directly:
python scripts/review_entities.py --repo /path/to/company-brain --pr 8
# or an explicit file list:
python scripts/review_entities.py --repo /path/to/company-brain --files a.json b.json
```

- `--schema` defaults to `<corpus>/../../schemas/entity.schema.json` (the latest /
  canonical schema). The reviewer always validates against **that file** — i.e. the
  latest schema version by definition.
- Reuses the `entitystore` engine in place (`cb_engine.load_corpus`, `wiki_audit`);
  point `--engine` at its `scripts/` dir if not auto-found.
- Exit code **1** if any `ERROR`-severity finding, else **0** — CI-ready.

## The checks (what "on track with the vision" means)

Severity: **ERROR** blocks merge · **WARN** needs a human call · **INFO** is a nudge.

| # | Check | Severity | Vision principle |
|---|---|---|---|
| C1 | **Schema validity** vs latest `entity.schema.json` | ERROR | the schema is the contract |
| C2 | **ID integrity** — URN `<kind>:<slug>`, unique, no corpus dupes | ERROR | ids are immutable |
| C3 | **Referential integrity (delta-scoped)** — every ref (`wiki_links`, `vessel.owner/operator/manager`, `navigation.vessel/captain_in_charge/charterer`, `client.csm/account_owner/sponsor`, `product.vendor`, `post.author`, `person.affiliations`, `org.members`) resolves | ERROR | no dangling edges |
| C4 | **Provenance honesty** — `extractor` + `extraction_method` present; `extraction_method=system` only for deterministic-source entities; `llm` without `evidence[]` flagged; `extraction_confidence ≤ 1` | WARN | every claim has receipts |
| C5 | **Anti-authority PROHIBITED fields** — enforces each kind's `PROHIBITED FIELDS:` note parsed from the schema (person: trust/reputation/tier/score; org: trust_default/tier; navigation: score/rating/satisfaction/zone-flag) | ERROR | *a person is a witness, not an authority; an org is context, not credential* |
| C6 | **Navigation reference-anchor discipline** — no embedded bulk (`track/weather/noon/fuel/waypoints/positions`); single-valued `captain_in_charge`; `backoffice_id` present | ERROR | a navigation is a key, not a data mirror |
| C7 | **Lazy-materialization / role boundary** — flags a push that mass-creates navigation (or any anchor) nodes that are **orphan** (0 inbound refs), or where >N added nodes all share ONE provenance source (enricher-shaped output from a single mirror) | WARN | *materialize lazily; scribe ≠ enricher* (locked 2026-06-02) |
| C8 | **Volatile-not-frozen** — observed-but-volatile integration fields (e.g. `vessel.live_status`, `vessel.contract_status`) must carry source attribution and must NOT be re-asserted as a `claims[]` fact | WARN | capture observations, don't freeze them as truth |
| C9 | **Identity-as-revisable (v6)** — if `identity_assertions[]` present: required keys; `status=retracted` carries `retraction_reason`; `echo_of` non-null ⇒ excluded from corroboration; `superseded_by` resolves to a sibling assertion | WARN | identity is a thresholded inference with receipts, not bedrock |
| C10 | **Truth ⊥ relevance** — `post.signal_quality` judges the post (never the author); `topics[]` stay identity-neutral; `concept` carries `falsifiability` + `specificity` | INFO | relevance and credibility are orthogonal |
| C11 | **Corroboration thinness** — `concept` of `type ∈ {claim, opportunity}` backed by <2 distinct org sources | INFO | one source is an echo, not a fact |
| C12 | **Un-merge via delete** — entity files *deleted* in the diff (status `D`) | WARN | un-merge = retract/supersede with a reason, not `rm` (v6) |

## Output

A markdown review: a verdict line, a summary table (files reviewed, by status), then
findings grouped by severity with `file → check → message`. Mirrors the PR #8 review
format so it reads the same whether a human or this skill produced it.

## Extending

New vision rules go in `scripts/review_entities.py` as a `check_*` function returning
`Finding`s. Prefer **sourcing the rule from the schema** (a new `PROHIBITED:` /
annotation convention) over hardcoding, to keep the engine schema-agnostic. Record any
cross-cutting decision in `company-brain/docs/proposals/` and reference it in the
check's message so a flagged author can find the rationale.
