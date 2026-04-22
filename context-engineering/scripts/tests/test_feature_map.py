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
