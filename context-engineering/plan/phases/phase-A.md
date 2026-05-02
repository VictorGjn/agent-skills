# Phase A — Anabasis tie (½ day, blocks YC submission)

> **Goal**: Make CE's relationship to Anabasis spec **demonstrably honest** for YC reviewers — visible cross-references, no broken links, no aspirational claims.
>
> **Source**: `Repos/anabasis/plan/ce-anabasis-tie.md` (2026-04-30) + reconciliation with current spec state (2026-05-01).

## What changed since ce-anabasis-tie.md was written

`ce-anabasis-tie.md` planned 4 artifacts assuming CE was reference skill #1. The Anabasis spec evolved between Apr 30 and May 1 — `install-department` is now reference skill #1, and `find-links` (where CE slots in) is named as a v0.2 reference skill alongside `audit-process`, `sota-search`, `refresh-department`.

**This is better positioning**, not a regression. install-department captures human knowledge (procedures, taxonomy, cadence); find-links retrieves and connects across departments. The two pillars of the YC RFS framing — "human knowledge" + "AI" — map cleanly to install-department + CE.

## Already shipped (verify, don't rewrite)

| Artifact | Path | Status |
|---|---|---|
| Spec README | `Repos/anabasis/spec/README.md` | ✅ Lists install-department as #1, find-links as v0.2 |
| Skill ABC | `Repos/anabasis/spec/skill.md` | ✅ v0.1 — agentskills.io conformance + optional metadata |
| Reference skill #1 | `Repos/anabasis/spec/reference-skills/install-department.md` | ✅ Full v0.1 spec |
| CE conformance section | `agent-skills/context-engineering/SKILL.md:350-365` | ✅ Coherent — names install-department as #1, says CE is independently useful |

## Phase A deliverables (what's left)

### A.1 — `find-links.md` v0.2 trajectory stub (30 min)

**Path**: `Repos/anabasis/spec/reference-skills/find-links.md`

**Purpose**: Give YC reviewers a click-through from spec/README.md ("future reference skill `find-links`") that lands at a real document showing CE's intended role. Mark clearly as `v0.2` draft.

**Contents (outline)**:
- One-paragraph purpose: "Given a query and a corpus of Department Specs + repo files + entity pages, return relevant context within a token budget."
- **Status: `v0.2`-draft (incomplete contract)** — explicit so reviewers know it's intent, not a frozen contract.
- Reference impl: link to `agent-skills/context-engineering/`.
- v0.2 contract surface (provisional, will land with CE Phase 1+2):
  - `pack(query, budget, mode, top, graphify_path) → markdown`
  - `index_workspace(path)` / `index_github_repo(owner, repo, branch)`
  - `wiki.{ask, add, audit, export}` (Phase 2 output)
- "What `find-links` operates on": Department Specs (output of install-department), repo content, entity pages (CE wiki/).
- Open questions for v0.2: emit-back when find-links surfaces a missing link, conflict policy across Department Specs, freshness of entity pages.

**Why a stub and not a full spec**: v0.2 ABCs (EntityStore, Routine, SignalSource, emit-back contract) aren't frozen yet — those land WITH CE Phase 1. Writing a "complete" find-links.md before Phase 1 ships would freeze contracts on assumptions. The stub clarifies intent without binding the contract prematurely.

### A.2 — Update CE SKILL.md "Anabasis conformance" section (15 min)

**Path**: `agent-skills/context-engineering/SKILL.md:350-365`

**Why**: Current section is correct but misses the v0.2 hook. Reviewers reading CE first need to see where it's heading.

**Change**: Append one paragraph after line 365 that mentions CE's planned v0.2 role as `find-links` reference impl, with link to the stub.

### A.3 — Verify cross-link integrity (15 min)

Run through the YC reviewer click-path:
1. Anabasis landing page → spec README
2. spec README → install-department ✅ (lands at full spec)
3. spec README → find-links (v0.2) → **NEW STUB** (must not 404)
4. find-links stub → CE GitHub repo → SKILL.md
5. CE SKILL.md "Anabasis conformance" → spec/skill.md ✅
6. CE SKILL.md "Anabasis conformance" → install-department ✅

If any step 404s, fix before YC submission Mon May 4.

## Acceptance criteria

- [ ] `Repos/anabasis/spec/reference-skills/find-links.md` exists with `v0.2-draft` warning
- [ ] CE SKILL.md "Anabasis conformance" section mentions v0.2 find-links role
- [ ] All 6 cross-links above resolve
- [ ] No claim in any artifact references runtime ABCs as "shipped" (they're v0.2-pending)

## What this phase does NOT do

- Does not write the full v0.2 find-links contract (deferred to end of CE Phase 2)
- Does not implement runtime ABCs (lives in Anabasis runtime, not CE)
- Does not migrate company-knowledge (long-term concern)
- Does not start Phase 0 work (incremental indexing, etc. — separate phase)

## YC RFS alignment (preview — full audit in plan/audits/phase-A.md)

| Pillar | How Phase A serves it |
|---|---|
| Executable runtime | install-department + find-links spec'd; runtime fires both |
| Installs through | install-department captures install path per department |
| Human knowledge | Department Spec format (7 sections) is the human-knowledge schema |
| Connections | install-department probes MCP-connected tools |
| AI | find-links (CE) does semantic + multi-hop retrieval |
| Skills that automate | install-department + find-links both shippable conformant skills |
| Company brain | Department Specs + entity pages = the brain artifact |

7/7 pillars covered at the spec level. Implementation lands in Phase 1+2.
