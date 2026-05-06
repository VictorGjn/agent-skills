# Generalist scribes & enrichers v0.1

> **Companion to:** `SPEC.md` (the contract), `PLAN.md` (the 17 connector scribes), and `SKILL_INTEGRATION.md` (the 3-skill family per connector).
>
> This doc catalogs **cross-connector primitives** — scribes and enrichers that are *not* tied to one upstream MCP. They're consumed by skills that need unstructured-text intake, live-web facts, dataset cleanup, unit conversion, or jargon→ELI5 translation, regardless of which connector originally produced the data.
>
> **First worked example:** `~/.claude/skills/board-deck-from-brain-dump/` (shipped 2026-05-06). Its `ANABASIS-SPEC.md` is the source these specs were lifted from. The skill currently inlines all six; this doc is the contract for the version those skills will eventually pull from instead.
>
> **Drafted:** 2026-05-06. Status: spec stubs, no code.

---

## 1. Frame

The 17 connector scribes in `PLAN.md` each pull from one upstream MCP. That covers ~80 % of "where does the brain get its data" but leaves a gap: skills that take **arbitrary unstructured text** (a brain dump, a transcript paste, a handoff doc) or need **live web facts with citations** can't reuse one connector scribe.

These six primitives close that gap. Each is independently useful; together they form the generalist intake + enrichment layer.

```
   user-pasted text                  live web
        │                                │
        ▼                                ▼
[unstructured-text-scribe]   [live-web-data-scribe]      ← scribe layer (raw)
        │                                │
        └────────────────┬───────────────┘
                         ▼
              ┌────────────────────────────┐
              │  ENRICHERS (cross-cutting):│              ← enricher layer
              │  - dataset-envelope-filter │
              │  - unit-normalizer         │
              │  - jargon-detector         │
              │  - eli5-rewriter           │
              └──────────────┬─────────────┘
                             ▼
                  consumer skills (decks, briefs,
                  PRDs, KB articles, copy)
```

---

## 2. Generalist scribes

### 2.1 `unstructured-text-scribe`

**Purpose.** Ingest any human-authored unstructured text — brain dumps, transcripts, handoffs, raw notes, email threads, chat exports — and emit a normalised entity stream.

**Input.**
- A list of file paths or inline text blocks.
- Optional intent hint (`extraction_focus`: `claims` | `decisions` | `numbers` | `open_questions`).

**Output.** `UnstructuredTextEntity` per logical paragraph or claim:

```yaml
- type: claim | decision | number | question | reference
  text: "..."
  source_ref: "{filepath}#L{line}" or "inline"
  confidence: 0.0-1.0
  related_topics: ["..."]
```

**Consumed by.**
- `board-deck-from-brain-dump` — Phase 0 narrative discovery
- `create-opportunity` — Discovery brief intake
- `create-prd` — rough notes → structured PRD
- `kb-article` — resolved-issue notes → KB draft
- Any skill that takes "I have these notes…" as input

**Why generalist.** Brain dumps are not specific to boards. Every skill that takes unstructured input as a starting point benefits from the same normaliser.

---

### 2.2 `live-web-data-scribe`

**Purpose.** Fetch a fact from the web with citation, restricted to an allowlist of trusted sources. Replaces ad-hoc `WebSearch` calls in skills that need defensible numbers.

**Input.**
- Natural-language query.
- Source allowlist (default: per-domain category — e.g. `commodities`, `regulation`, `shipping-rates`).
- Optional freshness window (`max_age_days`).

**Output.** `LiveFactEntity`:

```yaml
- value: 80
  unit: "USD/tCO2"
  topic: "EU ETS spot price"
  source_url: "https://tradingeconomics.com/commodity/carbon"
  source_publisher: "Trading Economics"
  observed_at: "current"          # date-relativizer downstream
  confidence: 0.0-1.0
```

**Consumed by.**
- `board-deck-from-brain-dump` — carbon prices, fuel prices, hire rates by vessel class
- `competitive-intelligence` — competitor pricing, headcount, funding
- `pipeline-review` — industry benchmarks
- `comp-analysis` — compensation benchmarks
- Any skill that needs current external numbers

**Why generalist.** Every skill that wants defensible numbers needs source attribution + freshness. The difference between EU ETS and Capesize day rates is configuration, not a new component.

---

## 3. Generalist enrichers

### 3.1 `dataset-envelope-filter`

**Purpose.** Take a noisy 2D dataset (x, y points) and return a clean visual envelope plus a target point count. Drops outliers below a local-max threshold; decimates to a stable count regardless of input density.

**Input.**
- Array of `{x, y}` points.
- Window size (default ±5 % of x-range).
- Threshold (drop points whose y is more than `threshold_value` below local max — units match the y-axis).
- Target point count (default 150).

**Output.** Filtered + decimated array of `{x, y}` points with `kept_ratio` metadata.

