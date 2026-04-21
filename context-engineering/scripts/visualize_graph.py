"""
Graph Visualizer — Interactive 3D force-directed graph of codebase structure.

Reads a workspace index (symbols from code, headings from docs) and builds
a 3D graph visualization. Optionally uses graphify for richer edges.

Output: standalone HTML file using 3d-force-graph (Three.js).

Usage:
  python3 visualize_graph.py                                    # uses cache/workspace-index.json
  python3 visualize_graph.py --index path/to/index.json         # custom index
  python3 visualize_graph.py --graphify path/to/graph.json      # richer edges from graphify
  python3 visualize_graph.py --top 50                           # limit to top 50 files
  python3 visualize_graph.py --no-symbols                       # file-level nodes only
  python3 visualize_graph.py -o my-graph.html                   # custom output path
"""

import json
import math
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from code_graph import build_graph, build_graph_with_fallback
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


def merge_indexes(indexes: list) -> dict:
    """Merge multiple workspace indexes into one, prefixing paths with repo name."""
    merged_files = []
    total_tokens = 0
    dirs = set()

    for idx in indexes:
        root = idx.get('root', 'unknown')
        repo_name = Path(root).name
        for f in idx.get('files', []):
            new_path = f'{repo_name}/{f["path"]}'.replace('\\', '/')
            new_file = {**f, 'path': new_path, 'repo': repo_name}
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


def find_cross_repo_links(nodes: list) -> list:
    """Find symbol nodes with the same label across different repos.

    Creates 'shared_type' edges between matching DTOs/types/classes
    in different repositories.
    """
    by_label = defaultdict(list)
    for n in nodes:
        if n.get('type') in ('type', 'class', 'interface', 'enum'):
            by_label[n['label']].append(n)

    links = []
    for label, group in by_label.items():
        if len(group) < 2:
            continue
        repos = set()
        for n in group:
            parts = n['path'].split('/')
            if len(parts) >= 2:
                repos.add(parts[0])
        if len(repos) < 2:
            continue

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


CODE_EXTENSIONS = {
    '.ts', '.tsx', '.js', '.jsx', '.py', '.go', '.rs', '.rb',
    '.java', '.c', '.cpp', '.cs', '.kt', '.scala', '.php',
}
DOC_EXTENSIONS = {'.md', '.mdx', '.rst', '.txt'}


def _node_size(tokens, is_file):
    """Logarithmic scaling for node size."""
    if is_file:
        return max(3, min(12, 2 + math.log1p(tokens) / 1.8))
    return max(1, min(6, 1 + math.log1p(tokens) / 2.5))


def _classify_symbol(title):
    """Classify a symbol node from its title (as stored by index_workspace)."""
    t = title.strip()
    tl = t.lower()
    for prefix in ('class ', 'abstract class '):
        if tl.startswith(prefix):
            return 'class', t[len(prefix):].split('(')[0].split('{')[0].strip()
    if tl.startswith('interface '):
        return 'interface', t[len('interface '):].split('{')[0].strip()
    if tl.startswith('type '):
        return 'type', t[len('type '):].split('=')[0].strip()
    if tl.startswith('enum '):
        return 'class', t[len('enum '):].strip()
    # Default: function/method/const
    return 'function', t.split('(')[0].strip()


def extract_nodes(index, top=None, include_symbols=True, graph_edges=None):
    """Extract graph nodes from workspace index.

    Code files  → function/class/interface nodes (from AST children)
    Doc files   → concept nodes (from heading children)
    Other files → file-level nodes

    If top is set and graph_edges is provided, picks the most-connected files
    rather than the largest by tokens.
    """
    nodes = []
    file_ids = set()

    files = index.get('files', [])

    if top and len(files) > top:
        if graph_edges:
            # Rank files by number of graph connections
            conn_count = defaultdict(int)
            for e in graph_edges:
                conn_count[e['source'].replace('\\', '/')] += 1
                conn_count[e['target'].replace('\\', '/')] += 1
            path_set = {f['path'].replace('\\', '/') for f in files}
            # Sort by connections (desc), break ties by token count
            files = sorted(
                files,
                key=lambda f: (conn_count.get(f['path'].replace('\\', '/'), 0),
                               f.get('tokens', 0)),
                reverse=True,
            )[:top]
        else:
            files = sorted(files, key=lambda f: f.get('tokens', 0), reverse=True)[:top]

    for f in files:
        path = f['path'].replace('\\', '/')
        tree = f.get('tree', {})
        children = tree.get('children', [])
        ext = Path(path).suffix.lower()
        is_code = ext in CODE_EXTENSIONS
        is_doc = ext in DOC_EXTENSIONS
        tokens = f.get('tokens', 0)

        # File node
        file_ids.add(path)
        nodes.append({
            'id': path,
            'label': Path(path).name,
            'type': 'file',
            'path': path,
            'group': str(PurePosixPath(path).parent) if '/' in path else '',
            'tokens': tokens,
            'val': _node_size(tokens, True),
        })

        if not include_symbols or not children:
            continue

        for child in children:
            title = child.get('title', '')
            if not title:
                continue

            node_id = f"{path}::{title}"
            child_tokens = child.get('tokens', child.get('totalTokens', 10))

            if is_code:
                kind, label = _classify_symbol(title)
            elif is_doc:
                kind, label = 'concept', title
            else:
                kind, label = 'other', title

            nodes.append({
                'id': node_id,
                'label': label,
                'type': kind,
                'path': path,
                'group': str(PurePosixPath(path).parent) if '/' in path else '',
                'tokens': child_tokens,
                'val': _node_size(child_tokens, False),
                'parent': path,
            })

    return nodes, file_ids


