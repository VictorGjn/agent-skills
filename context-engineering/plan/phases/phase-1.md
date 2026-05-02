# Phase 1 — Wiki schema + Source ABC + git signals + EntityStore reference impl

> **Goal**: Land the three-tier brain layer (`raw/` + `events/` + `wiki/`) with full provenance, the Source ABC that connectors implement, and the git-history signals that make `--task fix/review` actually use git. This phase produces the **EntityStore reference impl** that Anabasis spec v0.2 documents.
>
> **Effort**: M (~2 weeks) | **Status**: pending | **Blocks**: Phase 2, Anabasis spec v0.2 freeze
>
> **Source**: ROADMAP.md v3 § Phase 1 + multi-repo-spine.md § Phase 2 + Sourcegraph gap (git signals).

## Why this phase

Phase 0 fixed honesty. Phase 0.5 fixed friction. Phase 1 is where CE stops being "a code-context tool" and becomes "the engine that powers a company brain."

Three-tier storage from GAM (Wu et al, arXiv:2604.12285):
- `raw/` — verbatim sources (PDFs, transcripts, code snapshots)
- `events/` — append-only JSONL log of extracted claims (cheap, never rewritten)
- `wiki/` — consolidated entity pages, refreshed only on **semantic shift** (cosine drift threshold)

This is the schema that Anabasis spec v0.2 freezes as `EntityStore` ABC. CE Phase 1 produces the reference impl; the spec documents what CE already does.

## Deliverables

### 1.1 — Three-tier directory layout (S, ½ day)

Define and document the layout. All other deliverables build on this:

```
brain/                          ← root, configurable via --brain-dir
├── raw/                        ← verbatim sources (PDFs, transcripts, snapshots)
│   ├── code-snapshots/<commit-sha>/
│   └── department-specs/<dept>/
├── events/                     ← append-only JSONL (GAM event progression)
│   └── <YYYY-MM-DD>.jsonl
├── wiki/                       ← consolidated entity pages (GAM topic associative)
│   ├── _index.md               ← auto-maintained list
│   ├── _contradictions.md      ← cross-page contradictions surfaced
│   ├── _dataview.md            ← optional Dataview queries (Obsidian)
│   └── <slug>.md               ← entity pages
├── audit/                      ← lint reports, episodic log
│   ├── log.jsonl               ← who/what/when changed
│   └── proposals.md            ← Auditor's split/merge/contradiction queue
└── cache/                      ← CE's private cache (existing — Phase 0 cleaned)
    ├── workspace-index.json
    ├── workspace-index-light.json
    └── usage.jsonl
```

**Acceptance**: `python3 scripts/wiki/init_brain.py /path/to/brain/` produces the layout; `tree /path/to/brain/` matches above.

### 1.2.0 — Relationship to graphify's `--wiki` output (decision)

graphify v0.1.7+ ships a `--wiki` flag that generates Wikipedia-style entity pages with cross-community wikilinks, cohesion scores, and an audit trail (`graphify-out/wiki/`). CE's §1.2 schema overlaps that surface. **Decision**: hybrid via a `GraphifyWikiSource` (4th `Source` subclass alongside `WorkspaceSource` / `GithubRepoSource` / `EventStreamSource`) that consumes graphify's `wiki/` output as **input** when present and re-emits in CE's richer schema below.

Rationale:
- CE's frontmatter (`id`, `sources[]` with content_hash + ts, `confidence`, `centroid_embedding`, `links_in/out`, `kind`, `scope`) is a strict superset of graphify's output.
- Treating graphify's wiki as an input source preserves user choice (run graphify first if you want; CE consumes it) without duplicating community-detection or wikilink generation.
- CE's Auditor (`scripts/wiki/audit.py`) layers supersession/freshness rules on top — graphify ships neither.

`GraphifyWikiSource` lives in `scripts/wiki/source_adapter.py` (see §1.4). Implementation defers to Phase 1.4; this section is the architectural decision.

### 1.2 — `wiki/<slug>.md` schema (S, 1 day)

YAML frontmatter MUST include (per Animesh / Karpathy):

