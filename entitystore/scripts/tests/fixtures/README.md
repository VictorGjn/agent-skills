# Golden-query retrieval fixture

Retrieval-regression fixture for `wiki_ask` / `wiki_pack` (see
`../test_golden_queries.py`). Everything in this directory is **synthetic** —
see the data-governance record below before adding or changing anything here.

## 0. Data-governance verification (read first)

Verified 2026-07-11 (re-confirm if this repo's visibility ever changes):

```
$ gh repo view VictorGjn/agent-skills --json isPrivate,visibility
{"isPrivate":false,"visibility":"PUBLIC"}

$ gh repo view syrocolab/company-brain --json isPrivate
{"isPrivate":true}
```

`VictorGjn/agent-skills` — where `entitystore/` (the engine this fixture
tests) actually lives — is **public**. `syrocolab/company-brain` — where the
real entities live — is **private**.

**Decision: this fixture uses a SYNTHETIC seed corpus, never real
company-brain entities, and is not relocated into company-brain.**

Rejected alternatives and why:

- **Vendor real entities into this public repo.** An earlier version of this
  plan did exactly that (real Syroco commercial entities and their
  partnership/opportunity concept clusters, verbatim) — caught in review before
  any code was written. Rejected: no engineering need justifies publishing real
  commercial entities, and retrieval-regression coverage for scoring/banding/
  neighbor-expansion logic is **shape-driven, not content-driven** — a synthetic
  fixture with the same graph shape delivers the same coverage with zero
  data-exposure risk.
- **Relocate the fixture into private `company-brain` instead.** Rejected:
  it would leave `agent-skills`' own default CI with zero retrieval-regression
  coverage unless a contributor happens to have a private company-brain
  checkout pointed at via env var — defeating this workstream's purpose of a
  working gate on the repo where the engine actually lives.

No real Syroco/customer/competitor org, person, vessel, or concept name or id
may appear anywhere under this directory. Every new file added here should be
grepped against known real slugs before committing (see "Content scan"
below) — this is enforced procedure, not (yet) an automated pre-commit hook.

## 1. What's in here

| File | What | Authored or generated? |
|---|---|---|
| `golden_seed.json` | The seed graph: 16 fictional entities (3 orgs, 3 people, 10 concepts including one token-heavy hub) with hand-designed `wiki_links` fan-out. | **Authored** — the one artifact in this fixture that defines the graph shape by design. |
| `build_golden_corpus.py` | Reads the seed, fills required schema fields (`created_at`/`updated_at`/`provenance`), prunes dead links, writes `golden_corpus/entities/<kind>/<slug>.json`. | Authored (generator script). |
| `golden_corpus/` | The materialized entity JSON tree `test_golden_queries.py` runs the engine against. | **Generated** — regenerate with the command below, never hand-edit. |
| `golden_queries.json` | ~40 query cases (`wiki_ask` + `wiki_pack`) with expected results. | Authored structure; every `expect` value was captured by **actually running** `cb_engine` against the generated fixture (see "How the expected values were captured" below) — never hand-guessed. |
| `schemas/entity.schema.json` | A vendored copy of `company-brain/schemas/entity.schema.json`. | Copied verbatim — schema only, structural, not sensitive data. Used for local `--check` validation without needing a company-brain checkout. |

## 2. The seed graph's shape

Three fictional orgs (`org:atlas-marine`, `org:borealis-robotics`,
`org:meridian-foils`) each get a 3-concept cluster mirroring the real
competitive-intel shape this fixture replaces:

- a **named-partner relationship** concept (e.g. "Atlas Marine Named As
  Routing Partner")
- a **demand/opportunity-theme** concept (e.g. "growing demand for
  stability-aware routing")
- a **stability/performance theme** concept, deliberately evidence-heavy
  (2 long quotes each) so it carries real token weight

A 10th concept, `concept:foil-stability-cluster`, is a hub that `wiki_links`
to all 3 orgs and all 9 sub-concepts (12 outgoing links) and carries 4 long
evidence quotes — the single most token-expensive entity in the fixture,
purpose-built to force real `wiki_pack` band demotion (Full → Detail →
Summary → Headlines → Mention) under a tight budget. Three person entities
(one per org) round out the graph for id-substring and neighbor-expansion
coverage. 16 entities total.

## 3. Regenerating the corpus

```
cd entitystore/scripts/tests/fixtures
python build_golden_corpus.py --check
```

Deterministic: fixed `created_at`/`updated_at`/`provenance` mean re-running
this always produces byte-identical output for a given `golden_seed.json`.
`--check` also validates every generated entity against the vendored schema
and confirms 0 dead `wiki_links`. `test_golden_queries.py` regenerates the
corpus automatically if `golden_corpus/entities/` is missing, so a fresh
checkout doesn't require a manual step to run the suite.

## 4. How the expected values in `golden_queries.json` were captured

Every case has a `truth` field:

- **`"author"`** — true ground truth **by construction**. Either (a) an
  id-substring query (e.g. `"org:atlas-marine"`), which is unique by
  definition since entity ids are unique and no other entity's id contains
  that literal substring, or (b) a graph-traversal assertion (neighbor sets
  at a given `depth`), which follows deterministically from the seed's
  `wiki_links` regardless of scoring/tie-break behavior.
- **`"locked"`** — the CURRENT engine output, captured by running
  `wiki_ask`/`wiki_pack` against the generated fixture and recording the
  actual result (natural-language queries that score multiple entities, or
  outputs whose exact token counts depend on `cb_engine`'s token estimator).
  These exist to catch **regressions** in scoring/tie-break/token-estimation
  behavior — a `"locked"` case failing after an engine change means "this
  behavior changed," not automatically "this is now wrong"; re-verify by hand
  before updating the expected value.

No expected id or depth was hand-guessed: every value was produced by
actually invoking `cb_engine.wiki_ask` / `cb_engine.wiki_pack` against
`golden_corpus/` and reading back the result.

The primary demotion case (`pack-38-hub-demotion-spread`, query `"foil
stability"` at `budget=1200`) exercises all 5 depth bands in one pack
(`Full`, `Summary`, `Detail`, `Headlines`, `Mention`) — asserted on individual
item depths (`depth_exactly`), not just the aggregate token total, so a
demotion-logic regression that happens to preserve `used_tokens` would still
be caught.

## 5. Content scan (run before committing any change to this directory)

Before adding or modifying any files in this directory, verify that no real
Syroco, customer, or competitor organization/vessel/person names or slugs have
been included. Use a grep pattern appropriate to your Syroco entity-naming
conventions and verify: zero matches required. This repo (`VictorGjn/agent-skills`)
is public.

## 6. Opt-in live-corpus sanity check (local only, never in CI)

`test_golden_queries.py` has a second test class,
`TestGoldenQueriesAgainstLiveCorpus`, gated by
`@unittest.skipUnless(os.environ.get("CB_GOLDEN_LIVE_CORPUS"), ...)`. It is
**skipped by default** (including in CI) and only runs when explicitly
enabled locally against a real, already-private `company-brain` checkout:

```
CB_GOLDEN_LIVE_CORPUS=1 CB_CORPUS_DIR=/path/to/company-brain/corpora/syroco \
  python -m pytest entitystore/scripts/tests/test_golden_queries.py -v
```

This is the **only** code path in the suite that may see real entities. Its
assertions are deliberately relaxed (recall-only — "did wiki_ask return
something sane", "did wiki_pack respect the budget" — never exact-id or
exact-depth pinning, since the real corpus's content and size are outside
this suite's control) and it is purely a sanity check that the synthetic
fixture's demotion/recall behavior isn't wildly divergent from real-corpus
behavior. It never writes anything back to the fixture, the real corpus, or
any commit.