# We need PurePosixPath for consistent forward-slash paths
from pathlib import PurePosixPath


# ── HTML Template ──

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
<script src="https://unpkg.com/3d-force-graph@1"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Roboto', sans-serif;
  background: #FAFBFC;
  color: #1E293B;
  overflow: hidden;
}
#graph { width: 100vw; height: 100vh; }
#graph canvas { outline: none; }

#stats {
  position: fixed; top: 16px; left: 16px;
  background: white; border: 1px solid #E2E8F0; border-radius: 10px;
  padding: 14px 18px; font-size: 13px; color: #64748B;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  z-index: 10; max-width: 320px;
}
#stats h1 {
  font-size: 16px; font-weight: 600; color: #0F172A;
  margin-bottom: 6px; letter-spacing: -0.3px;
}
.stat { display: inline-block; margin-right: 14px; }
.stat b { color: #2563EB; font-weight: 500; }

#legend {
  position: fixed; bottom: 16px; left: 16px;
  background: white; border: 1px solid #E2E8F0; border-radius: 10px;
  padding: 14px 18px; font-size: 12px; color: #475569;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  z-index: 10;
}
#legend h3 {
  font-size: 10px; font-weight: 600; color: #94A3B8;
  text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 6px;
}
#legend h3:not(:first-child) { margin-top: 10px; }
.legend-item { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.legend-line { width: 18px; height: 2.5px; border-radius: 2px; flex-shrink: 0; }

#detail {
  position: fixed; top: 16px; right: 16px;
  background: white; border: 1px solid #E2E8F0; border-radius: 10px;
  padding: 18px; font-size: 13px; width: 300px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  z-index: 10; display: none;
}
#detail h2 {
  font-size: 15px; font-weight: 600; color: #0F172A;
  margin-bottom: 6px; word-break: break-word; padding-right: 20px;
}
#detail .meta { color: #64748B; margin-bottom: 3px; font-size: 12px; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 5px;
  font-size: 11px; font-weight: 500; color: white;
}
#detail .connections { margin-top: 12px; }
#detail .connections h3 {
  font-size: 10px; font-weight: 600; color: #94A3B8;
  text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 6px;
}
.conn {
  color: #475569; margin-bottom: 3px; font-size: 12px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.conn .kind { color: #94A3B8; font-size: 11px; }
#detail .close {
  position: absolute; top: 10px; right: 14px;
  cursor: pointer; color: #94A3B8; font-size: 20px; line-height: 1;
}
#detail .close:hover { color: #475569; }

#controls {
  position: fixed; bottom: 16px; right: 16px;
  background: white; border: 1px solid #E2E8F0; border-radius: 10px;
  padding: 10px 14px; font-size: 11px; color: #94A3B8;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  z-index: 10;
}
kbd {
  display: inline-block; padding: 1px 5px; border: 1px solid #CBD5E1;
  border-radius: 3px; font-size: 10px; font-family: inherit; color: #64748B;
  background: #F8FAFC;
}
#search {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  background: white; border: 1px solid #E2E8F0; border-radius: 10px;
  padding: 8px 14px; z-index: 10;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  display: flex; align-items: center; gap: 8px;
}
#search-input {
  border: none; outline: none; font-family: 'Roboto', sans-serif;
  font-size: 13px; color: #1E293B; width: 200px; background: transparent;
}
#search-input::placeholder { color: #94A3B8; }
#search-info { font-size: 11px; color: #64748B; white-space: nowrap; }
</style>
</head>
<body>
<div id="graph"></div>

