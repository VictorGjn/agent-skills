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


def test_minimal_pair():
    """A single edge pair forms one community of size 2 with no merging needed."""
    from community_detect import label_propagation

    # One edge pair — both nodes form a community of size 2
    edges = [
        {'source': 'solo1', 'target': 'solo2', 'weight': 1.0},
    ]
    # With min_size=2, both nodes form a community of size 2 — no merging needed
    communities = label_propagation(edges, min_size=2)
    assert len(communities) == 2
    assert communities['solo1'] == communities['solo2']


def test_nodes_absent_from_edges_not_in_output():
    """Nodes that never appear in edges are not in the output (caller's responsibility)."""
    from community_detect import label_propagation

    edges = [
        {'source': 'x', 'target': 'y', 'weight': 1.0},
        {'source': 'y', 'target': 'z', 'weight': 1.0},
    ]
    result = label_propagation(edges)
    assert set(result.keys()) == {'x', 'y', 'z'}
    # 'w' was never mentioned in edges -> not present
    assert 'w' not in result


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


def test_determinism():
    """Same seed on same input produces identical labels."""
    from community_detect import label_propagation

    edges = [
        {'source': 'a', 'target': 'b', 'weight': 1.0},
        {'source': 'b', 'target': 'c', 'weight': 1.0},
        {'source': 'c', 'target': 'a', 'weight': 0.5},
        {'source': 'd', 'target': 'e', 'weight': 1.0},
        {'source': 'e', 'target': 'f', 'weight': 1.0},
        {'source': 'f', 'target': 'd', 'weight': 0.5},
        {'source': 'c', 'target': 'd', 'weight': 0.2},
    ]
    r1 = label_propagation(edges, seed=42)
    r2 = label_propagation(edges, seed=42)
    assert r1 == r2


def test_label_clusters_by_directory():
    """Cluster where most files share a directory gets that directory as label."""
    from community_detect import label_clusters

    clusters = {
        0: {'nodes': ['src/hurricane/service.ts', 'src/hurricane/controller.ts',
                       'src/hurricane/dto.ts', 'src/shared/utils.ts']},
    }
    file_data = {
        'src/hurricane/service.ts': {'symbols': ['HurricaneService', 'fetchWeather']},
        'src/hurricane/controller.ts': {'symbols': ['HurricaneController']},
        'src/hurricane/dto.ts': {'symbols': ['HurricaneDto', 'HurricaneAlert']},
        'src/shared/utils.ts': {'symbols': ['formatDate']},
    }
    labels = label_clusters(clusters, file_data)
    assert 'hurricane' in labels[0].lower()


def test_label_clusters_by_symbols():
    """Cluster without clear directory → use top symbol names."""
    from community_detect import label_clusters

    clusters = {
        0: {'nodes': ['src/auth/login.ts', 'src/middleware/jwt.ts', 'src/routes/auth.ts']},
    }
    file_data = {
        'src/auth/login.ts': {'symbols': ['loginUser', 'validateCredentials']},
        'src/middleware/jwt.ts': {'symbols': ['verifyJwt', 'createToken']},
        'src/routes/auth.ts': {'symbols': ['authRouter']},
    }
    labels = label_clusters(clusters, file_data)
    assert len(labels[0]) > 0  # should have a meaningful name
