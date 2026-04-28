# Graph Visualizer Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add click-highlight, query overlay, and dual-graph concept clustering to the 3D force-directed graph visualizer.

**Architecture:** Feature 1 is pure JS in the HTML template. Feature 2 adds a Python scoring bridge (`--query` flag) + an in-browser search bar with live scoring. Feature 3 adds `--multi-index` for merging indexes, prefix-based concept clustering, and cross-repo DTO linking.

**Tech Stack:** Python 3.10+, 3d-force-graph (Three.js), pack_context_lib scoring pipeline, workspace index JSON.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `context-engineering/scripts/visualize_graph.py` | Modify | All 3 features: HTML template JS + Python CLI flags |
| `context-engineering/scripts/tests/test_visualize_graph.py` | Create | Unit tests for node extraction, scoring bridge, multi-index merge, clustering |

All changes live in `visualize_graph.py`. No new Python modules needed — the scoring functions are imported from existing `pack_context_lib.py` and `pack_context.py`.

---

## Task 1: Click-Highlight Connected Nodes (Pure JS)

**Files:**
- Modify: `context-engineering/scripts/visualize_graph.py` (HTML_TEMPLATE JS section, lines ~288-383)
- Test: `context-engineering/scripts/tests/test_visualize_graph.py`

- [ ] **Step 1: Write test for adjacency structure**

```python
# tests/test_visualize_graph.py
"""Tests for visualize_graph features."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def test_adjacency_built_from_edges():
    """The HTML template builds adj from links. Verify Python edge output has required fields."""
    from visualize_graph import extract_nodes, generate_html

    # Minimal index with two files
    index = {
        'files': [
            {'path': 'a.ts', 'tokens': 100, 'tree': {'title': 'a.ts', 'depth': 0, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
            {'path': 'b.ts', 'tokens': 80, 'tree': {'title': 'b.ts', 'depth': 0, 'tokens': 80, 'totalTokens': 80, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
        ]
    }
    nodes, file_ids = extract_nodes(index, include_symbols=False)
    edges = [{'source': 'a.ts', 'target': 'b.ts', 'kind': 'imports', 'weight': 1.0}]
    html = generate_html(nodes, edges, 'Test')

    # Verify graph data is embedded and parseable
    assert '"source": "a.ts"' in html or '"source":"a.ts"' in html
    assert 'highlightNodes' in html  # Feature 1 marker
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/victo/Repos/agent-skills && python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_adjacency_built_from_edges -v`
Expected: FAIL — `highlightNodes` not in HTML yet.

- [ ] **Step 3: Implement click-highlight in HTML template**

In the `<script>` section of `HTML_TEMPLATE`, add a highlight system to `onNodeClick`:

```javascript
// State for highlight
let highlightNodes = new Set();
let highlightLinks = new Set();
let selectedNode = null;

// On click: populate highlight sets
function selectNode(node) {
  highlightNodes.clear();
  highlightLinks.clear();
  selectedNode = node;

  if (node) {
    highlightNodes.add(node);
    // Add all directly connected nodes
    graphData.links.forEach(link => {
      const s = typeof link.source === 'object' ? link.source : graphData.nodes.find(n => n.id === link.source);
      const t = typeof link.target === 'object' ? link.target : graphData.nodes.find(n => n.id === link.target);
      if (s && s.id === node.id) { highlightNodes.add(t); highlightLinks.add(link); }
      if (t && t.id === node.id) { highlightNodes.add(s); highlightLinks.add(link); }
    });
  }
}
```

Update `onNodeClick` to call `selectNode(node)` then refresh appearance. Update `onBackgroundClick` to call `selectNode(null)`.

Add `.nodeColor()` override that dims non-highlighted nodes (opacity via hex alpha or direct color with reduced saturation):