```yaml
---
id: ent_a4f3                    # immutable, stable across renames
kind: concept | component | decision | actor | process | metric
title: Authentication Middleware
slug: auth-middleware           # readable filename, collision-safe (see Acceptance below)
scope: default                  # corpus / namespace; e.g. competitive-intel | code-context | leads
                                # default: 'default' for un-scoped writes; multi-source loops MUST set scope
sources:                        # full provenance per Animesh
  - { type: code, ref: src/auth/middleware.ts, line: 12, hash: <sha>, ts: 2026-04-15 }
  - { type: department-spec, ref: departments/eng/department-spec.md, hash: <sha>, ts: 2026-04-30 }
  - { type: rfc, ref: docs/rfcs/0042-token-rotation.md, hash: <sha>, ts: 2026-03-10 }
confidence: 0.85                # synthesizer-emitted score
updated: 2026-05-01T10:23:45Z
links_in: [ent_b7c1, ent_d8e2]  # entities that link TO this one
links_out: [ent_c5d6]           # entities this links to
centroid_embedding: [...]       # mean of source-event embeddings, used for shift detection
last_verified_at: 2026-05-01T10:23:45Z   # required; emitter sets on every event/refresh that touches this entity
                                # NOTE: freshness_score is NOT stored here — it is computed on
                                # read from last_verified_at + freshness_policy.py (§1.2.2 below)

# Decision-continuity fields — required only when kind == "decision",
# omitted otherwise. supersedes/superseded_by point at other entity ids;
# valid_until is an ISO date after which the decision is presumed stale.
supersedes: ent_99f2 | null     # this decision replaces a prior one
superseded_by: ent_a5b7 | null  # this decision has been replaced by a successor
valid_until: 2026-12-31 | null  # explicit decay date; null = open-ended
---

# Authentication Middleware

(body — concept description, key decisions, behavior summary)

## See also

- [[token-store]] — depends on
- [[session-policy]] — implements
- [[2026-Q1-compliance-ADR]] — references

## Provenance

1. `src/auth/middleware.ts:12` — concrete implementation (current HEAD)
2. `departments/eng/department-spec.md` — declared as a key Engineering tool
3. `docs/rfcs/0042-token-rotation.md` — design decision the impl realizes
```

