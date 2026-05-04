# Proposal — `wiki.impact_of` MCP verb

- **Status:** Draft (PR #?? — code below this proposal in same PR).
- **Date:** 2026-05-04
- **Supersedes:** none. Implements the only surviving item from `plan/proposals/job-shaped-mcp-surface.md` (RFC archived; rename premise abandoned per `~/.claude/handoffs/ce_mcp_jobs_surface.md` reconciliation 2026-05-04).
- **Triggered by:** Phase 1.2 schema landed in PR #20 (`supersedes:` / `superseded_by:` / `valid_until:`) + PR #22 (`validate_page` + freshness) + PR #23 (Auditor). The graph primitive exists; no verb surfaces it.
- **Scope:** local stdio MCP only (`scripts/mcp_server.py`). Promotion to deployed v1 is a separate concern; deployed surface gap is documented in SPEC §3.0.2.

## Job statement

*"Given an entity in the wiki, what's affected by it?"*

Cross-corpus phrasing (the cross-corpus naming argument from the abandoned RFC still holds — same primitive serves engineers, CSMs, execs, marketers without renaming):

- engineer: *"What does PolarRouting touch?"* → callers, extenders, dependent tests
- exec: *"What's the fallout of the Q2 freeze?"* → initiatives slipped, teams blocked
- CSM: *"Which accounts use the deprecated feature?"* → contracts, SLAs, renewal risk

## Signature

```python
@mcp.tool(name="wiki.impact_of")
def wiki_impact_of(
    entity: str,                          # slug, id, or title (case-insensitive)
    brain: str | None = None,             # CE_BRAIN_DIR or ./brain
    max_hops: int = 3,                    # BFS depth cap
    relation_kinds: list[str] | None = None,  # filter: ["mentions", "supersedes"]; None = all
    min_weight: float = 0.0,              # drop edges below this weight
    budget: int = 8000,                   # markdown char budget × 4
    include_hubs: bool = False,           # bypass hub stop-list
) -> str
```

Returns markdown with: entity header, affected-entity table (slug / kind / hops / risk / path / sources), recall annotation, hub-exclusion list when applicable.

## Algorithm

1. **Resolve entity argument** — match against `slug`, `id`, or case-insensitive `title` from frontmatter. Ambiguous → `ENTITY_AMBIGUOUS` markdown comment with candidates. Missing → `ENTITY_NOT_FOUND` with closest-3 slug suggestions (Levenshtein).
2. **Load pages** — reuse `audit._load_pages(wiki_dir)` to get `slug → {fm, body, sources}`. Inherits validate_page schema gating (1.2 only).
3. **Build inbound mention index** — single pass: for each page, harvest `wikiref.parse_wikirefs(body)` filtering to `kind == "slug"`; record `target_slug → [(source_slug, hop_weight=1.0)]`.
4. **Build supersession edges** — for each `kind: decision` page with non-null `superseded_by`, add edge `superseded_by → predecessor_slug` with weight 1.0 (a superseded decision still affects pages that referenced it).
5. **BFS from resolved slug** to `max_hops`, dedupe by visited_set. Track `(slug, hops, edge_path)` per visited node.
6. **Hub stop-list** — count inbound mentions per slug across the whole corpus. If `inbound_count(target) > HUB_THRESHOLD` (default 10), skip traversal *through* that target (still report it as 1-hop affected if directly mentioned, but don't fan out from it). Override with `include_hubs=True`. Excluded hubs surface in the recall annotation.
7. **Risk scoring** —

   ```
   risk = (1.0 / (1 + hops))
        × kind_multiplier
        × freshness_multiplier
   kind_multiplier:    decision=1.0, component=0.9, concept=0.8, code=0.7, actor/process/metric=0.6
   freshness_multiplier: clamp(freshness_policy.compute_freshness_multi_source(...), 0.3, 1.0)
                         (lower bound 0.3 — stale entities still register impact, just downweighted)
   ```

8. **Filter by `min_weight` and `relation_kinds`**. Pack as markdown within `budget × 4` char cap, truncated like `wiki.ask`.

## Output shape

```markdown
<!-- wiki.impact_of entity=auth-middleware hops≤3 brain=/path -->

## auth-middleware
kind: concept · scope: default · last_verified: 2026-04-30

## Affected entities — 7 found, recall: 100%

| slug | kind | hops | risk | edge |
|---|---|---|---|---|
| token-store | concept | 1 | 0.40 | mentions |
| session-policy | concept | 1 | 0.40 | mentions |
| decision-jwt-rotation | decision | 1 | 0.50 | mentions |
| rate-limiter | concept | 2 | 0.27 | mentions → mentions |
| ... |

## Sources

- **token-store** — `wiki/auth-middleware.md` body L42
- **session-policy** — `wiki/auth-middleware.md` body L58
- **decision-jwt-rotation** — `wiki/auth-middleware.md` body L71
- ...
```

When hubs were excluded:

```
## Affected entities — 12 found, recall: best-effort
Skipped traversal through 2 hub entities: logger-service (47 inbound), config-loader (31 inbound).
Pass include_hubs=true to bypass.
```

## Recall guarantee

- **100%** when the BFS visited every reachable node within `max_hops` AND no hub stop-list trips.
- **best-effort** when hubs were excluded. The list of skipped hubs surfaces explicitly so callers can pivot to `include_hubs=true` when needed.

## Edge cases handled

- Entity slug exists but has zero inbound mentions → return entity header + "No affected entities found at hops≤N". Not an error.
- Cycle in supersession chain → BFS visited_set prevents infinite loop.
- Missing brain dir → mirror `wiki.ask` behavior (HTML-comment error, no exception).
- Schema-stale page (1.0/1.1) → `validate_page` skips, telemetry warns; impact_of degrades gracefully (mentions inside skipped pages aren't harvested).
- `code_index.json`-backed code refs (`[[src/foo.ts#bar]]`) — **out of scope for v0.1**. v0.2 will follow the symbol → file-path edge once the AC tests pass on wiki-only mentions. Tracked as open question Q2 below.

## Out of scope (v0.1)

- Code-corpus impact (file → caller via `code_index.json`). Substrate exists, just not wired.
- Cross-corpus impact (entity in one scope affecting entities in another). Single `scope` per call for v0.1.
- Risk weights tuned per corpus type. Defaults are reasonable; tune from telemetry.

## Open questions

- **Q1.** Hub threshold — 10 inbound is a guess. Telemetry will tell. Keep configurable via env `CE_IMPACT_HUB_THRESHOLD`.
- **Q2.** Code-corpus integration — should `[[src/foo.ts#bar]]` refs feed impact_of via `code_index.json`, or stay in `lat.refs` only? Lean: yes, but follow-up PR after v0.1 lands.
- **Q3.** `relation_kinds` taxonomy — `["mentions", "supersedes"]` covers wiki today. When events.jsonl edge types land (Anabasis runtime), extend.
- **Q4.** Promotion to deployed MCP — only after the production v1 (gap from §3.0.2) graduates. Local stdio first.

## Test cases (in `scripts/tests/test_impact_of.py`)

1. **Direct mentions** — entity X mentioned in body of Y → impact_of(X) returns Y at hops=1.
2. **Multi-hop** — X mentioned in Y, Y mentioned in Z → impact_of(X, max_hops=2) returns Y@1 + Z@2.
3. **Supersession** — decision D superseded_by D' → impact_of(D) returns D' at hops=1.
4. **Hub stop** — entity H with 15 inbound + chain X → H → Y → impact_of(X) does NOT include Y; recall = "best-effort"; hub list mentions H.
5. **Unknown entity** — impact_of("not-a-slug") returns ENTITY_NOT_FOUND with suggestions.
6. **Cycle** — A mentions B, B mentions A → impact_of(A) terminates, returns B once.
7. **Empty corpus** — no `wiki/` dir → graceful HTML-comment error.

## What this proposal does NOT change

- SPEC v1.0-rc2 §3 (deployed MCP target) — `wiki.impact_of` is local stdio only per §10b.
- Tool-naming convention — `wiki.*` namespace per §10b.
- Hub stop-list curation — defaults to global threshold; per-corpus override is a v0.2 concern.
- `decision_log` (v1.1) — separate verb, separate substrate (wiki only, decision-kind pages).