```javascript
.nodeColor(n => {
  if (selectedNode && !highlightNodes.has(n))
    return '#E2E8F0'; // dimmed
  return nodeColors[n.type] || '#94A3B8';
})
.linkColor(l => {
  if (selectedNode && !highlightLinks.has(l))
    return 'rgba(203,213,225,0.15)'; // dimmed
  return edgeColors[l.kind] || '#CBD5E1';
})
.linkWidth(l => {
  if (selectedNode && highlightLinks.has(l))
    return Math.max(1.5, (l.weight || 0.3) * 4); // thicker
  return l.kind === 'contains' ? 0.2 : Math.max(0.4, (l.weight || 0.3) * 2.5);
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/victo/Repos/agent-skills && python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_adjacency_built_from_edges -v`
Expected: PASS

- [ ] **Step 5: Manual verification**

Run: `cd C:/Users/victo/Repos/agent-skills && python context-engineering/scripts/visualize_graph.py --index context-engineering/cache/workspace-index.json --top 50`
Open graph.html in browser. Click a node — connected nodes stay colored, unconnected dim to gray. Click background to reset.

- [ ] **Step 6: Commit**

```bash
git add context-engineering/scripts/visualize_graph.py context-engineering/scripts/tests/test_visualize_graph.py
git commit -m "feat(visualize): click-highlight connected nodes in 3D graph"
```

---

## Task 2: Query Overlay — Python Scoring Bridge

**Files:**
- Modify: `context-engineering/scripts/visualize_graph.py` (add `--query` flag, scoring bridge function)
- Test: `context-engineering/scripts/tests/test_visualize_graph.py`

- [ ] **Step 1: Write test for scoring bridge**

```python
def test_score_for_overlay():
    """score_for_overlay returns {path: relevance} dict."""
    from visualize_graph import score_for_overlay

    index = {
        'files': [
            {'path': 'src/auth/middleware.ts', 'tokens': 200,
             'tree': {'title': 'src/auth/middleware.ts', 'depth': 0, 'tokens': 200, 'totalTokens': 200,
                      'children': [{'title': 'authMiddleware', 'depth': 1, 'tokens': 100, 'totalTokens': 100,
                                    'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}],
                      'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'ground_truth'},
            {'path': 'README.md', 'tokens': 50,
             'tree': {'title': 'README.md', 'depth': 0, 'tokens': 50, 'totalTokens': 50,
                      'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'artifact'},
        ]
    }
    scores = score_for_overlay(index, 'auth middleware')
    assert 'src/auth/middleware.ts' in scores
    assert scores['src/auth/middleware.ts'] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_score_for_overlay -v`
Expected: FAIL — `score_for_overlay` doesn't exist yet.

- [ ] **Step 3: Implement `score_for_overlay()` function**

Add to `visualize_graph.py` after the imports:

```python
from pack_context_lib import tokenize_query, score_file

def score_for_overlay(index: dict, query: str) -> dict:
    """Score all files against query, return {path: relevance} dict.

    Used by --query flag to overlay relevance on the graph,
    and embedded as JSON in the HTML for the search bar.
    """
    query_tokens = tokenize_query(query)
    query_lower = query.lower()
    scores = {}
    for f in index.get('files', []):
        rel = score_file(f, query_tokens, query_lower)
        if rel > 0:
            path = f['path'].replace('\\', '/')
            scores[path] = round(rel, 4)
    return scores
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_score_for_overlay -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add context-engineering/scripts/visualize_graph.py context-engineering/scripts/tests/test_visualize_graph.py
git commit -m "feat(visualize): add score_for_overlay scoring bridge"
```

---

## Task 3: Query Overlay — CLI Flag + HTML Rendering

**Files:**
- Modify: `context-engineering/scripts/visualize_graph.py` (argparse, HTML template, generate_html)

- [ ] **Step 1: Write test for `--query` flag embedding scores in HTML**

