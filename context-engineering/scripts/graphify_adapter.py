"""
Graphify Adapter — Convert Graphify's NetworkX JSON graph to code_graph format.

Reads graphify-out/graph.json and adapts it for use by the context-engineering
graph traversal pipeline. Handles path normalization, relation mapping,
confidence weighting, and file-level edge deduplication.

Used by: code_graph.build_graph_with_fallback()
"""

import json
from pathlib import PurePosixPath
from collections import defaultdict


# ── Relation mapping: graphify relation → code_graph kind + base weight ──

_RELATION_MAP = {
    'imports': ('imports', 1.0),
    'imports_from': ('imports', 1.0),
    'calls': ('calls', 0.7),
    'method': ('calls', 0.7),
    'uses': ('calls', 0.7),
    'inherits': ('extends', 0.9),
    'contains': ('defined_in', 0.4),
    'rationale_for': ('documents', 0.5),
    'case_of': ('related', 0.3),
}
_DEFAULT_RELATION = ('related', 0.3)

_DEFAULT_CONFIDENCE_SCORE = 0.8


def load_graphify_graph(graph_json_path: str) -> dict | None:
    """Read a NetworkX node-link JSON file. Returns raw dict or None."""
    try:
        with open(graph_json_path) as f:
            data = json.load(f)
        if not isinstance(data, dict) or 'nodes' not in data:
            return None
        return data
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def adapt_to_code_graph(graphify_data: dict, indexed_paths: set[str]) -> dict:
    """Convert graphify NetworkX graph to code_graph {nodes, edges, outgoing, incoming, stats} format."""
    # Phase 1: Build node_id → source_file mapping
    node_file_map = {}
    prefix_cache = {}
    for node in graphify_data.get('nodes', []):
        node_id = node.get('id')
        source_file = node.get('source_file')
        if node_id is None or not source_file:
            continue
        normalized = _normalize_path(source_file, indexed_paths, prefix_cache)
        if normalized:
            node_file_map[node_id] = normalized

    # Phase 2: Parse links and build file-level edges
    raw_edges = {}  # (source_file, target_file, kind) → max weight
    for link in graphify_data.get('links', []):
        src_id = link.get('source')
        tgt_id = link.get('target')
        if src_id is None or tgt_id is None:
            continue

        src_file = node_file_map.get(src_id)
        tgt_file = node_file_map.get(tgt_id)
        if not src_file or not tgt_file or src_file == tgt_file:
            continue

        relation = link.get('relation', 'unknown')
        kind, base_weight = _RELATION_MAP.get(relation, _DEFAULT_RELATION)

        conf_score = link.get('confidence_score', _DEFAULT_CONFIDENCE_SCORE)
        weight = base_weight * conf_score

        # Deduplicate: keep highest weight per (source, target, kind)
        key = (src_file, tgt_file, kind)
        if key not in raw_edges or weight > raw_edges[key]:
            raw_edges[key] = weight

    # Phase 3: Build code_graph-compatible structure
    nodes = {}
    edges = []
    outgoing = defaultdict(list)
    incoming = defaultdict(list)

    all_files = set()
    for (src, tgt, _kind) in raw_edges:
        all_files.add(src)
        all_files.add(tgt)

    for path in all_files:
        nodes[path] = {
            'exports': [],
            'is_test': False,
            'is_doc': False,
            'is_code': True,
            'dir': str(PurePosixPath(path).parent),
        }

    for (src, tgt, kind), weight in raw_edges.items():
        edge = {'source': src, 'target': tgt, 'kind': kind, 'weight': weight}
        edges.append(edge)
        outgoing[src].append(edge)
        incoming[tgt].append(edge)

    return {
        'nodes': nodes,
        'edges': edges,
        'outgoing': dict(outgoing),
        'incoming': dict(incoming),
        'stats': {
            'total_nodes': len(nodes),
            'total_edges': len(edges),
            'code_files': len(nodes),
            'test_files': 0,
            'doc_files': 0,
        },
    }


def _normalize_path(abs_path: str, indexed_paths: set[str], prefix_cache: dict) -> str | None:
    """Strip prefix to match an indexed path. Cache discovered prefix."""
    normalized = abs_path.replace('\\', '/')

    if normalized in indexed_paths:
        return normalized

    cached = prefix_cache.get('_prefix')
    if cached is not None:
        suffix = normalized[len(cached):].lstrip('/')
        if suffix in indexed_paths:
            return suffix

    parts = normalized.split('/')
    for i in range(1, len(parts)):
        suffix = '/'.join(parts[i:])
        if suffix in indexed_paths:
            prefix_cache['_prefix'] = '/'.join(parts[:i]) + '/'
            return suffix

    return None
