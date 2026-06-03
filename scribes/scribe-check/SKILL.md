---
name: scribe-check
version: 0.1.0
description: >-
  Check a scribe — a scribe-pass module section or a per-scribe SKILL.md —
  against the scribe contract, the SOTA correctness rules, the brain ontology,
  and the product vision: the criteria a human would otherwise apply by hand.
  Use when reviewing a scribe, before flipping a module to enabled, or in CI when
  a scribe spec changes. Profile-aware (CE-push vs raw-JSONL/scribe-pass). Three
  modes: spec-lint (static), output-validate (sample events), vision-grade (LLM
  judgment). Produces a PASS/WARN/FAIL report with citations and fixes and a
  ship/enable gate. Do NOT use for SKILL.md structure (that is skill-check) or to
  review enrichers/consumers (different contract).
triggers:
  - "check this scribe"
  - "scribe-check"
  - "is this scribe conformant"
  - "audit the fathom/granola/slack scribe"
  - "is this scribe SOTA"
  - "can I enable this scribe module"
  - "review scribe before ship"
owners:
  primary: "victor.grosjean@syro.co"
---

# scribe-check

> Run the audit we keep doing by hand — verbatim discipline, content-fingerprint
> idempotency, supersession, look-back, source-local `entity_hint`, concept-first
> trust, federation-not-minting — repeatably, on any scribe, with citations.

The criteria live in **[`CRITERIA.md`](./CRITERIA.md)** (the single source of
truth, each criterion cited back to the contract it comes from). This file is
**how to apply them**. When the contract changes, edit `CRITERIA.md`, not this file.

## Inputs

- **target** — path to the scribe under test:
  - a `scribe-pass` module: `routines/scribe-pass/schedule.md` + the `## Module: <name>` heading, **or**
  - a skill: `agent-skills/scribes/<name>/SKILL.md`.
- **--profile A|B** *(optional)* — override profile detection.
- **--sample <glob>** *(optional, enables output mode)* — emitted events to validate, e.g. `company-brain/corpora/*/meetings/raw/<source>.jsonl`.
- **--modes spec,output,vision** *(default: all)*.

## Step 0 — detect profile (always)

Read the target. Decide **A** (calls `wiki.add`, resolved `entity_hint`) vs
**B** (emits `raw/*.jsonl`, `entity_hint` matches `^<scribe>:`). If it mixes
both signals → record **S0 FAIL** and stop the mechanical pass; everything else
is undefined until the model is chosen. If genuinely ambiguous, ask for
`--profile`. Print the detected profile and the standing A/B divergence note
from `CRITERIA.md`.

## Mode 1 — spec-lint (static)

For each criterion whose Mode includes `static` and whose Profile includes the
detected profile: read what the module/SKILL asserts and decide
PASS / WARN / FAIL / N-A. Cite `file:line` for every non-PASS. This is the audit
performed manually on the fathom/granola modules — e.g. S1 fires when a meeting
module sets `content_hash = sha256(title)` while `claim` = title.

## Mode 2 — output-validate (sample events)

Requires `--sample`. **Keep the raw bytes out of the conversation** — run the
checks in code (e.g. context-mode's `ctx_execute` over the JSONL, or
`scripts/check_scribes.py --sample`) and surface only counts + offending
records. Mechanical checks:

- **C1** every line has the profile's required fields (typed).
- **O1/O7** `entity_hint` present and, for B, matches `^<scribe>:`; never a
  resolved slug; never null.
- **O2/O3** no banned keys anywhere in event/payload:
  `score|severity|theme|category|sentiment|priority|trust|tier|reputation|quality`.
- **C2/S1** recompute `file_id = sha256(scribe+external_id+content_hash)` (B) and
  confirm it matches; confirm `content_hash` is not merely `sha256(claim)` when
  `claim` is just a title.
- **S5** no duplicate `file_id` in the sample (dedup holds).
- **S2** group by `external_id`; multiple rows ⇒ they differ only as content
  revisions (append-only, increasing `ts`), not random dupes.
- **S6** no inlined transcript/body blobs over a size threshold when an id field
  exists to re-fetch by.

## Mode 3 — vision-grade (LLM judgment)

