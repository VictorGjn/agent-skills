# Bird's-Eye Feature Map — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 2D SVG feature map that shows the natural feature clusters in a codebase and how they depend on each other, using community detection on the import graph.

**Architecture:** Label propagation on the import graph → meta-graph of clusters → D3 force-directed 2D SVG rendering in a standalone HTML file. Reuses `code_graph.py` for graph building and `index_workspace.py` for indexing. New files: `community_detect.py` (algorithm), `feature_map.py` (CLI + rendering).

**Tech Stack:** Python 3.10+, D3.js v7 (CDN), SVG rendering, existing `code_graph.py` + `index_workspace.py`

**Spec:** `docs/superpowers/specs/2026-04-22-graph-visualizer-v2-design.md`

---

### Task 1: Label Propagation Community Detection

**Files:**
- Create: `context-engineering/scripts/community_detect.py`
- Test: `context-engineering/scripts/tests/test_community_detect.py`

This is the core algorithm. Label propagation assigns each node a "label" (community ID), then iteratively updates each node's label to the most common label among its weighted neighbors. Converges in 5-15 iterations.

- [ ] **Step 1: Write failing test — basic community detection**

```python
# test_community_detect.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def test_two_clusters():
    """Two tightly connected groups should form two communities."""
    from community_detect import label_propagation

    # Cluster A: a1 <-> a2 <-> a3 (tight)
    # Cluster B: b1 <-> b2 <-> b3 (tight)
    # Weak link: a3 -> b1
    edges = [
        {'source': 'a1', 'target': 'a2', 'weight': 1.0},
        {'source': 'a2', 'target': 'a3', 'weight': 1.0},
        {'source': 'a1', 'target': 'a3', 'weight': 0.9},
        {'source': 'b1', 'target': 'b2', 'weight': 1.0},
        {'source': 'b2', 'target': 'b3', 'weight': 1.0},
        {'source': 'b1', 'target': 'b3', 'weight': 0.9},
        {'source': 'a3', 'target': 'b1', 'weight': 0.3},
    ]
    communities = label_propagation(edges)

    # Should produce 2 communities
    labels = set(communities.values())
    assert len(labels) == 2

    # a-nodes should share a label, b-nodes should share a label
    assert communities['a1'] == communities['a2'] == communities['a3']
    assert communities['b1'] == communities['b2'] == communities['b3']
    assert communities['a1'] != communities['b1']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest context-engineering/scripts/tests/test_community_detect.py::test_two_clusters -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'community_detect'`

- [ ] **Step 3: Implement label propagation**

