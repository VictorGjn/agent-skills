"""Tests for feature_map pipeline."""

from __future__ import annotations

import json
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


def test_build_feature_map_with_concept_labeler():
    """concept_llm output propagates as concept/description/sub_features per cluster."""
    from feature_map import build_feature_map

    index = {'root': '/repos/test', 'files': [
        {'path': 'src/nav/side.ts', 'tokens': 100,
         'tree': {'title': 'src/nav/side.ts', 'depth': 0, 'tokens': 100,
                   'totalTokens': 100, 'text': '', 'firstSentence': '',
                   'firstParagraph': '',
                   'children': [{'title': 'SideNavbar', 'depth': 1,
                                  'tokens': 50, 'totalTokens': 50,
                                  'children': [], 'text': '',
                                  'firstSentence': '', 'firstParagraph': ''}]}},
    ]}

    def fake_concept_llm(cluster, file_data, current_label, **_):
        return {'concept': 'Navigation', 'description': 'Top + side menu',
                'sub_features': ['Vessel List', 'Profile']}

    result = build_feature_map(index, concept_llm=fake_concept_llm)

    for c in result['clusters'].values():
        assert c['concept'] == 'Navigation'
        assert c['description'] == 'Top + side menu'
        assert c['sub_features'] == ['Vessel List', 'Profile']


def test_concept_fields_default_when_no_llm():
    """Without concept_llm, every cluster still has concept/description/sub_features keys."""
    from feature_map import build_feature_map

    index = {'root': '/repos/test', 'files': [
        {'path': 'a.ts', 'tokens': 10,
         'tree': {'title': 'a.ts', 'depth': 0, 'tokens': 10, 'totalTokens': 10,
                   'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
    ]}
    result = build_feature_map(index)
    for c in result['clusters'].values():
        assert 'concept' in c
        assert 'description' in c
        assert c['description'] == ''
        assert c['sub_features'] == []


def test_build_domain_layer_groups_clusters():
    """Heavily inter-connected clusters fold into one domain; weak links stay separate."""
    from feature_map import build_domain_layer

    feature_data = {
        'clusters': {
            0: {'concept': 'Navigation', 'nodes': ['a'], 'file_count': 1},
            1: {'concept': 'Menus', 'nodes': ['b'], 'file_count': 1},
            2: {'concept': 'Telemetry Ingest', 'nodes': ['c'], 'file_count': 1},
        },
        'meta_edges': [
            {'source': 0, 'target': 1, 'weight': 5},  # Nav <-> Menus strong
            {'source': 2, 'target': 0, 'weight': 1},  # Telemetry barely connected
        ],
    }
    domains = build_domain_layer(feature_data)

    assert domains[0] == domains[1], 'tightly connected clusters must share a domain'
    assert domains[0] != domains[2], 'weak link must not pull cluster 2 in'


def test_build_domain_layer_isolated_clusters_get_own_domain():
    """Clusters with no meta_edges still get a domain id (their own cluster id)."""
    from feature_map import build_domain_layer

    feature_data = {
        'clusters': {7: {'nodes': ['x'], 'file_count': 1}},
        'meta_edges': [],
    }
    domains = build_domain_layer(feature_data)
    assert domains[7] == 7


def test_build_feature_map_attaches_domain_field():
    """Every cluster in the result has a domain field, and result['domains'] exists."""
    from feature_map import build_feature_map

    index = {'root': '/repos/test', 'files': [
        {'path': 'a.ts', 'tokens': 10,
         'tree': {'title': 'a.ts', 'depth': 0, 'tokens': 10, 'totalTokens': 10,
                   'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
        {'path': 'b.ts', 'tokens': 10,
         'tree': {'title': 'b.ts', 'depth': 0, 'tokens': 10, 'totalTokens': 10,
                   'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
    ]}
    result = build_feature_map(index)
    assert 'domains' in result
    for c in result['clusters'].values():
        assert 'domain' in c
    for entry in result['domains'].values():
        assert 'name' in entry
        assert 'cluster_ids' in entry
        assert 'color_index' in entry
        assert 0 <= entry['color_index'] < 16


def test_html_renders_domain_legend_and_subfeatures():
    """Generated HTML embeds the domain registry plus per-cluster concept fields."""
    from feature_map import generate_html

    feature_data = {
        'clusters': {
            0: {'concept': 'Navigation', 'description': 'Menus',
                'sub_features': ['Vessel List', 'Profile'],
                'label': 'Navigation',
                'nodes': ['a.ts'], 'file_count': 1, 'total_tokens': 10,
                'internal_edges': 0, 'domain': 0, 'symbols': []},
            1: {'concept': 'Map Layers', 'description': 'Overlays',
                'sub_features': ['Weather Layers', 'Map Styles'],
                'label': 'Map Layers',
                'nodes': ['b.ts'], 'file_count': 1, 'total_tokens': 10,
                'internal_edges': 0, 'domain': 1, 'symbols': []},
        },
        'meta_edges': [{'source': 0, 'target': 1, 'weight': 3}],
        'cluster_labels': {0: 'Navigation', 1: 'Map Layers'},
        'domains': {
            0: {'name': 'Product UI', 'cluster_ids': [0], 'color_index': 0},
            1: {'name': 'Map Stack', 'cluster_ids': [1], 'color_index': 1},
        },
    }
    html = generate_html(feature_data, 'Test')

    assert 'Product UI' in html
    assert 'Map Stack' in html
    assert 'Vessel List' in html
    assert 'Weather Layers' in html
    assert 'legend' in html.lower()
    assert 'domain-list' in html  # legend container present
    assert 'clusterColor' in html  # domain-driven coloring fn present
    assert 'isCrossDomain' in html  # cross-domain edge styling present


def test_cli_end_to_end_with_fake_llm(tmp_path, monkeypatch, capsys):
    """main() runs end-to-end with a fake concept labeler — no live API call."""
    import feature_map

    index = {
        'root': str(tmp_path / 'fake-repo'),
        'totalFiles': 1, 'totalTokens': 50,
        'files': [{
            'path': 'src/nav/side.ts', 'tokens': 50,
            'tree': {'title': 'src/nav/side.ts', 'depth': 0, 'tokens': 50,
                      'totalTokens': 50, 'text': '', 'firstSentence': '',
                      'firstParagraph': '',
                      'children': [{'title': 'SideNavbar', 'depth': 1,
                                     'tokens': 30, 'totalTokens': 30,
                                     'children': [], 'text': '',
                                     'firstSentence': '', 'firstParagraph': ''}]}},
        ],
    }
    index_path = tmp_path / 'index.json'
    index_path.write_text(json.dumps(index), encoding='utf-8')
    output_path = tmp_path / 'feature-map.html'

    # Substitute the live LLM builder with a deterministic fake.
    def fake_builder(model):
        def call(*, cluster, file_data, current_label, cache_dir=None, **_):
            return {'concept': 'Navigation',
                    'description': 'Top + side menu',
                    'sub_features': ['Vessel List']}
        return call

    monkeypatch.setattr(feature_map, '_build_concept_llm_callable', fake_builder)
    monkeypatch.setattr(sys, 'argv', [
        'feature_map.py',
        '--index', str(index_path),
        '-o', str(output_path),
        '--concept-llm',
        '--concept-cache-dir', str(tmp_path / 'cache'),
    ])

    feature_map.main()

    assert output_path.exists()
    html = output_path.read_text(encoding='utf-8')
    assert 'Navigation' in html
    assert 'Vessel List' in html


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
