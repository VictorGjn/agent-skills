# Feature Map v2 — Concept Folding

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift the feature map from syntactic clusters (directory names, symbol names) to **conceptual clusters** that name what the code is *about* — and fold those concepts one tier higher into domains. Ascend from facts → features → domains. Anabasis.

**Problem today (v1 ships at `8ba9930`):**
- Clusters are named `SideNavbar, TopNavBar` or `PopoverBase, PopoverMapStyles` — the mechanical label-clusters strategy surfaces the most frequent symbol names, which are implementation details, not product concepts.
- No domain layer — every cluster floats at the same tier, so `Navigation`, `Vessel Detail`, and `Admin Settings` look equally important and visually unrelated.
- No shared color language — each cluster gets an arbitrary palette color with no legend, so the reader can't tell which clusters belong together.
- Edges between clusters are visually thin; cross-feature glue is invisible at first glance.

**What v2 delivers:**
1. LLM-assigned **concept labels** ("Navigation", "Map Layers") + one-line descriptions per cluster.
2. LLM-enumerated **sub-features** ("Vessel List, Reports, Settings, Profile") for the click-detail panel.
3. A second structural fold — **domains** — via label propagation on the meta-graph.
4. **Color-by-domain** palette + legend; cross-cluster edges weighted and visible.

**Architecture:** Add `concept_labeler.py` (LLM wrapper + cache) alongside the existing `community_detect.py` and `feature_map.py`. Reuse label propagation for the second fold (domain layer). No new runtime dependencies beyond `anthropic` (already expected in the environment).

**Tech Stack:** Python 3.10+, `anthropic` Python SDK (Claude Haiku 4.5), disk cache (JSON keyed by content hash), D3.js v7 (unchanged).

**Spec / prior work:**
- v1 plan: `docs/superpowers/plans/2026-04-22-birds-eye-feature-map.md`
- v2 design conversation: headless session ending 2026-04-23, summarized above.

**Prerequisites:**
- v1 branch `feature/graph-visualizer-features` merged (or this v2 branched off it).
- `ANTHROPIC_API_KEY` available in env; fail loud if missing when LLM passes are requested.
- `anthropic` package installable (`pip install anthropic>=0.40`).

---

### Task 1: LLM Concept Labeler — Client, Prompt, Cache

**Files:**
- Create: `context-engineering/scripts/concept_labeler.py`
- Test: `context-engineering/scripts/tests/test_concept_labeler.py`

Build the LLM-facing component in isolation so it's unit-testable and swappable. One call per cluster produces both a concept label AND a list of sub-features.

- [ ] **Step 1: Write failing test — prompt shape + cache behavior**

```python
def test_build_prompt_shape():
    """Prompt must include cluster label, top symbols, first sentences."""
    from concept_labeler import build_prompt

    cluster = {'nodes': ['src/nav/side.ts', 'src/nav/top.ts']}
    file_data = {
        'src/nav/side.ts': {'symbols': ['SideNavbar', 'renderNav'],
                             'first_sentence': 'Side navigation drawer for vessel list.'},
        'src/nav/top.ts': {'symbols': ['TopNavbar', 'renderMenu'],
                            'first_sentence': 'Top menu bar with profile dropdown.'},
    }
    prompt = build_prompt(cluster, file_data, current_label='SideNavbar, TopNavbar')

    assert 'SideNavbar' in prompt
    assert 'vessel list' in prompt.lower()
    assert 'concept' in prompt.lower()
    assert 'sub-features' in prompt.lower() or 'sub_features' in prompt.lower()


def test_cache_hit_skips_llm(tmp_path, monkeypatch):
    """A second call with identical inputs must not hit the LLM."""
    from concept_labeler import label_cluster

    calls = {'n': 0}
    def fake_llm(prompt: str) -> str:
        calls['n'] += 1
        return '{"concept": "Navigation", "description": "Top and side menus",'\
               ' "sub_features": ["Vessel List", "Profile"]}'

    cluster = {'nodes': ['src/nav/side.ts']}
    file_data = {'src/nav/side.ts': {'symbols': ['SideNavbar'],
                                      'first_sentence': 'Side nav.'}}

    r1 = label_cluster(cluster, file_data, llm=fake_llm, cache_dir=tmp_path)
    r2 = label_cluster(cluster, file_data, llm=fake_llm, cache_dir=tmp_path)

    assert r1 == r2
    assert r1['concept'] == 'Navigation'
    assert calls['n'] == 1  # second call served from cache


def test_malformed_json_falls_back():
    """If LLM returns unparseable JSON, return a safe fallback label."""
    from concept_labeler import label_cluster

    cluster = {'nodes': ['x.ts']}
    file_data = {'x.ts': {'symbols': ['X'], 'first_sentence': ''}}

    def bad_llm(prompt: str) -> str:
        return "this is not json"

    result = label_cluster(cluster, file_data, llm=bad_llm,
                            cache_dir=None, current_label='X')
    assert result['concept'] == 'X'  # falls back to current_label
    assert result['sub_features'] == []
```

