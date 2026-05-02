# Phase 3 — Anabasis 5-day bootstrap (post-funding)

> **Goal**: Make the methodology runnable, not just narrative. The thing a YC customer can install on day 1 and have working by day 5.
>
> **Effort**: M (1-2 weeks, post-funding) | **Status**: pending | **Blocks**: Phase 4 (open-core release)
>
> **Source**: methodology/onboarding-toc.md + multi-repo-spine.md § Phase 4.

## Why this phase post-funding

Phases 0-2 are independently useful and ship pre-funding (CE v1.0 ships before YC submission and stays alive whatever happens). Phase 3 is **integration**: the Anabasis runtime + CE + connectors-from-syroco-product-ops compose into a single 5-day install. Until funding lands, the runtime is closed/private and we can't open-source the integration.

Phase 3 is where the YC pitch's 5-day methodology becomes a CLI invocation:

```bash
npx @anabasis/init        # day 0
anabasis entities extract # day 1
anabasis sources add notion --auth oauth  # day 2
anabasis skills add find-links audit-process # day 3
anabasis routine create monday-brief --cron "0 8 * * 1" # day 4
anabasis dashboard open    # day 5 — operating mode
```

Each command at minimum invokes CE primitives (Phase 1+2) underneath.

## Deliverables

### 3.1 — Day 0: `npx @anabasis/init` (M, 2-3 days, Anabasis runtime team)

