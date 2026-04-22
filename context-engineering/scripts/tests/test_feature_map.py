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
    assert len(result['clusters']) >= 1  # at least 1 cluster
