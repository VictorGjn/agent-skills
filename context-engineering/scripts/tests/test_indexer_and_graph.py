"""Unit tests for the AST code indexer and graph-mode displacement fix.

Run: python3 -m unittest context-engineering.scripts.tests.test_indexer_and_graph
 or: cd context-engineering/scripts && python3 -m unittest tests.test_indexer_and_graph
"""

import os
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
            code_graph.build_graph_with_fallback = lambda files, graphify_path=None, corpus_root=None: {}
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
            code_graph.build_graph_with_fallback = lambda files, graphify_path=None, corpus_root=None: {}
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
            code_graph.build_graph_with_fallback = lambda files, graphify_path=None, corpus_root=None: {}
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


class ResolveMdLinkTests(unittest.TestCase):
    def test_resolves_with_forward_slash_keys_on_any_platform(self):
        """_resolve_md_link must return forward-slash paths so lookup hits the
        normalized file_index keys regardless of host OS."""
        import code_graph

        file_index = {
            'docs/guide.md': {'path': 'docs/guide.md'},
            'docs/sub/inner.md': {'path': 'docs/sub/inner.md'},
        }
        # Relative link from docs/index.md → guide.md
        resolved = code_graph._resolve_md_link('guide.md', 'docs', file_index)
        self.assertEqual(resolved, 'docs/guide.md')
        self.assertNotIn('\\', resolved)

        # Nested relative link
        nested = code_graph._resolve_md_link('sub/inner.md', 'docs', file_index)
        self.assertEqual(nested, 'docs/sub/inner.md')
        self.assertNotIn('\\', nested)


