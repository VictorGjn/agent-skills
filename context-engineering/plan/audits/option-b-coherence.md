# Option (b) coherence audit (2026-05-01, pre-YC-submission)

**Subagent**: senior-pm. **Verdict**: **PARTIAL** — 7 edited docs are individually coherent; **15+ unedited docs still pitch open-core / Temporal "Day 90 open-source flip"**, including reviewer-facing live landing page.

## Strongest remaining risk

The **landing page (anabasis-seven.vercel.app) and the founder-video script are still pitching "Temporal's playbook → open-sourced post-funding"** while the YC essay pitches "Cursor/Vercel/Snowflake → closed orchestration on open primitives." A reviewer who reads essay § 6, then clicks through to anabasis-seven.vercel.app, sees "Runtime — closed for 90 days, then open-sourced" prominently. Reads as "founders haven't decided their model" — exactly the criticism YC latches onto.

## Critical reviewer-facing fixes (before YC submit Mon May 4 8pm PT)

| Path | Status | Action |
|---|---|---|
| `Repos/anabasis/site/index.html` | LIVE on anabasis-seven.vercel.app | Fix lines 7, 9, 696, 757-772 (Temporal anchor + "closed 90 days then open-sourced" feature card). **Then `vercel --prod`.** |
| `Repos/anabasis/brand/landing-page.md` | Source for site | Fix lines 14, 34, 45-51, 93-94, 133, 158 |
| `Repos/anabasis/pitch/1-paragraph.md` | Founder-video script source | Fix lines 5, 27, 42-44, 61, 73-76 (Temporal anchor) |
| `Repos/anabasis/pitch/founder-video-script.md` | **Recording Sunday** | Fix lines 16, 38-40, 54-56, 68, 79-80, 96-98, 153 |
| `Repos/anabasis/demo/storyboard.md` | **Demo recording Friday** | Fix lines 90-93, 159 ("closed for 90 days, then open-sourced post-funding") |

## Essay-click-through fixes (also before submission)

| Path | Issue lines | Action |
|---|---|---|
| `yc-application/essays-outline.md` | § 1 lede ("Temporal for your company's brain"); § 3 line 94 ("Open-core distribution flywheels"); § 5 lines 146, 153 (Temporal/Airflow open-core precedents); § 7 line 234 ambiguity | Replace lede + open-core flywheel framing with Cursor/Vercel/Snowflake; for § 5 add disambiguation sentence "shape, not licensing — runtime stays closed" |
| `methodology/onboarding-toc.md` | Header lines 3-12 + lines 165, 168, 194, 219 | Header rewrite + retitle "Comparable closed-orchestration revenue" + line 194 cell + line 219 swap "Open-core orchestration" → "Open primitives + closed orchestration" |
| `spec/README.md` | Lines 5-9 + line 25 | Strike "open-source after"; rewrite line 25 to remove "when that runtime open-sources" |
| `spec/skill.md` | Line 89 "open-sources, then land here as `spec/runtime/`" | Rewrite (closed runtime; only stable shape lands in spec) |
| `spec/reference-skills/find-links.md` | Line 8 "as the runtime open-sources" | Rewrite "as the runtime ABCs publish" |
| `plan/multi-repo-spine.md` | Lines 36, 42, 58 (ASCII), 93, 159 (ASCII), 172 (license split) | Multi-line rewrites; line 172 license split is now answered (option b) |
| `plan/ce-anabasis-tie.md` | Lines 41, 133, 144 | Surgical swaps |

## Agent-facing fixes (post-submission OK)

| Path | Issue | Action |
|---|---|---|
| `Repos/anabasis/plan/BUILD-ROADMAP.md` | § "Licensing posture" header (line 10): "Engine substrate (CE) is **closed, not open-source**" | **Critical inversion** — directly contradicts option (b). Fix immediately or future agents derive option (a) from memory. |
| `agent-skills/context-engineering/plan/findings.md` | Lines 61, 102-104 ("engine substrate is closed") | Same inversion |
| `agent-skills/context-engineering/ROADMAP-v4.md` | § 2 sequencing diagram column "OPEN-CORE (Day 90+)"; line 45 cell "Open-core release" | Diagram label + cell rename |
| `agent-skills/context-engineering/plan/phases/phase-4.md` | Entire file titled "Open-core release"; 4.1 deliverable "Anabasis runtime open-source" + BUSL→Apache flip | Rewrite to "v1.0 commercial release" |
| `agent-skills/context-engineering/plan/phases/phase-3.md` | Lines 5, 158 reference Phase 4 as "open-core" | Surgical |
| `agent-skills/context-engineering/plan/phases/phase-5.md` | Lines 3, 11, 28, 118, 144, 168 — "open-core flywheel" + "open-source companion repos" | Surgical |
| `agent-skills/context-engineering/plan/audits/phase-4.md` | Lines 19, 28, 34, 38, 50, 52, 59, 67, 72 — entire audit assumes runtime opens | Rewrite for option (b) |
| `agent-skills/context-engineering/plan/audits/{phase-5,phase-0-5,phase-A,final-cross-phase}.md` | Open-core flywheel references | Surgical |
| `agent-skills/context-engineering/plan/task_plan.md` | Line 33 Phase 4 row "Open-core release + spec v0.2" | Rename |

## Per-doc verdict on the 7 edited docs

- **CE SKILL.md** — ✅ cleanest, fully coherent
- **methodology/onboarding-toc.md** — ⚠️ table coherent but header + 4 other sections still pre-pivot
- **spec/README.md** — ⚠️ License section coherent; lines 5-9 + 25 vestigial
- **find-links.md** — ⚠️ § 2 coherent; line 8 vestigial
- **ROADMAP-v4.md** — ⚠️ text body coherent; diagram labels stale
- **multi-repo-spine.md** — ⚠️ End state + Phase 5 coherent; 7 more references inside
- **ce-anabasis-tie.md** — ⚠️ closed-runtime line coherent; 3 more references inside
- **yc-application/essays-outline.md** — ⚠️ §§ 2,4,6,11 clean; § 1 lede + § 3 + § 5 + § 7 still mix anchors

## Recommended Sat/Sun work order before YC submission

1. **Sat morning**: site/index.html + redeploy (highest reviewer-risk)
2. **Sat midday**: brand/landing-page.md (so site source matches)
3. **Sat afternoon**: pitch/founder-video-script.md (recording Sunday)
4. **Sat evening**: pitch/1-paragraph.md + demo/storyboard.md
5. **Sun morning**: essay § 1 lede + § 3 + § 5 + § 7 disambiguation
6. **Sun midday**: spec/README.md + spec/skill.md + find-links.md line 8 (small)
7. **Sun afternoon**: methodology header + multi-repo-spine + ce-anabasis-tie surgical fixes
8. **Mon morning**: BUILD-ROADMAP.md § Licensing posture + findings.md (agent-facing, but fix before any new agent session)

Phase-4/5 plan + audits = post-submission cleanup. They don't reach YC reviewers.