- [ ] **Step 2: Run to verify tests fail**

- [ ] **Step 3: Implement `concept_labeler.py`**

Public surface:
```python
def build_prompt(cluster: dict, file_data: dict, current_label: str) -> str: ...

def label_cluster(cluster: dict, file_data: dict, *,
                  llm: Callable[[str], str] | None = None,
                  cache_dir: Path | None = None,
                  current_label: str = '') -> dict: ...

def label_all_clusters(clusters: dict, file_data: dict, cluster_labels: dict, *,
                       cache_dir: Path | None = None,
                       model: str = 'claude-haiku-4-5-20251001',
                       max_workers: int = 4) -> dict[int, dict]: ...
```

Prompt shape (system + user):
- System: "You name code feature clusters with product-level concepts. Respond with a single JSON object: {concept, description, sub_features}. concept is 1-3 words, title-cased. sub_features is 3-6 short human-readable items."
- User: `<cluster current_label='{current_label}'>\n<files>\n- {path} :: {symbols joined} :: {first_sentence}\n...</files>\nName this cluster."`

Cache key: `sha256(prompt)`. Cache dir default `cache/concept-labels/`. On miss: call LLM, parse JSON, write `{key}.json` with `{concept, description, sub_features, model, timestamp}`.

Fallbacks:
- LLM raises: log, return `{concept: current_label, description: '', sub_features: []}`.
- JSON parse fails: same fallback.
- Empty cluster: skip LLM, return fallback.

Concurrency: `label_all_clusters` uses `concurrent.futures.ThreadPoolExecutor(max_workers=4)`.

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add context-engineering/scripts/concept_labeler.py context-engineering/scripts/tests/test_concept_labeler.py
git commit -m "feat(graph): LLM concept labeler with disk cache"
```

---

### Task 2: Wire Concept Labels into Feature Map Pipeline

**Files:**
- Modify: `context-engineering/scripts/feature_map.py` (call `label_all_clusters` after mechanical labeling)
- Test: `context-engineering/scripts/tests/test_feature_map.py`

Keep the mechanical `label_clusters` as a FALLBACK so offline / no-API-key runs still produce a map. LLM labels override when present.

- [ ] **Step 1: Failing test — concept fields propagate through pipeline**

```python
def test_build_feature_map_with_concept_labeler():
    from feature_map import build_feature_map

    index = {'root': '/repos/test', 'files': [
        {'path': 'src/nav/side.ts', 'tokens': 100,
         'tree': {'title': 'src/nav/side.ts', 'depth': 0, 'tokens': 100,
                   'totalTokens': 100, 'text': '', 'firstSentence': '',
                   'firstParagraph': '',
                   'children': [{'title': 'SideNavbar', 'depth': 1,
                                  'tokens': 50, 'totalTokens': 50,
                                  'children': [], 'text': '',
                                  'firstSentence': '', 'firstParagraph': ''}]}},
    ]}

    def fake_concept_llm(cluster, file_data, current_label, **_):
        return {'concept': 'Navigation', 'description': 'Top + side menu',
                'sub_features': ['Vessel List', 'Profile']}

    result = build_feature_map(index, concept_llm=fake_concept_llm)

    for c in result['clusters'].values():
        assert c['concept'] == 'Navigation'
        assert c['description'] == 'Top + side menu'
        assert c['sub_features'] == ['Vessel List', 'Profile']
