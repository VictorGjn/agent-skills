# Graph Visualizer V2 — Feature Map & Impact Surface

## Problem

The current `visualize_graph.py` renders individual files/symbols as 3D WebGL nodes. This fails in practice:
- **Too many nodes** — a 200-file repo produces 500+ nodes (files + symbols), overwhelming the graph
- **Unreliable rendering** — 3D spheres and SpriteText fail to display in the browser at scale
- **Wrong abstraction level** — individual files don't answer the questions developers actually ask

## What developers actually ask

1. **"How is this repo structured?"** — What are the features, how do they connect, what depends on what?
2. **"If I change X, what breaks?"** — What's the blast radius of modifying a service, DTO, or endpoint?

These are two distinct tools:

## Tool 1: Bird's-Eye Feature Map

**Question:** "What are the natural features in this codebase and how do they depend on each other?"

**Input:** Workspace index (from `index_workspace.py`) + dependency graph (from `code_graph.py`)

**Algorithm:**
1. Build the import/dependency graph (already exists in `code_graph.py`)
2. Run **label propagation** community detection on the undirected weighted graph
3. Each community = a "feature cluster" — files that are tightly interconnected
4. Label each cluster by its most-connected symbols (top 3 by internal edge count)
5. Build a **meta-graph**: nodes = clusters, edges = cross-cluster dependencies (weighted by edge count)
6. Render as **2D SVG** using D3 force-directed layout

**Output:** Standalone HTML file with:
- 15-40 feature nodes (circles sized by file count, colored by dominant file type)
- Edges between features (thickness = dependency count)
- Click a feature → detail panel shows: files in cluster, top symbols, connections to other features
- Search bar to highlight features matching a query
- Works for code repos AND knowledge bases (docs get heading-based community detection)

**Why label propagation:**
- Zero external dependencies (pure Python, ~50 lines)
- O(n × k) where k is typically 5-10 iterations
- Naturally finds communities in sparse graphs
- Well-suited for import graphs where clusters form around feature directories

**Multi-repo support:**
- `--multi-index` merges indexes (reuse existing `merge_indexes()`)
- Cross-repo edges shown between feature clusters that share types
- Clusters colored/grouped by repo

**Node sizing and labeling:**
- Node size = log(file_count) in cluster
- Node label = top 2-3 symbol names (by internal connectivity)
- Tooltip shows: all files, all symbols, total tokens
- Cluster labeled by common directory prefix if >70% of files share one

### Knowledge base mode

For repos that are mostly markdown:
- Communities form around cross-referenced documents (links_to, references edges)
- Cluster labels come from heading titles instead of symbol names
- Same algorithm, different labeling heuristic

## Tool 2: Impact Surface

**Question:** "I'm about to change this function/file. Show me everything upstream and downstream."

**Input:** Workspace index + dependency graph + a starting point (file path or symbol name)

**Algorithm:**
1. Find starting node(s) matching the query in the graph
2. **BFS outward** using `traverse_from()` (already exists in `code_graph.py`) — but follow BOTH directions (outgoing imports AND incoming callers)
3. Collect all reachable nodes with distance from origin
4. Render as **2D radial layout**: starting point at center, rings outward by BFS distance

**Output:** Standalone HTML file with:
- Central node = the file/function being changed
- Ring 1 = direct dependencies and callers
- Ring 2 = transitive dependencies
- Ring 3+ = further ripple effects
- Color by repo (multi-repo: red = different repo = higher risk)
- Edge direction arrows (→ imports, ← imported by)
- Click any node → see its connections, file path, symbols
- Summary: "Changing X directly affects N files across M repos"

**Key difference from bird's-eye:** Impact surface is query-driven and shows individual files (not clusters), because when you're about to change something you need the specific file list.

## Rendering: 2D SVG with D3

Both tools use D3.js force-directed layout rendered to SVG:
- **Always renders** — SVG text is native, no WebGL issues
- **Readable text** — node labels are actual DOM text, not sprite textures
- **Lightweight** — standalone HTML, no build step, loads fast
- **Interactive** — D3 zoom/pan, click-to-inspect, hover tooltips

Arctic Maritime theme:
- Background: `#FAFBFC`
- Primary: `#2563EB` (ocean blue)
- Secondary: `#0D9488` (teal)
- Font: Roboto
- Node fill: pastel variants per cluster
- Edge: `#CBD5E1` default, colored by relation type on hover

## File Structure

```
context-engineering/scripts/
  feature_map.py          # NEW — Bird's-eye feature map (community detection + D3 2D)
  impact_surface.py       # NEW — Impact surface (BFS + D3 2D radial)
  community_detect.py     # NEW — Label propagation algorithm, shared by both tools
  code_graph.py           # EXISTING — unchanged, provides build_graph() and traverse_from()
  index_workspace.py      # EXISTING — unchanged, provides workspace index
  visualize_graph.py      # EXISTING — keep as-is (legacy 3D visualizer)
```

## CLI

```bash
# Bird's-eye: single repo
python3 scripts/feature_map.py --index cache/workspace-index.json

# Bird's-eye: multi-repo
python3 scripts/feature_map.py --multi-index cache/fleet-index.json cache/backend-index.json

# Impact surface
python3 scripts/impact_surface.py "HurricaneService" --index cache/workspace-index.json

# Impact surface: multi-repo
python3 scripts/impact_surface.py "HurricaneDto" --multi-index cache/fleet-index.json cache/backend-index.json
```

## Non-goals

- No 3D rendering — 2D SVG only
- No AST-level symbol nodes in the graph — symbols are in the detail panel
- No real-time re-indexing — use existing `index_workspace.py` then visualize
- No Graphify requirement — works with import-only graph, optionally richer with Graphify
