# Academic positioning

This document situates `context-engineering` (CE) in the literature it draws from, names the open problems it doesn't solve, and clarifies what's novel vs. what's known prior art. Written for readers who want to know whether CE is reinventing wheels or actually contributing primitives.

## What CE is

A retrieval engine that turns code, documents, and structured signals into LLM-ready depth-packed context, addressable by `corpus_id`, sized to the model's appetite. CE does not generate; it composes. CE does not reason across corpora; it indexes one and serves it. CE's wiki layer (Phase 1) is an entity store on top of an append-only event log, designed to be the reference implementation of an `EntityStore` ABC for an agent runtime.

## Literature CE draws from

### Event-graph + semantic-shift consolidation

CE Phase 1's three-tier architecture (`raw/` → `events/` → `wiki/`) follows the pattern in **Wu et al., "Graph-Augmented Memory for LLM Agents,"** arXiv:2604.12285 (Apr 2026). Their event-graph + topic-network + semantic-shift consolidation pattern is the structural model CE adopts:
- Append-only `events/<date>.jsonl` = their event log
- Wiki entity pages = their topic-network nodes
- `semantic_shift.py` consolidation trigger = their semantic-shift detector

CE doesn't reproduce their numerical experiments; the contribution is making the pattern operable as a CLI + MCP service that any agent runtime can call.

### Depth-packed retrieval

The "Full / Detail / Summary / Headings / Mention" depth bands within a token budget are not in the literature as a primitive. The closest precedents:
- **Sourcegraph Cody Context API** — returns ranked snippets to a human; CE returns a depth-packed bundle to an LLM. Different shape.
- **LlamaIndex** routers + selectors — choose which retriever to call; CE chooses *how much of each file* to include given a budget.
- **Cursor `@codebase`** — IDE-side keyword + embedding hybrid; CE is upstream of any IDE.

The depth-aware budget-fused packing primitive (per-file granularity, not per-snippet) is, to our knowledge, novel as a stable wire contract. We invite citations and corrections.

### Wikilink-based knowledge graph

The `[[wiki-link]]` syntax + community-detection + cross-link surfacing follows **Andy Matuschak's evergreen-notes / Roam Research / Obsidian** lineage. CE's contribution is making this format **machine-readable as a graph** (frontmatter + stable IDs + `links_in/out`) without losing human-readability — so the same pages a human edits in Obsidian an LLM can traverse via MCP. The hybrid via `GraphifyWikiSource` (Phase 1.2.0) explicitly consumes [graphify](https://github.com/safishamsi/graphify)'s `--wiki` output as input rather than competing with it.

### Karpathy's wiki + agent retrieval framing

Karpathy's public commentary on agent-readable wikis as the right format for LLM context (vs. raw documents or vector DBs alone) shaped CE's emphasis on the wiki layer as the durable surface. CE is one concrete implementation of that thesis with full provenance per claim and a stable MCP wire contract.

## What CE doesn't claim

- **Not a graph database.** Markdown + frontmatter + `[[wiki-links]]` is the graph store. We deliberately reject Neo4j / pgvector-as-graph stacks (see `ROADMAP-v4.md` §7 non-goals). The cost is no Cypher queries; the win is that any human or LLM can read the source-of-truth without a query language.
- **Not a knowledge-graph reasoning system.** CE retrieves; it does not multi-hop reason or generate. Reasoning is the agent's job.
- **Not an embedding research project.** Embedding provider is pluggable (see `scripts/embed_resolve.py` — openai/mistral/voyage/external). CE is provider-agnostic; we don't claim novel embedding models.
- **Not a benchmark-led project at this stage.** Eval suites exist for regression (`references/eval-results.md`); external benchmarks will surface when CE goes OSS-default. We deliberately avoid premature benchmarking against incomparable systems.

## Open problems CE punts on

These are real research questions that CE inherits and does not attempt to solve:

1. **Cross-corpus reasoning.** CE indexes one corpus and serves it. Reasoning across corpora is the runtime's job (Anabasis or any wrapping skill). We do not make claims about cross-corpus inference.
2. **Decision continuity at scale.** Phase 1.2 introduces `supersedes:` / `superseded_by:` / `valid_until:` fields on `kind: decision` pages with an explicit-link Auditor rule. Detecting *implicit* contradiction (semantic similarity → flag) is v2 work and an open research problem.
3. **Freshness scoring without ground truth.** CE's `freshness_score` is heuristic (last_verified_at + source-type half-life). Calibrating it against actual decision drift is unresolved.
4. **Depth-band optimality.** The 5-band depth ladder is empirically chosen; we have not proven it Pareto-optimal vs. continuous depth, n-band variants, or learned compression policies.

## Where to argue with us

- The depth-band primitive is novel only if we missed prior art — file an issue with the citation and we'll update this doc.
- The wiki-as-EntityStore reference implementation is a design choice, not a proof. The competitive set (graphify, Serena MCP, Kai, gitnexus, codebase-memory, mache) makes different choices; whether CE's choice is right will be settled by adoption, not argument.
- The non-goals (no graph DB, no reasoning, no benchmarks-as-proof) are deliberate. If you think one is wrong, the conversation is welcome — but the bar is "show me a customer use case the current shape can't serve," not "X system is more general."

## Citation suggestion

If CE's wiki schema or event-graph pattern is useful in your work:

```
context-engineering. (2026). Anabasis Skill ABC reference implementation:
depth-packed retrieval over an entity store. 
https://github.com/victorgjn/agent-skills/tree/main/context-engineering
```

The schema, MCP spec, and roadmap are versioned in the repo; cite the spec version (`SPEC-mcp.md` v1.0-rc2 at time of writing) for reproducibility.