class TsconfigAliasResolutionTests(unittest.TestCase):
    """Verify TS tsconfig.json paths-alias resolution wires through build_graph.

    Regression target: code_graph.py:302 used to skip every non-relative TS
    import unconditionally, dropping `@/foo`-style aliases. With tsconfig
    resolution wired in, an alias that maps to a real indexed file should
    produce an `imports` edge.
    """

    def test_alias_resolves_to_indexed_file(self):
        import json as _json
        import code_graph

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'tsconfig.json').write_text(_json.dumps({
                'compilerOptions': {
                    'baseUrl': './src',
                    'paths': {'@/*': ['*']},
                },
            }))
            (root / 'src').mkdir()
            foo = "import { bar } from '@/bar';\nexport const x = bar;\n"
            bar = "export const bar = 1;\n"
            (root / 'src' / 'foo.ts').write_text(foo)
            (root / 'src' / 'bar.ts').write_text(bar)

            files = [
                {'path': 'src/foo.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': foo}, 'content': foo},
                {'path': 'src/bar.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': bar}, 'content': bar},
            ]
            graph = code_graph.build_graph(files, corpus_root=str(root))

            edges = [(e['source'], e['target'], e['kind']) for e in graph['edges']]
            self.assertIn(('src/foo.ts', 'src/bar.ts', 'imports'), edges)

    def test_unresolved_alias_emits_diagnostic_edge(self):
        """B2: an import that matches a tsconfig paths pattern but doesn't
        resolve to a real file emits an `unresolved_alias` diagnostic edge,
        rather than being silently dropped. Distinguishes broken aliases
        (worth surfacing) from genuine npm package imports (still silent).
        Engineer-flagged on PR #15 review.
        """
        import json as _json
        import code_graph

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'tsconfig.json').write_text(_json.dumps({
                'compilerOptions': {
                    'baseUrl': './src',
                    'paths': {'@/*': ['*']},
                },
            }))
            (root / 'src').mkdir()
            foo = "import { missing } from '@/does-not-exist';\n"
            (root / 'src' / 'foo.ts').write_text(foo)

            files = [
                {'path': 'src/foo.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': foo}, 'content': foo},
            ]
            graph = code_graph.build_graph(files, corpus_root=str(root))
            unresolved = [e for e in graph['edges'] if e['kind'] == 'unresolved_alias']
            self.assertEqual(len(unresolved), 1, f"expected 1 unresolved_alias edge, got: {graph['edges']}")
            self.assertEqual(unresolved[0]['source'], 'src/foo.ts')
            self.assertEqual(unresolved[0]['target'], '@/does-not-exist')
            # Genuine npm package imports must STILL be silent
            self.assertFalse(any(
                e['target'] == 'react' or e['target'] == 'lodash'
                for e in graph['edges']
            ))

    def test_genuine_package_import_stays_silent(self):
        """B2 corollary: an import that does NOT match any tsconfig paths
        pattern (a real npm package) MUST NOT produce an unresolved_alias
        edge — those would pollute the graph with every `react`/`lodash` etc.
        """
        import json as _json
        import code_graph

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'tsconfig.json').write_text(_json.dumps({
                'compilerOptions': {
                    'baseUrl': './src',
                    'paths': {'@/*': ['*']},  # only @/* is an alias
                },
            }))
            (root / 'src').mkdir()
            foo = "import React from 'react';\nimport _ from 'lodash';\n"
            (root / 'src' / 'foo.ts').write_text(foo)

            files = [
                {'path': 'src/foo.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': foo}, 'content': foo},
            ]
            graph = code_graph.build_graph(files, corpus_root=str(root))
            self.assertEqual(graph['stats']['total_edges'], 0,
                             f"genuine package imports should be silent: {graph['edges']}")

    def test_relative_corpus_root_resolves_against_cwd(self):
        """Codex PR #17 P1 regression: legacy indexes from
        `python index_workspace.py .` stored root='.'. Build_graph must
        treat the relative root as cwd-relative (abspath internally) and
        let the resolver fire — not silently skip TS alias resolution.
        """
        import json as _json
        import code_graph

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'tsconfig.json').write_text(_json.dumps({
                'compilerOptions': {
                    'baseUrl': './src',
                    'paths': {'@/*': ['*']},
                },
            }))
            (root / 'src').mkdir()
            foo = "import { bar } from '@/bar';\n"
            bar = "export const bar = 1;\n"
            (root / 'src' / 'foo.ts').write_text(foo)
            (root / 'src' / 'bar.ts').write_text(bar)

            files = [
                {'path': 'src/foo.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': foo}, 'content': foo},
                {'path': 'src/bar.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': bar}, 'content': bar},
            ]
            # Run build_graph with cwd inside the corpus and corpus_root='.'
            # (matches the legacy-index pattern).
            old_cwd = os.getcwd()
            try:
                os.chdir(str(root))
                graph = code_graph.build_graph(files, corpus_root='.')
            finally:
                os.chdir(old_cwd)

            edges = [(e['source'], e['target'], e['kind']) for e in graph['edges']]
            self.assertIn(
                ('src/foo.ts', 'src/bar.ts', 'imports'),
                edges,
                f"relative corpus_root should still resolve aliases. edges: {edges}",
            )

    def test_logical_corpus_root_skips_resolution(self):
        """B1 corpus_root validity guard: github-indexed corpora write a
        logical 'root' like 'owner/repo@branch'. The resolver MUST NOT walk
        up from cwd looking for a tsconfig — that would pick up unrelated
        tsconfigs and produce false-positive edges.
        """
        import code_graph

        foo = "import { x } from '@/anything';\n"
        files = [
            {'path': 'src/foo.ts', 'tokens': 5,
             'tree': {'totalTokens': 5, 'text': foo}, 'content': foo},
        ]
        # Pass a logical (non-filesystem) root — like index_github_repo writes
        graph = code_graph.build_graph(files, corpus_root='owner/repo@main')
        # No edges from this file — resolver must have no-op'd
        self.assertEqual(graph['stats']['total_edges'], 0)

    def test_inherited_baseurl_anchored_to_parent_config(self):
        """Regression: when baseUrl/paths come from an `extends`-ed parent
        tsconfig, they MUST resolve relative to the parent's directory, not
        the child's. Common monorepo pattern (Nx, Turborepo): packages/<x>/
        tsconfig.json extends ../../tsconfig.base.json which sets
        `baseUrl: "."` — that "." is repo-root, not packages/<x>.
        Surfaced by PR #15 review (chatgpt-codex-connector P1 comment).
        """
        import json as _json
        import code_graph

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Parent at repo-root: defines baseUrl + paths
            (root / 'tsconfig.base.json').write_text(_json.dumps({
                'compilerOptions': {
                    'baseUrl': '.',
                    'paths': {'@shared/*': ['shared/*']},
                },
            }))
            # Child in a subpackage: inherits via extends, no own baseUrl
            (root / 'packages').mkdir()
            (root / 'packages' / 'app').mkdir()
            (root / 'packages' / 'app' / 'tsconfig.json').write_text(_json.dumps({
                'extends': '../../tsconfig.base.json',
            }))
            # Real file at the parent-anchored alias target
            (root / 'shared').mkdir()
            shared_lib = "export const helper = 1;\n"
            (root / 'shared' / 'lib.ts').write_text(shared_lib)
            # Child file imports via the parent-defined alias
            importer = "import { helper } from '@shared/lib';\nexport const x = helper;\n"
            (root / 'packages' / 'app' / 'src').mkdir()
            (root / 'packages' / 'app' / 'src' / 'main.ts').write_text(importer)

            files = [
                {'path': 'packages/app/src/main.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': importer}, 'content': importer},
                {'path': 'shared/lib.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': shared_lib}, 'content': shared_lib},
            ]
            graph = code_graph.build_graph(files, corpus_root=str(root))

            edges = [(e['source'], e['target'], e['kind']) for e in graph['edges']]
            self.assertIn(
                ('packages/app/src/main.ts', 'shared/lib.ts', 'imports'),
                edges,
                f"alias inherited via extends did not resolve. Edges: {edges}",
            )

    def test_no_tsconfig_no_op(self):
        """Corpora without tsconfig.json must be byte-identical to pre-fix behavior:
        non-relative TS imports are silently skipped (current behavior preserved)."""
        import code_graph

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'src').mkdir()
            foo = "import { bar } from '@/bar';\n"
            bar = "export const bar = 1;\n"
            (root / 'src' / 'foo.ts').write_text(foo)
            (root / 'src' / 'bar.ts').write_text(bar)

            files = [
                {'path': 'src/foo.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': foo}, 'content': foo},
                {'path': 'src/bar.ts', 'tokens': 5,
                 'tree': {'totalTokens': 5, 'text': bar}, 'content': bar},
            ]
            graph = code_graph.build_graph(files, corpus_root=str(root))
            # No tsconfig → resolver returns None → existing skip applies → no edge
            self.assertEqual(graph['stats']['total_edges'], 0)


if __name__ == '__main__':
    unittest.main()