```python
def test_query_flag_embeds_scores():
    """When query is provided, HTML should contain relevanceScores JSON."""
    from visualize_graph import generate_html, extract_nodes

    index = {
        'files': [
            {'path': 'auth.ts', 'tokens': 100, 'tree': {'title': 'auth.ts', 'depth': 0, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
        ]
    }
    nodes, _ = extract_nodes(index, include_symbols=False)
    edges = []
    scores = {'auth.ts': 0.75}
    html = generate_html(nodes, edges, 'Test', query='auth', relevance_scores=scores)

    assert 'relevanceScores' in html
    assert '0.75' in html
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `generate_html` doesn't accept `query`/`relevance_scores` params yet.

- [ ] **Step 3: Implement CLI flag and HTML overlay**

**3a. Update `generate_html()` signature:**
```python
def generate_html(nodes, edges, title, query=None, relevance_scores=None):
```

Add to template replacement:
```python
html = html.replace('{{QUERY}}', json.dumps(query or ''))
html = html.replace('{{RELEVANCE_SCORES}}', json.dumps(relevance_scores or {}, ensure_ascii=False))
```

**3b. Add `--query` flag to argparse in `main()`:**
```python
parser.add_argument('--query', default=None, help='Overlay relevance scores for a query')
```

In `main()`, after loading index, if `args.query`:
```python
from pack_context_lib import tokenize_query, score_file
relevance_scores = score_for_overlay(index, args.query)
```

Pass to `generate_html(..., query=args.query, relevance_scores=relevance_scores)`.

**3c. Add search bar HTML** (inside `HTML_TEMPLATE`, after `#stats`):

```html
<div id="search">
  <input id="search-input" type="text" placeholder="Search query..." value="">
  <span id="search-info"></span>
</div>
```

Style it Arctic Maritime (fixed top-center, white card, ocean blue accent).

**3d. Add JS overlay logic** (in `<script>`, after Graph initialization):

```javascript
const relevanceScores = {{RELEVANCE_SCORES}};
const initialQuery = {{QUERY}};

function applyRelevanceOverlay(scores) {
  if (!scores || Object.keys(scores).length === 0) {
    // Reset — restore original colors/sizes
    Graph.nodeColor(n => {
      if (selectedNode && !highlightNodes.has(n)) return '#E2E8F0';
      return nodeColors[n.type] || '#94A3B8';
    });
    Graph.nodeVal(n => n.val || 2);
    document.getElementById('search-info').textContent = '';
    return;
  }

  const maxRel = Math.max(...Object.values(scores), 0.01);
  const matchCount = Object.keys(scores).length;
  document.getElementById('search-info').textContent = matchCount + ' matches';

  Graph.nodeColor(n => {
    const filePath = n.parent || n.id;
    const rel = scores[filePath] || 0;
    if (rel > 0) {
      const t = rel / maxRel;
      return t > 0.6 ? '#2563EB' : t > 0.3 ? '#0D9488' : '#38BDF8';
    }
    return '#E2E8F0';
  });
  Graph.nodeVal(n => {
    const filePath = n.parent || n.id;
    const rel = scores[filePath] || 0;
    if (rel > 0) return (n.val || 2) * (1 + rel * 2);
    return (n.val || 2) * 0.5;
  });
}

// Apply initial overlay if --query was used
if (initialQuery) {
  document.getElementById('search-input').value = initialQuery;
  applyRelevanceOverlay(relevanceScores);
}
```

**3e. Add lightweight client-side search** using node data already in `graphData`:

```javascript
// Client-side keyword search using existing graph node data
function clientScore(query) {
  if (!query || query.trim().length < 2) return {};
  const terms = query.toLowerCase().split(/\s+/).filter(t => t.length >= 2);
  const scores = {};
  graphData.nodes.forEach(n => {
    const searchable = ((n.path || '') + ' ' + (n.label || '')).toLowerCase();
    let score = 0;
    terms.forEach(t => { if (searchable.includes(t)) score += 0.3; });
    if (score > 0) {
      const filePath = n.parent || n.id;
      scores[filePath] = Math.max(scores[filePath] || 0, Math.min(1.0, score));
    }
  });
  return scores;
}

let searchTimeout;
document.getElementById('search-input').addEventListener('input', e => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    const q = e.target.value.trim();
    applyRelevanceOverlay(q ? clientScore(q) : {});
  }, 300);
});
```