```

- [ ] **Step 2: Implement `concept_llm` parameter in `build_feature_map`**

Signature:
```python
def build_feature_map(index: dict, graphify_path: str | None = None, *,
                      concept_llm: Callable | None = None,
                      cache_dir: Path | None = None) -> dict: ...
```

Behavior:
- Run mechanical `label_clusters` as before (fallback labels).
- If `concept_llm` is None, skip LLM enrichment; each cluster gets `concept = cluster_labels[id]`, `description = ''`, `sub_features = []` so the downstream template can treat all clusters uniformly.
- If `concept_llm` provided, iterate clusters and attach `concept`, `description`, `sub_features` from the LLM response.

- [ ] **Step 3: Update existing `test_pipeline_produces_meta_graph`** to assert every cluster has `concept`, `description`, `sub_features` keys (empty allowed when no LLM).

- [ ] **Step 4: Run full suite, verify pass**

- [ ] **Step 5: Commit**

```bash
git add context-engineering/scripts/feature_map.py context-engineering/scripts/tests/test_feature_map.py
git commit -m "feat(graph): surface concept, description, sub_features on clusters"
```

---

### Task 3: Hierarchical Folding — Feature → Domain

**Files:**
- Modify: `context-engineering/scripts/feature_map.py` (add `build_domain_layer`)
- Test: `context-engineering/scripts/tests/test_feature_map.py`

Run label propagation a SECOND time — on the meta-graph — to group clusters into domains. This is purely structural; no LLM needed.

- [ ] **Step 1: Failing test — domain layer clusters feature clusters**

```python
def test_build_domain_layer_groups_clusters():
    """Two heavily inter-connected clusters fold into one domain."""
    from feature_map import build_domain_layer

    feature_data = {
        'clusters': {
            0: {'concept': 'Navigation', 'nodes': ['a'], 'file_count': 1},
            1: {'concept': 'Menus', 'nodes': ['b'], 'file_count': 1},
            2: {'concept': 'Telemetry Ingest', 'nodes': ['c'], 'file_count': 1},
        },
        'meta_edges': [
            {'source': 0, 'target': 1, 'weight': 5},  # Navigation <-> Menus strong
            {'source': 2, 'target': 0, 'weight': 1},  # Telemetry barely connected
        ],
    }
    domains = build_domain_layer(feature_data)

    # Expect clusters 0 and 1 in same domain, cluster 2 separate
    assert domains[0] == domains[1]
    assert domains[0] != domains[2]
```

- [ ] **Step 2: Implement `build_domain_layer`**

```python
def build_domain_layer(feature_data: dict) -> dict[int, int]:
    """Return {cluster_label: domain_label} via label propagation on the meta-graph."""
    from community_detect import label_propagation
    edges = [{'source': e['source'], 'target': e['target'],
              'weight': e['weight']} for e in feature_data['meta_edges']]
    return label_propagation(edges, min_size=1)
```

- [ ] **Step 3: Attach domain to each cluster in `build_feature_map`**

After `build_domain_layer` runs, set `cluster['domain'] = domains.get(cluster_id, cluster_id)` for every cluster. Isolated clusters (no meta_edges) get their own domain id (fall through the `min_size=1` behavior).

Also add `result['domains']: dict[int, dict]` — one entry per unique domain id with:
- `name`: If `concept_llm` was provided, ask it to name the domain from concatenated member concepts (optional; acceptable to default to `f'Domain {id}'` for this task — a future pass can upgrade).
- `cluster_ids`: list of feature-cluster ids in this domain.
- `color_index`: stable int in [0, 15) for palette assignment.

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add context-engineering/scripts/feature_map.py context-engineering/scripts/tests/test_feature_map.py
git commit -m "feat(graph): domain layer via hierarchical label propagation"
```

