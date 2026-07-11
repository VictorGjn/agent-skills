# scribe-check criteria

The single source of truth for what makes a scribe conformant. Each criterion is
**derived from** an existing contract artifact (cited), not invented here. When
the contract changes, change the criterion here and bump `criteria_version`.

```
criteria_version: 0.1.0
derived_from:
  - agent-skills/scribes/SPEC.md                         # general scribe contract (Profile B = live model, Profile A = legacy)
  - syroco-product-ops/routines/scribe-pass/schedule.md  # Profile B contract (raw-JSONL verbatim)
  - memory: project_scribes_architecture                 # ontology + routing
  - memory: reference_fathom_hubspot_pattern             # resolve-at-ingestion / federation
  - memory: concept-first-trust (entity schema v4)       # no trust/score on person/org
```

## Two profiles — detect first, then apply

A scribe conforms to **one** of two models. Applying the wrong profile's rules
produces false findings, so detection is step 0.

| | **Profile A — CE push** | **Profile B — raw-JSONL verbatim** |
|---|---|---|
| Sink | `wiki.add` MCP | append to `company-brain/<stream>/raw/<source>.jsonl` |
| `entity_hint` | **resolved** slug (`anthony-veder`) | **source-local** (`fathom:<recording_id>`) |
| Extraction | at scribe (T0–T3 claim tiers) | none — verbatim; enricher extracts |
| Resolution | librarian merges by shared hint | enricher resolves, frozen `as_of` |
| Authority | `agent-skills/scribes/SPEC.md` v0.1 (2026-05-03, legacy/reference) | `scribe-pass` applied contract (2026-05-19, canonical/live) |
| Examples | `granola-scribe/SKILL.md` | `scribe-pass` `fathom` / `granola` modules |

**Detection.** Calls `wiki.add` + resolved hints → **A**. Emits `raw/*.jsonl` +
`entity_hint` matching `^<scribe>:` → **B**. Ambiguous → operator passes
`--profile A|B`. If a scribe mixes both (e.g. resolved hint *and* raw JSONL),
that is itself a **FAIL (S0)** — pick one model.

> ⚠️ **Known divergence (report on every run):** Profiles A and B are two
> models for the same job and have not been fully reconciled (see
> `project_scribes_architecture` "Open reconciliation"). Profile B is the live
> canonical model at Syroco (since 2026-05-19) and is now documented first in
> SPEC.md; Profile A is retained for reference. A scribe is judged against its
> own profile, **and** the report notes which profile and that convergence is
> outstanding.

## Rejected profiles