```python
# community_detect.py
"""
Label Propagation Community Detection — pure Python, zero dependencies.

Algorithm:
1. Each node starts with its own unique label
2. In each iteration, every node adopts the label with highest weighted
   frequency among its neighbors
3. Repeat until convergence (labels stop changing) or max iterations
4. Merge tiny communities (< min_size) into their most-connected neighbor
"""

from collections import defaultdict
import random


def label_propagation(edges: list, max_iter: int = 15,
                      min_size: int = 2, seed: int = 42) -> dict:
    """Run label propagation on an undirected weighted graph.

    Args:
        edges: [{'source': str, 'target': str, 'weight': float}]
        max_iter: maximum iterations before stopping
        min_size: communities smaller than this get merged into neighbors
        seed: random seed for deterministic results

    Returns:
        {node_id: community_label} mapping
    """
    random.seed(seed)

    # Build undirected adjacency list
    adj = defaultdict(list)
    for e in edges:
        s, t, w = e['source'], e['target'], e.get('weight', 1.0)
        adj[s].append((t, w))
        adj[t].append((s, w))

    # Initialize: each node is its own community
    all_nodes = list(adj.keys())
    labels = {n: n for n in all_nodes}

    for _ in range(max_iter):
        changed = False
        order = list(all_nodes)
        random.shuffle(order)

        for node in order:
            if not adj[node]:
                continue

            # Count weighted label frequency among neighbors
            label_weight = defaultdict(float)
            for neighbor, weight in adj[node]:
                label_weight[labels[neighbor]] += weight

            # Pick the label with highest weight (tie-break: current label)
            best_label = max(label_weight, key=lambda l: (label_weight[l], l == labels[node]))
            if best_label != labels[node]:
                labels[node] = best_label
                changed = True

        if not changed:
            break

    # Merge tiny communities into most-connected neighbor
    community_sizes = defaultdict(int)
    for label in labels.values():
        community_sizes[label] += 1

    tiny = {label for label, size in community_sizes.items() if size < min_size}
    if tiny:
        for node in all_nodes:
            if labels[node] in tiny:
                # Find most-connected non-tiny neighbor community
                label_weight = defaultdict(float)
                for neighbor, weight in adj[node]:
                    nl = labels[neighbor]
                    if nl not in tiny:
                        label_weight[nl] += weight
                if label_weight:
                    labels[node] = max(label_weight, key=label_weight.get)

    # Normalize labels to 0-indexed integers
    unique = sorted(set(labels.values()))
    label_map = {l: i for i, l in enumerate(unique)}
    return {node: label_map[label] for node, label in labels.items()}


def build_meta_graph(labels: dict, edges: list) -> dict:
    """Build a cluster-level meta-graph from node labels and edges.

    Returns:
        {
            'clusters': {label: {'nodes': [...], 'internal_edges': int}},
            'meta_edges': [{'source': label, 'target': label, 'weight': int}]
        }
    """
    clusters = defaultdict(lambda: {'nodes': [], 'internal_edges': 0})
    for node, label in labels.items():
        clusters[label]['nodes'].append(node)

    # Count internal and cross-cluster edges
    cross_edges = defaultdict(int)
    for e in edges:
        s_label = labels.get(e['source'])
        t_label = labels.get(e['target'])
        if s_label is None or t_label is None:
            continue
        if s_label == t_label:
            clusters[s_label]['internal_edges'] += 1
        else:
            pair = (min(s_label, t_label), max(s_label, t_label))
            cross_edges[pair] += 1

    meta_edges = []
    for (s, t), count in cross_edges.items():
        meta_edges.append({'source': s, 'target': t, 'weight': count})

    return {'clusters': dict(clusters), 'meta_edges': meta_edges}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest context-engineering/scripts/tests/test_community_detect.py::test_two_clusters -v`
Expected: PASS

- [ ] **Step 5: Write additional tests**

Add tests for:
- `test_single_cluster` — fully connected graph → 1 community
- `test_isolated_nodes` — nodes with no edges stay as singletons (then merge via min_size)
- `test_build_meta_graph` — verifies cluster node lists and cross-cluster edge counts
- `test_min_size_merge` — a 1-node community merges into its neighbor

- [ ] **Step 6: Run all tests**

Run: `pytest context-engineering/scripts/tests/test_community_detect.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add context-engineering/scripts/community_detect.py context-engineering/scripts/tests/test_community_detect.py
git commit -m "feat(graph): label propagation community detection algorithm"
```

---

### Task 2: Cluster Labeling — Name Features from Code

**Files:**
- Modify: `context-engineering/scripts/community_detect.py` (add `label_clusters()`)
- Test: `context-engineering/scripts/tests/test_community_detect.py`

Each cluster needs a human-readable label. Strategy:
1. If >70% of files share a directory prefix → use directory name (e.g., `hurricane/`)
2. Otherwise → use top 2-3 symbol names by internal connectivity (e.g., "HurricaneService, HurricaneDto")
3. For knowledge bases (docs) → use the most common heading terms

- [ ] **Step 1: Write failing test**

