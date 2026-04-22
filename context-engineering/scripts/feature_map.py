"""
Feature Map — Bird's-eye view of codebase features via community detection.

Pipeline: workspace index → import graph → label propagation → meta-graph → D3 SVG

Usage:
  python3 feature_map.py                                      # uses cache/workspace-index.json
  python3 feature_map.py --index path/to/index.json           # custom index
  python3 feature_map.py --multi-index idx1.json idx2.json    # multi-repo
  python3 feature_map.py -o my-map.html                       # custom output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from code_graph import build_graph_with_fallback
from community_detect import build_meta_graph, label_clusters, label_propagation


def merge_indexes(indexes: list[dict[str, Any]]) -> dict[str, Any]:
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


def build_feature_map(index: dict[str, Any], graphify_path: str | None = None) -> dict[str, Any]:
    """Full pipeline: index → graph → communities → labeled meta-graph."""
    files = index.get('files', [])
    graph = build_graph_with_fallback(files, graphify_path)
    labels = label_propagation(graph['edges'])
    meta = build_meta_graph(labels, graph['edges'])

    file_data: dict[str, dict[str, Any]] = {}
    path_tokens: dict[str, int] = {}
    for f in files:
        path = f['path'].replace('\\', '/')
        tree = f.get('tree', {})
        symbols = [c.get('title', '') for c in tree.get('children', []) if c.get('title')]
        headings = [h.get('title', '') for h in f.get('headings', [])]
        file_data[path] = {'symbols': symbols, 'headings': headings}
        path_tokens[path] = f.get('tokens', 0)

    cluster_labels = label_clusters(meta['clusters'], file_data)

    for label, cluster in meta['clusters'].items():
        cluster['label'] = cluster_labels.get(label, f'Cluster {label}')
        cluster['file_count'] = len(cluster['nodes'])
        cluster['total_tokens'] = sum(path_tokens.get(n, 0) for n in cluster['nodes'])

    return {
        'clusters': meta['clusters'],
        'meta_edges': meta['meta_edges'],
        'cluster_labels': cluster_labels,
        'node_labels': labels,
    }


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__ — Feature Map</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  :root {
    --bg: #FAFBFC;
    --panel: #FFFFFF;
    --ink: #0F172A;
    --muted: #64748B;
    --border: #E2E8F0;
    --blue: #2563EB;
    --teal: #0D9488;
    --shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 4px 12px rgba(15, 23, 42, 0.04);
  }
  html, body {
    margin: 0;
    padding: 0;
    height: 100%;
    background: var(--bg);
    color: var(--ink);
    font-family: Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    overflow: hidden;
  }
  svg#graph {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    display: block;
    background: var(--bg);
  }
  #stats, #detail, #search {
    position: absolute;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: var(--shadow);
    padding: 12px 16px;
    font-size: 13px;
    line-height: 1.5;
  }
  #stats {
    top: 16px;
    left: 16px;
    max-width: 320px;
  }
  #stats h1 {
    margin: 0 0 4px;
    font-size: 15px;
    font-weight: 600;
    color: var(--blue);
  }
  #stats .legend {
    color: var(--muted);
    font-size: 12px;
  }
  #search {
    top: 16px;
    right: 16px;
    padding: 8px 12px;
  }
  #search input {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    font: inherit;
    color: var(--ink);
    background: var(--bg);
    outline: none;
    width: 220px;
  }
  #search input:focus {
    border-color: var(--blue);
  }
  #detail {
    bottom: 16px;
    right: 16px;
    max-width: 360px;
    max-height: 55vh;
    overflow-y: auto;
    display: none;
  }
  #detail.visible { display: block; }
  #detail h2 {
    margin: 0 0 8px;
    font-size: 14px;
    font-weight: 600;
    color: var(--teal);
  }
  #detail h3 {
    margin: 10px 0 4px;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  #detail ul {
    margin: 0;
    padding-left: 18px;
  }
  #detail li {
    font-size: 12px;
    color: var(--ink);
    font-family: "SF Mono", Menlo, Consolas, monospace;
    word-break: break-all;
  }
  .node circle {
    stroke: #FFFFFF;
    stroke-width: 2px;
    cursor: pointer;
    transition: stroke-width 120ms ease;
  }
  .node.highlight circle {
    stroke: var(--blue);
    stroke-width: 3px;
  }
  .node.dimmed { opacity: 0.2; }
  .node text {
    font-size: 11px;
    font-weight: 500;
    fill: var(--ink);
    pointer-events: none;
    text-anchor: middle;
  }
  .edge {
    stroke: #94A3B8;
    stroke-opacity: 0.5;
  }
</style>
</head>
<body>
<svg id="graph"></svg>
<div id="stats">
  <h1>__TITLE__</h1>
  <div class="legend" id="legend">Loading...</div>
</div>
<div id="search">
  <input type="text" id="searchInput" placeholder="Search clusters..." autocomplete="off">
</div>
<div id="detail"></div>
<script>
  const data = __GRAPH_DATA__;
  const palette = [
    '#2563EB', '#0D9488', '#7C3AED', '#F59E0B', '#EF4444',
    '#10B981', '#6366F1', '#EC4899', '#14B8A6', '#8B5CF6',
    '#F97316', '#06B6D4', '#84CC16', '#E11D48', '#0EA5E9'
  ];

  const clusters = data.clusters || {};
  const metaEdges = data.meta_edges || [];

  const nodes = Object.keys(clusters).map(function (key) {
    const c = clusters[key];
    return {
      id: key,
      label: c.label || ('Cluster ' + key),
      nodes: c.nodes || [],
      file_count: c.file_count || 0,
      total_tokens: c.total_tokens || 0,
      internal_edges: c.internal_edges || 0,
      symbols: c.symbols || []
    };
  });

  const nodeById = new Map(nodes.map(function (n) { return [n.id, n]; }));

  const links = metaEdges
    .map(function (e) {
      return {
        source: String(e.source),
        target: String(e.target),
        weight: e.weight || 1
      };
    })
    .filter(function (e) { return nodeById.has(e.source) && nodeById.has(e.target); });

  const totalFiles = nodes.reduce(function (sum, n) { return sum + n.file_count; }, 0);
  document.getElementById('legend').textContent =
    nodes.length + ' clusters · ' + totalFiles + ' files';

  const svg = d3.select('#graph');
  const width = window.innerWidth;
  const height = window.innerHeight;
  svg.attr('viewBox', [0, 0, width, height]);

  const container = svg.append('g');

  svg.call(
    d3.zoom()
      .scaleExtent([0.1, 8])
      .on('zoom', function (event) {
        container.attr('transform', event.transform);
      })
  );

  const linkSel = container.append('g')
    .attr('class', 'edges')
    .selectAll('line')
    .data(links)
    .enter().append('line')
    .attr('class', 'edge')
    .attr('stroke-width', function (d) { return 1 + Math.log(d.weight + 1); });

  const nodeSel = container.append('g')
    .attr('class', 'nodes')
    .selectAll('g')
    .data(nodes)
    .enter().append('g')
    .attr('class', 'node')
    .call(
      d3.drag()
        .on('start', function (event, d) {
          if (!event.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on('drag', function (event, d) {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on('end', function (event, d) {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        })
    );

  nodeSel.append('circle')
    .attr('r', function (d) { return 8 + Math.sqrt(d.file_count) * 4; })
    .attr('fill', function (d, i) { return palette[i % palette.length]; });

  nodeSel.append('text')
    .attr('dy', function (d) { return -(8 + Math.sqrt(d.file_count) * 4 + 6); })
    .text(function (d) { return d.label; });

  nodeSel.on('click', function (event, d) { showDetail(d); });

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(120))
    .force('charge', d3.forceManyBody().strength(-320))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide().radius(function (d) {
      return 8 + Math.sqrt(d.file_count) * 4 + 8;
    }));

  simulation.on('tick', function () {
    linkSel
      .attr('x1', function (d) { return d.source.x; })
      .attr('y1', function (d) { return d.source.y; })
      .attr('x2', function (d) { return d.target.x; })
      .attr('y2', function (d) { return d.target.y; });

    nodeSel.attr('transform', function (d) {
      return 'translate(' + d.x + ',' + d.y + ')';
    });
  });

  function connectionsFor(id) {
    const out = [];
    links.forEach(function (e) {
      const sId = typeof e.source === 'object' ? e.source.id : e.source;
      const tId = typeof e.target === 'object' ? e.target.id : e.target;
      if (sId === id && nodeById.has(tId)) {
        out.push({ label: nodeById.get(tId).label, weight: e.weight });
      } else if (tId === id && nodeById.has(sId)) {
        out.push({ label: nodeById.get(sId).label, weight: e.weight });
      }
    });
    return out;
  }

  function showDetail(d) {
    const panel = document.getElementById('detail');
    const conns = connectionsFor(d.id);
    const parts = [];
    parts.push('<h2>' + escapeHtml(d.label) + '</h2>');
    parts.push('<div class="legend">' + d.file_count + ' files · ' +
      d.total_tokens + ' tokens · ' + d.internal_edges + ' internal edges</div>');
    parts.push('<h3>Files</h3><ul>');
    d.nodes.forEach(function (f) { parts.push('<li>' + escapeHtml(f) + '</li>'); });
    parts.push('</ul>');
    if (d.symbols && d.symbols.length) {
      parts.push('<h3>Symbols</h3><ul>');
      d.symbols.forEach(function (s) { parts.push('<li>' + escapeHtml(s) + '</li>'); });
      parts.push('</ul>');
    }
    parts.push('<h3>Connections</h3>');
    if (conns.length === 0) {
      parts.push('<div class="legend">No connections</div>');
    } else {
      parts.push('<ul>');
      conns.forEach(function (c) {
        parts.push('<li>' + escapeHtml(c.label) + ' · weight ' + c.weight + '</li>');
      });
      parts.push('</ul>');
    }
    panel.innerHTML = parts.join('');
    panel.classList.add('visible');
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  const searchInput = document.getElementById('searchInput');
  searchInput.addEventListener('input', function () {
    const q = searchInput.value.trim().toLowerCase();
    nodeSel.classed('highlight', false).classed('dimmed', false);
    if (!q) return;
    nodeSel.each(function (d) {
      const matches = d.label.toLowerCase().indexOf(q) !== -1;
      d3.select(this)
        .classed('highlight', matches)
        .classed('dimmed', !matches);
    });
  });

  window.addEventListener('resize', function () {
    const w = window.innerWidth;
    const h = window.innerHeight;
    svg.attr('viewBox', [0, 0, w, h]);
    simulation.force('center', d3.forceCenter(w / 2, h / 2));
    simulation.alpha(0.3).restart();
  });
</script>
</body>
</html>
"""


