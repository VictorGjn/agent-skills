# Step 5 — EntityStore and wiki

> **Compounding memory.** Events from any number of Sources accumulate. The wiki layer consolidates them into entity pages where every claim resolves to a source.

## What you'll do

Run `wiki_init` on a brain you populated in step 4, inspect a generated entity page, then run `audit` to see what the synthesizer would change next.

## The three tiers

The brain is a directory with three layers:

```
brain/
├── raw/        verbatim source bytes, content-addressed
├── events/     append-only JSONL of extracted claims (one event = one claim)
└── wiki/       consolidated entity pages (one page = one entity)
```

| Tier | Lifetime | Mutability | Who writes |
|------|----------|------------|------------|
| `raw/` | Permanent | Append-only | Sources via `fetch()` |
| `events/` | Permanent | Append-only | Sources via `emit_events()` |
| `wiki/<slug>.md` | Living | Consolidated | `wiki_init.py`, `wiki_add` MCP tool |

The wiki is *generated*. Humans curate at the edges (renaming entities, splitting/merging, marking authoritative); the synthesizer compounds.

## Seed the wiki from events

```bash
python3 scripts/wiki/wiki_init.py --brain ./brain
```

The seeder reads every event, groups them by entity (using slug heuristics + clustering), and writes one `wiki/<slug>.md` per group. Each page has:

- Frontmatter with `slug`, `id`, `scope`, source signatures
- A "Sources" table — every cited claim resolves to `file:line + content_hash + ts`
- A consolidated body, generated from the strongest claims

Add `--rebuild` to regenerate every page from scratch (drops human-curated edits — use carefully).

## Read a page

```bash
ls brain/wiki/
cat brain/wiki/oklch-palette.md
```

Every paragraph in the body is backed by one or more rows in the Sources table. There are no orphan claims.

## What the synthesizer guards against

The wiki layer is built around a hard rule: **consolidate only on cosine drift, never on every event.** This is the GAM-grade semantic-shift detector — when events for an entity stay near the existing consensus, the page doesn't churn. When they drift, a new consolidation pass fires.

This is what keeps the wiki from collapsing into noise as event volume grows.

## Audit what to change next

```bash
python3 scripts/wiki/audit.py --brain ./brain
```

The Auditor scans `wiki/` and writes `brain/audit/proposals.md` with:

- **Splits** — pages where claims have diverged into two distinct concepts
- **Merges** — near-duplicate slugs (`oauth-flow` vs `oauth-login-flow`)
- **Contradictions** — claims that disagree, with provenance for each side
- **Dead links** — `[[wiki-links]]` pointing at slugs that don't exist
- **Stale supersessions** — entities marked superseded but still active
- **Freshness expiries** — entities older than their freshness policy

Proposals are *suggestions*, not auto-applied. A human (or a curator agent) reviews and accepts.

## Pack against the wiki

Once the wiki has pages, the packer can use them as another corpus:

```bash
python3 scripts/index_workspace.py ./brain/wiki
python3 scripts/pack_context.py "OKLCH palette decision" --budget 8000
```

The wiki is just markdown — the indexer treats it the same as any other workspace. Multi-hop reasoning (step 3) follows `[[wiki-links]]` between pages.

## What this gets you

| Without | With |
|---------|------|
| RAG over raw transcripts/docs | Provenance-tracked entity pages |
| Stale snippets with no audit trail | Every claim has `file:line + hash + ts` |
| Re-derive context every query | Compounding wiki you can curate |
| "Does this still apply?" → re-read source | Auditor surfaces stale supersessions |

## Concept

The packer (steps 0-3) is the *retrieval surface*. The wiki (this step) is the *memory surface*. Steps 0-4 work without ever building a wiki — pure retrieval over raw structure. The wiki is what turns the engine from a smarter RAG into a brain that compounds.

## Where to next

You've walked the engine end-to-end. Three good follow-ups:

- **[`SPEC-mcp.md`](../../SPEC-mcp.md)** — the full MCP tool surface (15 tools). Everything in this ladder is reachable from MCP.
- **[`docs/vs-lat-md.md`](../vs-lat-md.md)** and **[`docs/vs-context-signals.md`](../vs-context-signals.md)** — where this engine sits relative to adjacent tools.
- **[`scribes/`](https://github.com/victorgjn/agent-skills/tree/main/scribes)** — real connector implementations: Granola, Slack, HubSpot. Same `Source` ABC as step 4.

The skill is the engine. The runtime that schedules indexing, scribing, and consolidation is [Anabasis](https://github.com/VictorGjn/anabasis). The catalogue of connectors is the scribes skill. This ladder stays in the engine.
