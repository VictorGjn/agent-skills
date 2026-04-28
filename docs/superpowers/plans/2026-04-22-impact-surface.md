# Impact Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a query-driven 2D radial visualization that shows the blast radius of changing a specific file, function, or DTO — "if I change X, what breaks?"

**Architecture:** Query → find matching nodes in import graph → BFS outward (both directions: imports AND callers) → collect all reachable nodes with distance → render as 2D radial SVG with D3. Central node = change point, rings = hop distance. Cross-repo edges highlighted as high-risk.

**Tech Stack:** Python 3.10+, D3.js v7 (CDN), SVG rendering, existing `code_graph.py` (provides `build_graph()` + `traverse_from()`), `index_workspace.py`, `community_detect.py` (from bird's-eye map)

**Spec:** `docs/superpowers/specs/2026-04-22-graph-visualizer-v2-design.md`

**Prerequisite:** Bird's-eye feature map plan must be completed first (depends on `community_detect.py`).

---

### Task 1: Bidirectional BFS for Impact Analysis

**Files:**
- Create: `context-engineering/scripts/impact_surface.py`
- Test: `context-engineering/scripts/tests/test_impact_surface.py`

The existing `traverse_from()` in `code_graph.py` supports `follow_callers=True` but doesn't distinguish direction in results. For impact visualization, we need to know: "this file is affected because it IMPORTS the changed file" vs "this file is affected because the changed file IMPORTS it."

- [ ] **Step 1: Write failing test**

```python
def test_impact_bfs_finds_upstream_and_downstream():
    """Impact BFS returns nodes with direction (upstream = callers, downstream = dependencies)."""
    from impact_surface import impact_bfs

    # A imports B imports C, D imports B (D is upstream of B)
    edges = [
        {'source': 'a.ts', 'target': 'b.ts', 'kind': 'imports', 'weight': 1.0},
        {'source': 'b.ts', 'target': 'c.ts', 'kind': 'imports', 'weight': 1.0},
        {'source': 'd.ts', 'target': 'b.ts', 'kind': 'imports', 'weight': 1.0},
    ]
    result = impact_bfs('b.ts', edges, max_depth=2)

    paths = {r['path'] for r in result}
    assert 'b.ts' in paths  # origin
    assert 'c.ts' in paths  # downstream (b imports c)
    assert 'a.ts' in paths  # upstream (a imports b)
    assert 'd.ts' in paths  # upstream (d imports b)

    # Check direction annotation
    for r in result:
        if r['path'] == 'c.ts':
            assert r['direction'] == 'downstream'
        if r['path'] in ('a.ts', 'd.ts'):
            assert r['direction'] == 'upstream'
        if r['path'] == 'b.ts':
            assert r['direction'] == 'origin'
```

- [ ] **Step 2: Implement `impact_bfs()`**

Custom BFS that tracks direction:
- From the origin, follow outgoing edges → "downstream" (things the changed file depends on)
- From the origin, follow incoming edges → "upstream" (things that depend on the changed file = things that BREAK)
- Each result node gets: `{path, distance, direction, reason, relevance}`

- [ ] **Step 3: Run test, verify passes, commit**

---

### Task 2: Query Matching — Find the Starting Point

**Files:**
- Modify: `context-engineering/scripts/impact_surface.py`
- Test: `context-engineering/scripts/tests/test_impact_surface.py`

The user queries with a name like "HurricaneService" or "hurricane/dto.ts". We need to find matching files/symbols in the index.

- [ ] **Step 1: Write failing test**

```python
def test_find_entry_points():
    """Query matches file paths and symbol names."""
    from impact_surface import find_entry_points

    index = {
        'files': [
            {'path': 'src/hurricane/service.ts', 'tree': {
                'children': [{'title': 'class HurricaneService'}]}},
            {'path': 'src/voyage/manager.ts', 'tree': {
                'children': [{'title': 'class VoyageManager'}]}},
        ]
    }
    # Match by symbol name
    results = find_entry_points('HurricaneService', index)
    assert len(results) == 1
    assert results[0]['path'] == 'src/hurricane/service.ts'

    # Match by partial path
    results = find_entry_points('hurricane/service', index)
    assert len(results) == 1
```

- [ ] **Step 2: Implement, verify passes, commit**

---

### Task 3: D3 Radial Layout Template

**Files:**
- Modify: `context-engineering/scripts/impact_surface.py` (add `generate_html()`)
- Test: `context-engineering/scripts/tests/test_impact_surface.py`

Render the impact graph as a 2D radial layout:
- Center: origin node (the file being changed)
- Ring 1: direct dependencies and callers (distance=1)
- Ring 2: transitive (distance=2)
- Ring 3+: further ripple
- Upstream nodes (callers = things that break) on the LEFT
- Downstream nodes (dependencies) on the RIGHT
- Color: by repo in multi-repo mode, by type in single-repo
- Edge arrows showing direction
- Summary banner: "Changing X directly affects N files across M repos"

- [ ] **Step 1: Write failing test**

```python
def test_generate_impact_html():
    """Impact HTML includes origin, upstream/downstream sections."""
    from impact_surface import generate_html

    impact_data = {
        'origin': 'src/hurricane/service.ts',
        'nodes': [
            {'path': 'src/hurricane/service.ts', 'distance': 0, 'direction': 'origin'},
            {'path': 'src/hurricane/controller.ts', 'distance': 1, 'direction': 'upstream'},
            {'path': 'src/hurricane/dto.ts', 'distance': 1, 'direction': 'downstream'},
        ],
        'edges': [
            {'source': 'src/hurricane/controller.ts', 'target': 'src/hurricane/service.ts'},
            {'source': 'src/hurricane/service.ts', 'target': 'src/hurricane/dto.ts'},
        ],
    }
    html = generate_html(impact_data, 'Impact: HurricaneService')

    assert '<!DOCTYPE html>' in html
    assert 'HurricaneService' in html or 'hurricane/service.ts' in html
    assert 'upstream' in html.lower() or 'callers' in html.lower()
    assert 'downstream' in html.lower() or 'dependencies' in html.lower()
```

- [ ] **Step 2: Implement D3 radial template**

Use `d3.forceRadial()` to position nodes in rings by distance. Layout:
- `d3.forceRadial(d => d.distance * 120)` — rings at 120px intervals
- `d3.forceCollide(30)` — prevent overlap
- Node circles with text labels
- Directed edges with arrow markers
- Click detail panel showing file symbols and connections

- [ ] **Step 3: Run test, verify passes, commit**

---

### Task 4: CLI + Multi-Repo Support

**Files:**
- Modify: `context-engineering/scripts/impact_surface.py` (add `main()`)
- Test: manual CLI

```python
parser.add_argument('query', help='File path or symbol name to analyze')
parser.add_argument('--index', default=None)
parser.add_argument('--multi-index', nargs='+', default=None)
parser.add_argument('--graphify', default=None)
parser.add_argument('--depth', type=int, default=3, help='Max BFS depth')
parser.add_argument('-o', '--output', default=None)
```

Multi-repo: merge indexes, run BFS, highlight cross-repo edges in red.

- [ ] **Step 1: Implement main(), run on real indexes**

```bash
# Single repo
python3 scripts/impact_surface.py "HurricaneService" --index cache/fleet-index.json

# Multi-repo
python3 scripts/impact_surface.py "HurricaneDto" --multi-index cache/fleet-index.json cache/backend-index.json
```

- [ ] **Step 2: Verify renders correctly in browser**
- [ ] **Step 3: Commit**

---

### Task 5: Integration with Feature Map

**Files:**
- Modify: `context-engineering/scripts/impact_surface.py`

Optional enhancement: overlay the feature map's community labels on the impact surface. When showing impact, annotate each node with which feature cluster it belongs to. This answers: "changing HurricaneService impacts 3 files in the Hurricane feature and 1 file in the Shared Utils feature."

- [ ] **Step 1: Add `--features` flag that loads community labels**
- [ ] **Step 2: Annotate impact nodes with feature name**
- [ ] **Step 3: Show feature names in the detail panel**
- [ ] **Step 4: Commit**

---

### Task 6: SKILL.md Documentation

**Files:**
- Modify: `context-engineering/SKILL.md`

Add impact surface documentation under Graph visualization section:

```markdown
### Impact surface (change blast radius)

```bash
# What breaks if I change HurricaneService?
python3 scripts/impact_surface.py "HurricaneService" --index cache/workspace-index.json

# Cross-repo impact
python3 scripts/impact_surface.py "HurricaneDto" --multi-index cache/fleet-index.json cache/backend-index.json

# Deeper analysis (default: 3 hops)
python3 scripts/impact_surface.py "PaymentService" --depth 5
```

Shows upstream callers (things that break) and downstream dependencies in a radial 2D layout. Cross-repo edges highlighted in red. Click any node to see its symbols and connections.
```

Add to Scripts table: `impact_surface.py` | Impact analysis: BFS from change point → D3 2D radial SVG

- [ ] **Step 1: Update SKILL.md, commit**
