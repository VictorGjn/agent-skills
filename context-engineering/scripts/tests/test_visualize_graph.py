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


def test_cluster_by_prefix():
    """Symbols with shared naming prefix get grouped into cluster nodes."""
    from visualize_graph import cluster_by_prefix

    nodes = [
        {'id': 'types.ts::type VoyageReport', 'label': 'VoyageReport', 'type': 'type', 'path': 'types.ts', 'tokens': 50, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type VoyageDetail', 'label': 'VoyageDetail', 'type': 'type', 'path': 'types.ts', 'tokens': 40, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type VoyageStatus', 'label': 'VoyageStatus', 'type': 'type', 'path': 'types.ts', 'tokens': 30, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type FleetVessel', 'label': 'FleetVessel', 'type': 'type', 'path': 'types.ts', 'tokens': 60, 'val': 2, 'parent': 'types.ts'},
        {'id': 'types.ts::type FleetStatus', 'label': 'FleetStatus', 'type': 'type', 'path': 'types.ts', 'tokens': 20, 'val': 2, 'parent': 'types.ts'},
        {'id': 'utils.ts::doStuff', 'label': 'doStuff', 'type': 'function', 'path': 'utils.ts', 'tokens': 10, 'val': 2},
    ]

    clustered, cluster_edges = cluster_by_prefix(nodes, min_group=3)

    # Voyage* has 3 members -> should be clustered
    cluster_ids = [n['id'] for n in clustered if n['type'] == 'cluster']
    assert any('Voyage' in c for c in cluster_ids)

    # Fleet* has only 2 members -> should NOT be clustered
    assert not any('Fleet' in c for c in cluster_ids if c.endswith(':cluster'))

    # Individual Voyage* nodes should be marked as clustered, not removed
    clustered_members = [n for n in clustered if n.get('clustered')]
    assert len(clustered_members) == 3
    assert all(m['val'] == 0.3 for m in clustered_members)

    # Cluster edges should connect cluster -> member
    assert len(cluster_edges) >= 3


def test_extract_focused():
    """Focus mode expands one repo and collapses others into bubbles."""
    from visualize_graph import extract_focused, merge_indexes

    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 1, 'totalTokens': 150,
        'files': [{
            'path': 'src/types.ts', 'tokens': 150, 'knowledge_type': 'ground_truth',
            'tree': {
                'title': 'src/types.ts', 'depth': 0, 'tokens': 150, 'totalTokens': 150,
                'text': '', 'firstSentence': '', 'firstParagraph': '',
                'children': [
                    {'title': 'type VoyageReport', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                ],
            },
        }],
        'directories': ['src'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 2, 'totalTokens': 400,
        'files': [
            {'path': 'src/dto.ts', 'tokens': 200, 'knowledge_type': 'ground_truth',
             'tree': {'title': 'src/dto.ts', 'depth': 0, 'tokens': 200, 'totalTokens': 200,
                      'text': '', 'firstSentence': '', 'firstParagraph': '',
                      'children': [
                          {'title': 'type VoyageReport', 'depth': 1, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                      ]}},
            {'path': 'src/service.ts', 'tokens': 200, 'knowledge_type': 'ground_truth',
             'tree': {'title': 'src/service.ts', 'depth': 0, 'tokens': 200, 'totalTokens': 200,
                      'text': '', 'firstSentence': '', 'firstParagraph': '',
                      'children': []}},
        ],
        'directories': ['src'],
    }
    merged = merge_indexes([idx_a, idx_b])
    nodes, file_ids, repo_bubbles = extract_focused(merged, 'fleet', include_symbols=True)

    # Fleet files/symbols should be expanded
    fleet_nodes = [n for n in nodes if n.get('path', '').startswith('fleet/')]
    assert len(fleet_nodes) >= 2  # 1 file + 1 symbol

    # Backend should be a single bubble
    assert 'backend' in repo_bubbles
    bubble = [n for n in nodes if n['id'] == repo_bubbles['backend']]
    assert len(bubble) == 1
    assert bubble[0]['type'] == 'repo'
    assert bubble[0]['fileCount'] == 2
    assert bubble[0]['symbolCount'] == 1  # only VoyageReport child


def test_find_cross_repo_links_focused():
    """Focused cross-repo links connect focus types to repo bubbles."""
    from visualize_graph import extract_focused, find_cross_repo_links_focused, merge_indexes

    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 1, 'totalTokens': 150,
        'files': [{
            'path': 'src/types.ts', 'tokens': 150, 'knowledge_type': 'ground_truth',
            'tree': {
                'title': 'src/types.ts', 'depth': 0, 'tokens': 150, 'totalTokens': 150,
                'text': '', 'firstSentence': '', 'firstParagraph': '',
                'children': [
                    {'title': 'type VoyageReport', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                ],
            },
        }],
        'directories': ['src'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 1, 'totalTokens': 200,
        'files': [{
            'path': 'src/dto.ts', 'tokens': 200, 'knowledge_type': 'ground_truth',
            'tree': {
                'title': 'src/dto.ts', 'depth': 0, 'tokens': 200, 'totalTokens': 200,
                'text': '', 'firstSentence': '', 'firstParagraph': '',
                'children': [
                    {'title': 'type VoyageReport', 'depth': 1, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                    {'title': 'type FleetStatus', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                ],
            },
        }],
        'directories': ['src'],
    }
    merged = merge_indexes([idx_a, idx_b])
    nodes, file_ids, repo_bubbles = extract_focused(merged, 'fleet', include_symbols=True)
    links = find_cross_repo_links_focused(nodes, merged, 'fleet', repo_bubbles)

    # VoyageReport is shared -> 1 link to backend bubble
    assert len(links) == 1
    assert links[0]['kind'] == 'shared_type'
    assert links[0]['target'] == repo_bubbles['backend']
    assert links[0]['label'] == 'VoyageReport'


def test_full_pipeline_multi_index():
    """End-to-end: merge two indexes, extract nodes, cluster, cross-link, generate HTML."""
    from visualize_graph import (
        merge_indexes, extract_nodes, find_cross_repo_links,
        cluster_by_prefix, generate_html, score_for_overlay,
    )

    # Two mini repos with shared DTOs
    idx_a = {
        'root': '/repos/fleet',
        'totalFiles': 1, 'totalTokens': 300,
        'files': [{
            'path': 'src/types.ts', 'tokens': 300, 'knowledge_type': 'ground_truth',
            'tree': {
                'title': 'src/types.ts', 'depth': 0, 'tokens': 300, 'totalTokens': 300,
                'text': '', 'firstSentence': '', 'firstParagraph': '',
                'children': [
                    {'title': 'type VoyageReport', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                    {'title': 'type VoyageDetail', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                    {'title': 'type VoyageStatus', 'depth': 1, 'tokens': 50, 'totalTokens': 50, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                ],
            },
        }],
        'directories': ['src'],
    }
    idx_b = {
        'root': '/repos/backend',
        'totalFiles': 1, 'totalTokens': 200,
        'files': [{
            'path': 'src/dto.ts', 'tokens': 200, 'knowledge_type': 'ground_truth',
            'tree': {
                'title': 'src/dto.ts', 'depth': 0, 'tokens': 200, 'totalTokens': 200,
                'text': '', 'firstSentence': '', 'firstParagraph': '',
                'children': [
                    {'title': 'type VoyageReport', 'depth': 1, 'tokens': 100, 'totalTokens': 100, 'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''},
                ],
            },
        }],
        'directories': ['src'],
    }

    # Merge
    merged = merge_indexes([idx_a, idx_b])
    assert merged['totalFiles'] == 2

    # Extract nodes
    nodes, file_ids = extract_nodes(merged, include_symbols=True)
    assert len(nodes) >= 6  # 2 files + 4 symbols

    # Cross-repo links
    cross_links = find_cross_repo_links(nodes)
    assert len(cross_links) >= 1  # VoyageReport shared

    # Clustering (Voyage* should cluster in fleet)
    nodes, cluster_edges = cluster_by_prefix(nodes, min_group=3)
    cluster_ids = [n['id'] for n in nodes if n.get('type') == 'cluster']
    assert len(cluster_ids) >= 1

    # Score overlay
    scores = score_for_overlay(merged, 'VoyageReport')
    assert len(scores) >= 1

    # Generate HTML
    all_edges = cross_links + cluster_edges
    html = generate_html(nodes, all_edges, 'Dual Graph', query='VoyageReport', relevance_scores=scores)
    assert 'VoyageReport' in html
    assert len(html) > 1000


def test_max_symbols_caps_symbol_extraction():
    """extract_nodes(max_symbols=N) must emit at most N symbol nodes per file."""
    from visualize_graph import extract_nodes

    # File with 10 symbols of varying token sizes
    children = [
        {'title': f'sym_{i}', 'depth': 1, 'tokens': i * 10, 'totalTokens': i * 10,
         'children': [], 'text': '', 'firstSentence': '', 'firstParagraph': ''}
        for i in range(1, 11)
    ]
    index = {
        'files': [{
            'path': 'big.ts', 'tokens': 1000,
            'tree': {'title': 'big.ts', 'depth': 0, 'tokens': 1000, 'totalTokens': 1000,
                     'children': children, 'text': '', 'firstSentence': '', 'firstParagraph': ''},
        }],
    }

    # Without cap: 1 file + 10 symbols
    nodes_uncapped, _ = extract_nodes(index, include_symbols=True, max_symbols=None)
    assert sum(1 for n in nodes_uncapped if n['type'] != 'file') == 10

    # With cap=3: 1 file + 3 symbols (largest by tokens)
    nodes_capped, _ = extract_nodes(index, include_symbols=True, max_symbols=3)
    sym_nodes = [n for n in nodes_capped if n['type'] != 'file']
    assert len(sym_nodes) == 3
    # Largest tokens = sym_10, sym_9, sym_8
    sym_labels = {n['label'] for n in sym_nodes}
    assert 'sym_10' in sym_labels
    assert 'sym_9' in sym_labels
    assert 'sym_8' in sym_labels


def test_html_escapes_symbol_text_in_detail_panel():
    """Symbol text containing HTML must be escaped before being rendered into the detail panel."""
    from visualize_graph import generate_html

    nodes = [
        {'id': 'evil.ts', 'label': 'evil.ts', 'type': 'file', 'path': 'evil.ts',
         'tokens': 100, 'val': 2,
         'symbols': ['<img src=x onerror=alert(1)>', 'normal_symbol']},
    ]
    html = generate_html(nodes, [], 'XSS Detail')

    # The escapeHtml helper must be present so runtime concatenation is safe.
    assert 'function escapeHtml' in html
    # Detail-panel concatenation paths must call escapeHtml on every symbol slice.
    assert 'escapeHtml(s)' in html
    # Connection rendering must escape both kind and name.
    assert 'escapeHtml(c.kind)' in html
    assert 'escapeHtml(name)' in html


def test_generate_html_escapes_script_breakout():
    """A node label/path containing </script> must not break out of the embedding script tag."""
    from visualize_graph import generate_html, _js_safe_json

    # Raw escape helper must turn `<` into <
    payload = {'evil': '</script><script>alert(1)</script>'}
    serialized = _js_safe_json(payload)
    assert '</script>' not in serialized
    assert '\\u003c' in serialized

    # Full pipeline: count of literal `</script>` in output must equal the count in
    # the empty-data baseline — i.e. no leakage from injected node data.
    safe_html = generate_html([], [], 'Safe', query=None, relevance_scores=None)
    baseline_count = safe_html.count('</script>')

    evil_nodes = [
        {'id': '</script><script>alert(1)</script>',
         'label': '</script>', 'type': 'file', 'path': '</script>',
         'tokens': 1, 'val': 1},
    ]
    evil_html = generate_html(evil_nodes, [], 'Evil', query='</script>',
                              relevance_scores={'</script>': 0.5})
    assert evil_html.count('</script>') == baseline_count