def _js_safe_json(obj: Any) -> str:
    """Serialize to JSON with HTML-sensitive chars escaped so a `</script>` in
    the data cannot break out of the embedding `<script>` tag. The escaped
    `\\uXXXX` sequences parse back to the original chars in JSON, so JS sees
    the real string at runtime."""
    raw = json.dumps(obj, default=str)
    return (
        raw
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
        .replace('&', '\\u0026')
        .replace(' ', '\\u2028')
        .replace(' ', '\\u2029')
    )


def generate_html(feature_data: dict[str, Any], title: str) -> str:
    """Render feature_data as an interactive D3 force-directed HTML document."""
    graph_json = _js_safe_json(feature_data)
    safe_title = (
        str(title)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )
    return (
        _HTML_TEMPLATE
        .replace('__TITLE__', safe_title)
        .replace('__GRAPH_DATA__', graph_json)
    )


def _apply_min_cluster(feature_data: dict[str, Any], min_cluster: int) -> dict[str, Any]:
    """Drop clusters smaller than min_cluster and any meta_edges referencing them."""
    clusters = feature_data.get('clusters', {})
    kept = {k: v for k, v in clusters.items() if v.get('file_count', 0) >= min_cluster}
    kept_keys = set(kept.keys())
    meta_edges = [
        e for e in feature_data.get('meta_edges', [])
        if e.get('source') in kept_keys and e.get('target') in kept_keys
    ]
    labels = feature_data.get('cluster_labels', {})
    return {
        **feature_data,
        'clusters': kept,
        'meta_edges': meta_edges,
        'cluster_labels': {k: v for k, v in labels.items() if k in kept_keys},
    }


