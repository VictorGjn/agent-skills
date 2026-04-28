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


def extract_focused(index: dict, focus_repo: str, top=None,
                     include_symbols=True, graph_edges=None,
                     max_symbols=None) -> tuple:
    """Extract nodes in focus mode: expand one repo, collapse others into bubbles.

    The focused repo gets full file + symbol extraction (same as extract_nodes).
    Every other repo becomes a single large "repo" node. Cross-repo edges are
    re-routed to point at the repo bubble instead of individual files.

    Returns (nodes, file_ids, repo_bubbles) where repo_bubbles maps
    repo_name -> bubble_node_id.
    """
    files = index.get('files', [])
    focus_files = [f for f in files if f.get('repo') == focus_repo]
    other_repos = defaultdict(list)
    for f in files:
        repo = f.get('repo', '')
        if repo and repo != focus_repo:
            other_repos[repo].append(f)

    # Extract focus repo nodes using existing logic
    focus_index = {**index, 'files': focus_files}
    nodes, file_ids = extract_nodes(
        focus_index, top=top, include_symbols=include_symbols,
        graph_edges=graph_edges, max_symbols=max_symbols)

    # Create repo bubble nodes for non-focus repos
    repo_bubbles = {}
    for repo, repo_files in other_repos.items():
        total_tokens = sum(f.get('tokens', 0) for f in repo_files)
        bubble_id = f'__repo__/{repo}'
        repo_bubbles[repo] = bubble_id

        # Count types for the label
        type_count = 0
        for f in repo_files:
            tree = f.get('tree', {})
            type_count += len(tree.get('children', []))

        nodes.append({
            'id': bubble_id,
            'label': repo,
            'type': 'repo',
            'path': repo,
            'group': repo,
            'tokens': total_tokens,
            'val': max(8, min(25, 4 + math.log1p(total_tokens) / 1.5)),
            'fileCount': len(repo_files),
            'symbolCount': type_count,
        })
        file_ids.add(bubble_id)

    return nodes, file_ids, repo_bubbles


def find_cross_repo_links_focused(nodes: list, index: dict,
                                   focus_repo: str, repo_bubbles: dict) -> list:
    """Find shared types between the focus repo and collapsed repo bubbles.

    Scans ALL files in non-focus repos (not just visible nodes) for type names
    that match types in the focus repo. Links point to the repo bubble node.
    """
    # Collect type labels from focus repo nodes
    focus_types = {}
    for n in nodes:
        if n.get('type') in ('type', 'class', 'interface', 'enum'):
            path_parts = n.get('path', '').split('/')
            if len(path_parts) >= 1 and path_parts[0] == focus_repo:
                focus_types.setdefault(n['label'], []).append(n['id'])

    if not focus_types:
        return []

    # Scan all files in other repos for matching type names
    links = []
    seen_pairs = set()
    for f in index.get('files', []):
        repo = f.get('repo', '')
        if repo == focus_repo or repo not in repo_bubbles:
            continue
        tree = f.get('tree', {})
        for child in tree.get('children', []):
            title = child.get('title', '')
            # Extract label same way as _classify_symbol
            tl = title.strip().lower()
            label = title.strip()
            for pfx in ('class ', 'abstract class ', 'interface ', 'type ', 'enum '):
                if tl.startswith(pfx):
                    label = title.strip()[len(pfx):].split('(')[0].split('{')[0].split('=')[0].strip()
                    break

            if label in focus_types:
                bubble_id = repo_bubbles[repo]
                for focus_id in focus_types[label]:
                    pair = (focus_id, bubble_id, label)
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        links.append({
                            'source': focus_id,
                            'target': bubble_id,
                            'kind': 'shared_type',
                            'weight': 0.8,
                            'label': label,
                        })

    return links


def cluster_by_prefix(nodes: list, min_group: int = 3) -> tuple:
    """Group symbol nodes by CamelCase prefix to reduce visual clutter.

    E.g., VoyageReport, VoyageDetail, VoyageStatus -> "Voyage* (3)" cluster node.
    Only clusters type/class/interface symbols with min_group or more members.

    Returns (new_nodes, cluster_edges).
    """
    import re

    def get_prefix(label):
        m = re.match(r'^([A-Z][a-z]+)', label)
        return m.group(1) if m else None

    clusterable_types = {'type', 'class', 'interface', 'enum'}
    groups = defaultdict(list)
    for n in nodes:
        if n.get('type') in clusterable_types and n.get('parent'):
            prefix = get_prefix(n['label'])
            if prefix and len(prefix) >= 3:
                groups[(n.get('parent', ''), prefix)].append(n)

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
    # Keep clustered members (marked hidden) for edge routing
    new_nodes = []
    for n in nodes:
        if n['id'] in clustered_ids:
            new_nodes.append({**n, 'clustered': True, 'val': 0.3})
        else:
            new_nodes.append(n)
    new_nodes.extend(cluster_nodes)

    return new_nodes, cluster_edges


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


