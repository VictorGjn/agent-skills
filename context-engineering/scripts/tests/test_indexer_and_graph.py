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

    def test_root_tokens_reflect_truncated_text_not_whole_file(self):
        # Regression for P1: root stores `text = content[:1000]` but previously
        # counted `tokens = estimate_tokens(content)` (the whole file). On large
        # files this inflated totalTokens multi-fold and caused the packer to
        # demote them under fixed budgets.
        large = ('def f():\n    return 1\n\n' * 2000)  # ~40KB, far above 1000 chars
        tree = index_workspace.parse_code_tree('large.py', large, 'python')
        root_own_tokens = tree['tokens']
        child_tokens = sum(c['totalTokens'] for c in tree['children'])
        self.assertEqual(tree['totalTokens'], root_own_tokens + child_tokens)
        # Root's own tokens must be bounded by the 1000-char truncation
        # (≈ len(text) / 4 + word count). A whole-file estimate would be >> 1000.
        self.assertLess(root_own_tokens, 1000)


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

    def _make_large_index(self, n):
        files = []
        for i in range(n):
            path = f'f{i:02d}.py'
            files.append({
                'path': path, 'tokens': 10, 'tree': {'totalTokens': 10},
                'knowledge_type': 'evidence', 'headings': [],
            })
        return {'files': files}

    def test_top_keyword_winner_never_displaced(self):
        # Strong contract: regardless of graph relevance, the highest-scoring
        # keyword winner is always present in the results.
        index = self._make_large_index(20)

        keyword_scored = [
            {'path': f'f{i:02d}.py', 'relevance': 0.9 - i * 0.05,
             'tokens': 10, 'tree': {'totalTokens': 10},
             'knowledge_type': 'evidence'} for i in range(10)
        ]

        import code_graph
        orig_build = code_graph.build_graph_with_fallback
        orig_traverse = code_graph.traverse_from
        orig_entry = code_graph.find_entry_points
        try:
            code_graph.build_graph_with_fallback = lambda files, graphify_path=None: {}
            code_graph.find_entry_points = lambda scored, threshold=0.3: ['f00.py']
            code_graph.traverse_from = lambda entry_points, graph, **kw: [
                {'path': f'f{i:02d}.py', 'relevance': 0.99, 'reason': 'graph'}
                for i in range(10, 20)
            ]

            results = pack_context.score_with_graph(
                index, query_tokens=[], query_lower='',
                top=10, entry_point_source=keyword_scored,
            )
        finally:
            code_graph.build_graph_with_fallback = orig_build
            code_graph.traverse_from = orig_traverse
            code_graph.find_entry_points = orig_entry

        result_paths = [r['path'] for r in results]
        # Highest-ranked keyword winner always retained.
        self.assertEqual(result_paths[0], 'f00.py')
        # Majority of slots still go to keyword winners (quota <= top // 5 + floor 1).
        kw_paths = {f'f{i:02d}.py' for i in range(10)}
        kept = sum(1 for p in result_paths if p in kw_paths)
        self.assertGreaterEqual(kept, 8)  # 10 - max(1, 10//5) = 8

    def test_graph_only_quota_applied_when_keyword_pool_fills_top(self):
        # P2 regression: previously `keyword_winners[:top]` starved graph-only
        # neighbors in --semantic --graph. Now reserve a small quota for them.
        index = self._make_large_index(20)

        keyword_scored = [
            {'path': f'f{i:02d}.py', 'relevance': 0.9 - i * 0.05,
             'tokens': 10, 'tree': {'totalTokens': 10},
             'knowledge_type': 'evidence'} for i in range(10)
        ]

        import code_graph
        orig_build = code_graph.build_graph_with_fallback
        orig_traverse = code_graph.traverse_from
        orig_entry = code_graph.find_entry_points
        try:
            code_graph.build_graph_with_fallback = lambda files, graphify_path=None: {}
            code_graph.find_entry_points = lambda scored, threshold=0.3: ['f00.py']
            code_graph.traverse_from = lambda entry_points, graph, **kw: [
                {'path': f'f{i:02d}.py', 'relevance': 0.7, 'reason': 'graph'}
                for i in range(10, 15)
            ]

            results = pack_context.score_with_graph(
                index, query_tokens=[], query_lower='',
                top=10, entry_point_source=keyword_scored,
            )
        finally:
            code_graph.build_graph_with_fallback = orig_build
            code_graph.traverse_from = orig_traverse
            code_graph.find_entry_points = orig_entry

        result_paths = [r['path'] for r in results]
        graph_paths = {f'f{i:02d}.py' for i in range(10, 15)}
        # At least one graph-only file appears even though keyword pool fills top.
        self.assertTrue(any(p in graph_paths for p in result_paths))

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
