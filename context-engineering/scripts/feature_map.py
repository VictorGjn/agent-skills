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

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from code_graph import build_graph_with_fallback
from community_detect import build_meta_graph, label_clusters, label_propagation


def build_feature_map(index: dict[str, Any], graphify_path: str | None = None) -> dict[str, Any]:
    """Full pipeline: index → graph → communities → labeled meta-graph."""
    files = index.get('files', [])
    graph = build_graph_with_fallback(files, graphify_path)
    labels = label_propagation(graph['edges'])
    meta = build_meta_graph(labels, graph['edges'])

    file_data: dict[str, dict[str, Any]] = {}
    for f in files:
        path = f['path'].replace('\\', '/')
        tree = f.get('tree', {})
        symbols = [c.get('title', '') for c in tree.get('children', []) if c.get('title')]
        headings = [h.get('title', '') for h in f.get('headings', [])]
        file_data[path] = {'symbols': symbols, 'headings': headings}

    cluster_labels = label_clusters(meta['clusters'], file_data)

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
        'node_labels': labels,
    }