def extract_nodes(index, top=None, include_symbols=True, graph_edges=None,
                  max_symbols=None):
    """Extract graph nodes from workspace index.

    Code files  → function/class/interface nodes (from AST children)
    Doc files   → concept nodes (from heading children)
    Other files → file-level nodes

    If top is set and graph_edges is provided, picks the most-connected files
    rather than the largest by tokens. If max_symbols is set, each file emits
    at most that many symbol nodes (largest by token count).
    """
    nodes = []
    file_ids = set()

    files = index.get('files', [])

    if top and len(files) > top:
        # Check if multi-repo (files have 'repo' field from merge_indexes)
        repos = set(f.get('repo', '') for f in files)
        repos.discard('')
        is_multi = len(repos) > 1

        if graph_edges:
            conn_count = defaultdict(int)
            for e in graph_edges:
                conn_count[e['source'].replace('\\', '/')] += 1
                conn_count[e['target'].replace('\\', '/')] += 1
            sort_key = lambda f: (conn_count.get(f['path'].replace('\\', '/'), 0), f.get('tokens', 0))
        else:
            sort_key = lambda f: (f.get('tokens', 0), 0)

        if is_multi:
            # Proportional selection: each repo gets quota based on file count
            by_repo = defaultdict(list)
            for f in files:
                by_repo[f.get('repo', 'unknown')].append(f)
            selected = []
            for repo in repos:
                repo_files = by_repo[repo]
                quota = max(1, round(top * len(repo_files) / len(files)))
                repo_sorted = sorted(repo_files, key=sort_key, reverse=True)
                selected.extend(repo_sorted[:quota])
            # Trim to exact top if rounding gave us extra
            files = sorted(selected, key=sort_key, reverse=True)[:top]
        else:
            files = sorted(files, key=sort_key, reverse=True)[:top]

    for f in files:
        path = f['path'].replace('\\', '/')
        tree = f.get('tree', {})
        children = tree.get('children', [])
        ext = Path(path).suffix.lower()
        is_code = ext in CODE_EXTENSIONS
        is_doc = ext in DOC_EXTENSIONS
        tokens = f.get('tokens', 0)

        # File node — include symbol names for tooltip
        file_ids.add(path)
        sym_names = []
        for child in children:
            t = child.get('title', '')
            if t:
                sym_names.append(t)
        file_node = {
            'id': path,
            'label': Path(path).name,
            'type': 'file',
            'path': path,
            'group': str(PurePosixPath(path).parent) if '/' in path else '',
            'tokens': tokens,
            'val': _node_size(tokens, True),
        }
        if sym_names:
            file_node['symbols'] = sym_names
        nodes.append(file_node)

        if not include_symbols or not children:
            continue

        # Optionally cap to top-N symbols per file by token count
        symbol_children = children
        if max_symbols is not None and len(children) > max_symbols:
            symbol_children = sorted(
                children,
                key=lambda c: c.get('tokens', c.get('totalTokens', 0)),
                reverse=True,
            )[:max_symbols]

        for child in symbol_children:
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
<script src="https://unpkg.com/three@0.175.0/build/three.min.js"></script>
<script src="https://unpkg.com/three@0.175.0/examples/jsm/renderers/CSS2DRenderer.js" type="module"></script>
<script src="https://unpkg.com/3d-force-graph@1"></script>
<script src="https://unpkg.com/three-spritetext@1"></script>
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
  <div class="legend-item"><div class="legend-dot" style="background:#F59E0B"></div> cluster</div>
  <div class="legend-item"><div class="legend-dot" style="background:#1E293B"></div> repo</div>
  <div class="legend-item"><div class="legend-dot" style="background:#94A3B8"></div> file</div>
  <h3>Edges</h3>
  <div class="legend-item"><div class="legend-line" style="background:#2563EB"></div> imports</div>
  <div class="legend-item"><div class="legend-line" style="background:#0EA5E9"></div> calls</div>
  <div class="legend-item"><div class="legend-line" style="background:#7C3AED"></div> extends</div>
  <div class="legend-item"><div class="legend-line" style="background:#10B981"></div> tests</div>
  <div class="legend-item"><div class="legend-line" style="background:#F59E0B"></div> documents</div>
  <div class="legend-item"><div class="legend-line" style="background:#EF4444"></div> shared type</div>
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
  cluster: '#F59E0B',
  repo: '#1E293B',
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
  shared_type: '#EF4444',
};

// Escape user-controlled strings before they enter innerHTML or HTML
// template literals. Symbol/heading/path text comes from indexed repos and
// must not execute as markup when rendered into the detail panel or tooltip.
function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

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