---

### Task 4: UI — Color by Domain, Legend, Edge Weighting, Concept Panel

**Files:**
- Modify: `context-engineering/scripts/feature_map.py` (`_HTML_TEMPLATE` + `generate_html`)
- Test: `context-engineering/scripts/tests/test_feature_map.py`

Rewire the HTML to consume the v2 data shape. Four visible changes:

1. **Color = domain** (not cluster). Each domain picks a palette slot; all clusters in that domain share the color with varying lightness per cluster.
2. **Legend** in the stats panel: `<swatch> Domain name (N clusters · M files)`, clickable to filter to that domain only.
3. **Edge thickness** scaled by `weight` with a visible minimum stroke width. Cross-domain edges drawn in a slightly darker shade so "glue" reads as glue, not noise.
4. **Detail panel** restructured: cluster concept + description at top, `Sub-features` bullet list, then `Files` collapsed by default with an expand toggle.

- [ ] **Step 1: Failing test — HTML contains domain legend and sub-feature bullets**

```python
def test_html_renders_domain_legend_and_subfeatures():
    from feature_map import generate_html

    feature_data = {
        'clusters': {
            0: {'concept': 'Navigation', 'description': 'Menus',
                'sub_features': ['Vessel List', 'Profile'],
                'nodes': ['a.ts'], 'file_count': 1, 'total_tokens': 10,
                'internal_edges': 0, 'domain': 0, 'symbols': []},
            1: {'concept': 'Map Layers', 'description': 'Overlays',
                'sub_features': ['Weather Layers', 'Map Styles'],
                'nodes': ['b.ts'], 'file_count': 1, 'total_tokens': 10,
                'internal_edges': 0, 'domain': 1, 'symbols': []},
        },
        'meta_edges': [{'source': 0, 'target': 1, 'weight': 3}],
        'cluster_labels': {0: 'Navigation', 1: 'Map Layers'},
        'domains': {
            0: {'name': 'Product UI', 'cluster_ids': [0], 'color_index': 0},
            1: {'name': 'Map Stack', 'cluster_ids': [1], 'color_index': 1},
        },
    }
    html = generate_html(feature_data, 'Test')

    assert 'Product UI' in html
    assert 'Map Stack' in html
    assert 'Vessel List' in html
    assert 'Weather Layers' in html
    assert 'legend' in html.lower()
```

- [ ] **Step 2: Implement**

JS changes:
- `color(d)` looks up `domains[d.domain].color_index`, maps to palette[idx].
- Within a domain, offset lightness by `cluster_ids.indexOf(cluster.id) * 0.06`.
- Render `<div class="legend">` in the stats panel, driven by `data.domains`.
- Edge `stroke-width = 1 + Math.log(weight + 1) * 1.5` (up from 1.0 to make glue visible).
- Cross-domain edges get `stroke-opacity = 0.85`; same-domain edges get `0.45` (so you see inter-domain edges pop).
- Detail panel template:
  ```
  <h3>{concept}</h3>
  <p class="desc">{description}</p>
  <h4>Sub-features</h4>
  <ul>...{sub_features}...</ul>
  <details><summary>Files ({file_count})</summary>
    <ul>...{nodes}...</ul>
  </details>
  ```
- Keep `_js_safe_json` XSS escaping.

CSS: Roboto + Arctic Maritime palette unchanged. Add `.legend`, `.legend-row`, `.legend-swatch` styles, and `.desc`.

- [ ] **Step 3: Full suite passes**

- [ ] **Step 4: Manual CLI smoke on fleet index**

```bash
python3 scripts/feature_map.py --index cache/fleet-index.json \
  -o cache/fleet-features-v2.html --concept-llm
```

Open the file in a browser. Verify:
- Clusters with related concepts share a color.
- Legend is present and clickable.
- Clicking a cluster shows concept title, description, sub-features, expandable files.
- Edges thicker than v1; cross-domain edges visually distinguishable.

- [ ] **Step 5: Commit**