```python
def test_label_clusters_by_directory():
    """Cluster where most files share a directory gets that directory as label."""
    from community_detect import label_clusters

    clusters = {
        0: {'nodes': ['src/hurricane/service.ts', 'src/hurricane/controller.ts',
                       'src/hurricane/dto.ts', 'src/shared/utils.ts']},
    }
    file_data = {
        'src/hurricane/service.ts': {'symbols': ['HurricaneService', 'fetchWeather']},
        'src/hurricane/controller.ts': {'symbols': ['HurricaneController']},
        'src/hurricane/dto.ts': {'symbols': ['HurricaneDto', 'HurricaneAlert']},
        'src/shared/utils.ts': {'symbols': ['formatDate']},
    }
    labels = label_clusters(clusters, file_data)
    assert 'hurricane' in labels[0].lower()


def test_label_clusters_by_symbols():
    """Cluster without clear directory → use top symbol names."""
    from community_detect import label_clusters

    clusters = {
        0: {'nodes': ['src/auth/login.ts', 'src/middleware/jwt.ts', 'src/routes/auth.ts']},
    }
    file_data = {
        'src/auth/login.ts': {'symbols': ['loginUser', 'validateCredentials']},
        'src/middleware/jwt.ts': {'symbols': ['verifyJwt', 'createToken']},
        'src/routes/auth.ts': {'symbols': ['authRouter']},
    }
    labels = label_clusters(clusters, file_data)
    assert len(labels[0]) > 0  # should have a meaningful name
```

- [ ] **Step 2: Run to verify fails, implement `label_clusters()`, run to verify passes**

`label_clusters(clusters, file_data)` takes:
- `clusters`: `{label: {'nodes': [path, ...]}}` from `build_meta_graph`
- `file_data`: `{path: {'symbols': [name, ...], 'headings': [title, ...]}}` from workspace index

Returns: `{label: human_readable_name}`

Algorithm:
1. Collect directory prefixes for all files in cluster
2. If one directory covers >70% of files → use `dirname` as label
3. Else collect all symbol names, pick top 2 by frequency across files
4. For doc-only clusters → use top heading terms instead

- [ ] **Step 3: Commit**

```bash
git add context-engineering/scripts/community_detect.py context-engineering/scripts/tests/test_community_detect.py
git commit -m "feat(graph): cluster labeling by directory and symbol names"
```

---

### Task 3: Feature Map Script — Index to Meta-Graph

**Files:**
- Create: `context-engineering/scripts/feature_map.py`
- Test: `context-engineering/scripts/tests/test_feature_map.py`

This script connects the indexer → code_graph → community detection → meta-graph pipeline. No rendering yet — just the data pipeline.

- [ ] **Step 1: Write failing test**

```python
def test_pipeline_produces_meta_graph():
    """Index → graph → communities → meta-graph pipeline works end-to-end."""
    from feature_map import build_feature_map

    # Mini index with two feature clusters
    index = {
        'root': '/repos/test',
        'totalFiles': 4,
        'files': [
            # Hurricane feature
            {'path': 'src/hurricane/service.ts', 'tokens': 200,
             'tree': {'title': 'src/hurricane/service.ts', 'depth': 0,
                      'tokens': 200, 'totalTokens': 200, 'text': "import { HurricaneDto } from './dto';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class HurricaneService', 'depth': 1,
                                    'tokens': 150, 'totalTokens': 150, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/hurricane/dto.ts', 'tokens': 100,
             'tree': {'title': 'src/hurricane/dto.ts', 'depth': 0,
                      'tokens': 100, 'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type HurricaneDto', 'depth': 1,
                                    'tokens': 50, 'totalTokens': 50, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            # Voyage feature
            {'path': 'src/voyage/manager.ts', 'tokens': 300,
             'tree': {'title': 'src/voyage/manager.ts', 'depth': 0,
                      'tokens': 300, 'totalTokens': 300, 'text': "import { VoyageDto } from './dto';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class VoyageManager', 'depth': 1,
                                    'tokens': 200, 'totalTokens': 200, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/voyage/dto.ts', 'tokens': 100,
             'tree': {'title': 'src/voyage/dto.ts', 'depth': 0,
                      'tokens': 100, 'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type VoyageDto', 'depth': 1,
                                    'tokens': 50, 'totalTokens': 50, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
        ],
    }
    result = build_feature_map(index)

    assert 'clusters' in result
    assert 'meta_edges' in result
    assert 'cluster_labels' in result
    assert len(result['clusters']) >= 1  # at least 1 cluster
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement `build_feature_map()`**

```python
# feature_map.py
"""
Feature Map — Bird's-eye view of codebase features via community detection.

Pipeline: workspace index → import graph → label propagation → meta-graph → D3 SVG

Usage:
  python3 feature_map.py                                      # uses cache/workspace-index.json
  python3 feature_map.py --index path/to/index.json           # custom index
  python3 feature_map.py --multi-index idx1.json idx2.json    # multi-repo
  python3 feature_map.py -o my-map.html                       # custom output
"""

