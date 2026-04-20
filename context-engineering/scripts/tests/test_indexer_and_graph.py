"""Unit tests for the AST code indexer and graph-mode displacement fix.

Run: python3 -m unittest context-engineering.scripts.tests.test_indexer_and_graph
 or: cd context-engineering/scripts && python3 -m unittest tests.test_indexer_and_graph
"""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import index_workspace
import pack_context


PY_SAMPLE = '''"""Sample module."""

import os

def alpha(x, y):
    """Add two numbers."""
    return x + y

class Beta:
    """Holds state."""
    def gamma(self, z):
        return z * 2
'''


class ParseCodeTreeTests(unittest.TestCase):
    def test_extracts_symbols_as_children(self):
        tree = index_workspace.parse_code_tree('sample.py', PY_SAMPLE, 'python')
        titles = [c['title'] for c in tree['children']]
        # alpha is a function — title should be just the name.
        # Beta is a class — title should be "class Beta".
        self.assertIn('alpha', titles)
        self.assertTrue(any(t == 'class Beta' or t.endswith('Beta') for t in titles))

    def test_root_metadata(self):
        tree = index_workspace.parse_code_tree('sample.py', PY_SAMPLE, 'python')
        self.assertEqual(tree['title'], 'sample.py')
        self.assertEqual(tree['depth'], 0)
        self.assertGreater(tree['totalTokens'], 0)

    def test_no_exported_dead_branch(self):
        # Regression: previously a no-op `if sym.get('exported')` block reassigned
        # title to itself. Verify titles round-trip cleanly for an exported-style symbol.
        tree = index_workspace.parse_code_tree('sample.py', PY_SAMPLE, 'python')
        for child in tree['children']:
            self.assertIsInstance(child['title'], str)
            self.assertNotEqual(child['title'], '')


class ScannerPruningTests(unittest.TestCase):
    def test_skip_dirs_are_pruned(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'keep.py').write_text('def kept():\n    pass\n')
            skipped = root / 'node_modules' / 'pkg'
            skipped.mkdir(parents=True)
            # A poison file that would blow up if read (binary garbage marker name);
            # the pruning fix means os.walk should never even list it.
            (skipped / 'poison.py').write_text('def should_not_be_indexed():\n    pass\n')

            index = index_workspace.scan_directory(str(root))
            paths = {f['path'] for f in index['files']}
            self.assertIn('keep.py', paths)
            self.assertFalse(any('node_modules' in p for p in paths))


class GraphDisplacementTests(unittest.TestCase):
    """Verify that graph traversal cannot displace keyword winners out of top-N."""

    def _make_index(self):
        files = []
        for path in ('a.py', 'b.py', 'c.py', 'd.py'):
            files.append({
                'path': path, 'tokens': 10, 'tree': {'totalTokens': 10},
                'knowledge_type': 'evidence', 'headings': [],
            })
        return {'files': files}

    def test_keyword_winners_preserved_when_graph_boosts_others(self):
        index = self._make_index()

        # Pretend a/b are strong keyword matches; c/d are graph-only with high relevance.
        keyword_scored = [
            {'path': 'a.py', 'relevance': 0.9, 'tokens': 10, 'tree': {'totalTokens': 10},
             'knowledge_type': 'evidence'},
            {'path': 'b.py', 'relevance': 0.8, 'tokens': 10, 'tree': {'totalTokens': 10},
             'knowledge_type': 'evidence'},
        ]

        # Stub the code_graph functions imported inside score_with_graph.
        import code_graph
        orig_build = code_graph.build_graph_with_fallback
        orig_traverse = code_graph.traverse_from
        orig_entry = code_graph.find_entry_points
        try:
            code_graph.build_graph_with_fallback = lambda files, graphify_path=None: {}
            code_graph.find_entry_points = lambda scored, threshold=0.3: ['a.py']
            code_graph.traverse_from = lambda entry_points, graph, **kw: [
                {'path': 'c.py', 'relevance': 0.95, 'reason': 'graph'},
                {'path': 'd.py', 'relevance': 0.93, 'reason': 'graph'},
            ]

            results = pack_context.score_with_graph(
                index, query_tokens=[], query_lower='',
                top=2, entry_point_source=keyword_scored,
            )
        finally:
            code_graph.build_graph_with_fallback = orig_build
            code_graph.traverse_from = orig_traverse
            code_graph.find_entry_points = orig_entry

        result_paths = [r['path'] for r in results]
        # Both keyword winners must remain in top-2 — no displacement by graph-only files.
        self.assertEqual(result_paths, ['a.py', 'b.py'])

    def test_graph_only_files_fill_remaining_slots(self):
        index = self._make_index()
        keyword_scored = [
            {'path': 'a.py', 'relevance': 0.9, 'tokens': 10, 'tree': {'totalTokens': 10},
             'knowledge_type': 'evidence'},
        ]

        import code_graph
        orig_build = code_graph.build_graph_with_fallback
        orig_traverse = code_graph.traverse_from
        orig_entry = code_graph.find_entry_points
        try:
            code_graph.build_graph_with_fallback = lambda files, graphify_path=None: {}
            code_graph.find_entry_points = lambda scored, threshold=0.3: ['a.py']
            code_graph.traverse_from = lambda entry_points, graph, **kw: [
                {'path': 'c.py', 'relevance': 0.5, 'reason': 'graph'},
            ]

            results = pack_context.score_with_graph(
                index, query_tokens=[], query_lower='',
                top=3, entry_point_source=keyword_scored,
            )
        finally:
            code_graph.build_graph_with_fallback = orig_build
            code_graph.traverse_from = orig_traverse
            code_graph.find_entry_points = orig_entry

        result_paths = [r['path'] for r in results]
        self.assertEqual(result_paths[0], 'a.py')
        self.assertIn('c.py', result_paths)


if __name__ == '__main__':
    unittest.main()
