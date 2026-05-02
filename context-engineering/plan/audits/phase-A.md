# Phase A audit — YC RFS alignment

**Audit reference**: "the executable runtime that installs through human knowledge, connections and AI to build skills that automate your company brain."

## Scoring matrix

| Pillar | Phase A delivers? | How |
|---|---|---|
| **Executable runtime** | Indirect | Phase A ships *spec*, not runtime. But: spec names install-department + find-links as runnable reference impls — no aspirational placeholders. |
| **Installs through** | ✅ | install-department (already shipped, this phase verifies tie) IS the install path. find-links stub names CE as the v0.2 retrieval install. |
| **Human knowledge** | ✅ | Department Spec format (7 canonical sections) is the human-knowledge schema. install-department captures it via interview + tool probing. |
| **Connections** | ✅ | install-department probes Pipedream/MCP-connected tools as part of capture. find-links stub explicitly operates across Department Specs + repo content + entity pages. |
| **AI** | ✅ | find-links stub commits to semantic + multi-hop + lens at v0.2 (provisional, will tighten with CE Phase 1+2). |
| **Skills that automate** | ✅ | Two reference skills (install-department v0.1, find-links v0.2-draft) both shippable as agentskills.io-conformant skills. |
| **Company brain** | ✅ | brain/ layout in find-links § 4 makes the brain artifact concrete: departments/ + repos/ + events/ + wiki/ + audit/. |

**Score: 6/7 with 1 indirect.** The indirect (executable runtime) is by design — Phase A is spec/tie work, not runtime work. Runtime evidence comes from the YC demo (Loom on Fri May 1) and the methodology doc (already shipped).

## Drift risks flagged

1. **find-links.md draft status** — using `v0.2-draft` in a public spec is unusual. **Mitigation**: explicit warning at top of doc; reviewers see it's intentional. The alternative (omit find-links entirely until v0.2 freezes) loses the YC click-through.

2. **CE SKILL.md "trajectory" claim** — promising find-links role at v0.2 binds CE to deliver Phase 1+2. **Mitigation**: CE Phase 1+2 are already in the unified roadmap; trajectory is a commitment to existing plan, not new scope.

3. **brain/ layout** — find-links § 4 lists a brain layout (`departments/`, `repos/`, `events/`, `wiki/`, `audit/`). This is the merged CE+Anabasis layout — not what either ships today. **Mitigation**: stub status protects this; Phase 1 reconciles. **Action**: cross-reference in plan/findings.md.

## What this audit does NOT cover

- The actual YC demo video (separate critical-path item, not CE's responsibility)
- Anabasis runtime conformance (closed for 90 days, no public artifact to audit)
- company-knowledge migration cost (unresolved, Phase 1 concern)

## Recommendation

**Phase A is YC-submission-ready.** Ship the find-links stub + CE SKILL.md update before Mon May 4 8pm PT. No blockers identified.

The unified roadmap continues into Phase 0 immediately — Phase A is documentation/spec work that doesn't gate Phase 0 implementation.

## Cross-link integrity check (run before YC submission)

- [ ] anabasis-seven.vercel.app → /spec → spec/README.md ✅
- [ ] spec/README.md → install-department.md ✅
- [ ] spec/README.md → find-links.md (NEW STUB) — verify lands at the new file
- [ ] spec/README.md → skill.md ✅
- [ ] CE SKILL.md "Anabasis conformance" → spec/skill.md ✅
- [ ] CE SKILL.md "trajectory" → find-links.md (NEW) — verify lands at new file
- [ ] CE SKILL.md → install-department repo path ✅

Run as a final pre-flight Sun May 3 evening.