No extra template variables needed — uses `graphData.nodes` already embedded in the page.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_query_flag_embeds_scores -v`
Expected: PASS

- [ ] **Step 5: Manual verification**

Run with fleet index:
```bash
python context-engineering/scripts/visualize_graph.py \
  --index context-engineering/cache/workspace-index.json \
  --top 80 --query "VoyageReport"
```
Open graph.html. Matched nodes should glow blue, unmatched dimmed. Type in search bar to re-query live.

- [ ] **Step 6: Commit**

```bash
git add context-engineering/scripts/visualize_graph.py context-engineering/scripts/tests/test_visualize_graph.py
git commit -m "feat(visualize): query overlay with --query flag and live search bar"
```

---

## Task 4: Multi-Index Merge (`--multi-index`)

**Files:**
- Modify: `context-engineering/scripts/visualize_graph.py` (add `merge_indexes()` function, `--multi-index` flag)
- Test: `context-engineering/scripts/tests/test_visualize_graph.py`

- [ ] **Step 1: Write test for `merge_indexes()`**

```python
def test_merge_indexes():
    """merge_indexes combines two indexes with repo-prefixed paths."""
    from visualize_graph import merge_indexes

    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 1, 'totalTokens': 100,
        'files': [
            {'path': 'src/types.ts', 'tokens': 100, 'tree': {'title': 'src/types.ts', 'depth': 0,
             'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'ground_truth'},
        ],
        'directories': ['src'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 1, 'totalTokens': 200,
        'files': [
            {'path': 'src/dto.ts', 'tokens': 200, 'tree': {'title': 'src/dto.ts', 'depth': 0,
             'tokens': 200, 'totalTokens': 200, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'ground_truth'},
        ],
        'directories': ['src'],
    }
    merged = merge_indexes([idx_a, idx_b])

    assert merged['totalFiles'] == 2
    paths = [f['path'] for f in merged['files']]
    # Paths should be prefixed with repo name
    assert any('fleet/' in p for p in paths)
    assert any('backend/' in p for p in paths)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `merge_indexes` doesn't exist.

- [ ] **Step 3: Implement `merge_indexes()`**

```python
def merge_indexes(indexes: list) -> dict:
    """Merge multiple workspace indexes into one, prefixing paths with repo name.

    Each index gets its paths prefixed with the repo directory name
    (last component of root) to avoid collisions.
    """
    merged_files = []
    total_tokens = 0
    dirs = set()

    for idx in indexes:
        root = idx.get('root', 'unknown')
        repo_name = Path(root).name
        for f in idx.get('files', []):
            new_path = f'{repo_name}/{f["path"]}'.replace('\\', '/')
            new_file = {**f, 'path': new_path, 'repo': repo_name}
            # Update tree title to include repo prefix
            if 'tree' in new_file and new_file['tree']:
                new_file['tree'] = {**new_file['tree'], 'title': new_path}
            merged_files.append(new_file)
            total_tokens += f.get('tokens', 0)
        for d in idx.get('directories', []):
            dirs.add(f'{repo_name}/{d}')

    return {
        'root': 'multi-repo',
        'totalFiles': len(merged_files),
        'totalTokens': total_tokens,
        'files': merged_files,
        'directories': sorted(dirs),
    }
```

- [ ] **Step 4: Add `--multi-index` CLI flag**

```python
parser.add_argument('--multi-index', nargs='+', default=None,
                    help='Multiple workspace-index.json paths to merge')
```

In `main()`, if `args.multi_index`:
```python
if args.multi_index:
    indexes = []
    for path in args.multi_index:
        with open(path, encoding='utf-8') as f:
            indexes.append(json.load(f))
    index = merge_indexes(indexes)
else:
    # existing single-index loading
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_merge_indexes -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add context-engineering/scripts/visualize_graph.py context-engineering/scripts/tests/test_visualize_graph.py
git commit -m "feat(visualize): --multi-index flag for merging multiple workspace indexes"
```

---

## Task 5: Cross-Repo DTO Linking

**Files:**
- Modify: `context-engineering/scripts/visualize_graph.py` (add `find_cross_repo_links()`)
- Test: `context-engineering/scripts/tests/test_visualize_graph.py`

- [ ] **Step 1: Write test for `find_cross_repo_links()`**

```python
def test_find_cross_repo_links():
    """Matching symbol names across repos create cross-repo edges."""
    from visualize_graph import find_cross_repo_links

    nodes = [
        {'id': 'fleet/src/types.ts::type VoyageReport', 'label': 'VoyageReport', 'type': 'type', 'path': 'fleet/src/types.ts'},
        {'id': 'backend/src/dto.ts::type VoyageReport', 'label': 'VoyageReport', 'type': 'type', 'path': 'backend/src/dto.ts'},
        {'id': 'fleet/src/types.ts::type FleetStatus', 'label': 'FleetStatus', 'type': 'type', 'path': 'fleet/src/types.ts'},
        {'id': 'fleet/src/utils.ts::doSomething', 'label': 'doSomething', 'type': 'function', 'path': 'fleet/src/utils.ts'},
    ]
    links = find_cross_repo_links(nodes)

    # VoyageReport appears in both repos — should create a link
    assert len(links) >= 1
    link = links[0]
    assert link['kind'] == 'shared_type'
    assert 'fleet' in link['source'] and 'backend' in link['target'] or \
           'backend' in link['source'] and 'fleet' in link['target']

    # FleetStatus and doSomething have no cross-repo match — no extra links
    assert len(links) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `find_cross_repo_links` doesn't exist.

- [ ] **Step 3: Implement `find_cross_repo_links()`**

```python
def find_cross_repo_links(nodes: list) -> list:
    """Find symbol nodes with the same label across different repos.

    Creates 'shared_type' edges between matching DTOs/types/classes
    in different repositories (e.g., fleet VoyageReport ↔ backend VoyageReport).
    """
    # Group symbol nodes by label (excluding file nodes)
    from collections import defaultdict
    by_label = defaultdict(list)
    for n in nodes:
        if n['type'] in ('type', 'class', 'interface', 'enum'):
            by_label[n['label']].append(n)

    links = []
    for label, group in by_label.items():
        if len(group) < 2:
            continue
        # Get unique repos for this label
        repos = set()
        for n in group:
            parts = n['path'].split('/')
            if len(parts) >= 2:
                repos.add(parts[0])
        if len(repos) < 2:
            continue  # same repo, skip

        # Create links between all cross-repo pairs
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                repo_i = group[i]['path'].split('/')[0]
                repo_j = group[j]['path'].split('/')[0]
                if repo_i != repo_j:
                    links.append({
                        'source': group[i]['id'],
                        'target': group[j]['id'],
                        'kind': 'shared_type',
                        'weight': 0.8,
                    })
    return links
```

- [ ] **Step 4: Wire into `main()`**

After building nodes and edges, if multi-index:
```python
if args.multi_index:
    cross_links = find_cross_repo_links(nodes)
    edges.extend(cross_links)
    print(f'{len(cross_links)} cross-repo type links', file=sys.stderr)
```

Add `shared_type` to edge colors and legend in HTML template:
```javascript
shared_type: '#F59E0B',  // amber for cross-repo links
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_find_cross_repo_links -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add context-engineering/scripts/visualize_graph.py context-engineering/scripts/tests/test_visualize_graph.py
git commit -m "feat(visualize): cross-repo DTO linking via shared_type edges"
```

---

## Task 6: Concept Clustering by Naming Prefix

**Files:**
- Modify: `context-engineering/scripts/visualize_graph.py` (add `cluster_by_prefix()`, update `extract_nodes()`)
- Test: `context-engineering/scripts/tests/test_visualize_graph.py`

- [ ] **Step 1: Write test for `cluster_by_prefix()`**

```python
def test_cluster_by_prefix():
    """Symbols with shared naming prefix get grouped into cluster nodes."""
    from visualize_graph import cluster_by_prefix

    nodes = [
        {'id': 'types.ts::type VoyageReport', 'label': 'VoyageReport', 'type': 'type', 'path': 'types.ts', 'tokens': 50, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type VoyageDetail', 'label': 'VoyageDetail', 'type': 'type', 'path': 'types.ts', 'tokens': 40, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type VoyageStatus', 'label': 'VoyageStatus', 'type': 'type', 'path': 'types.ts', 'tokens': 30, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type FleetVessel', 'label': 'FleetVessel', 'type': 'type', 'path': 'types.ts', 'tokens': 60, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type FleetStatus', 'label': 'FleetStatus', 'type': 'type', 'path': 'types.ts', 'tokens': 20, 'val': 2, 'parent': 'types.ts'},
        {'id': 'utils.ts::doStuff', 'label': 'doStuff', 'type': 'function', 'path': 'utils.ts', 'tokens': 10, 'val': 2},
    ]

    clustered, cluster_edges = cluster_by_prefix(nodes, min_group=3)

    # Voyage* has 3 members -> should be clustered
    cluster_ids = [n['id'] for n in clustered if n['type'] == 'cluster']
    assert any('Voyage' in c for c in cluster_ids)

    # Fleet* has only 2 members -> should NOT be clustered (min_group=3)
    assert not any('Fleet' in c for c in cluster_ids if c.endswith(':cluster'))

    # Individual Voyage* nodes should be marked as clustered, not removed
    clustered_members = [n for n in clustered if n.get('clustered')]
    assert len(clustered_members) == 3  # 3 Voyage* members
    assert all(m['val'] == 0.3 for m in clustered_members)

    # Cluster edges should connect cluster -> member
    assert len(cluster_edges) >= 3  # one per Voyage* member
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `cluster_by_prefix` doesn't exist.

- [ ] **Step 3: Implement `cluster_by_prefix()`**

```python
def cluster_by_prefix(nodes: list, min_group: int = 3) -> tuple:
    """Group symbol nodes by CamelCase prefix to reduce visual clutter.

    E.g., VoyageReport, VoyageDetail, VoyageStatus → "Voyage*" cluster node.
    Only clusters type/class/interface symbols with min_group or more members.

    Returns (new_nodes, cluster_edges).
    """
    import re
    # Extract CamelCase prefix (first capital word)
    def get_prefix(label):
        m = re.match(r'^([A-Z][a-z]+)', label)
        return m.group(1) if m else None

    # Group clusterables by (file, prefix)
    clusterable_types = {'type', 'class', 'interface', 'enum'}
    groups = defaultdict(list)
    for n in nodes:
        if n.get('type') in clusterable_types and n.get('parent'):
            prefix = get_prefix(n['label'])
            if prefix and len(prefix) >= 3:
                groups[(n.get('parent', ''), prefix)].append(n)

    # Identify which nodes to cluster
    clustered_ids = set()
    cluster_nodes = []
    cluster_edges = []

    for (parent, prefix), members in groups.items():
        if len(members) < min_group:
            continue

        cluster_id = f'{parent}::{prefix}*:cluster'
        total_tokens = sum(m.get('tokens', 0) for m in members)

        cluster_nodes.append({
            'id': cluster_id,
            'label': f'{prefix}* ({len(members)})',
            'type': 'cluster',
            'path': parent,
            'group': members[0].get('group', ''),
            'tokens': total_tokens,
            'val': _node_size(total_tokens, False) * 1.5,
            'parent': parent,
            'members': [m['id'] for m in members],
        })

        for m in members:
            clustered_ids.add(m['id'])
            cluster_edges.append({
                'source': cluster_id,
                'target': m['id'],
                'kind': 'contains_member',
                'weight': 0.15,
            })

    # Build new node list: non-clustered + cluster nodes
    # Keep clustered members as hidden (small, dimmed) for edge routing
    new_nodes = []
    for n in nodes:
        if n['id'] in clustered_ids:
            # Keep but mark as clustered (will be hidden/tiny in rendering)
            new_nodes.append({**n, 'clustered': True, 'val': 0.3})
        else:
            new_nodes.append(n)
    new_nodes.extend(cluster_nodes)

    return new_nodes, cluster_edges
```

- [ ] **Step 4: Wire into `main()` and add cluster styling**

In `main()`, after `extract_nodes()`:
```python
# Concept clustering — collapse blob nodes like types.ts with 100+ DTOs
nodes, cluster_edges = cluster_by_prefix(nodes, min_group=3)
edges.extend(cluster_edges)
```

Add cluster node color to HTML template:
```javascript
cluster: '#F59E0B',  // amber for concept clusters
```

Add cluster styling: diamond shape via `nodeThreeObject` for cluster type nodes. Or simpler: just use a distinct color + larger size. Add to legend.

In the JS node rendering, hide `clustered: true` nodes (opacity near 0):
```javascript
.nodeOpacity(n => n.clustered ? 0.08 : 0.92)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_cluster_by_prefix -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add context-engineering/scripts/visualize_graph.py context-engineering/scripts/tests/test_visualize_graph.py
git commit -m "feat(visualize): concept clustering by naming prefix to reduce blob nodes"
```

---

## Task 7: Integration Test — Dual Graph with All Features

**Files:**
- Test: `context-engineering/scripts/tests/test_visualize_graph.py`

- [ ] **Step 1: Write integration test**

```python
def test_full_pipeline_multi_index():
    """End-to-end: merge two indexes, extract nodes, cluster, cross-link, generate HTML."""
    from visualize_graph import (
        merge_indexes, extract_nodes, find_cross_repo_links,
        cluster_by_prefix, generate_html, score_for_overlay,
    )

    # Two mini repos with shared DTOs
    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 1, 'totalTokens': 300,
        'files': [{
            'path': 'src/types.ts', 'tokens': 300, 'knowledge_type': 'ground_truth',
            'tree': {
                'title': 'src/types.ts', 'depth': 0, 'tokens': 300, 'totalTokens': 300,
                'text': '', 'firstSentence': '', 'firstParagraph': '',
                'children': [
                    {'title': 'type VoyageReport', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                    {'title': 'type VoyageDetail', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                    {'title': 'type VoyageStatus', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                ],
            },
        }],
        'directories': ['src'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 1, 'totalTokens': 200,
        'files': [{
            'path': 'src/dto.ts', 'tokens': 200, 'knowledge_type': 'ground_truth',
            'tree': {
                'title': 'src/dto.ts', 'depth': 0, 'tokens': 200, 'totalTokens': 200,
                'text': '', 'firstSentence': '', 'firstParagraph': '',
                'children': [
                    {'title': 'type VoyageReport', 'depth': 1, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                ],
            },
        }],
        'directories': ['src'],
    }

    # Merge
    merged = merge_indexes([idx_a, idx_b])
    assert merged['totalFiles'] == 2

    # Extract nodes
    nodes, file_ids = extract_nodes(merged, include_symbols=True)
    assert len(nodes) >= 6  # 2 files + 4 symbols

    # Cross-repo links
    cross_links = find_cross_repo_links(nodes)
    assert len(cross_links) >= 1  # VoyageReport shared

    # Clustering (Voyage* should cluster in fleet)
    nodes, cluster_edges = cluster_by_prefix(nodes, min_group=3)
    cluster_ids = [n['id'] for n in nodes if n.get('type') == 'cluster']
    assert len(cluster_ids) >= 1

    # Score overlay
    scores = score_for_overlay(merged, 'VoyageReport')
    assert len(scores) >= 1

    # Generate HTML
    all_edges = cross_links + cluster_edges
    html = generate_html(nodes, all_edges, 'Dual Graph', query='VoyageReport', relevance_scores=scores)
    assert 'VoyageReport' in html
    assert len(html) > 1000
```

- [ ] **Step 2: Run integration test**

Run: `python -m pytest context-engineering/scripts/tests/test_visualize_graph.py::test_full_pipeline_multi_index -v`
Expected: PASS

- [ ] **Step 3: Manual verification with real indexes**

```bash
# Dual graph: fleet + efficientship-backend
python context-engineering/scripts/visualize_graph.py \
  --multi-index context-engineering/cache/fleet-index.json context-engineering/cache/backend-index.json \
  --top 100 --query "VoyageReport" \
  -o context-engineering/cache/dual-graph.html \
  --title "Fleet + Backend"
```

Open `dual-graph.html` in browser. Verify:
1. Click a node -> connected nodes highlight, others dim
2. Search bar filters by relevance (VoyageReport nodes glow)
3. Voyage* cluster nodes visible in fleet repo
4. Amber cross-repo links between fleet and backend shared types
5. Types.ts is no longer a single blob — DTOs are grouped by prefix

- [ ] **Step 4: Commit**

```bash
git add context-engineering/scripts/tests/test_visualize_graph.py
git commit -m "test(visualize): integration test for dual graph pipeline"
```

---

## Task 8: Update SKILL.md and Legend

**Files:**
- Modify: `context-engineering/SKILL.md` (document new flags)
- Modify: `context-engineering/scripts/visualize_graph.py` (update legend for new node/edge types)

- [ ] **Step 1: Update HTML legend**

Add to the legend in the HTML template:
- Node: `cluster` (amber) — "concept cluster"
- Edge: `shared_type` (amber) — "cross-repo match"
- Edge: `contains_member` (light gray) — "cluster member"

- [ ] **Step 2: Update SKILL.md Graph visualization section**

Add new flags documentation:
```markdown
### Graph visualization

```bash
# 3D force-directed graph — opens in any browser
python3 scripts/visualize_graph.py --top 50

# With query overlay — shows how the packer "sees" the codebase
python3 scripts/visualize_graph.py --top 80 --query "authentication"

# Dual graph — two repos with cross-repo DTO linking
python3 scripts/visualize_graph.py --multi-index cache/fleet-index.json cache/backend-index.json --top 100

# File-level only (no symbols), custom output path
python3 scripts/visualize_graph.py --no-symbols -o my-graph.html
```
```

- [ ] **Step 3: Commit**

```bash
git add context-engineering/SKILL.md context-engineering/scripts/visualize_graph.py
git commit -m "docs(visualize): document --query and --multi-index flags in SKILL.md"
```

---

## Summary of Changes

| Task | Feature | Commits |
|------|---------|---------|
| 1 | Click-highlight connected nodes | 1 |
| 2-3 | Query overlay (scoring bridge + HTML) | 2 |
| 4 | Multi-index merge | 1 |
| 5 | Cross-repo DTO linking | 1 |
| 6 | Concept clustering | 1 |
| 7 | Integration test | 1 |
| 8 | Docs + legend update | 1 |

**Total: 8 tasks, 8 commits, 3 files modified (`visualize_graph.py`, `SKILL.md`, `test_visualize_graph.py`).**