function refreshSprites() {
  // Re-render node sprites with updated highlight state
  Graph.nodeThreeObject(Graph.nodeThreeObject());
}

const Graph = ForceGraph3D()
  (document.getElementById('graph'))
  .backgroundColor('#FAFBFC')
  .graphData(graphData)
  .nodeLabel(n => {
    if (n.type === 'repo') return `<b>${escapeHtml(n.label)}</b><br/>${n.fileCount} files, ${n.symbolCount} types`;
    let tip = `<b>${escapeHtml(n.label)}</b>`;
    if (n.tokens) tip += ` (${n.tokens} tok)`;
    if (n.type !== 'file') tip += ` <span style="color:#94A3B8">${escapeHtml(n.type)}</span>`;
    // Show symbol list in tooltip for files
    if (n.type === 'file' && n.symbols && n.symbols.length) {
      tip += '<br/><span style="color:#94A3B8">';
      n.symbols.slice(0, 12).forEach(s => { tip += escapeHtml(s) + '<br/>'; });
      if (n.symbols.length > 12) tip += '... +' + (n.symbols.length - 12) + ' more';
      tip += '</span>';
    }
    return tip;
  })
  .nodeThreeObject(n => {
    const sprite = new SpriteText(n.label);
    let color = nodeColors[n.type] || '#94A3B8';
    // Highlight state
    if (selectedNode && !highlightNodes.has(n)) color = '#CBD5E1';
    // Query overlay
    if (activeOverlay) {
      const filePath = n.parent || n.id;
      const rel = activeOverlay.scores[filePath] || 0;
      if (rel > 0) {
        const t = rel / activeOverlay.maxRel;
        color = t > 0.6 ? '#2563EB' : t > 0.3 ? '#0D9488' : '#38BDF8';
      } else {
        color = '#CBD5E1';
      }
    }
    sprite.color = color;
    sprite.backgroundColor = n.type === 'repo' ? 'rgba(30,41,59,0.9)' :
                              n.type === 'cluster' ? 'rgba(245,158,11,0.12)' : false;
    sprite.borderRadius = 3;
    sprite.padding = n.type === 'repo' ? [3, 6] : [1, 3];
    sprite.textHeight = n.type === 'repo' ? 7 :
                        n.type === 'file' ? 4 :
                        n.type === 'cluster' ? 5 : 3;
    sprite.fontWeight = n.type === 'repo' ? '700' :
                        n.type === 'file' ? '500' : '400';
    sprite.fontFace = 'Roboto, sans-serif';
    if (n.clustered) sprite.color = 'rgba(148,163,184,0.15)';
    return sprite;
  })
  .nodeThreeObjectExtend(false)
  .linkColor(l => {
    if (selectedNode && !highlightLinks.has(l)) return 'rgba(203,213,225,0.08)';
    if (selectedNode && highlightLinks.has(l)) return edgeColors[l.kind] || '#2563EB';
    return edgeColors[l.kind] || '#CBD5E1';
  })
  .linkWidth(l => {
    if (selectedNode && highlightLinks.has(l)) return Math.max(1.5, (l.weight || 0.3) * 4);
    return l.kind === 'contains' ? 0.15 : Math.max(0.3, (l.weight || 0.3) * 2);
  })
  .linkOpacity(l => {
    if (selectedNode && !highlightLinks.has(l)) return 0.04;
    return l.kind === 'contains' ? 0.1 : 0.3;
  })
  .linkDirectionalArrowLength(l => l.kind === 'contains' ? 0 : 3)
  .linkDirectionalArrowRelPos(1)
  .linkDirectionalArrowColor(l => edgeColors[l.kind] || '#CBD5E1')
  .d3AlphaDecay(0.02)
  .d3VelocityDecay(0.4)
  .warmupTicks(graphData.nodes.length > 200 ? 60 : 120)
  .cooldownTime(graphData.nodes.length > 200 ? 5000 : 15000)
  .onNodeClick(node => {
    selectNode(node);
    refreshSprites();
    Graph.linkColor(Graph.linkColor()).linkWidth(Graph.linkWidth()).linkOpacity(Graph.linkOpacity());
    const detail = document.getElementById('detail');
    document.getElementById('d-name').textContent = node.label;
    document.getElementById('d-path').textContent = node.path;
    const badge = document.getElementById('d-type');
    badge.textContent = node.type;
    badge.style.background = nodeColors[node.type] || '#94A3B8';
    document.getElementById('d-tokens').textContent = node.type === 'repo'
      ? node.fileCount + ' files, ' + node.symbolCount + ' types'
      : node.tokens + ' tokens';

    // Symbols list (for file nodes) — escape every value before innerHTML.
    let html = '';
    if (node.type === 'file' && node.symbols && node.symbols.length) {
      html += '<h3>Symbols (' + node.symbols.length + ')</h3>';
      node.symbols.slice(0, 30).forEach(s => {
        html += '<div class="conn" style="color:#475569">' + escapeHtml(s) + '</div>';
      });
      if (node.symbols.length > 30) html += '<div class="conn" style="color:#94A3B8">... +' + (node.symbols.length - 30) + ' more</div>';
    }

    // Connections (skip containment, limit to 20)
    const conns = (adj[node.id] || []).filter(c => c.kind !== 'contains');
    if (conns.length) {
      html += '<h3>Connections (' + conns.length + ')</h3>';
      conns.slice(0, 20).forEach(c => {
        const name = c.node.split('/').pop().split('::').pop();
        const arrow = c.dir === 'out' ? '&rarr;' : '&larr;';
        html += '<div class="conn"><span class="kind">' + escapeHtml(c.kind) + '</span> '
              + arrow + ' ' + escapeHtml(name) + '</div>';
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
    refreshSprites();
    Graph.linkColor(Graph.linkColor()).linkWidth(Graph.linkWidth()).linkOpacity(Graph.linkOpacity());
    document.getElementById('detail').style.display = 'none';
  });

// Customize forces
Graph.d3Force('link').distance(link =>
  link.kind === 'contains' ? 12 : 50 + (1 - (link.weight || 0.3)) * 50
);
Graph.d3Force('charge').strength(n =>
  n.type === 'repo' ? -400 : n.type === 'cluster' ? -200 : n.type === 'file' ? -150 : -50
);

// ── Query Overlay ──
const relevanceScores = {{RELEVANCE_SCORES}};
const initialQuery = {{QUERY}};

let activeOverlay = null;  // {scores, maxRel} or null
function applyRelevanceOverlay(scores) {
  if (!scores || Object.keys(scores).length === 0) {
    activeOverlay = null;
    document.getElementById('search-info').textContent = '';
    refreshSprites();
    return;
  }
  const maxRel = Math.max(...Object.values(scores), 0.01);
  const matchCount = Object.keys(scores).length;
  document.getElementById('search-info').textContent = matchCount + ' matches';
  activeOverlay = { scores, maxRel };
  refreshSprites();
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


def _js_safe_json(obj):
    """JSON with HTML-sensitive chars escaped so a `</script>` in any value
    cannot break out of the embedding <script> tag. The \\uXXXX sequences
    parse back to the original chars in JSON at runtime."""
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
        .replace('&', '\\u0026')
        .replace(' ', '\\u2028')
        .replace(' ', '\\u2029')
    )


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
    html = html.replace('{{GRAPH_DATA}}', _js_safe_json(graph_data))
    html = html.replace('{{QUERY}}', _js_safe_json(query or ''))
    html = html.replace('{{RELEVANCE_SCORES}}', _js_safe_json(relevance_scores or {}))

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
    parser.add_argument('--focus', default=None,
                        help='Focus on one repo (expand it, collapse others into bubbles)')
    parser.add_argument('--top-symbols', type=int, default=0,
                        help='Max symbols per file to show (0 = file-level only, default in focus mode)')
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

    # Extract nodes
    repo_bubbles = {}
    max_symbols = args.top_symbols if args.top_symbols > 0 else None
    if args.focus and args.multi_index:
        # Focus mode defaults: file-level only (no symbols), top 40
        focus_top = args.top or 40
        focus_symbols = args.top_symbols > 0 and not args.no_symbols
        nodes, file_ids, repo_bubbles = extract_focused(
            index, args.focus, top=focus_top,
            include_symbols=focus_symbols, max_symbols=max_symbols,
            graph_edges=all_graph_edges)
        print(f'Focus: {args.focus} ({sum(1 for n in nodes if n["type"] != "repo")} nodes) '
              f'+ {len(repo_bubbles)} repo bubbles', file=sys.stderr)
    else:
        nodes, file_ids = extract_nodes(
            index, top=args.top, include_symbols=not args.no_symbols,
            max_symbols=max_symbols, graph_edges=all_graph_edges)

    if not nodes:
        print('No files found in index.', file=sys.stderr)
        sys.exit(1)

    # Concept clustering — collapse blob nodes like types.ts with 100+ DTOs
    nodes, cluster_edges = cluster_by_prefix(nodes, min_group=3)

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
    if args.focus and repo_bubbles:
        cross_links = find_cross_repo_links_focused(nodes, index, args.focus, repo_bubbles)
        edges.extend(cross_links)
        if cross_links:
            print(f'{len(cross_links)} cross-repo type links', file=sys.stderr)
    elif args.multi_index:
        cross_links = find_cross_repo_links(nodes)
        edges.extend(cross_links)
        if cross_links:
            print(f'{len(cross_links)} cross-repo type links', file=sys.stderr)

    # Cluster containment edges
    edges.extend(cluster_edges)

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
