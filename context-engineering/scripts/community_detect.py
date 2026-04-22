"""
Label Propagation Community Detection — pure Python, zero dependencies.

Algorithm:
1. Each node starts with its own unique label
2. In each iteration, every node adopts the label with highest weighted
   frequency among its neighbors
3. Repeat until convergence (labels stop changing) or max iterations
4. Merge tiny communities (< min_size) into their most-connected neighbor
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
import random


def label_propagation(edges: list[dict[str, Any]], max_iter: int = 15,
                      min_size: int = 2, seed: int = 42) -> dict[str, int]:
    """Run label propagation on an undirected weighted graph.

    Args:
        edges: [{'source': str, 'target': str, 'weight': float}]
        max_iter: maximum iterations before stopping
        min_size: communities smaller than this get merged into neighbors
        seed: random seed for deterministic results

    Returns:
        {node_id: community_label} mapping. Returns {} if edges is empty.
    """
    if not edges:
        return {}
    random.seed(seed)

    # Build undirected adjacency list
    adj = defaultdict(list)
    for e in edges:
        s, t, w = e['source'], e['target'], e.get('weight', 1.0)
        adj[s].append((t, w))
        adj[t].append((s, w))

    # Initialize: each node is its own community
    all_nodes = list(adj.keys())
    labels = {n: n for n in all_nodes}

    for _ in range(max_iter):
        changed = False
        order = list(all_nodes)
        random.shuffle(order)

        for node in order:
            if not adj[node]:
                continue

            # Count weighted label frequency among neighbors
            label_weight = defaultdict(float)
            for neighbor, weight in adj[node]:
                label_weight[labels[neighbor]] += weight

            # Pick the label with highest weight (tie-break: current label)
            best_label = max(label_weight, key=lambda l: (label_weight[l], l == labels[node]))
            if best_label != labels[node]:
                labels[node] = best_label
                changed = True

        if not changed:
            break

    # Merge tiny communities into most-connected non-tiny neighbor (iterate until stable)
    for _ in range(max_iter):
        community_sizes = defaultdict(int)
        for label in labels.values():
            community_sizes[label] += 1
        tiny = {label for label, size in community_sizes.items() if size < min_size}
        if not tiny:
            break

        merged_any = False
        for node in all_nodes:
            if labels[node] in tiny:
                label_weight = defaultdict(float)
                for neighbor, weight in adj[node]:
                    nl = labels[neighbor]
                    if nl not in tiny:
                        label_weight[nl] += weight
                if label_weight:
                    new_label = max(label_weight, key=label_weight.get)
                    if new_label != labels[node]:
                        labels[node] = new_label
                        merged_any = True
        if not merged_any:
            break

    # Normalize labels to 0-indexed integers
    unique = sorted(set(labels.values()))
    label_map = {l: i for i, l in enumerate(unique)}
    return {node: label_map[label] for node, label in labels.items()}


def build_meta_graph(labels: dict[str, int], edges: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a cluster-level meta-graph from node labels and edges.

    Returns:
        {
            'clusters': {label: {'nodes': [...], 'internal_edges': int}},
            'meta_edges': [{'source': label, 'target': label, 'weight': int}]  # weight = cross-cluster edge count, not sum of original float weights
        }
    """
    clusters = defaultdict(lambda: {'nodes': [], 'internal_edges': 0})
    for node, label in labels.items():
        clusters[label]['nodes'].append(node)

    # Count internal and cross-cluster edges
    cross_edges = defaultdict(int)
    for e in edges:
        s_label = labels.get(e['source'])
        t_label = labels.get(e['target'])
        if s_label is None or t_label is None:
            continue
        if s_label == t_label:
            clusters[s_label]['internal_edges'] += 1
        else:
            pair = (min(s_label, t_label), max(s_label, t_label))
            cross_edges[pair] += 1

    meta_edges = []
    for (s, t), count in cross_edges.items():
        meta_edges.append({'source': s, 'target': t, 'weight': count})

    return {'clusters': dict(clusters), 'meta_edges': meta_edges}


def label_clusters(
    clusters: dict[int, dict[str, Any]],
    file_data: dict[str, dict[str, Any]],
) -> dict[int, str]:
    """Assign human-readable labels to clusters.

    Strategy (in priority order):
    1. If >=70% of files share a directory prefix (and count >= 2) → directory name.
    2. Otherwise → top 2 symbol names by frequency.
    3. For doc-only clusters → top 2 heading titles by frequency.
    4. Final fallback → "Cluster {label}".

    Args:
        clusters: {label: {'nodes': [path, ...]}} from build_meta_graph
        file_data: {path: {'symbols': [...], 'headings': [...]}} from workspace index

    Returns:
        {label: human_readable_name}
    """
    result: dict[int, str] = {}

    for label, data in clusters.items():
        nodes: list[str] = data.get('nodes', [])

        if not nodes:
            result[label] = f"Cluster {label}"
            continue

        # --- Directory prefix strategy ---
        dir_counts: Counter[str] = Counter()
        for path in nodes:
            normalized = path.replace('\\', '/')
            parts = normalized.rsplit('/', 1)
            # parent segment: take the last component of the directory portion
            if len(parts) == 2:
                dir_name = parts[0].rsplit('/', 1)[-1]
            else:
                dir_name = ''
            if dir_name:
                dir_counts[dir_name] += 1

        if dir_counts:
            top_dir, top_count = dir_counts.most_common(1)[0]
            if top_count >= 2 and top_count >= 0.7 * len(nodes):
                result[label] = top_dir.lower()
                continue

        # --- Symbol fallback ---
        all_symbols: list[str] = []
        all_headings: list[str] = []
        for path in nodes:
            entry = file_data.get(path, {})
            all_symbols.extend(entry.get('symbols', []))
            all_headings.extend(entry.get('headings', []))

        if all_symbols:
            sym_counts: Counter[str] = Counter(all_symbols)
            top_symbols = [s for s, _ in sorted(sym_counts.most_common(2), key=lambda x: (-x[1], x[0]))]
            result[label] = ', '.join(top_symbols)
            continue

        # --- Knowledge-base (headings) fallback ---
        if all_headings:
            heading_counts: Counter[str] = Counter(all_headings)
            top_headings = [h for h, _ in heading_counts.most_common(2)]
            result[label] = ', '.join(top_headings)
            continue

        result[label] = f"Cluster {label}"

    return result
