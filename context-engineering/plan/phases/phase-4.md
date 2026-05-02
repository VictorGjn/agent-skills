# Phase 4 — Open-core release + Anabasis spec v0.2

> **Goal**: Open-source the Anabasis runtime. Freeze Anabasis spec v0.2 (EntityStore + SignalSource + Routine ABCs). Tag CE v0.2.0 as the canonical `find-links` reference impl. Ship the HN essay.
>
> **Effort**: S (release work, ~1 week elapsed) | **Status**: pending | **Trigger**: Day 90 post-funding OR pilot #3 lands, whichever later
>
> **Source**: methodology/onboarding-toc.md § "What we sell vs what we share" + multi-repo-spine.md § Phase 5.

## Why this phase

The 90-day closed window is bought time to prove the orchestrator on three real customers without commoditization. At Day 90, the runtime opens. The flywheel — open spec + open runtime + paid hosted plane — kicks in.

For CE specifically: Phase 1+2 produced the EntityStore + SignalSource ABC reference impls. Phase 4 freezes them as Anabasis spec v0.2 (drop the `-draft` suffix on `find-links.md`). CE's role finally has a documented, public contract.

## Deliverables

### 4.1 — Anabasis runtime open-source (Anabasis team, not CE)

License flip: `Anabasis/runtime/LICENSE` BUSL-1.1 → Apache-2.0. Repo opens publicly. README at top of Anabasis repo updates.

**CE deliverable**: nothing — CE is already MIT and unaffected by runtime license.

### 4.2 — Anabasis spec v0.2 freeze (M, 2-3 days)

Update `Repos/anabasis/spec/`:

| File | Change |
|---|---|
| `spec/skill.md` | Bump v0.1 → v0.2; add EntityStore + SignalSource + Routine + emit-back to "What's new in v0.2" |
| `spec/runtime/entity-store.md` | NEW — documented from CE's reference impl (started in Phase 1.9) |
| `spec/runtime/signal-source.md` | NEW — same |
| `spec/runtime/routine.md` | NEW — documents the YAML routine format from Phase 3.5 |
| `spec/runtime/scheduler.md` | NEW — documents the cron + signal-trigger semantics |
| `spec/runtime/conflict-resolution.md` | NEW — when two sources disagree, who wins (per pitch) |
| `spec/reference-skills/find-links.md` | Drop `-draft`; resolve open questions; reference v0.2 ABCs |
| `spec/reference-skills/install-department.md` | Update to reference v0.2 ABCs (was v0.1) |
| `spec/reference-skills/audit-process.md` | NEW v0.2 reference skill |
| `spec/reference-skills/sota-search.md` | NEW v0.2 reference skill |
| `spec/reference-skills/refresh-department.md` | NEW v0.2 reference skill |
| `spec/README.md` | Update reading order; v0.2 bumped |

**CE deliverable**: review every doc that references CE; ensure consistency with CE v0.2.0 implementation.

**Acceptance**: spec/skill.md v0.2 published; SkillCheck Free updated to recognize v0.2 fields.

### 4.3 — CE tagged v0.2.0 (S, ½ day)

```bash
git tag v0.2.0 -m "Anabasis spec v0.2 find-links reference implementation"
git push origin v0.2.0
```

Update SKILL.md:
- `version: 0.3.0` → `version: 0.2.0` (semver alignment with spec — bumping major when v1 stabilizes)
- `description`: replace "v0.2-draft" with "v0.2" wherever it appears
- "Anabasis conformance" section: drop draft language

Wait — current SKILL.md is already at `version: 0.3.0`. Decision needed: align with spec (downgrade to 0.2.0 to match spec v0.2) OR keep CE on its own semver (0.3.0+). **Recommend**: keep CE versioning independent (0.4.0 at this milestone), document spec compatibility separately in frontmatter as `anabasis_spec_compat: v0.2`.

**Acceptance**: CE GitHub release page shows v0.2.0 (or v0.4.0 — decide); release notes reference spec v0.2.

