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
    # Graphify v0.1.7+ semantic similarity edges (vector-based, not AST-based)
    'semantic_similarity': ('related', 0.5),
}
_DEFAULT_RELATION = ('related', 0.3)

# TODO(p3.2-followup): hyperedge support deferred. Graphify v2 (April 2026)
# emits hyperedges in graph.json — links with multiple `_src` and/or `_tgt`
# lists representing N-ary relationships (community membership, etc.). The
# binary _src/_tgt parser below skips them silently. Forcing function for
# de-deferral: a real corpus surfaces with hyperedges
# (`any(isinstance(link.get('_src'), list) for link in graph.json['links'])`).
# Implementation sketch + deferral conditions in
# ~/.claude/handoffs/ce_must_should_prep.md "Follow-ups carried over" §B7.

# Confidence string → weight multiplier. Graphify's real output uses these
# string tags (confidence_score is documented but not emitted in practice).
_CONFIDENCE_MULT = {
    'EXTRACTED': 1.0,
    'INFERRED': 0.5,
    'AMBIGUOUS': 0.2,
}
_DEFAULT_CONFIDENCE_MULT = 0.8


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
    """Convert graphify NetworkX graph to code_graph {nodes, edges, outgoing, incoming, stats} format.

    Handles Windows-indexed paths (backslashes) by building a lookup from
    forward-slash form back to the original indexed path format. Returned
    edges always use the original indexed path format so downstream traversal
    can match on keyword-scored entry points.
    """
    # Build forward-slash → original-path lookup (Windows paths have backslashes)
    path_lookup = {p.replace('\\', '/'): p for p in indexed_paths}
    normalized_paths = set(path_lookup.keys())

    # Phase 1: Build node_id → indexed path mapping
    node_file_map = {}
    node_is_doc = {}  # indexed path → is_doc flag
    prefix_cache = {}
    for node in graphify_data.get('nodes', []):
        node_id = node.get('id')
        source_file = node.get('source_file')
        if node_id is None or not source_file:
            continue
        matched = _normalize_path(source_file, normalized_paths, prefix_cache)
        if matched:
            original = path_lookup[matched]
            node_file_map[node_id] = original
            if node.get('file_type') == 'documentation':
                node_is_doc[original] = True

    # Phase 2: Parse links and build file-level edges.
    # Graphify graphs are undirected; _src/_tgt preserve the original direction.
    raw_edges = {}  # (source_file, target_file, kind) → max weight
    for link in graphify_data.get('links', []):
        src_id = link.get('_src') or link.get('source')
        tgt_id = link.get('_tgt') or link.get('target')
        if src_id is None or tgt_id is None:
            continue

        src_file = node_file_map.get(src_id)
        tgt_file = node_file_map.get(tgt_id)
        if not src_file or not tgt_file or src_file == tgt_file:
            continue

        relation = link.get('relation', 'unknown')
        kind, base_weight = _RELATION_MAP.get(relation, _DEFAULT_RELATION)

        # Prefer explicit confidence_score float, fall back to confidence string,
        # default to 0.8. Guard against None.
        conf_score = link.get('confidence_score')
        if conf_score is None:
            conf_score = _CONFIDENCE_MULT.get(link.get('confidence'), _DEFAULT_CONFIDENCE_MULT)
        try:
            weight = base_weight * float(conf_score)
        except (TypeError, ValueError):
            weight = base_weight * _DEFAULT_CONFIDENCE_MULT

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
        is_doc = node_is_doc.get(path, False)
        nodes[path] = {
            'exports': [],
            'is_test': False,
            'is_doc': is_doc,
            'is_code': not is_doc,
            'dir': str(PurePosixPath(path.replace('\\', '/')).parent),
        }

    for (src, tgt, kind), weight in raw_edges.items():
        edge = {'source': src, 'target': tgt, 'kind': kind, 'weight': weight}
        edges.append(edge)
        outgoing[src].append(edge)
        incoming[tgt].append(edge)

    doc_count = sum(1 for n in nodes.values() if n['is_doc'])
    return {
        'nodes': nodes,
        'edges': edges,
        'outgoing': dict(outgoing),
        'incoming': dict(incoming),
        'stats': {
            'total_nodes': len(nodes),
            'total_edges': len(edges),
            'code_files': len(nodes) - doc_count,
            'test_files': 0,
            'doc_files': doc_count,
        },
    }


def _normalize_path(source_path: str, normalized_indexed: set[str], prefix_cache: dict) -> str | None:
    """Match a Graphify source_file to a normalized (forward-slash) indexed path.

    Tries direct match, cached prefix stripping, then progressive prefix stripping.
    Returns the normalized form, or None if no match.
    """
    normalized = source_path.replace('\\', '/')

    if normalized in normalized_indexed:
        return normalized

    cached = prefix_cache.get('_prefix')
    if cached is not None and normalized.startswith(cached):
        suffix = normalized[len(cached):].lstrip('/')
        if suffix in normalized_indexed:
            return suffix

    parts = normalized.split('/')
    for i in range(1, len(parts)):
        suffix = '/'.join(parts[i:])
        if suffix in normalized_indexed:
            prefix_cache['_prefix'] = '/'.join(parts[:i]) + '/'
            return suffix

    return None