<div id="stats">
  <h1>{{TITLE}}</h1>
  <span class="stat"><b>{{NODE_COUNT}}</b> nodes</span>
  <span class="stat"><b>{{EDGE_COUNT}}</b> edges</span>
  <span class="stat"><b>{{FILE_COUNT}}</b> files</span>
  <span class="stat"><b>{{SYMBOL_COUNT}}</b> symbols</span>
</div>

<div id="search">
  <input id="search-input" type="text" placeholder="Search query...">
  <span id="search-info"></span>
</div>

<div id="legend">
  <h3>Nodes</h3>
  <div class="legend-item"><div class="legend-dot" style="background:#2563EB"></div> function</div>
  <div class="legend-item"><div class="legend-dot" style="background:#7C3AED"></div> class / interface</div>
  <div class="legend-item"><div class="legend-dot" style="background:#0D9488"></div> concept</div>
  <div class="legend-item"><div class="legend-dot" style="background:#94A3B8"></div> file</div>
  <h3>Edges</h3>
  <div class="legend-item"><div class="legend-line" style="background:#2563EB"></div> imports</div>
  <div class="legend-item"><div class="legend-line" style="background:#0EA5E9"></div> calls</div>
  <div class="legend-item"><div class="legend-line" style="background:#7C3AED"></div> extends</div>
  <div class="legend-item"><div class="legend-line" style="background:#10B981"></div> tests</div>
  <div class="legend-item"><div class="legend-line" style="background:#F59E0B"></div> documents</div>
  <div class="legend-item"><div class="legend-line" style="background:#F59E0B"></div> shared type</div>
</div>

<div id="detail">
  <span class="close" onclick="this.parentElement.style.display='none'">&times;</span>
  <h2 id="d-name"></h2>
  <div class="meta" id="d-path"></div>
  <div class="meta">
    <span class="badge" id="d-type"></span>
    <span id="d-tokens" style="margin-left:6px;color:#94A3B8;font-size:11px"></span>
  </div>
  <div class="connections" id="d-conns"></div>
</div>

<div id="controls">
  <kbd>click</kbd> inspect &nbsp; <kbd>drag</kbd> orbit &nbsp; <kbd>scroll</kbd> zoom
</div>

<script>
const graphData = {{GRAPH_DATA}};

const nodeColors = {
  function: '#2563EB', method: '#3B82F6',
  class: '#7C3AED', interface: '#7C3AED', type: '#6366F1',
  concept: '#0D9488',
  file: '#94A3B8', other: '#6B7280',
};

const edgeColors = {
  imports: '#2563EB', calls: '#0EA5E9',
  extends: '#7C3AED', implements: '#7C3AED', uses_type: '#6366F1',
  tests: '#10B981', tested_by: '#10B981',
  documents: '#F59E0B', links_to: '#94A3B8', references: '#94A3B8',
  contains: '#D1D5DB', related: '#CBD5E1',
  configured_by: '#94A3B8', depends_on: '#94A3B8',
  co_located: '#E2E8F0', defined_in: '#CBD5E1',
  shared_type: '#F59E0B',
};

// Pre-build adjacency for detail panel
const adj = {};
graphData.links.forEach(l => {
  const s = typeof l.source === 'object' ? l.source.id : l.source;
  const t = typeof l.target === 'object' ? l.target.id : l.target;
  if (!adj[s]) adj[s] = [];
  if (!adj[t]) adj[t] = [];
  adj[s].push({ node: t, kind: l.kind, dir: 'out' });
  adj[t].push({ node: s, kind: l.kind, dir: 'in' });
});

let highlightNodes = new Set();
let highlightLinks = new Set();
let selectedNode = null;

function selectNode(node) {
  highlightNodes.clear();
  highlightLinks.clear();
  selectedNode = node;
  if (node) {
    highlightNodes.add(node);
    graphData.links.forEach(link => {
      const s = typeof link.source === 'object' ? link.source : graphData.nodes.find(n => n.id === link.source);
      const t = typeof link.target === 'object' ? link.target : graphData.nodes.find(n => n.id === link.target);
      if (s && s.id === node.id) { highlightNodes.add(t); highlightLinks.add(link); }
      if (t && t.id === node.id) { highlightNodes.add(s); highlightLinks.add(link); }
    });
  }
}

