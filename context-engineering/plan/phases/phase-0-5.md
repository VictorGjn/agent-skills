# Phase 0.5 — Surface collapse + telemetry + BGE local embeddings

> **Goal**: Make CE usable in 10 seconds, instrumented from day 1, and free of the OpenAI dependency.
>
> **Effort**: S (3-5 days) | **Status**: pending | **Blocks**: Phase 1
>
> **Source**: ROADMAP.md v3 § Phase 0.5 + Sourcegraph gap (local embeddings).

## Why this phase before Phase 1

Phase 0 closed honesty problems. Phase 0.5 closes friction problems before the wiki layer (Phase 1) makes the surface area larger. The principle: **don't add complexity to a UI nobody can use.**

The user's question "what should I run?" today has 4 candidate answers (`pack_context.py`, `pack_context.py --semantic`, `pack_context.py --graph`, `pack_context.py --semantic --graph`) plus a config matrix of 4 modes × 6 task presets × 5 depths × 17 relations × 6 knowledge types. That's unnavigable.

Phase 0.5 collapses the surface to one verb + three flags. Then Phase 1 stacks on a clean UX, not a tangled one.

## Deliverables

### 0.5.1 — One verb: `pack "query"` (S, 1 day)

A single CLI entry point that auto-decides everything by default:
- **Mode**: proper-noun / `CamelCase` / `snake_case` query → `graph`; `how/why/what` → `semantic` (if backend available, else `keyword`); else `keyword`
- **Task preset**: `fix` / `bug` / `401` / `traceback` → `--task fix`; `review` / `pr` → `review`; `explain` / `how does` → `explain`; default `explain`
- **Index**: auto-builds for `cwd` if no index at the configured cache path

The 4 modes + 6 task presets + 17 relations + 5 depths still exist as advanced flags, but **users don't see them by default**. `--help` shows 3 flags; `--help --advanced` shows everything.

**Acceptance**: a new user runs `pack "users getting 401"` in a fresh repo and gets useful output without reading any docs.

### 0.5.2 — Three user-facing flags (S, ½ day)

Visible: `--budget`, `--mode auto|deep|wide`, `--task`. That's it.

- `--budget`: integer, default 8000
- `--mode auto`: the auto-decision tree above (default)
- `--mode deep`: forces semantic + graph + multi-hop (when 0.5+ Phase 1+2 land)
- `--mode wide`: forces a wider keyword/embedding scan with shallower depth
- `--task`: same 6 presets as today, but hidden behind `--task` (not 6 separate flags)

**Acceptance**: `pack --help` shows ≤ 5 flags total; the rest are under `--help --advanced`.

### 0.5.3 — `--why` flag (S, ½ day)

Shows the resolution trace inline, not as a separate command:
```
$ pack "users getting 401" --why
> mode=semantic+graph (auto: contains "users" + question form)
> task=fix (auto: matches "users getting" pattern)
> 14 files, 7,612 / 8,000 tok used (95%)
> entry: auth/refresh.ts → traversed: jwt.ts, session.ts, middleware.ts, ...
> 6 files at Full, 5 at Detail, 3 at Mention
> off-topic guard: 2 files filtered (cosine 0.31, 0.29)
> [packed markdown follows]
```

**Acceptance**: `--why` output is the YC demo "show your work" panel.

### 0.5.4 — BGE-small as default semantic backend (M, 1-2 days) **[Sourcegraph gap]**

Today's flow: `--semantic` requires `OPENAI_API_KEY` or fails to keyword silently.

New backend selection (priority order):
1. If `sentence-transformers` is importable AND `BAAI/bge-small-en-v1.5` is downloadable/cached → use BGE-small (134MB model, MTEB-competitive)
2. Else if `OPENAI_API_KEY` is set → use OpenAI `text-embedding-3-small`
3. Else fall back to keyword + warn once

Implementation in `embed_resolve.py`:
- New `EmbeddingBackend` ABC with `BGEBackend`, `OpenAIBackend`, `KeywordOnlyBackend` concrete classes
- `--embedding-backend bge|openai|keyword` overrides auto-selection
- BGE model cached under `${XDG_CACHE_HOME}/context-engineering/models/bge-small-en-v1.5/` (one-time 134MB download)

