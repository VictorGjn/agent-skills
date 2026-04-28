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
from collections import Counter
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent))
from code_graph import build_graph_with_fallback
from community_detect import build_meta_graph, label_clusters, label_propagation


def build_domain_layer(feature_data: dict[str, Any],
                        min_edge_weight: int = 2) -> dict[Any, Any]:
    """Group feature clusters into domains via a second pass of label propagation.

    The meta-graph (cluster ↔ cluster, weighted by cross-cluster edge count)
    is fed back into label_propagation. Only edges with weight ≥ `min_edge_weight`
    count as structural coupling — incidental single-import links between two
    otherwise-separate clusters do not pull them into the same domain. Isolated
    clusters with no qualifying edges get their own domain id (cluster id reused).
    """
    edges = [
        {'source': e['source'], 'target': e['target'], 'weight': e.get('weight', 1)}
        for e in feature_data.get('meta_edges', [])
        if e.get('weight', 1) >= min_edge_weight
    ]
    domain_map: dict[Any, Any] = label_propagation(edges, min_size=1)
    for cid in feature_data.get('clusters', {}):
        domain_map.setdefault(cid, cid)
    return domain_map


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


def build_feature_map(index: dict[str, Any], graphify_path: str | None = None, *,
                      concept_llm: Callable[..., dict[str, Any]] | None = None,
                      cache_dir: Path | None = None) -> dict[str, Any]:
    """Full pipeline: index → graph → communities → labeled meta-graph.

    When `concept_llm` is provided, each cluster also gets an LLM-assigned
    concept, description, and sub_features. Without it, those fields are
    populated with the mechanical label / empty defaults so downstream
    rendering can treat every cluster uniformly.
    """
    files = index.get('files', [])
    graph = build_graph_with_fallback(files, graphify_path)
    labels = label_propagation(graph['edges'])

    # Seed singleton clusters for files with no graph edges so sparse / disconnected
    # repos still produce a meaningful map. Each isolated file gets its own label.
    if labels:
        next_label = max(labels.values()) + 1
    else:
        next_label = 0
    for f in files:
        path = f['path'].replace('\\', '/')
        if path not in labels:
            labels[path] = next_label
            next_label += 1

    meta = build_meta_graph(labels, graph['edges'])

    file_data: dict[str, dict[str, Any]] = {}
    path_tokens: dict[str, int] = {}
    for f in files:
        path = f['path'].replace('\\', '/')
        tree = f.get('tree', {})
        symbols = [c.get('title', '') for c in tree.get('children', []) if c.get('title')]
        headings = [h.get('title', '') for h in f.get('headings', [])]
        file_data[path] = {
            'symbols': symbols,
            'headings': headings,
            'first_sentence': f.get('firstSentence', '') or tree.get('firstSentence', ''),
        }
        path_tokens[path] = f.get('tokens', 0)

    cluster_labels = label_clusters(meta['clusters'], file_data)

    concept_results: dict[Any, dict[str, Any]] = {}
    if concept_llm is not None and meta['clusters']:
        # concept_llm is the user-facing seam: it can be either a
        # label_all_clusters-shaped function (cluster, file_data, current_label)
        # → dict, or the already-fanned-out dict produced upstream. The first
        # form is what tests pass; we adapt to label_all_clusters here.
        for cid, cluster in meta['clusters'].items():
            try:
                concept_results[cid] = concept_llm(
                    cluster=cluster,
                    file_data=file_data,
                    current_label=cluster_labels.get(cid, f'Cluster {cid}'),
                    cache_dir=cache_dir,
                )
            except TypeError:
                # concept_llm signature mismatch — skip silently and use defaults.
                concept_results[cid] = {}

    for label, cluster in meta['clusters'].items():
        mechanical = cluster_labels.get(label, f'Cluster {label}')
        cluster['label'] = mechanical
        cluster['file_count'] = len(cluster['nodes'])
        cluster['total_tokens'] = sum(path_tokens.get(n, 0) for n in cluster['nodes'])
        sym_counts: Counter[str] = Counter()
        for path in cluster['nodes']:
            for sym in file_data.get(path, {}).get('symbols', []):
                if sym:
                    sym_counts[sym] += 1
        cluster['symbols'] = [
            s for s, _ in sorted(sym_counts.items(), key=lambda x: (-x[1], x[0]))[:8]
        ]
        # Concept fields — always present, populated from LLM when available
        concept = concept_results.get(label, {})
        cluster['concept'] = concept.get('concept', mechanical)
        cluster['description'] = concept.get('description', '')
        cluster['sub_features'] = concept.get('sub_features', [])

    # Hierarchical fold: feature clusters → domains via label propagation on the meta-graph.
    domain_map = build_domain_layer({'clusters': meta['clusters'],
                                       'meta_edges': meta['meta_edges']})
    domains: dict[Any, dict[str, Any]] = {}
    for cid, cluster in meta['clusters'].items():
        did = domain_map.get(cid, cid)
        cluster['domain'] = did
        entry = domains.setdefault(did, {'name': '', 'cluster_ids': [], 'color_index': 0})
        entry['cluster_ids'].append(cid)
    # Stable color_index by first appearance order; default name from the
    # largest member cluster's concept so v2 has something readable until
    # a future v3 adds a domain-naming LLM pass.
    for idx, (did, entry) in enumerate(domains.items()):
        entry['color_index'] = idx % 16
        members = [meta['clusters'][cid] for cid in entry['cluster_ids']]
        biggest = max(members, key=lambda c: c.get('file_count', 0), default=None)
        if biggest is not None and biggest.get('concept'):
            entry['name'] = biggest['concept']
        else:
            entry['name'] = f'Domain {did}'

    return {
        'clusters': meta['clusters'],
        'meta_edges': meta['meta_edges'],
        'cluster_labels': cluster_labels,
        'node_labels': labels,
        'domains': domains,
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
  #stats .domain-list {
    margin-top: 10px;
    max-height: 50vh;
    overflow-y: auto;
    border-top: 1px solid var(--border);
    padding-top: 8px;
  }
  .legend-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 4px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    color: var(--ink);
  }
  .legend-row:hover { background: var(--bg); }
  .legend-row.active {
    background: var(--bg);
    border: 1px solid var(--blue);
    padding: 3px 3px;
  }
  .legend-swatch {
    width: 12px;
    height: 12px;
    border-radius: 3px;
    flex-shrink: 0;
  }
  .legend-name { flex: 1; font-weight: 500; }
  .legend-count {
    color: var(--muted);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  #detail .desc {
    color: var(--muted);
    font-size: 12px;
    margin: 4px 0 8px;
    font-style: italic;
  }
  #detail details summary {
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    cursor: pointer;
    margin-top: 10px;
  }
  #detail details[open] summary { margin-bottom: 4px; }
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
  <div class="domain-list" id="domainList"></div>
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
  const domains = data.domains || {};

  // Domain → ordered position of cluster in domain (for lightness offset)
  const clusterIdxInDomain = {};
  Object.keys(domains).forEach(function (did) {
    (domains[did].cluster_ids || []).forEach(function (cid, i) {
      clusterIdxInDomain[String(cid)] = i;
    });
  });

  function hexToHsl(hex) {
    const m = hex.replace('#', '');
    const r = parseInt(m.substring(0, 2), 16) / 255;
    const g = parseInt(m.substring(2, 4), 16) / 255;
    const b = parseInt(m.substring(4, 6), 16) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h = 0, s = 0;
    const l = (max + min) / 2;
    if (max !== min) {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      switch (max) {
        case r: h = ((g - b) / d + (g < b ? 6 : 0)); break;
        case g: h = ((b - r) / d + 2); break;
        case b: h = ((r - g) / d + 4); break;
      }
      h /= 6;
    }
    return { h: h * 360, s: s * 100, l: l * 100 };
  }

  function clusterColor(cluster) {
    const did = String(cluster.domain);
    const dom = domains[did];
    const baseIdx = (dom && typeof dom.color_index === 'number') ? dom.color_index : 0;
    const base = palette[baseIdx % palette.length];
    const hsl = hexToHsl(base);
    const offset = (clusterIdxInDomain[String(cluster.id)] || 0) * 6;
    const l = Math.max(20, Math.min(85, hsl.l + offset - 6));
    return 'hsl(' + Math.round(hsl.h) + ', ' + Math.round(hsl.s) + '%, ' + Math.round(l) + '%)';
  }

  const nodes = Object.keys(clusters).map(function (key) {
    const c = clusters[key];
    return {
      id: key,
      label: c.label || ('Cluster ' + key),
      concept: c.concept || c.label || ('Cluster ' + key),
      description: c.description || '',
      sub_features: c.sub_features || [],
      domain: c.domain != null ? c.domain : key,
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
  const domainCount = Object.keys(domains).length;
  document.getElementById('legend').textContent =
    nodes.length + ' clusters · ' + totalFiles + ' files · ' + domainCount + ' domains';

  // Render domain legend rows — clickable to filter to one domain.
  let activeDomain = null;
  const domainList = document.getElementById('domainList');
  Object.keys(domains).forEach(function (did) {
    const dom = domains[did];
    const memberClusters = (dom.cluster_ids || []).map(function (cid) {
      return clusters[String(cid)] || {};
    });
    const fileCount = memberClusters.reduce(function (s, c) { return s + (c.file_count || 0); }, 0);
    const swatchColor = palette[(dom.color_index || 0) % palette.length];
    const row = document.createElement('div');
    row.className = 'legend-row';
    row.dataset.domain = String(did);
    row.innerHTML =
      '<span class="legend-swatch" style="background:' + swatchColor + '"></span>' +
      '<span class="legend-name"></span>' +
      '<span class="legend-count"></span>';
    row.querySelector('.legend-name').textContent = dom.name || ('Domain ' + did);
    row.querySelector('.legend-count').textContent =
      (dom.cluster_ids || []).length + ' · ' + fileCount + 'f';
    row.addEventListener('click', function () {
      const dStr = row.dataset.domain;
      activeDomain = (activeDomain === dStr) ? null : dStr;
      applyDomainFilter();
    });
    domainList.appendChild(row);
  });

  function applyDomainFilter() {
    document.querySelectorAll('.legend-row').forEach(function (r) {
      r.classList.toggle('active', r.dataset.domain === activeDomain);
    });
    nodeSel.classed('dimmed', function (d) {
      return activeDomain !== null && String(d.domain) !== activeDomain;
    });
    linkSel.style('opacity', function (d) {
      if (activeDomain === null) return null;
      const sId = typeof d.source === 'object' ? d.source.id : d.source;
      const tId = typeof d.target === 'object' ? d.target.id : d.target;
      const s = nodeById.get(sId), t = nodeById.get(tId);
      const inDomain = s && t && String(s.domain) === activeDomain && String(t.domain) === activeDomain;
      return inDomain ? null : 0.05;
    });
  }

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

  function isCrossDomain(e) {
    const sId = typeof e.source === 'object' ? e.source.id : e.source;
    const tId = typeof e.target === 'object' ? e.target.id : e.target;
    const s = nodeById.get(sId), t = nodeById.get(tId);
    return !!(s && t && String(s.domain) !== String(t.domain));
  }

  const linkSel = container.append('g')
    .attr('class', 'edges')
    .selectAll('line')
    .data(links)
    .enter().append('line')
    .attr('class', 'edge')
    .attr('stroke-width', function (d) { return 1 + Math.log(d.weight + 1) * 1.5; })
    .attr('stroke-opacity', function (d) { return isCrossDomain(d) ? 0.85 : 0.45; })
    .attr('stroke', function (d) { return isCrossDomain(d) ? '#475569' : '#94A3B8'; });

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
    .attr('fill', function (d) { return clusterColor(d); });

  nodeSel.append('text')
    .attr('dy', function (d) { return -(8 + Math.sqrt(d.file_count) * 4 + 6); })
    .text(function (d) { return d.concept; });

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
    parts.push('<h2>' + escapeHtml(d.concept) + '</h2>');
    if (d.description) {
      parts.push('<p class="desc">' + escapeHtml(d.description) + '</p>');
    }
    parts.push('<div class="legend">' + d.file_count + ' files · ' +
      d.total_tokens + ' tokens · ' + d.internal_edges + ' internal edges</div>');
    if (d.sub_features && d.sub_features.length) {
      parts.push('<h3>Sub-features</h3><ul>');
      d.sub_features.forEach(function (s) {
        parts.push('<li>' + escapeHtml(s) + '</li>');
      });
      parts.push('</ul>');
    }
    parts.push('<details><summary>Files (' + d.file_count + ')</summary><ul>');
    d.nodes.forEach(function (f) { parts.push('<li>' + escapeHtml(f) + '</li>'); });
    parts.push('</ul></details>');
    if (d.symbols && d.symbols.length) {
      parts.push('<details><summary>Symbols (' + d.symbols.length + ')</summary><ul>');
      d.symbols.forEach(function (s) { parts.push('<li>' + escapeHtml(s) + '</li>'); });
      parts.push('</ul></details>');
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
      const haystack = (d.concept + ' ' + d.label + ' ' + d.description + ' ' +
                        (d.sub_features || []).join(' ')).toLowerCase();
      const matches = haystack.indexOf(q) !== -1;
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
    node_labels = feature_data.get('node_labels', {})
    return {
        **feature_data,
        'clusters': kept,
        'meta_edges': meta_edges,
        'cluster_labels': {k: v for k, v in labels.items() if k in kept_keys},
        'node_labels': {path: label for path, label in node_labels.items() if label in kept_keys},
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
    parser.add_argument('--min-cluster', type=int, default=1,
                        help='Min files per cluster (default 1 keeps singleton clusters '
                             'for disconnected files; raise to filter noise)')
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