**Profile C — system-of-record EntityStore writer** was proposed on branch
`feat/scribe-check-profile-c` (commit 101772b, 2026-06-04) as a third scribe
model where the scribe writes resolved entities + identity_assertions directly
to the EntityStore. This profile was rejected the same day by THE WRITER RULE
(company-brain/CLAUDE.md, locked 2026-06-04) which establishes that enrichers
are the **sole** authorized entity writers. See
`company-brain/schemas/entity.schema.json` line 91 ("Writers: enrich-pass
ONLY") for the canonical technical authority.

**Status:** Rejected, branch unmaintained. Do not resurrect. The `check_scribes.py`
detection logic and two-profile gate remain unchanged.

## Severities

- **FAIL** — violates the contract; blocks ship / blocks flipping `enabled:true`.
- **WARN** — should-fix; ship only with an explicit logged exception.
- **INFO** — advisory / metadata completeness.

## Check modes

- **static** — read the scribe's SKILL.md / module section; check what it asserts.
- **output** — read a sample of emitted events (`raw/*.jsonl` or the wiki events log); check real records.
- **llm** — judgment the regex can't make ("is this observation or interpretation in disguise?").

---

## Group C — Contract (SPEC.md §Event schema / Idempotency / Naming)

| ID | Rule | Sev | Profile | Mode | Pass criterion | Fix |
|---|---|---|---|---|---|---|
| C1 | All required envelope fields present & typed | FAIL | both | static, output | A: `source_type, source_ref, file_id, claim`. B: `schema_version, scribe, scribed_at, source_type, source_ref, file_id, external_id, content_hash, claim, ts, entity_hint, payload, data_classification` (`data_classification` enum: `public/internal/confidential/restricted`, see `context-engineering/SPEC-mcp.md` §6.3) | add the missing field to the emit spec |
| C2 | `file_id` is the deterministic cross-run dedup key | FAIL | both | static, output | A: stable per artifact. B: `sha256(scribe + external_id + content_hash)` | define `file_id` exactly; never random/time-based |
| C3 | `source_type` registered in `freshness_policy.HALF_LIVES` before ship | FAIL | A | static | half-life key declared & registered (else 60-day default silently applies) | register the key in CE |
| C4 | `source_ref` stable across re-runs | FAIL | both | static, output | a permalink/id that does not change run-to-run | use upstream stable URL/id, not a positional ref |
| C5 | Telemetry / audit emitted | WARN | both | static, output | A: `scribe.run.*` JSONL on stderr. B: `audit/scribe-pass/<run_id>.jsonl` run record (per-module counts) | add the run record |
| C6 | Batches ≤100 events to `wiki.add` | WARN | A | static | batch size bounded | chunk pushes |
| C7 | Discovery metadata declared | INFO | both | static | connector = MCP namespace; `mcp_required`; `ce_event_schema`; cadence | add SCRIBE.toml / module header |
| C8 | Naming: `<connector>-scribe` / module = connector, matches MCP namespace | FAIL | both | static | name derives from the MCP namespace it reads | rename to match |

## Group S — SOTA correctness (model-agnostic; the rules we exercised today)

| ID | Rule | Sev | Profile | Mode | Pass criterion | Fix |
|---|---|---|---|---|---|---|
| S0 | Single model — does not mix A and B | FAIL | both | static | exactly one sink + one `entity_hint` style | pick a profile |
| S1 | Dedup key fingerprints **content**, not a label | FAIL | both | static, output | when `claim` ≠ full captured content (e.g. `claim`=title), `content_hash`/`file_id` incorporates the substantive material (summary/transcript/body) | hash content, not the title — *this is the bug that ships silently* |
| S2 | Supersession: stable `external_id`, `file_id` per content version, append-only | FAIL | both | static, output | edited/finalized object appends a NEW line sharing `external_id`; downstream takes latest by `ts` | document supersession; never rewrite the log |
| S3 | Look-back re-scan for async-finalized content | FAIL | both | static | modules with async-generated payload (summaries, late transcripts) re-list a trailing window (default 7d), not watermark-only | add `createdAfter = min(watermark, now−7d)` |
| S4 | Bounded context: fetch → emit → drop | FAIL | both | static | raw upstream content not held across artifact/module boundary | process one artifact at a time |
| S5 | Idempotency: read-before-emit; no-change re-run byte-identical modulo run-ts | FAIL | both | static, output | dedup against existing log by `file_id`; CE does not dedupe | add the read-before-emit step |
| S6 | Large / PII content by-reference | WARN | both | static, output | transcripts/bodies not inlined when re-fetchable by id | store the id; let the enricher re-fetch |
| S7 | Cross-source correlation keys surfaced | WARN | both | static | when a sibling source can record the same artifact, emit join keys (start time + participant set + calendar id) raw | add the keys to payload |
| S8 | Failure isolation; no partial JSONL line | FAIL | both | static, output | one artifact/module failure is recorded and the run continues; never a half-written line | wrap per-artifact; flush whole lines |

## Group O — Ontology / trust (memories + entity schema v4)

| ID | Rule | Sev | Profile | Mode | Pass criterion | Fix |
|---|---|---|---|---|---|---|
| O1 | `entity_hint` style matches profile | FAIL | both | static, output | A: resolved kebab slug. B: source-local `^<scribe>:<id>` — **never** a resolved slug at scribe time | B: emit `<scribe>:<id>`, defer resolution to enricher |
| O2 | No interpretation fields on events | FAIL (B) / WARN (A) | both | static, output | no `score/severity/theme/category/sentiment/priority`; A's T2 `{confidence,kind}` is extraction metadata, never treated as truth | move interpretation to enricher/consumer |
| O3 | Concept-first trust | FAIL | both | static, output | no `trust/tier/reputation/quality/zone-flag` on person/org hints or payload | drop the field; trust is computed downstream, never stored as authority |
| O4 | Producer-only — no sink writes | FAIL | both | static | writes only its event sink; never Notion/Linear/Slack/docs | remove the write; that's the consumer |
| O5 | No cross-source joins/resolution in scribe | FAIL | both | static | A defers entity merge to librarian via shared hint; B defers to enricher | surface raw keys, don't resolve |
| O6 | Domain-entity conventions (schema v4) respected where referenced | INFO | both | static, llm | vessel slug = IMO; captain = person + edge (no captain kind / maritime_role) | align hint/payload naming |
| O7 | `entity_hint` always set (never null) | FAIL | both | static, output | every event has a hint (null → dropped at consolidation) | fall back to source-local id / slugified title |

## Group V — Vision alignment (judgment; llm-graded)

| ID | Rule | Mode | What it checks |
|---|---|---|---|
| V1 | Describes, doesn't decide | llm | scan `claim` + `payload` for disguised interpretation (a scored/ranked/categorised "fact") |
| V2 | Federation, not entity-minting | llm | resolves into / defers to a canonical source per domain; does not spawn a parallel "17th entity universe" |
| V3 | Single upstream source | static, llm | one scribe reads exactly one source |
| V4 | Canonical-source-per-domain | llm | commercial→HubSpot, code→GitHub, product→Notion, eng→Linear; meetings resolve into one of these (not a parallel "Meeting" universe) |
| V5 | Scope / PII hygiene | static, llm | external/customer data not landed in a broad-read `default` scope |

---

## Scoring & gate

```
score = { fail: <count>, warn: <count>, info: <count>, na: <count> }
gate  = PASS  if fail == 0
        BLOCK if fail  > 0     # do not flip enabled:true / do not ship
```

The report always states: **scribe name · detected profile · gate · the
A/B-divergence note · each non-PASS rule with citation + fix.**
