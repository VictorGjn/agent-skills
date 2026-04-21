"""Tests for visualize_graph features."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def test_adjacency_built_from_edges():
    """The HTML template builds adj from links. Verify Python edge output has required fields."""
    from visualize_graph import extract_nodes, generate_html

    index = {
        'files': [
            {'path': 'a.ts', 'tokens': 100, 'tree': {'title': 'a.ts', 'depth': 0, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
            {'path': 'b.ts', 'tokens': 80, 'tree': {'title': 'b.ts', 'depth': 0, 'tokens': 80, 'totalTokens': 80, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
        ]
    }
    nodes, file_ids = extract_nodes(index, include_symbols=False)
    edges = [{'source': 'a.ts', 'target': 'b.ts', 'kind': 'imports', 'weight': 1.0}]
    html = generate_html(nodes, edges, 'Test')

    assert '"source": "a.ts"' in html or '"source":"a.ts"' in html
    assert 'highlightNodes' in html


def test_score_for_overlay():
    """score_for_overlay returns {path: relevance} dict."""
    from visualize_graph import score_for_overlay

    index = {
        'files': [
            {'path': 'src/auth/middleware.ts', 'tokens': 200,
             'tree': {'title': 'src/auth/middleware.ts', 'depth': 0, 'tokens': 200, 'totalTokens': 200,
                      'children': [{'title': 'authMiddleware', 'depth': 1, 'tokens': 100, 'totalTokens': 100,
                                    'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}],
                      'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'ground_truth'},
            {'path': 'README.md', 'tokens': 50,
             'tree': {'title': 'README.md', 'depth': 0, 'tokens': 50, 'totalTokens': 50,
                      'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'artifact'},
        ]
    }
    scores = score_for_overlay(index, 'auth middleware')
    assert 'src/auth/middleware.ts' in scores
    assert scores['src/auth/middleware.ts'] > 0


def test_query_flag_embeds_scores():
    """When query is provided, HTML should contain relevanceScores JSON."""
    from visualize_graph import generate_html, extract_nodes

    index = {
        'files': [
            {'path': 'auth.ts', 'tokens': 100, 'tree': {'title': 'auth.ts', 'depth': 0, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}},
        ]
    }
    nodes, _ = extract_nodes(index, include_symbols=False)
    edges = []
    scores = {'auth.ts': 0.75}
    html = generate_html(nodes, edges, 'Test', query='auth', relevance_scores=scores)

    assert 'relevanceScores' in html
    assert '0.75' in html


def test_merge_indexes():
    """merge_indexes combines two indexes with repo-prefixed paths."""
    from visualize_graph import merge_indexes

    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 1, 'totalTokens': 100,
        'files': [
            {'path': 'src/types.ts', 'tokens': 100, 'tree': {'title': 'src/types.ts', 'depth': 0,
             'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'ground_truth'},
        ],
        'directories': ['src'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 1, 'totalTokens': 200,
        'files': [
            {'path': 'src/dto.ts', 'tokens': 200, 'tree': {'title': 'src/dto.ts', 'depth': 0,
             'tokens': 200, 'totalTokens': 200, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
             'knowledge_type': 'ground_truth'},
        ],
        'directories': ['src'],
    }
    merged = merge_indexes([idx_a, idx_b])

    assert merged['totalFiles'] == 2
    paths = [f['path'] for f in merged['files']]
    assert any('fleet/' in p for p in paths)
    assert any('backend/' in p for p in paths)


def test_find_cross_repo_links():
    """Matching symbol names across repos create cross-repo edges."""
    from visualize_graph import find_cross_repo_links

    nodes = [
        {'id': 'fleet/src/types.ts::type VoyageReport', 'label': 'VoyageReport', 'type': 'type', 'path': 'fleet/src/types.ts'},
        {'id': 'backend/src/dto.ts::type VoyageReport', 'label': 'VoyageReport', 'type': 'type', 'path': 'backend/src/dto.ts'},
        {'id': 'fleet/src/types.ts::type FleetStatus', 'label': 'FleetStatus', 'type': 'type', 'path': 'fleet/src/types.ts'},
        {'id': 'fleet/src/utils.ts::doSomething', 'label': 'doSomething', 'type': 'function', 'path': 'fleet/src/utils.ts'},
    ]
    links = find_cross_repo_links(nodes)

    assert len(links) == 1
    link = links[0]
    assert link['kind'] == 'shared_type'
    assert 'fleet' in link['source'] and 'backend' in link['target'] or \
           'backend' in link['source'] and 'fleet' in link['target']