**Acceptance**:
- Cold run with no `OPENAI_API_KEY`: BGE downloads, semantic works
- Cold run with no internet: keyword fallback with clear warning
- `pack --mode deep "how does auth work"` returns sensible results offline

**Why this matters for the company brain**: a customer running Anabasis doesn't want to give CE their OpenAI API key for routine indexing. Local embeddings = no cloud dependency for the find skill = the runtime is genuinely "in their environment, our key is yours" (per pitch).

### 0.5.5 — Per-call telemetry to `cache/usage.jsonl` (S, ½ day)

Every `pack` call appends one line:
```json
{
  "ts": "2026-05-01T10:23:45Z",
  "query_length": 42,
  "mode": "semantic+graph",
  "task": "fix",
  "files_packed": 14,
  "budget_used_pct": 0.95,
  "time_ms": 487,
  "embedding_backend": "bge",
  "filtered_off_topic": 2
}
```

**No query content** — only metadata. No file paths — only counts. This is observability, not data exfil.

**Acceptance**: 100 calls produces 100 lines; size <50KB; no PII or query strings logged.

### 0.5.6 — Activation metric instrumented (S, ½ day)

Define and expose a "good user" signal:
- New user runs `pack` ≥ 3 times in 7 days
- AND ≥ 80% of those runs hit ≥ 80% budget utilization

Compute from `usage.jsonl`. Expose via `pack --activation-status` (returns "active" / "trying" / "stalled").

**Acceptance**: after 5 sample runs, `--activation-status` returns the right state.

### 0.5.7 — Promote anti-hallucination filters to headline (S, 15 min)

Doc-only edit. Today the topic-filter / section-filter / confidence-scoring features are buried mid-SKILL.md.

**Change**: add to top of SKILL.md:
> **Off-topic guard**: CE filters results with <25% query-term overlap before packing — your LLM never sees noise it would cite as fact.

**Acceptance**: SKILL.md leads with off-topic guard within first 100 lines.

### 0.5.8 — Slash commands `/pack` and `/pack-why` (S, ½ day)

Wrap CE as Claude Code slash commands. Use the existing skill-author pattern.

`~/.claude/commands/pack.md`:
```markdown
---
description: Pack relevant codebase context for the given query
---
Run `python3 ~/Repos/agent-skills/context-engineering/scripts/pack_context.py "$ARGUMENTS"` and return the output.
```

Similar for `/pack-why`.

**Acceptance**: typing `/pack auth middleware` in Claude Code returns packed context.

## Acceptance criteria (phase-level)

- [ ] All 8 deliverables shipped
- [ ] Phase 0 still passes regression eval baseline (Phase 0.5 doesn't regress)
- [ ] `OPENAI_API_KEY` is genuinely optional — no silent degradation that breaks UX
- [ ] `pack --help` ≤ 5 flags
- [ ] `usage.jsonl` populated after first run
- [ ] `/pack` slash command works in Claude Code

## Dependencies

- Phase 0 must ship 0.2 (`sentence-transformers` pinned in requirements) before 0.5.4 begins
- Phase 0 must ship 0.10 (regression eval) — Phase 0.5 must not regress baseline

## What this phase does NOT do

- No wiki schema (Phase 1)
- No `pack --wiki` (Phase 2)
- No connector adapters (lives in syroco-product-ops)
- No public benchmarks (Deferred — Proving Layer)
- No SaaS hosting (Phase 4+)

## YC RFS alignment (preview)

| Pillar | Phase 0.5 contribution |
|---|---|
| Executable | One-verb `pack` is what humans type; `/pack` slash is what skills compose |
| **Installs through** | **0.5.4 BGE local — install no longer requires a cloud key. Critical for "runtime in your environment" promise.** |
| Human knowledge | 0.5.3 `--why` shows what was retrieved — humans verify the brain works without reading code |
| Connections | (indirect — surface collapse makes it easier to wire skills as connectors) |
| AI | 0.5.4 BGE backend + auto-mode selection = AI without API keys |
| Skills that automate | 0.5.8 slash commands = the find skill is now a 1-keystroke composable primitive |
| Company brain | 0.5.5 telemetry = first compounding signal (which queries get packed) |

7/7 served, with the strongest pillar being **Installs through** — Phase 0.5 is the first phase where "install" becomes genuinely frictionless.