**Acceptance**:
- `python3 scripts/wiki/validate_page.py wiki/auth-middleware.md` passes schema check
- Slug is human-readable; `id` is stable (renames don't change `id`)
- Every cited claim has `file:line + content_hash + ts`
- **Slug collision rule** (MUST):
  1. Slugify title to lowercase kebab-case (`Authentication Middleware` → `auth-middleware`).
  2. Maintain a per-write-batch `used_slugs: set[str]` keyed by **lowercased** slug to handle case-insensitive filesystems (Windows NTFS + macOS APFS default).
  3. On collision, append `-2`, `-3`, … until a free slug is found.
  4. The colliding entity's `slug` frontmatter field reflects the final filename (e.g. `data-processing-2`); `id` remains the original immutable hash.
  5. Renaming an entity (changing `title`) MUST NOT change `slug` or `id`. Only fresh writes participate in collision detection.
  6. The `_index.md` table tracks collisions in a footnote so audits can surface near-misses ("`data-processing-2` collided with `data-processing` on 2026-05-15").
- **Scope rule** (MUST): `scope` is required on multi-source writes; `wiki.ask --scope=<corpus_id>` filters by it; absent scope = `default` corpus only.
- **Decision-continuity rule** (MUST when `kind: decision`): `supersedes`, `superseded_by`, `valid_until` are tri-state — present-with-value, present-as-null, or omitted. Concrete entity ids in `supersedes` / `superseded_by` MUST point at existing entity files; the Auditor (§1.7) flags broken references. Renaming a decision MUST NOT break the chain — `supersedes`/`superseded_by` reference `id`, not `slug`.
- **`last_verified_at` rule** (MUST): every emitter (events extractor, EventStreamSource, GraphifyWikiSource, manual edits) sets this to the wall-clock time of the touch. The wiki page itself stores this only; `freshness_score` is never stored — it is computed on read per §1.2.2 below.

### 1.2.2 — Freshness policy (computed-on-read freshness_score)

CE deliberately stores no `freshness_score` field on entity pages. Instead, callers (the Auditor at §1.7, `wiki.ask` MCP at §2.4) compute it at query time from the entity's `last_verified_at` + a per-source-type half-life policy. This split matters:

- **Avoids write-back to age pages.** If freshness were stored, every entity would need rewriting on a clock cadence — battling the immutability of `events/`-derived consolidation.
- **Keeps the policy tuneable.** Adjusting half-life for a source type doesn't require touching historical wiki pages; the next read picks up the new policy.
- **Survives schema migrations.** A v1.0 page with `last_verified_at` is consumable by a v1.1 reader using a different decay formula without rebuilding the wiki.

**Half-life table** (defaults, tuneable per-corpus):

| Source type | Half-life (days) | Rationale |
|---|--:|---|
| `code` | 90 | Refactor cycles are months; a function's role decays slowly. |
| `web` | 30 | Competitor pages, marketing copy, blog posts; rapid drift. |
| `transcript` | 60 | Meeting notes, decisions still load-bearing for ~2 months. |
| `email` | 21 | Personal communication churns fast; old context goes stale. |
| `notion` | 60 | Internal docs decay between two release cycles. |
| `rfc` | 180 | Architectural decisions decay slowly. |
| `department-spec` | 180 | Same rationale as `rfc` — long-half-life architectural artefacts. |
| `default` | 60 | Catch-all for source types not in this table. |

(Each table row corresponds 1:1 to a key in `HALF_LIVES: dict[str, int]`. Don't merge rows that share a half-life — the table is the spec for the dict, and a key like `"rfc / department-spec"` would silently miss `HALF_LIVES["rfc"]` lookups at runtime.)

**Decay formula** — linear over 2× half-life, clamped to [0, 1]:

```
freshness_score = max(0.0, 1.0 - elapsed_days / (2 × half_life_days))
```

Properties:
- At `t = 0` (just verified): score = 1.0
- At `t = half_life`: score = 0.5
- At `t = 2 × half_life`: score = 0.0
- Beyond `2 × half_life`: clamped at 0.0

The Auditor's "freshness expired" rule (§1.7) flags any entity where computed `freshness_score < 0.3` AND `last_verified_at` is older than the source-type's half-life. Both conditions required — prevents flagging a fresh-but-low-half-life source and prevents flagging a long-half-life source that's barely past its midpoint.

**Implementation contract** (Phase P1 of `plan/prd-closed-loop.md`):
- `scripts/wiki/freshness_policy.py` exports a `HALF_LIVES: dict[str, int]` with the table above and a `compute_freshness(last_verified_at, source_type, now=None) -> float` function applying the formula.
- Callers import and apply at query time. Stored field is `last_verified_at` only.

**Multi-source entities**: when an entity's `sources[]` contains heterogeneous source types, use the **shortest** half-life among them — the entity is only as fresh as its fastest-decaying source. Conservative; avoids the "1 ancient code reference + 5 fresh web sources = looks fresh" trap.

### 1.2.1 — Schema evolution policy

**Wiki pages (`wiki/<slug>.md`)**: refusal-and-rebuild while corpus < 10k entities.
- Frontmatter MUST include `schema_version: "1.1"` (current). Bumped from `1.0` when `make_id` widened to 12 hex chars — pre-1.1 brains must run `--rebuild`.
- The validator in `scripts/wiki/validate_page.py` errors hard on mismatch with a clear remediation: "Run `python3 scripts/wiki/wiki_init.py --rebuild`."
- When schema bumps occur, `wiki_init.py` regenerates all pages from the (immutable) `events/` log. Idempotent.
- At ≥10k entities, switch to forward-migration: add `scripts/wiki/migrate_v1_to_v2.py` etc. Threshold revisit gated by `wiki_init.py` runtime exceeding 5 minutes.

**Events (`events/<YYYY-MM-DD>.jsonl`)**: forward-migrate from day one.
- Each event line MUST include `schema_version`.
- Loader in `scripts/wiki/load_events.py` runs migrators in order on legacy lines: `v0 → v1 → v2 …`. Migrators are pure functions in `scripts/wiki/migrations/`.
- Events are append-only and may originate from sources that no longer exist (deleted Granola transcripts, removed Slack messages). Refusal-and-rebuild is not safe here.

**Why split**: `wiki/` is materializable from `events/`; events are primary truth. Migration cost lives on the side that can't be rebuilt.

### 1.3 — `events/<YYYY-MM-DD>.jsonl` event extractor (M, 2 days)

For each indexed file, the event extractor emits one JSONL line per significant heading / exported symbol / class / function:

```json
{"ts": "2026-05-01T10:23:45Z", "source_type": "code", "source_ref": "src/auth/middleware.ts:12", "file_id": "abc12345...", "claim": "function authenticate(req, res, next) — JWT verification middleware", "embedding": [...], "entity_hint": "auth-middleware"}
```

`entity_hint` is optional — synthesizer uses it (when present) to skip clustering for known entities; absence triggers clustering.

**Append-only**: events files are never modified after write. `<YYYY-MM-DD>.jsonl` rolls daily.

**Acceptance**:
- Re-indexing a workspace appends new events but doesn't rewrite old ones
- 1-day events file fits in memory comfortably (~10MB for a typical day)
- Each event has all 7 fields above

### 1.4 — Source ABC + concrete `WorkspaceSource`, `GithubRepoSource` (M, 2 days)

`scripts/wiki/source_adapter.py` defines:

```python
from abc import ABC, abstractmethod

class Source(ABC):
    """Anabasis SignalSource ABC (CE reference impl)."""

    @abstractmethod
    def list_artifacts(self) -> list[str]:
        """Return list of artifact references (paths, URLs, IDs)."""

    @abstractmethod
    def fetch(self, ref: str) -> bytes:
        """Fetch raw bytes for an artifact."""

    @abstractmethod
    def metadata(self, ref: str) -> dict:
        """Return source-type metadata (mtime, hash, author, etc.)."""

    @abstractmethod
    def emit_events(self, ref: str, content: bytes) -> list[dict]:
        """Convert artifact to event dicts (ts, source_type, source_ref, claim, embedding)."""


class WorkspaceSource(Source):
    """Local filesystem workspace."""
    # ... implementation


class GithubRepoSource(Source):
    """Remote GitHub repo via API."""
    # ... implementation
```

**Concrete classes shipped in CE**: `WorkspaceSource`, `GithubRepoSource` only.

**NOT shipped in CE**: `NotionSource`, `GmailSource`, `HubSpotSource`, `GranolaSource`, `SlackSource`, `LinearSource`. These live in `syroco-product-ops` (or future Anabasis adapter library) and import CE's Source ABC.

**Acceptance**:
- `WorkspaceSource('/path/to/repo').list_artifacts()` returns file paths
- `GithubRepoSource('owner', 'repo').list_artifacts()` returns file paths via GitHub API
- A 3rd-party connector (test fixture: `MockNotionSource`) implements Source ABC and round-trips events without CE importing the Notion library

### 1.5 — `semantic_shift.py` consolidation trigger (S, ½ day) — verify shipped

Per recovered ultraplan note, semantic_shift.py shipped in PR #10 on 2026-04-29. Verify against repo HEAD; complete if partial.

**Detector signature**:
```python
def should_consolidate(entity_id: str, recent_events: list[dict], existing_centroid: list[float]) -> bool:
    """Returns True when avg cosine distance of recent events vs centroid > threshold (default 0.35)
    OR when len(recent_events) >= N (default 8)."""
```

**Acceptance**:
- Threshold defaults match GAM paper (0.35 cosine, N=8 events)
- `--threshold` and `--min-events` CLI flags override defaults
- Returns `True` in both edge cases (drift OR backlog), `False` otherwise

### 1.6 — `wiki_init.py` one-shot seeder (M, 2 days)

Cluster the current cache index → seed entity pages with events + initial citations.

```bash
python3 scripts/wiki/wiki_init.py --brain /path/to/brain/ --index cache/workspace-index.json
```

Process:
1. Read cache index (Phase 0 schema)
2. Run label-propagation community detection (already in CE for feature_map.py — reuse)
3. For each cluster, propose an entity page with title, kind, sources, links
4. (Optional, if `ANTHROPIC_API_KEY` set) Use `--concept-llm` Haiku-pass for human-quality entity titles + descriptions
5. Write `wiki/<slug>.md` for each entity, append seeding events to `events/<today>.jsonl`

**Acceptance**:
- Seeded brain on CE itself produces ~30-50 entity pages
- Re-running on same index is idempotent (no duplicates)
- Each entity page validates against schema (1.2)

### 1.7 — Auditor (S, 1 day)

Lints + proposes splits/merges/contradictions:

```bash
python3 scripts/wiki/audit.py --brain /path/to/brain/ > audit/proposals.md
```

Checks:
- **Drift**: entity page where `centroid_embedding` is far from current source events → propose re-synthesize
- **Multi-concept entity**: cluster of source events with internal cosine variance > 0.4 → propose split
- **Duplicates**: two entities with cosine > 0.9 → propose merge
- **Dead links**: `[[wiki-link]]` to non-existent entity → propose remove or create
- **Contradictions**: two source events for the same entity with claims that NLI-classify as contradictory → flag in `_contradictions.md`
- **Stale supersession** (M4 from `plan/prd-closed-loop.md`): a `kind: decision` entity has `superseded_by: <id>` set, AND another entity still has a `[[wiki-link]]` to the superseded decision rather than its successor. Surface under "Stale references" heading in `audit/proposals.md` with both decision ids + the referencing entity's path, so the operator can decide whether to update the link or revoke the supersession.
- **Freshness expired** (M4): per the policy in §1.2.2, compute each entity's `freshness_score` from `last_verified_at` + source-type half-life. Flag if **both** computed score < 0.3 AND `last_verified_at` is older than the source-type's half-life. The double condition guards against flagging a fresh-but-fast-decaying source AND against flagging a long-decay source that's only just past its midpoint.

NLI classification (for the Contradictions check) is **deferred** (Phase 4 / Proving Layer). For Phase 1, contradiction detection is keyword-based ("not", "no longer", "instead of"). The new "Stale supersession" rule is a **structural** check (graph walk: does any entity link to a decision whose `superseded_by` chain has moved on?) — no NLI required, so it ships in v1.

**Acceptance**: Auditor runs on seeded brain; produces `audit/proposals.md` with at least one of each proposal kind.

### 1.8 — Git signals (M, 2 days) **[Sourcegraph gap]**

Three new flags + one ranking signal:

**`pack --since 7d`**: restrict resolution pool to files touched in last N days/commits.
- Implemented via `git log --since=7.days --name-only` (or N commits via `git log -N --name-only`)
- Useful for "what changed last week" queries against the company brain

**`pack --pr <num>`**: treat the diff as the resolution pool.
- Implemented via `gh pr diff <num> --name-only`
- Pack changed files at Full + their import-callers at Detail
- Direct match for `--task review` workflow

**`pack --diff <ref-A..ref-B>`**: same as `--pr` but for arbitrary refs.

**Churn-as-relevance signal**:
- Compute file churn over last 90 days (`git log --since=90.days <file> | wc -l`)
- Under `--task fix`: boost recently-edited files by ~1.2× (recent change = probably the bug locus)
- Under `--task explain`: demote them by ~0.9× (stable code = more authoritative)

**Acceptance**:
- `pack "auth" --since 7d` returns only files matching auth that changed in last 7 days
- `pack --pr 42` returns the PR's changed files at Full
- Churn signal verified via regression eval (Phase 0.10)

**Why this matters for the company brain**: connectors fire on cron and produce delta events. Git signals are the *retrieval-side* equivalent — letting consumers ask "what's new in the brain since X" without re-indexing.

### 1.9 — Anabasis spec v0.2 EntityStore + SignalSource ABC (S, 1 day)

This deliverable is **documentation**, not code. CE Phase 1 produces the reference impl; we now document it as the Anabasis spec v0.2.

Write `Repos/anabasis/spec/runtime/entity-store.md` and `signal-source.md`:
- EntityStore: read/write/list/link entity pages; reference impl = the markdown directory layout from 1.1
- SignalSource: artifact discovery + fetch + event emission; reference impl = CE's Source ABC from 1.4

Cross-link to `find-links.md` (Phase A).

Update spec/README.md to mention v0.2 ABCs are now drafted.

**Acceptance**:
- `Repos/anabasis/spec/runtime/{entity-store, signal-source}.md` exist
- Both reference CE's reference impl explicitly with file paths
- spec/README.md updated to reflect v0.2 ABCs in flight

### 1.10 — Schema round-trip test (S, ½ day)

Test that a `wiki/<slug>.md` page validates against:
1. CE's own schema (1.2)
2. The Anabasis spec v0.2 EntityStore ABC (1.9)
3. Syroco's `company-knowledge` vault (the actual dogfood)

```bash
python3 scripts/wiki/test_round_trip.py --brain /path/to/test-brain
```

If the same page passes all three validators, schemas are coherent. If one fails, iterate before Phase 2.

**Acceptance**: round-trip test passes for at least 5 entity pages from CE-seeded brain.

## Acceptance criteria (phase-level)

- [ ] Three-tier brain layout (1.1) instantiable via `init_brain.py`
- [ ] Wiki page schema (1.2) defined and validated
- [ ] Events extractor (1.3) writes append-only JSONL
- [ ] Source ABC (1.4) + 2 concrete subclasses + 1 mock 3rd-party shipped
- [ ] semantic_shift.py (1.5) — verified against PR #10
- [ ] wiki_init.py (1.6) seeds CE-on-CE brain
- [ ] Auditor (1.7) produces proposals
- [ ] Git signals (1.8) — `--since`, `--pr`, `--diff`, churn
- [ ] Anabasis spec v0.2 EntityStore + SignalSource ABCs (1.9) documented
- [ ] Schema round-trip test (1.10) green for 5+ pages
- [ ] Phase 0 regression eval still passes
- [ ] `entity_kinds` field added to CE SKILL.md frontmatter (now real entity types exist)

## Dependencies

- ✅ Phase 0 schema versioning + atomic writes (events/ writes need them)
- ✅ Phase 0.5 BGE embeddings (semantic_shift cosine needs an embedding source)
- ⚠️ company-knowledge accessibility (1.10 round-trip needs read access)

## What this phase does NOT do

- No `pack --wiki` mode (Phase 2)
- No multi-hop traversal (Phase 2)
- No NLI contradiction classification (deferred to Proving Layer)
- No Notion / HubSpot / Gmail Source classes (lives in syroco-product-ops)
- No runtime that *fires* skills against entities (lives in Anabasis runtime, closed)

## YC RFS alignment (preview)

| Pillar | Phase 1 contribution |
|---|---|
| Executable | EntityStore + SignalSource ABCs are the runtime contracts. Anabasis runtime calls them. |
| Installs | `init_brain.py` is the install primitive — one command produces the brain layout. |
| **Human knowledge** | **Wiki schema with full provenance is the human-knowledge schema. Department Specs feed in via WorkspaceSource. Decisions, RFCs, ADRs become entity pages.** |
| **Connections** | **Source ABC is the connection contract. Connectors implement it; CE doesn't import them. This is how the company brain ingests from CRM/Notion/etc. without CE becoming a connectors monorepo.** |
| AI | semantic_shift detector + concept_labeler (1.6 with --concept-llm) = AI-grade synthesis. |
| Skills that automate | Auditor is itself a skill — runs scheduled, surfaces audit/proposals.md. |
| **Company brain** | **Phase 1 IS the brain. Three-tier storage + provenance + auditor = a queryable, compounding entity vault.** |

7/7 served, with Phase 1 being the first phase where **Human knowledge + Connections + Company brain** are all delivered concretely. This is the load-bearing phase for the YC pitch.