Scaffolds the brain layout (calls CE's `init_brain.py`):
- `brain/` directory at chosen path
- Empty `events/`, `wiki/`, `audit/`, `cache/`
- `anabasis.config.yaml` with chosen embedding backend (BGE default), cache location, MCP server port
- Pre-downloads BGE-small model (avoids first-run latency surprise)
- Verifies system requirements (Python 3.10+, disk space, optional Anthropic key)

**CE deliverable**: ensure `init_brain.py` is callable from a TypeScript wrapper (npx layer) without Python-side ceremony. Document in find-links.md v0.2.

**Acceptance**: `npx @anabasis/init` on a fresh machine completes in <5 minutes including BGE download.

### 3.2 — Day 1: `anabasis entities extract` (S, 1 day, Anabasis runtime team)

Calls CE's `wiki_init.py` against existing repos/docs to seed entity pages.

If the user has existing `company-knowledge`-style markdown vault, `wiki_init.py` ingests it via WorkspaceSource and seeds entity pages. If not, it scans connected repos and Department Specs (output of install-department).

**CE deliverable**: ensure `wiki_init.py` handles three input shapes:
1. Existing Obsidian vault with `[[wiki-links]]` (preserve them)
2. Department Specs (output of install-department) — extract entities per section
3. Pure code repo — cluster + label (existing concept_labeler.py path)

**Acceptance**: 3 customer profiles tested:
- Profile A (no existing vault): seeds from code + Department Specs
- Profile B (existing vault): merges with code-extracted entities, no duplicates
- Profile C (markdown-heavy, no code): seeds from doc clusters

### 3.3 — Day 2: `anabasis sources add` (M, 2-3 days, Anabasis + syroco-product-ops)

Wires the first connector. The Anabasis runtime delegates to `syroco-product-ops`-style connector library:

```bash
anabasis sources add notion --auth oauth
# triggers OAuth flow, persists token in anabasis.config.yaml
# adds NotionSource to runtime config
# scheduled artifact discovery + event emission
```

**CE deliverable**: stability under high-volume event emission. A connector firing every 5 minutes shouldn't degrade `pack --wiki` latency.

**Performance acceptance**:
- Brain with 50k events / 1000 entity pages: `pack --wiki` p95 < 2s
- Connector emitting 100 events/min for 1 hour: no p99 degradation in `pack --wiki`
- `wiki.audit` runs on schedule without blocking `wiki.ask` calls

### 3.4 — Day 3: `anabasis skills add` (S, 1-2 days, Anabasis runtime team)

Customer picks ≥1 skill from the directory. CE = default `find-links`. Other skills compose:

```bash
anabasis skills add find-links     # = CE — already implicit in step 0
anabasis skills add audit-process  # NEW skill (v0.2 reference) — TBD
anabasis skills add sota-search    # NEW skill (v0.2 reference) — TBD
```

**CE deliverable**: nothing new — CE is a static skill registered at install. Verify CE's MCP surface is callable from Anabasis runtime as a remote skill.

### 3.5 — Day 4: Routine YAML (S, 1 day, Anabasis runtime team)

Customer wires routines (cron + signal-trigger):

```yaml
# routines/monday-brief.yaml
schedule: "0 8 * * 1"
steps:
  - skill: find-links
    args:
      query: "what changed in pipeline last week"
      budget: 8000
      since: 7d
  - skill: brief-formatter   # post-Phase-3 skill, syroco-product-ops
    args:
      output: slack://#weekly-brief
```

**CE deliverable**: nothing new — `pack --since 7d` (Phase 1.8) supports the routine.

### 3.6 — Day 5: Loop closure (M, 2 days, Anabasis runtime team)

`anabasis schedule` starts cron. Auditor proposes splits/merges. Customer reviews `audit/proposals.md`.

**CE deliverable**: ensure Auditor (Phase 1.7) runs cleanly under runtime cron without conflicting with concurrent `wiki.ask` calls.

### 3.7 — YC demo storyboard updated to 5-day path (S, ½ day)

The current 3-min Loom storyboard (`Repos/anabasis/demo/storyboard.md`) shows Syroco's brain mid-operation. Post-Phase-3, re-record OR add a second Loom showing the 5-day cold-start path:

Day 0 → npx init → brain layout
Day 1 → entities extract → first 30 entity pages
Day 2 → notion source → 200 new events
Day 3 → audit-process skill → first split proposal
Day 4 → monday-brief routine → first scheduled fire
Day 5 → dashboard → operating mode

**Recommendation**: keep the existing Syroco-mid-operation Loom for YC submission; add the 5-day Loom for HN launch (Phase 5).

### 3.8 — Bootstrap services package (S, 1 day, sales/CSM)

For pilot customers ($25k-$50k bootstrap fee per pricing tier in methodology):
- Anabasis team runs the 5-day install with the customer (high-touch)
- Customizes Department Spec templates per customer's domain
- Imports any existing knowledge sources
- Trains internal champion

**CE deliverable**: documented bootstrap runbook citing CE primitives (init_brain, wiki_init, MCP wiki.*).

By Q4 2026 per methodology: bootstrap automated through MCP install, no high-touch needed.

## Acceptance criteria (phase-level)

- [ ] All 8 deliverables shipped (collaboration with Anabasis runtime team)
- [ ] Cold-start bootstrap on a fresh customer environment completes in 5 working days
- [ ] CE primitives stable under runtime load (50k events, 1000 entities, concurrent calls)
- [ ] 3 customer profiles tested (no vault, existing vault, markdown-heavy)
- [ ] YC demo + HN demo both runnable

## Dependencies

- ✅ Phase 1+2 = v1.0 of CE (this phase composes them, doesn't extend them)
- ⚠️ Anabasis runtime (closed) — schedule with Anabasis team
- ⚠️ syroco-product-ops connectors (private) — extract NotionSource, HubSpotSource, etc. as the first 5 reference adapters
- ⚠️ install-department reference impl shipped (already done per spec/reference-skills/install-department.md)
- ⚠️ audit-process, sota-search v0.2 reference skills shipped (out of CE scope; tracked in Anabasis project)

## What this phase does NOT do

- No new CE features (Phase 1+2 already shipped what's needed)
- No new Source classes (NotionSource etc. live in syroco-product-ops, not CE)
- No runtime open-source (Phase 4)
- No public distribution (Phase 5)

## YC RFS alignment (preview)

| Pillar | Phase 3 contribution |
|---|---|
| **Executable runtime** | **5-day bootstrap turns the methodology into 6 commands. The runtime fires.** |
| **Installs through** | **Phase 3 IS the install. Day 0-5 = the install path.** |
| **Human knowledge** | **Day 2 connectors ingest from where humans put their knowledge (Notion / HubSpot / etc.).** Department Specs from install-department feed in Day 1. |
| **Connections** | **Day 2 OAuth / API auth flow. Day 3 skill composition. Day 4 routine wiring.** |
| AI | (inherited from Phase 1+2 — no new AI surface) |
| Skills that automate | Day 3 skill composition; Day 4 routine = automated weekly run |
| **Company brain** | **End-of-Day-5 = a working company brain with cron. Promise delivered.** |

7/7 served, with **Executable + Installs + Human knowledge + Connections + Company brain** at full strength. **Phase 3 is the YC pitch made operational.**