import json
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from code_graph import build_graph, build_graph_with_fallback
from community_detect import label_propagation, build_meta_graph, label_clusters


def build_feature_map(index: dict, graphify_path: str = None) -> dict:
    """Full pipeline: index → graph → communities → labeled meta-graph."""
    files = index.get('files', [])
    graph = build_graph_with_fallback(files, graphify_path)

    # Run community detection
    labels = label_propagation(graph['edges'])

    # Build meta-graph
    meta = build_meta_graph(labels, graph['edges'])

    # Build file_data for labeling
    file_data = {}
    for f in files:
        path = f['path'].replace('\\', '/')
        tree = f.get('tree', {})
        symbols = [c.get('title', '') for c in tree.get('children', []) if c.get('title')]
        headings = [h.get('title', '') for h in f.get('headings', [])]
        file_data[path] = {'symbols': symbols, 'headings': headings}

    # Label clusters
    cluster_labels = label_clusters(meta['clusters'], file_data)

    # Enrich clusters with metadata
    for label, cluster in meta['clusters'].items():
        cluster['label'] = cluster_labels.get(label, f'Cluster {label}')
        cluster['file_count'] = len(cluster['nodes'])
        cluster['total_tokens'] = sum(
            next((f['tokens'] for f in files if f['path'].replace('\\', '/') == n), 0)
            for n in cluster['nodes']
        )

    return {
        'clusters': meta['clusters'],
        'meta_edges': meta['meta_edges'],
        'cluster_labels': cluster_labels,
        'node_labels': labels,  # per-file community assignment
    }
```

- [ ] **Step 4: Run test, verify passes**

- [ ] **Step 5: Commit**

```bash
git add context-engineering/scripts/feature_map.py context-engineering/scripts/tests/test_feature_map.py
git commit -m "feat(graph): feature map data pipeline — index to meta-graph"
```

---

### Task 4: D3 SVG Rendering Template

**Files:**
- Modify: `context-engineering/scripts/feature_map.py` (add `generate_html()` + HTML template)
- Test: `context-engineering/scripts/tests/test_feature_map.py`

Build the HTML template with D3.js force-directed 2D SVG graph. This is the biggest task — the full interactive visualization.

- [ ] **Step 1: Write failing test**

```python
def test_generate_html():
    """generate_html produces valid HTML with D3 graph data."""
    from feature_map import generate_html

    feature_data = {
        'clusters': {
            0: {'label': 'Hurricane', 'nodes': ['a.ts', 'b.ts'],
                'file_count': 2, 'total_tokens': 300, 'internal_edges': 1},
            1: {'label': 'Voyage', 'nodes': ['c.ts', 'd.ts'],
                'file_count': 2, 'total_tokens': 400, 'internal_edges': 1},
        },
        'meta_edges': [{'source': 0, 'target': 1, 'weight': 2}],
        'cluster_labels': {0: 'Hurricane', 1: 'Voyage'},
    }
    html = generate_html(feature_data, 'Test Repo')

    assert '<!DOCTYPE html>' in html
    assert 'd3.js' in html or 'd3@' in html or 'd3.v' in html
    assert 'Hurricane' in html
    assert 'Voyage' in html
    assert '<svg' in html or 'createSvg' in html or 'svg' in html.lower()