const Graph = ForceGraph3D()
  (document.getElementById('graph'))
  .backgroundColor('#FAFBFC')
  .graphData(graphData)
  .nodeLabel(n => n.type === 'file'
    ? `<b>${n.label}</b> <span style="color:#94A3B8">(${n.tokens} tok)</span>`
    : `<b>${n.label}</b> <span style="color:#94A3B8">${n.type}</span>`)
  .nodeColor(n => {
    if (selectedNode && !highlightNodes.has(n)) return '#E2E8F0';
    return nodeColors[n.type] || '#94A3B8';
  })
  .nodeVal(n => n.val || 2)
  .nodeOpacity(0.92)
  .nodeResolution(12)
  .linkColor(l => {
    if (selectedNode && !highlightLinks.has(l)) return 'rgba(203,213,225,0.15)';
    if (selectedNode && highlightLinks.has(l)) return edgeColors[l.kind] || '#2563EB';
    return edgeColors[l.kind] || '#CBD5E1';
  })
  .linkWidth(l => {
    if (selectedNode && highlightLinks.has(l)) return Math.max(1.5, (l.weight || 0.3) * 4);
    return l.kind === 'contains' ? 0.2 : Math.max(0.4, (l.weight || 0.3) * 2.5);
  })
  .linkOpacity(l => {
    if (selectedNode && !highlightLinks.has(l)) return 0.05;
    return l.kind === 'contains' ? 0.12 : 0.35;
  })
  .linkDirectionalArrowLength(l => l.kind === 'contains' ? 0 : 3.5)
  .linkDirectionalArrowRelPos(1)
  .linkDirectionalArrowColor(l => edgeColors[l.kind] || '#CBD5E1')
  .d3AlphaDecay(0.015)
  .d3VelocityDecay(0.35)
  .warmupTicks(80)
  .onNodeClick(node => {
    selectNode(node);
    Graph.nodeColor(Graph.nodeColor()).linkColor(Graph.linkColor()).linkWidth(Graph.linkWidth()).linkOpacity(Graph.linkOpacity());
    const detail = document.getElementById('detail');
    document.getElementById('d-name').textContent = node.label;
    document.getElementById('d-path').textContent = node.path;
    const badge = document.getElementById('d-type');
    badge.textContent = node.type;
    badge.style.background = nodeColors[node.type] || '#94A3B8';
    document.getElementById('d-tokens').textContent = node.tokens + ' tokens';

    // Connections (skip containment, limit to 20)
    const conns = (adj[node.id] || []).filter(c => c.kind !== 'contains');
    let html = '';
    if (conns.length) {
      html = '<h3>Connections (' + conns.length + ')</h3>';
      conns.slice(0, 20).forEach(c => {
        const name = c.node.split('/').pop().split('::').pop();
        const arrow = c.dir === 'out' ? '&rarr;' : '&larr;';
        html += '<div class="conn"><span class="kind">' + c.kind + '</span> '
              + arrow + ' ' + name + '</div>';
      });
      if (conns.length > 20) html += '<div class="conn" style="color:#94A3B8">... and ' + (conns.length - 20) + ' more</div>';
    }
    document.getElementById('d-conns').innerHTML = html;
    detail.style.display = 'block';

    // Fly to node
    const dist = 100;
    const r = 1 + dist / Math.hypot(node.x, node.y, node.z);
    Graph.cameraPosition(
      { x: node.x * r, y: node.y * r, z: node.z * r },
      node, 800
    );
  })
  .onBackgroundClick(() => {
    selectNode(null);
    Graph.nodeColor(Graph.nodeColor()).linkColor(Graph.linkColor()).linkWidth(Graph.linkWidth()).linkOpacity(Graph.linkOpacity());
    document.getElementById('detail').style.display = 'none';
  });

// Customize forces
Graph.d3Force('link').distance(link =>
  link.kind === 'contains' ? 12 : 50 + (1 - (link.weight || 0.3)) * 50
);
Graph.d3Force('charge').strength(n =>
  n.type === 'file' ? -120 : -40
);

// ── Query Overlay ──
const relevanceScores = {{RELEVANCE_SCORES}};
const initialQuery = {{QUERY}};

