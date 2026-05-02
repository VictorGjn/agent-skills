# Wave 0 sign-off — closed-loop demo

> Per `plan/prd-closed-loop.md` rollout plan. Wave 0 = "closed-loop demo,
> Victor only, on a synthetic 50-entity brain. M1–M7 working end-to-end
> on a fixture corpus; AC1–AC8 pass."

## How to run

```bash
cd context-engineering
python -m scripts.wiki.demo.run_demo            # uses a temp brain dir
python -m scripts.wiki.demo.run_demo --brain ./brain-demo --keep
```

The demo seeds a synthetic ~50-event brain across three scopes
(`default`, `competitive-intel`, `lead-qual`), exercises the full closed
loop end-to-end, and asserts every PRD acceptance criterion in scope for
Wave 0.

## What it exercises

```
seed_brain (44 events)
   │
   ▼
EventStreamSource.emit_events  ◄── M1
   │
   ▼
events/<date>.jsonl
   │
   ▼
wiki_init.write_wiki  ◄── M2 (idempotent, scope-preserving)
   │
   ▼
wiki/<slug>.md (45 entity pages)
   │
   ├──▶ validate_page  ◄── M3
   │      schema-version refusal
   │
   ├──▶ wiki.ask --scope=...  ◄── M7 (mcp_server)
   │      namespace filter
   │
   └──▶ audit.run_audit  ◄── M4
          ├── stale-supersession
          ├── freshness-expired
          └── slug-collision
          │
          ▼
       audit/proposals.md
          │
          ▼
       wiki.audit (mcp_server)  ◄── S3
```

## Acceptance criteria coverage

| AC | What it tests | Demo result (latest run) |
|---|---|---|
| **AC1** | EventStreamSource round-trip < 100ms | ✅ 0.3 ms |
| **AC2** | wiki_init idempotent across stable runs | ✅ 45/45 unchanged |
| **AC3** | Auditor flags stale supersession | ✅ 1 flag |
| **AC4** | Auditor flags freshness expired | ✅ 17 flags (3 seeded stale + 14 ancient) |
| **AC5** | wiki.ask scope filter (no leakage either direction) | ✅ 4/4 directional checks pass |
| **AC6** | validate_page passes on rendered pages + refuses stale schema with --rebuild remediation | ✅ both |
| **AC7** | Slug-collision rule (numeric suffix + index footnote) | unit-tested in PR #22 — not exercised by demo seed |
| **AC8** | Emit-then-ask race documented in `next_tool_suggestions` | unit-tested in PR #21 |

The demo touches the cron-driven path (everything except S1 GraphifyWikiSource which is Wave 1).

## What gets generated

A typical run produces (under the brain dir):

```
brain/
├── events/2024-05-02.jsonl    (~44 lines, one per claim seeded)
├── wiki/                      (~45 entity pages)
│   ├── _index.md              (collision footnotes if any)
│   ├── auth-middleware.md     (scope: default)
│   ├── acme-pricing-q2.md     (scope: competitive-intel)
│   ├── decision-acme-pricing-v1.md  (scope: competitive-intel, kind: decision, superseded_by: ent_acme_v2)
│   ├── lead-acme-corp.md      (scope: lead-qual)
│   └── ...
└── audit/
    └── proposals.md           (Stale references / Freshness expired / Slug collisions)
```

## Wave 0 sign-off checklist

- [ ] `python -m scripts.wiki.demo.run_demo` reports "All AC1-AC6 checks PASSED"
- [ ] Spot-check 3 randomly-selected entity pages — each passes `validate_page` manually
- [ ] `audit/proposals.md` is human-readable, not auto-generated noise
- [ ] `wiki.audit refresh=True` via the MCP returns the same proposals content
- [ ] Telemetry events (`tool.call`, `audit.flagged`, `freshness.expired`) appear on stderr during the run

## Wave 1 prerequisites (post sign-off)

- **S1 `GraphifyWikiSource`** (PRD M2 deferred): consume `graphify-out/wiki/` if present and re-emit in CE schema. Lands when the first non-Python skill needs it.
- **First real cron routine** (`product-signals-pipeline` per memory): emit events from real upstream sources (Granola transcripts, Notion meetings, Gmail OnWatch) into the brain. Auditor surfaces real flags. AC9 from the PRD passes.
- **Source-aware scope derivation in `wiki_init`**: today the `scope` field is preserved across runs (Wave 0 fix in this PR) but new-page scope still defaults to the caller's `--scope` arg. Wave 1 derives scope from event source_type or hint pattern at consolidation time.