```

- [ ] **Step 2: Run to verify fails**

- [ ] **Step 3: Implement HTML template**

The D3 template should include:
- SVG container (full viewport)
- Force simulation: charge (repulsion), links (attraction), center
- Circle nodes sized by `file_count` (radius = `8 + sqrt(file_count) * 4`)
- Text labels on each node (cluster label)
- Lines for edges (width = `1 + log(weight)`)
- Zoom/pan via `d3.zoom()`
- Click handler: show detail panel with file list, symbols, connections
- Search bar: highlight clusters matching query text
- Arctic Maritime theme colors
- Legend showing cluster count and file count

Key template structure:
```html
<!DOCTYPE html>
<html>
<head>
  <script src="https://d3js.org/d3.v7.min.js"></script>
  <style>/* Arctic Maritime theme */</style>
</head>
<body>
  <svg id="graph"></svg>
  <div id="stats"><!-- title, cluster count, file count --></div>
  <div id="detail"><!-- click detail: files, symbols, connections --></div>
  <div id="search"><!-- search bar --></div>
  <script>
    const data = {{GRAPH_DATA}};
    // D3 force simulation
    // Node rendering (circles + text)
    // Edge rendering (lines)
    // Click handlers
    // Search
  </script>
</body>
</html>
```

Node colors: assign each cluster a color from a palette. Use D3's `d3.schemeTableau10` or a custom maritime palette:
```javascript
const palette = [
  '#2563EB', '#0D9488', '#7C3AED', '#F59E0B', '#EF4444',
  '#10B981', '#6366F1', '#EC4899', '#14B8A6', '#8B5CF6',
  '#F97316', '#06B6D4', '#84CC16', '#E11D48', '#0EA5E9'
];
```

- [ ] **Step 4: Run test, verify passes**

- [ ] **Step 5: Write test for click detail content**

```python
def test_html_includes_file_lists():
    """HTML embeds per-cluster file lists for the detail panel."""
    from feature_map import generate_html

    feature_data = {
        'clusters': {
            0: {'label': 'Hurricane', 'nodes': ['src/hurricane/service.ts', 'src/hurricane/dto.ts'],
                'file_count': 2, 'total_tokens': 300, 'internal_edges': 1},
        },
        'meta_edges': [],
        'cluster_labels': {0: 'Hurricane'},
    }
    html = generate_html(feature_data, 'Test')
    assert 'hurricane/service.ts' in html
    assert 'hurricane/dto.ts' in html
```

- [ ] **Step 6: Run all tests, commit**

```bash
git add context-engineering/scripts/feature_map.py context-engineering/scripts/tests/test_feature_map.py
git commit -m "feat(graph): D3 2D SVG rendering for feature map"
```

---

### Task 5: CLI + Multi-Repo Support

**Files:**
- Modify: `context-engineering/scripts/feature_map.py` (add `main()`, argparse, multi-index)
- Test: `context-engineering/scripts/tests/test_feature_map.py`

Wire up CLI arguments and add multi-repo support (reuse `merge_indexes` from `visualize_graph.py` or reimplement simply).

- [ ] **Step 1: Write failing test for multi-repo**

```python
def test_multi_repo_clusters():
    """Multi-repo feature map shows per-repo clusters with cross-repo edges."""
    from feature_map import build_feature_map, merge_indexes

    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 2, 'totalTokens': 300,
        'files': [
            {'path': 'src/hurricane/map.tsx', 'tokens': 200,
             'tree': {'title': 'src/hurricane/map.tsx', 'depth': 0, 'tokens': 200,
                      'totalTokens': 200, 'text': "import { HurricaneDto } from './types';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'HurricaneMap', 'depth': 1, 'tokens': 150,
                                    'totalTokens': 150, 'children': [], 'text': '',
                                    'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/hurricane/types.ts', 'tokens': 100,
             'tree': {'title': 'src/hurricane/types.ts', 'depth': 0, 'tokens': 100,
                      'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type HurricaneDto', 'depth': 1, 'tokens': 50,
                                    'totalTokens': 50, 'children': [], 'text': '',
                                    'firstSentence': '', 'firstParagraph': ''}]}},
        ],
        'directories': ['src', 'src/hurricane'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 1, 'totalTokens': 200,
        'files': [
            {'path': 'src/hurricane/service.ts', 'tokens': 200,
             'tree': {'title': 'src/hurricane/service.ts', 'depth': 0, 'tokens': 200,
                      'totalTokens': 200, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class HurricaneService', 'depth': 1, 'tokens': 150,
                                    'totalTokens': 150, 'children': [], 'text': '',
                                    'firstSentence': '', 'firstParagraph': ''}]}},
        ],
        'directories': ['src', 'src/hurricane'],
    }
    merged = merge_indexes([idx_a, idx_b])
    result = build_feature_map(merged)

    assert len(result['clusters']) >= 1
    # Check that cluster nodes have repo-prefixed paths
    all_nodes = []
    for c in result['clusters'].values():
        all_nodes.extend(c['nodes'])
    assert any('fleet/' in n for n in all_nodes) or any('backend/' in n for n in all_nodes)