def _load_index(path: str) -> dict[str, Any]:
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def _resolve_index_and_defaults(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], str, str]:
    """Return (index, title, output_path) based on args, with sensible defaults."""
    if args.multi_index:
        indexes = [_load_index(p) for p in args.multi_index]
        index = merge_indexes(indexes)
        title = args.title or 'Multi-Repo Feature Map'
        output = args.output or 'cache/multi-repo-features.html'
        return index, title, output

    index_path = args.index or 'cache/workspace-index.json'
    index = _load_index(index_path)
    repo_name = Path(index.get('root', '')).name or 'Feature Map'
    title = args.title or repo_name
    output = args.output or 'cache/feature-map.html'
    return index, title, output


def main() -> None:
    parser = argparse.ArgumentParser(description="Bird's-eye feature map of a codebase.")
    parser.add_argument('--index', default=None, help='Path to workspace-index.json')
    parser.add_argument('--multi-index', nargs='+', default=None, help='Multiple indexes to merge')
    parser.add_argument('--graphify', default=None, help='Path to graphify graph.json')
    parser.add_argument('-o', '--output', default=None, help='Output HTML path')
    parser.add_argument('--title', default=None, help='Graph title')
    parser.add_argument('--min-cluster', type=int, default=2, help='Min files per cluster')
    args = parser.parse_args()

    index, title, output = _resolve_index_and_defaults(args)
    feature_data = build_feature_map(index, args.graphify)
    feature_data = _apply_min_cluster(feature_data, args.min_cluster)

    html = generate_html(feature_data, title)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')

    clusters = feature_data.get('clusters', {})
    file_count = sum(c.get('file_count', 0) for c in clusters.values())
    print(f'Feature map: {len(clusters)} clusters, {file_count} files -> {out_path}')


if __name__ == '__main__':
    main()