### 4.4 — HN front-page essay (M, 3-5 days, marketing/Anabasis team)

Drop the launch essay. Title candidates:
- "We open-sourced the orchestration runtime for company-knowledge agents"
- "Temporal for company-knowledge agents — the runtime is now open"
- "What we learned building the executable company brain (now open-source)"

Essay structure (per CPO review in ROADMAP.md):
1. The 90-day proof: 3 customer outcomes
2. The architecture: install-department + find-links + audit-process composing on a runtime
3. What's open (spec + runtime + reference skills) and what's paid (hosted)
4. The link to clone + try in 5 minutes

**CE deliverable**: provide concrete numbers from regression eval + 3 customer brains' size + `pack --wiki` latency stats. Ground the essay's claims in CE telemetry (Phase 0.5.5).

**Acceptance**: essay on HN front page within 72h of submission. Track inbound install rate.

### 4.5 — CE downloads spike capture (S, ½ day)

When the essay drops, expect spike in CE GitHub clone + `pip install` + Cursor `.cursorrules` snippet copies.

**CE deliverable**: GitHub release page polished; README clear; first-run experience verified.

Pre-flight checklist before essay drop:
- [ ] `pip install -e .` from a fresh checkout works in <30 seconds
- [ ] `pack "test"` runs end-to-end without errors
- [ ] BGE-small downloads cleanly
- [ ] MCP server starts on `127.0.0.1:8000` with `--auth`
- [ ] All 14 advertised tree-sitter languages parse correctly
- [ ] SKILL.md headlines the engine framing (post-rewrite)

**Acceptance**: 0 broken-install bug reports in first 48h post-essay. (Some bugs expected, but install-blocking ones tracked separately.)

### 4.6 — Hosted MCP endpoint v1 (M, 1 week, Anabasis team)

A free-tier hosted CE MCP server: `mcp.anabasis.tech/find-links`. Lets users try CE without `pip install`. Limits: 100 packs/day per IP, no persistent index (re-index per query).

**CE deliverable**: MCP server (Phase 0.11) ready to run as a hosted service. Acceptance: 99% uptime over 30 days.

This is also the seed for Anabasis's commercial hosted orchestration cloud (paid plane).

## Acceptance criteria (phase-level)

- [ ] Anabasis runtime open-sourced
- [ ] Spec v0.2 frozen + published
- [ ] CE tagged at the v0.2-aligned version
- [ ] HN essay landed (≥front page)
- [ ] CE first-run experience verified pre-essay
- [ ] Hosted MCP endpoint v1 live
- [ ] No regression vs Phase 0+0.5+1+2 acceptance criteria

## Dependencies

- ✅ Phase 1+2 = CE v1.0 of Wiki + retrieval surface
- ✅ Phase 3 = 3 pilot customers running (the proof for the essay)
- ⚠️ Anabasis runtime maturity (closed-source progress; outside CE)
- ⚠️ Three pilots delivered (commercial milestone, not engineering)
- ⚠️ HN essay timing aligned with runtime open-source date

## What this phase does NOT do

- Does not extend CE features (those land in Phase 5+ as needed)
- Does not change CE scope (engine framing remains)
- Does not start Phase 5 distribution work (separate phase, independent cadence)

## YC RFS alignment (preview)

| Pillar | Phase 4 contribution |
|---|---|
| **Executable runtime** | **Runtime open. Spec v0.2 documents what executes.** |
| Installs | Hosted MCP endpoint = zero-install evaluation path |
| Human knowledge | Reference skills (`audit-process`, `sota-search`, `refresh-department`) extend the human-knowledge surface |
| Connections | (inherited — Phase 1 Source ABC stable) |
| AI | (inherited — Phase 1+2 AI surface stable) |
| **Skills that automate** | **Five reference skills published at v0.2 (install-department, find-links, audit-process, sota-search, refresh-department) — the skill ecosystem is real.** |
| **Company brain** | **Three customer brains running publicly. Proof, not pitch.** |

7/7 served. Phase 4 is the **commodification phase** — turning private engineering into a public open-core product.