```

- [ ] **Step 2: Implement CLI main() and merge_indexes**

The `merge_indexes` function can be copied from `visualize_graph.py` or extracted into a shared utility. Prefer copying for now (keep scripts independent).

CLI arguments:
```python
parser.add_argument('--index', default=None, help='Path to workspace-index.json')
parser.add_argument('--multi-index', nargs='+', default=None, help='Multiple indexes to merge')
parser.add_argument('--graphify', default=None, help='Path to graphify graph.json')
parser.add_argument('-o', '--output', default=None, help='Output HTML path')
parser.add_argument('--title', default=None, help='Graph title')
parser.add_argument('--min-cluster', type=int, default=2, help='Min files per cluster')
```

- [ ] **Step 3: Run all tests, commit**

```bash
git add context-engineering/scripts/feature_map.py context-engineering/scripts/tests/test_feature_map.py
git commit -m "feat(graph): feature map CLI with multi-repo support"
```

---

### Task 6: Integration Test with Real Indexes

**Files:**
- Test: manual CLI test (not automated — uses cached indexes)

- [ ] **Step 1: Run on fleet index**

```bash
python3 scripts/feature_map.py --index cache/fleet-index.json -o cache/fleet-features.html
```

Verify: opens in browser, shows feature clusters, labels make sense, click works.

- [ ] **Step 2: Run on multi-repo**

```bash
python3 scripts/feature_map.py --multi-index cache/fleet-index.json cache/backend-index.json -o cache/multi-features.html
```

Verify: shows clusters from both repos, cross-repo edges visible.

- [ ] **Step 3: Run on backend index (large)**

```bash
python3 scripts/feature_map.py --index cache/backend-index.json -o cache/backend-features.html
```

Verify: handles 2000+ file repo, produces reasonable cluster count (15-40), renders smoothly.

- [ ] **Step 4: Fix any issues found, commit**

---

### Task 7: Update SKILL.md Documentation

**Files:**
- Modify: `context-engineering/SKILL.md`

- [ ] **Step 1: Add feature map documentation**

Add under the existing "Graph visualization" section:

```markdown
### Feature map (bird's-eye)

```bash
# Single repo — shows feature clusters and how they depend on each other
python3 scripts/feature_map.py --index cache/workspace-index.json

# Multi-repo
python3 scripts/feature_map.py --multi-index cache/fleet-index.json cache/backend-index.json

# Custom output
python3 scripts/feature_map.py --index cache/workspace-index.json -o my-features.html
```

Uses label propagation community detection on the import graph to discover natural feature clusters. Each cluster is labeled by its dominant directory or top symbol names. Renders as interactive 2D SVG (D3 force-directed). Click a cluster to see its files and connections.
```

- [ ] **Step 2: Commit**

```bash
git add context-engineering/SKILL.md
git commit -m "docs(graph): add feature map documentation to SKILL.md"
```

---

### Task 8: Scripts table update

**Files:**
- Modify: `context-engineering/SKILL.md`

- [ ] **Step 1: Add new scripts to the table**

Add to the Scripts table:

| `feature_map.py` | Bird's-eye feature map: community detection → D3 2D SVG |
| `community_detect.py` | Label propagation community detection (pure Python) |

- [ ] **Step 2: Commit**

```bash
git add context-engineering/SKILL.md
git commit -m "docs(graph): add feature_map and community_detect to scripts table"
```