For Group V (and the `llm` parts of O/S): read the scribe + a small sample of
claims and judge the calls a regex can't — V1 *"is this a described fact or an
interpretation in disguise?"*, V2 federation-not-minting, V4
canonical-source-per-domain, V5 scope/PII hygiene. One finding per concern, each
with the evidence that triggered it. Be adversarial: default to flagging when
uncertain, and say why.

## Output — the report

```
# scribe-check: <scribe-name>
Profile: <A|B>   ·   Gate: <PASS | BLOCK (n FAIL)>
⚠ A/B divergence: <one line — which profile, convergence outstanding>

## C — Contract     [✓k ⚠k ✗k]
## S — SOTA         [✓k ⚠k ✗k]
## O — Ontology     [✓k ⚠k ✗k]
## V — Vision       [✓k ⚠k ✗k]

### Findings (non-PASS only)
- ✗ S1 (FAIL) — content_hash = sha256(title); finalized summaries never re-emit.
    └ schedule.md:NNN   fix: hash title+summary+action_items+updated_at
- ⚠ S7 (WARN) — no cross-source correlation keys; Fathom/Granola dupes won't merge.
    └ fix: surface scheduled_start_time + invitee emails + calendar_event_id
...
```

**Gate rule:** any FAIL ⇒ **BLOCK** — do not flip `enabled:true` / do not ship.
WARNs ship only with a logged exception.

## Running in CI (deterministic subset)

The mechanical criteria run headless via `scripts/check_scribes.py` — no LLM, so
it works as a PR gate exactly like `entity-review`. The judgment criteria
(Group V vision-grade, the `llm` parts of O/S) are NOT run in CI; invoke the
skill for those.

```bash
# lint changed scribe specs (advisory; FAIL only on mixed-model S0)
python scripts/check_scribes.py --specs routines/scribe-pass/schedule.md

# output-validate emitted events (the hard gate; FAIL blocks)
python scripts/check_scribes.py --sample 'corpora/**/raw/*.jsonl'
```

Exit 1 on any FAIL (CI blocks), else 0. Wired up in two repos, mirroring the
entity-review pattern (a `SKILLS_TOKEN` checkout of agent-skills for the script):

| Repo | Workflow trigger | Mode | Blocks on |
|---|---|---|---|
| `company-brain` | `corpora/**/raw/*.jsonl` | output-validate | any FAIL (structural twin of entity-review) |
| `syroco-product-ops` | `routines/scribe-pass/schedule.md` | spec-lint | S0 mixed-model only (rest advisory) |

**Why spec-lint is advisory:** it heuristically reads prose, so it emits
WARN/INFO and never false-blocks a valid module; the real structural gate is
output-validate over JSON-structured events. This matches entity-review's
anti-false-block stance.

## Prerequisites

- `CRITERIA.md` (sibling) — the criteria the skill applies.
- A **target** to check (a `scribe-pass` module section or a per-scribe SKILL.md).
- For output mode: a `--sample` of emitted `raw/*.jsonl` events.
- For CI use: Python 3.12 + a `SKILLS_TOKEN` secret in the consuming repo.

## Limitations

- Spec-lint is heuristic over markdown prose — high-signal patterns only; it will
  miss semantic violations a human/LLM catches (that is what vision-grade is for).
- `file_id` recomputation assumes the Profile-B formula `sha256(scribe+external_id+content_hash)`;
  a scribe using a different concatenation will not be byte-verified.
- CI runs no LLM — vision-grade findings never appear in a PR check.

## What scribe-check is NOT

- Not a runtime/cron validator (does the schedule fire?) — that's ops.
- Not `skill-check` — that validates SKILL.md *structure* against the agentskills
  spec; this validates scribe *semantics* against the scribe contract.
- Not an enricher/consumer checker — those have a different contract (interpret
  *with* provenance); scribe-check fails a scribe **for** doing enricher work.

## Maintenance

The criteria drift when the contract evolves (it did today: content-fingerprint,
supersession, look-back, correlation post-date SPEC.md v0.1). On each run, if a
rule references a SOTA property the target's authority doc (SPEC.md for A) does
not yet contain, emit an **INFO: spec-behind-applied** note so the spec and the
applied contract reconcile instead of silently diverging.