function applyRelevanceOverlay(scores) {
  if (!scores || Object.keys(scores).length === 0) {
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

if (initialQuery) {
  document.getElementById('search-input').value = initialQuery;
  applyRelevanceOverlay(relevanceScores);
}

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
</script>
</body>
</html>"""


def generate_html(nodes, edges, title, query=None, relevance_scores=None):
    """Fill in the HTML template with graph data."""
    graph_data = {'nodes': nodes, 'links': edges}

    file_count = sum(1 for n in nodes if n['type'] == 'file')
    symbol_count = len(nodes) - file_count

    html = HTML_TEMPLATE
    html = html.replace('{{TITLE}}', title)
    html = html.replace('{{NODE_COUNT}}', str(len(nodes)))
    html = html.replace('{{EDGE_COUNT}}', str(len(edges)))
    html = html.replace('{{FILE_COUNT}}', str(file_count))
    html = html.replace('{{SYMBOL_COUNT}}', str(symbol_count))
    html = html.replace('{{GRAPH_DATA}}', json.dumps(graph_data, ensure_ascii=False))
    html = html.replace('{{QUERY}}', json.dumps(query or ''))
    html = html.replace('{{RELEVANCE_SCORES}}', json.dumps(relevance_scores or {}, ensure_ascii=False))

    return html


def main():
    parser = argparse.ArgumentParser(
        description='3D graph visualizer for context-engineering')
    parser.add_argument('--index', default=None,
                        help='Path to workspace-index.json')
    parser.add_argument('--graphify', default=None,
                        help='Path to graphify graph.json')
    parser.add_argument('--top', type=int, default=None,
                        help='Limit to top N files by token count')
    parser.add_argument('--no-symbols', action='store_true',
                        help='File-level nodes only (no symbols/headings)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output HTML path')
    parser.add_argument('--title', default='Context Engineering Graph',
                        help='Graph title')
    parser.add_argument('--query', default=None,
                        help='Overlay relevance scores for a query')
    parser.add_argument('--multi-index', nargs='+', default=None,
                        help='Multiple workspace-index.json paths to merge')
    args = parser.parse_args()

    # Load index (single or multi)
    script_dir = Path(__file__).resolve().parent.parent
    if args.multi_index:
        indexes = []
        for path in args.multi_index:
            print(f'Reading index: {path}', file=sys.stderr)
            with open(path, encoding='utf-8') as f:
                indexes.append(json.load(f))
        index = merge_indexes(indexes)
        print(f'Merged {len(indexes)} indexes: {index["totalFiles"]} files', file=sys.stderr)
    else:
        index_path = args.index or str(script_dir / 'cache' / 'workspace-index.json')
        print(f'Reading index: {index_path}', file=sys.stderr)
        with open(index_path, encoding='utf-8') as f:
            index = json.load(f)

    # Query overlay scoring
    relevance_scores = {}
    if args.query:
        relevance_scores = score_for_overlay(index, args.query)
        print(f'Query "{args.query}": {len(relevance_scores)} matches', file=sys.stderr)

    # Auto-detect graphify
    graphify_path = args.graphify
    if not graphify_path:
        root = index.get('root', '.')
        candidate = Path(root) / 'graphify-out' / 'graph.json'
        if candidate.exists():
            graphify_path = str(candidate)
            print(f'Auto-detected graphify: {graphify_path}', file=sys.stderr)

    # Build graph first so we can rank files by connectivity
    graph = build_graph_with_fallback(index['files'], graphify_path)
    all_graph_edges = graph.get('edges', [])

    # Extract nodes (uses graph edges to pick most-connected files when --top)
    nodes, file_ids = extract_nodes(
        index, top=args.top, include_symbols=not args.no_symbols,
        graph_edges=all_graph_edges)

    if not nodes:
        print('No files found in index.', file=sys.stderr)
        sys.exit(1)

    # Build edges (filter to visible nodes + add containment)
    node_id_set = {n['id'] for n in nodes}
    edges = []
    for edge in all_graph_edges:
        source = edge['source'].replace('\\', '/')
        target = edge['target'].replace('\\', '/')
        if source in node_id_set and target in node_id_set:
            edges.append({
                'source': source,
                'target': target,
                'kind': edge.get('kind', 'related'),
                'weight': edge.get('weight', 0.3),
            })
    # Containment edges (symbol → parent file)
    for node in nodes:
        parent = node.get('parent')
        if parent and parent in node_id_set:
            edges.append({
                'source': node['id'],
                'target': parent,
                'kind': 'contains',
                'weight': 0.1,
            })

    # Cross-repo DTO linking
    if args.multi_index:
        cross_links = find_cross_repo_links(nodes)
        edges.extend(cross_links)
        if cross_links:
            print(f'{len(cross_links)} cross-repo type links', file=sys.stderr)

    # Generate HTML
    root_name = Path(index.get('root', 'graph')).name
    title = args.title if args.title != 'Context Engineering Graph' else root_name

    html = generate_html(nodes, edges, title, query=args.query, relevance_scores=relevance_scores)

    # Write output
    output_path = args.output or str(script_dir / 'cache' / 'graph.html')
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    file_count = sum(1 for n in nodes if n['type'] == 'file')
    sym_count = len(nodes) - file_count
    edge_count = sum(1 for e in edges if e['kind'] != 'contains')
    print(f'\n{file_count} files, {sym_count} symbols, {edge_count} edges', file=sys.stderr)
    print(f'Written to: {output_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