```bash
git add context-engineering/scripts/feature_map.py context-engineering/scripts/tests/test_feature_map.py
git commit -m "feat(graph): domain-colored legend, edge weighting, concept detail panel"
```

---

### Task 5: CLI Flags + Integration Test + Docs

**Files:**
- Modify: `context-engineering/scripts/feature_map.py` (argparse)
- Modify: `context-engineering/SKILL.md`
- Test: `context-engineering/scripts/tests/test_feature_map.py` (one integration test)

- [ ] **Step 1: Add CLI flags**

```python
parser.add_argument('--concept-llm', action='store_true',
                     help='Enable LLM concept labeling (requires ANTHROPIC_API_KEY)')
parser.add_argument('--concept-model', default='claude-haiku-4-5-20251001',
                     help='Claude model id for concept labeling')
parser.add_argument('--concept-cache-dir', default='cache/concept-labels',
                     help='Disk cache directory for concept labels')
parser.add_argument('--concept-workers', type=int, default=4,
                     help='Parallel LLM calls')
```

Wire up `main()`: if `--concept-llm`, import `concept_labeler.label_all_clusters` and pass as `concept_llm` to `build_feature_map`. Print `Concept labels: N clusters labeled (K cache hits, L LLM calls)` in the summary line.

Fail fast with a clean error if `ANTHROPIC_API_KEY` is missing and `--concept-llm` was requested.

- [ ] **Step 2: Integration test — end-to-end with fake LLM**

```python
def test_cli_end_to_end_with_fake_llm(tmp_path, monkeypatch):
    """Verify main() runs through with a fake concept labeler."""
    # Build a tiny index file at tmp_path/index.json, invoke main(),
    # assert the HTML output exists and contains a concept label from the fake.
```

- [ ] **Step 3: Update `context-engineering/SKILL.md`**

Append to the Feature Map section:
```markdown
**Concept labeling (v2):** Add `--concept-llm` to use Claude Haiku to assign product-level concept names ("Navigation" instead of "SideNavbar, TopNavBar") plus a sub-feature list in the click-detail panel. Labels are cached per cluster content hash under `cache/concept-labels/` — the same cluster shape reuses the same label until files move between clusters. Budget: ~$0.05 and ~1 min on a 100-cluster repo.
```

- [ ] **Step 4: Run on fleet + backend indexes (manual)**

```bash
python3 scripts/feature_map.py --index cache/fleet-index.json \
  -o cache/fleet-features-v2.html --concept-llm

python3 scripts/feature_map.py --index cache/backend-index.json --min-cluster 5 \
  -o cache/backend-features-v2.html --concept-llm
```

Open both, check the labels actually read like product concepts. If a label looks off, inspect the cached JSON at `cache/concept-labels/` and iterate on the prompt in `concept_labeler.build_prompt`.

- [ ] **Step 5: Commit**

```bash
git add context-engineering/scripts/feature_map.py \
        context-engineering/scripts/tests/test_feature_map.py \
        context-engineering/SKILL.md
git commit -m "feat(graph): --concept-llm CLI flag + docs for v2 concept labeling"
```

---

## Rollout

- v2 lands as additive CLI flags. Existing `feature_map.py` invocations keep working (no LLM unless `--concept-llm` passed).
- Cache is safe to commit or gitignore — user's preference. Default: gitignore under `cache/`.
- If Anthropic API is down or key is missing and `--concept-llm` was NOT passed, the pipeline is unchanged from v1. If `--concept-llm` WAS passed, fail fast rather than silently degrading — the user explicitly asked for the concept fold.

## Out of scope for v2 (note for a future v3)

- Domain *naming* via a second LLM call (currently `f'Domain {id}'` fallback). v3 should pass concatenated member concepts to Haiku to get "Product UI" instead of "Domain 0".
- Interactive collapse — click a domain swatch to collapse all its clusters into one mega-node and re-run force simulation. Strong UX win but non-trivial.
- Time-series: snapshot the feature map on every commit and animate how domains shift. Nice for retros, out of scope here.
