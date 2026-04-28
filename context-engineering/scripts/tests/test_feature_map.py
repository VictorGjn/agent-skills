"""Tests for feature_map pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_pipeline_produces_meta_graph():
    """Index → graph → communities → meta-graph pipeline works end-to-end."""
    from feature_map import build_feature_map

    # Mini index with two feature clusters
    index = {
        'root': '/repos/test',
        'totalFiles': 4,
        'files': [
            # Hurricane feature
            {'path': 'src/hurricane/service.ts', 'tokens': 200,
             'tree': {'title': 'src/hurricane/service.ts', 'depth': 0,
                      'tokens': 200, 'totalTokens': 200, 'text': "import { HurricaneDto } from './dto';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class HurricaneService', 'depth': 1,
                                    'tokens': 150, 'totalTokens': 150, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/hurricane/dto.ts', 'tokens': 100,
             'tree': {'title': 'src/hurricane/dto.ts', 'depth': 0,
                      'tokens': 100, 'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type HurricaneDto', 'depth': 1,
                                    'tokens': 50, 'totalTokens': 50, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            # Voyage feature
            {'path': 'src/voyage/manager.ts', 'tokens': 300,
             'tree': {'title': 'src/voyage/manager.ts', 'depth': 0,
                      'tokens': 300, 'totalTokens': 300, 'text': "import { VoyageDto } from './dto';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class VoyageManager', 'depth': 1,
                                    'tokens': 200, 'totalTokens': 200, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/voyage/dto.ts', 'tokens': 100,
             'tree': {'title': 'src/voyage/dto.ts', 'depth': 0,
                      'tokens': 100, 'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type VoyageDto', 'depth': 1,
                                    'tokens': 50, 'totalTokens': 50, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
        ],
    }
    result = build_feature_map(index)

    assert 'clusters' in result
    assert 'meta_edges' in result
    assert 'cluster_labels' in result
    assert 'node_labels' in result
    assert len(result['clusters']) >= 1  # at least 1 cluster

    for cluster in result['clusters'].values():
        assert 'label' in cluster
        assert 'file_count' in cluster
        assert 'total_tokens' in cluster
        assert 'symbols' in cluster
        assert cluster['total_tokens'] > 0
        assert cluster['file_count'] == len(cluster['nodes'])

    assert len(result['node_labels']) >= 1
    input_paths = {f['path'].replace('\\', '/') for f in index['files']}
    node_label_keys = set(result['node_labels'].keys())
    assert input_paths & node_label_keys, 'input paths should appear as keys in node_labels'


def test_generate_html():
    """generate_html produces valid HTML with D3 graph data."""
    from feature_map import generate_html

    feature_data = {
        'clusters': {
            0: {'label': 'Hurricane', 'nodes': ['a.ts', 'b.ts'],
                'file_count': 2, 'total_tokens': 300, 'internal_edges': 1},
            1: {'label': 'Voyage', 'nodes': ['c.ts', 'd.ts'],
                'file_count': 2, 'total_tokens': 400, 'internal_edges': 1},
        },
        'meta_edges': [{'source': 0, 'target': 1, 'weight': 2}],
        'cluster_labels': {0: 'Hurricane', 1: 'Voyage'},
    }
    html = generate_html(feature_data, 'Test Repo')

    assert '<!DOCTYPE html>' in html
    assert 'd3.js' in html or 'd3@' in html or 'd3.v' in html
    assert 'Hurricane' in html
    assert 'Voyage' in html
    assert '<svg' in html or 'createSvg' in html or 'svg' in html.lower()


def test_html_includes_file_lists():
    """HTML embeds per-cluster file lists for the detail panel."""
    from feature_map import generate_html

    feature_data = {
        'clusters': {
            0: {'label': 'Hurricane', 'nodes': ['src/hurricane/service.ts', 'src/hurricane/dto.ts'],
                'file_count': 2, 'total_tokens': 300, 'internal_edges': 1},
        },
        'meta_edges': [],
        'cluster_labels': {0: 'Hurricane'},
    }
    html = generate_html(feature_data, 'Test')
    assert 'hurricane/service.ts' in html
    assert 'hurricane/dto.ts' in html


def test_multi_repo_clusters():
    """Multi-repo feature map shows per-repo clusters with cross-repo edges."""
    from feature_map import build_feature_map, merge_indexes

    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 2, 'totalTokens': 300,
        'files': [
            {'path': 'src/hurricane/map.tsx', 'tokens': 200,
             'tree': {'title': 'src/hurricane/map.tsx', 'depth': 0, 'tokens': 200,
                      'totalTokens': 200, 'text': "import { HurricaneDto } from './types';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'HurricaneMap', 'depth': 1, 'tokens': 150,
                                    'totalTokens': 150, 'children': [], 'text': '',
                                    'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/hurricane/types.ts', 'tokens': 100,
             'tree': {'title': 'src/hurricane/types.ts', 'depth': 0, 'tokens': 100,
                      'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type HurricaneDto', 'depth': 1, 'tokens': 50,
                                    'totalTokens': 50, 'children': [], 'text': '',
                                    'firstSentence': '', 'firstParagraph': ''}]}},
        ],
        'directories': ['src', 'src/hurricane'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 2, 'totalTokens': 300,
        'files': [
            {'path': 'src/hurricane/service.ts', 'tokens': 200,
             'tree': {'title': 'src/hurricane/service.ts', 'depth': 0, 'tokens': 200,
                      'totalTokens': 200, 'text': "import { HurricaneDto } from './dto';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class HurricaneService', 'depth': 1, 'tokens': 150,
                                    'totalTokens': 150, 'children': [], 'text': '',
                                    'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/hurricane/dto.ts', 'tokens': 100,
             'tree': {'title': 'src/hurricane/dto.ts', 'depth': 0, 'tokens': 100,
                      'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type HurricaneDto', 'depth': 1, 'tokens': 50,
                                    'totalTokens': 50, 'children': [], 'text': '',
                                    'firstSentence': '', 'firstParagraph': ''}]}},
        ],
        'directories': ['src', 'src/hurricane'],
    }
    merged = merge_indexes([idx_a, idx_b])
    result = build_feature_map(merged)

    assert len(result['clusters']) >= 1
    # Check that cluster nodes have repo-prefixed paths
    all_nodes = []
    for c in result['clusters'].values():
        all_nodes.extend(c['nodes'])
    assert any('fleet/' in n for n in all_nodes) and any('backend/' in n for n in all_nodes)


def test_apply_min_cluster_drops_small_and_orphans():
    """_apply_min_cluster drops undersized clusters, their edges, labels, and node_labels."""
    from feature_map import _apply_min_cluster

    feature_data = {
        'clusters': {
            0: {'label': 'Big', 'nodes': ['a.ts', 'b.ts', 'c.ts', 'd.ts', 'e.ts'],
                'file_count': 5, 'total_tokens': 500, 'internal_edges': 4},
            1: {'label': 'Mid', 'nodes': ['f.ts', 'g.ts'],
                'file_count': 2, 'total_tokens': 200, 'internal_edges': 1},
            2: {'label': 'Tiny', 'nodes': ['h.ts'],
                'file_count': 1, 'total_tokens': 50, 'internal_edges': 0},
        },
        'meta_edges': [
            {'source': 0, 'target': 1, 'weight': 3},
            {'source': 1, 'target': 2, 'weight': 1},
        ],
        'cluster_labels': {0: 'Big', 1: 'Mid', 2: 'Tiny'},
        'node_labels': {
            'a.ts': 0, 'b.ts': 0, 'c.ts': 0, 'd.ts': 0, 'e.ts': 0,
            'f.ts': 1, 'g.ts': 1,
            'h.ts': 2,
        },
        'root': '/repos/test',
    }

    result = _apply_min_cluster(feature_data, 2)

    # Tiny cluster dropped, Big and Mid remain
    assert 2 not in result['clusters']
    assert 0 in result['clusters']
    assert 1 in result['clusters']

    # Edge referencing dropped cluster is gone; edge between kept clusters remains
    assert {'source': 0, 'target': 1, 'weight': 3} in result['meta_edges']
    assert all(e.get('target') != 2 and e.get('source') != 2 for e in result['meta_edges'])

    # cluster_labels pruned
    assert 2 not in result['cluster_labels']
    assert result['cluster_labels'][0] == 'Big'
    assert result['cluster_labels'][1] == 'Mid'

    # node_labels no longer references files in the dropped cluster
    assert 'h.ts' not in result['node_labels']
    assert result['node_labels']['a.ts'] == 0
    assert result['node_labels']['f.ts'] == 1

    # Original feature_data is not mutated
    assert 2 in feature_data['clusters']
    assert 'h.ts' in feature_data['node_labels']
    assert len(feature_data['meta_edges']) == 2
    assert feature_data['root'] == '/repos/test'


def test_cluster_symbols_populated():
    """Each cluster exposes a `symbols` list aggregated from file_data, used by the HTML detail panel."""
    from feature_map import build_feature_map

    index = {
        'root': '/repos/test',
        'totalFiles': 4,
        'files': [
            {'path': 'src/hurricane/service.ts', 'tokens': 200,
             'tree': {'title': 'src/hurricane/service.ts', 'depth': 0,
                      'tokens': 200, 'totalTokens': 200,
                      'text': "import { HurricaneDto } from './dto';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class HurricaneService', 'depth': 1,
                                    'tokens': 150, 'totalTokens': 150, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/hurricane/dto.ts', 'tokens': 100,
             'tree': {'title': 'src/hurricane/dto.ts', 'depth': 0,
                      'tokens': 100, 'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type HurricaneDto', 'depth': 1,
                                    'tokens': 50, 'totalTokens': 50, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/voyage/manager.ts', 'tokens': 300,
             'tree': {'title': 'src/voyage/manager.ts', 'depth': 0,
                      'tokens': 300, 'totalTokens': 300,
                      'text': "import { VoyageDto } from './dto';",
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'class VoyageManager', 'depth': 1,
                                    'tokens': 200, 'totalTokens': 200, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
            {'path': 'src/voyage/dto.ts', 'tokens': 100,
             'tree': {'title': 'src/voyage/dto.ts', 'depth': 0,
                      'tokens': 100, 'totalTokens': 100, 'text': '',
                      'firstSentence': '', 'firstParagraph': '',
                      'children': [{'title': 'type VoyageDto', 'depth': 1,
                                    'tokens': 50, 'totalTokens': 50, 'children': [],
                                    'text': '', 'firstSentence': '', 'firstParagraph': ''}]}},
        ],
    }
    result = build_feature_map(index)

    input_symbols = set()
    for f in index['files']:
        for child in f['tree'].get('children', []):
            title = child.get('title')
            if title:
                input_symbols.add(title)

    for cluster in result['clusters'].values():
        assert 'symbols' in cluster
        assert isinstance(cluster['symbols'], list)
        assert len(cluster['symbols']) <= 8

    non_empty = [c for c in result['clusters'].values() if c['symbols']]
    assert non_empty, 'at least one cluster should have non-empty symbols'
    for cluster in non_empty:
        for sym in cluster['symbols']:
            assert sym in input_symbols


def test_generate_html_escapes_script_breakout():
    """A `</script>` in a file path must not break out of the embedded script tag."""
    from feature_map import generate_html

    feature_data = {
        'clusters': {
            0: {'label': 'Evil', 'nodes': ['</script><img src=x>evil.ts'],
                'file_count': 1, 'total_tokens': 10, 'internal_edges': 0},
        },
        'meta_edges': [],
        'cluster_labels': {0: 'Evil'},
    }
    html = generate_html(feature_data, 'Test')

    # The dangerous literal must NOT appear raw in the output.
    assert '</script><img' not in html
    # The escaped form proves the escaping ran.
    assert '\\u003c/script' in html or 'u003c/script' in html


def test_disconnected_files_get_singleton_clusters():
    """Files with no graph edges must still appear in the feature map as singleton clusters."""
    from feature_map import build_feature_map

    # Three files, no imports between them — purely disconnected
    index = {
        'root': '/repos/sparse',
        'totalFiles': 3,
        'files': [
            {'path': 'README.md', 'tokens': 50,
             'tree': {'title': 'README.md', 'depth': 0,
                      'tokens': 50, 'totalTokens': 50, 'text': '',
                      'firstSentence': '', 'firstParagraph': '', 'children': []}},
            {'path': 'utils/orphan.ts', 'tokens': 80,
             'tree': {'title': 'utils/orphan.ts', 'depth': 0,
                      'tokens': 80, 'totalTokens': 80, 'text': '',
                      'firstSentence': '', 'firstParagraph': '', 'children': []}},
            {'path': 'docs/notes.md', 'tokens': 30,
             'tree': {'title': 'docs/notes.md', 'depth': 0,
                      'tokens': 30, 'totalTokens': 30, 'text': '',
                      'firstSentence': '', 'firstParagraph': '', 'children': []}},
        ],
    }
    result = build_feature_map(index)

    # Every file must appear as a labeled node (otherwise it gets dropped from the map)
    expected = {f['path'].replace('\\', '/') for f in index['files']}
    assert expected.issubset(result['node_labels'].keys()), \
        f'disconnected files dropped: missing {expected - set(result["node_labels"].keys())}'

    # Total file_count across clusters must equal input file count
    total_files_in_clusters = sum(c['file_count'] for c in result['clusters'].values())
    assert total_files_in_clusters == len(index['files'])


def test_apply_min_cluster_default_keeps_singletons():
    """Default CLI behaviour (min_cluster=1) must NOT drop singleton clusters
    seeded for disconnected files — otherwise the build-time fix is undone."""
    from feature_map import _apply_min_cluster

    feature_data = {
        'clusters': {
            0: {'label': 'A', 'nodes': ['a.ts', 'b.ts'],
                'file_count': 2, 'total_tokens': 100, 'internal_edges': 1},
            1: {'label': 'B', 'nodes': ['solo.ts'],
                'file_count': 1, 'total_tokens': 50, 'internal_edges': 0},
        },
        'meta_edges': [],
        'cluster_labels': {0: 'A', 1: 'B'},
        'node_labels': {'a.ts': 0, 'b.ts': 0, 'solo.ts': 1},
    }
    kept = _apply_min_cluster(feature_data, 1)
    assert 0 in kept['clusters']
    assert 1 in kept['clusters'], 'singleton cluster must survive default min_cluster=1'
    assert kept['node_labels']['solo.ts'] == 1
