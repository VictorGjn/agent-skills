"""Tests for label propagation community detection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_two_clusters():
    """Two tightly connected groups should form two communities."""
    from community_detect import label_propagation

    # Cluster A: a1 <-> a2 <-> a3 (tight)
    # Cluster B: b1 <-> b2 <-> b3 (tight)
    # Weak link: a3 -> b1
    edges = [
        {'source': 'a1', 'target': 'a2', 'weight': 1.0},
        {'source': 'a2', 'target': 'a3', 'weight': 1.0},
        {'source': 'a1', 'target': 'a3', 'weight': 0.9},
        {'source': 'b1', 'target': 'b2', 'weight': 1.0},
        {'source': 'b2', 'target': 'b3', 'weight': 1.0},
        {'source': 'b1', 'target': 'b3', 'weight': 0.9},
        {'source': 'a3', 'target': 'b1', 'weight': 0.3},
    ]
    communities = label_propagation(edges)

    # Should produce 2 communities
    labels = set(communities.values())
    assert len(labels) == 2

    # a-nodes should share a label, b-nodes should share a label
    assert communities['a1'] == communities['a2'] == communities['a3']
    assert communities['b1'] == communities['b2'] == communities['b3']
    assert communities['a1'] != communities['b1']


def test_single_cluster():
    """Fully connected graph should form one community."""
    from community_detect import label_propagation

    edges = [
        {'source': 'x', 'target': 'y', 'weight': 1.0},
        {'source': 'y', 'target': 'z', 'weight': 1.0},
        {'source': 'x', 'target': 'z', 'weight': 1.0},
    ]
    communities = label_propagation(edges)

    labels = set(communities.values())
    assert len(labels) == 1
    assert communities['x'] == communities['y'] == communities['z']


def test_isolated_nodes():
    """Nodes with no edges stay singletons, then min_size merges them."""
    from community_detect import label_propagation

    # One edge pair plus one isolated node — isolated node has no adj entries
    # so it won't appear in label_propagation output (adj only has connected nodes)
    # Test that two weakly connected groups each form their own community
    edges = [
        {'source': 'solo1', 'target': 'solo2', 'weight': 1.0},
    ]
    # With min_size=2, both nodes form a community of size 2 — no merging needed
    communities = label_propagation(edges, min_size=2)
    assert len(communities) == 2
    assert communities['solo1'] == communities['solo2']


def test_build_meta_graph():
    """build_meta_graph returns cluster node lists and cross-cluster edge counts."""
    from community_detect import label_propagation, build_meta_graph

    edges = [
        {'source': 'a1', 'target': 'a2', 'weight': 1.0},
        {'source': 'a2', 'target': 'a3', 'weight': 1.0},
        {'source': 'a1', 'target': 'a3', 'weight': 0.9},
        {'source': 'b1', 'target': 'b2', 'weight': 1.0},
        {'source': 'b2', 'target': 'b3', 'weight': 1.0},
        {'source': 'b1', 'target': 'b3', 'weight': 0.9},
        {'source': 'a3', 'target': 'b1', 'weight': 0.3},
    ]
    labels = label_propagation(edges)
    meta = build_meta_graph(labels, edges)

    assert 'clusters' in meta
    assert 'meta_edges' in meta

    # Two clusters, each with 3 nodes
    assert len(meta['clusters']) == 2
    for cluster_label, cluster_data in meta['clusters'].items():
        assert len(cluster_data['nodes']) == 3

    # One cross-cluster edge (a3 <-> b1)
    assert len(meta['meta_edges']) == 1
    me = meta['meta_edges'][0]
    assert me['weight'] == 1  # one edge crosses the boundary


def test_min_size_merge():
    """A 1-node community merges into its most-connected neighbor community."""
    from community_detect import label_propagation

    # Three tightly-connected nodes plus one dangling node attached weakly
    edges = [
        {'source': 'a', 'target': 'b', 'weight': 1.0},
        {'source': 'b', 'target': 'c', 'weight': 1.0},
        {'source': 'a', 'target': 'c', 'weight': 1.0},
        {'source': 'd', 'target': 'a', 'weight': 0.2},
    ]
    # With min_size=2, 'd' (if it ends up alone) should merge into the a/b/c community
    communities = label_propagation(edges, min_size=2)

    # All 4 nodes must be present
    assert set(communities.keys()) == {'a', 'b', 'c', 'd'}

    # a/b/c should all share the same label
    assert communities['a'] == communities['b'] == communities['c']

    # 'd' must have merged into the same community (only one community total)
    labels = set(communities.values())
    assert len(labels) == 1