**Consumed by.**
- `board-deck-from-brain-dump` — Pareto cloud cleanup (760 → 152 points)
- `build-dashboard` — any scatter chart with too many points
- `create-viz` — pre-chart cleanup for Plotly / matplotlib

**Why generalist.** Every chart with a noisy point cloud benefits from the same filter shape. Pure function, no domain knowledge.

---

### 3.2 `unit-normalizer`

**Purpose.** Convert a value with a unit and context (date, region, vessel class, etc.) into a target unit, with assumptions surfaced in the output.

**Input.** `value`, `from_unit`, `to_unit`, optional `context` (free-form key/values).

**Output.**

```yaml
- value: 643
  from_unit: "EUR/t VLSFO equivalent deficit"
  to_unit: "EUR/t fuel"
  assumptions:
    - "VLSFO energy density: 41 GJ/t"
    - "VLSFO GHG intensity: 91 gCO2eq/MJ (well-to-wake)"
    - "FuelEU 2026 target: 89.34 gCO2eq/MJ (2 % below 91.16 baseline)"
  source_calc: "EUR/t VLSFO_eq × deficit_factor × VLSFO intensity"
```

**Consumed by.**
- `board-deck-from-brain-dump` — FuelEU penalty → effective $/t fuel; EU ETS scope ramp; bunker $/mt across geographies
- `create-viz` — financial axis conversions
- `pipeline-review` — currency normalization
- Any skill that needs $/€/£ or unit-class conversions

**Why generalist.** Unit conversion with traceable assumptions is a primitive operation. Domain-specific conversion tables (regulatory units, energy units, currency) are configuration, not new components.

---

### 3.3 `jargon-detector`

**Purpose.** Scan caption-length text for internal acronyms, framework references, and undefined-prior-work mentions that an outside reader would not understand. Flags candidates for ELI5 rewrite.

**Input.** Text block + optional vocabulary allowlist (terms the audience already knows).

**Output.** Array of flagged spans with reason codes (`internal_acronym`, `unexplained_framework`, `assumed_prior_context`).

**Consumed by.**
- `board-deck-from-brain-dump` — caption rewrite candidates
- `kb-article` — translate internal-only language to customer-readable
- `ux-copy` — UX copy review for non-employee readers

**Why generalist.** Every artefact that leaves the company benefits from the same scan.

---

### 3.4 `eli5-rewriter`

**Purpose.** Rewrite a flagged span in plain language. Constraint-driven: preserve claim, preserve register, never add new claims.

**Input.** Original text + audience profile (e.g. `outside-employee`, `non-technical`, `domain-expert-but-different-domain`).

**Output.** Rewritten text + diff explanation.

**Consumed by.**
- `board-deck-from-brain-dump` — caption rewrite when jargon-detector flags
- `kb-article`
- `ux-copy`
- `proactive-brief` when the brief leaves the team

**Why generalist.** Same as jargon-detector — translation is not domain-specific.

---

## 4. Implementation order

When porting from per-skill inlining to standalone components, build in this order:

1. **`unstructured-text-scribe`** — broadest reuse; unblocks `create-opportunity`, `create-prd`, `kb-article` immediately.
2. **`live-web-data-scribe`** — same pattern, outward-facing skills.
3. **`unit-normalizer`** — pure function, smallest scope, fastest to ship.
4. **`dataset-envelope-filter`** — pure function, second smallest.
5. **`jargon-detector` + `eli5-rewriter`** — pair; same prompt-template family.

## 5. Effort estimate

| Component | Type | Days | Reuse signal |
|---|---|---|---|
| `unstructured-text-scribe` | Scribe | 3 | High — 4+ skills |
| `live-web-data-scribe` | Scribe | 1 | High — 4+ skills |
| `dataset-envelope-filter` | Enricher | 0.5 | Medium — 2-3 skills |
| `unit-normalizer` | Enricher | 1 | Medium — 2-3 skills |
| `jargon-detector` | Enricher | 0.5 | Medium — 2-3 skills |
| `eli5-rewriter` | Enricher | 0.5 | Medium — 2-3 skills |

Total: ~6.5 days for the full generalist tier. Each component unblocks 2-4 downstream skills.

## 6. Relationship to PLAN.md scribes

`PLAN.md`'s 17 entries are **connector scribes** — each one tied to one upstream MCP. The 6 here are **generalist primitives** — not tied to a connector, consumed across many skills.

Both shapes call `wiki.add` (per `SPEC.md`). The difference is what fills `source_type`:

- Connector scribe: `source_type = "<mcp>"` (e.g. `granola`, `slack`).
- Generalist scribe: `source_type = "unstructured-text"` or `"live-web-fact"`.

`source_type` registration in `freshness_policy.HALF_LIVES` should add these two values when the scribes ship.
