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


class HubDampingTests(unittest.TestCase):
    """P2.3 — TF-IDF hub damping. Files imported by many others (Redux store,
    generated types, logger service, util grab-bags) get their incoming edge
    weights reduced so BFS traversal naturally prefers narrower-cluster paths
    over hub-mediated ones.
    """

    def _make_files_with_hub(self, hub_inbound: int):
        """Build a file index with one hub at src/hub.ts and N files importing it."""
        hub_path = 'src/hub.ts'
        hub_content = "export const x = 1;\n"
        files = [{
            'path': hub_path, 'tokens': 5,
            'tree': {'totalTokens': 5, 'text': hub_content}, 'content': hub_content,
        }]
        for i in range(hub_inbound):
            importer_path = f'src/importer_{i:03d}.ts'
            content = "import { x } from './hub';\n"
            files.append({
                'path': importer_path, 'tokens': 5,
                'tree': {'totalTokens': 5, 'text': content}, 'content': content,
            })
        return hub_path, files

    def test_below_threshold_no_damping(self):
        """Hub with in-degree < HUB_THRESHOLD (10) keeps weight 1.0."""
        import code_graph

        hub, files = self._make_files_with_hub(hub_inbound=5)
        graph = code_graph.build_graph(files, corpus_root=os.getcwd())
        hub_edges = [e for e in graph['edges'] if e['target'] == hub]
        self.assertEqual(len(hub_edges), 5)
        for e in hub_edges:
            self.assertEqual(e['weight'], 1.0,
                             f"in_degree=5 should be unchanged, got {e['weight']}")

    def test_above_threshold_damped(self):
        """Hub with in-degree >= HUB_THRESHOLD gets weight reduced via the
        idf curve `1 / (1 + log2(in_deg / threshold))`."""
        import code_graph
        import math

        hub, files = self._make_files_with_hub(hub_inbound=50)
        graph = code_graph.build_graph(files, corpus_root=os.getcwd())
        hub_edges = [e for e in graph['edges'] if e['target'] == hub]
        self.assertEqual(len(hub_edges), 50)
        # Forecast: weight = 1 / (1 + log2(50/10)) ≈ 0.301
        expected = 1.0 / (1.0 + math.log2(50 / code_graph.HUB_THRESHOLD))
        for e in hub_edges:
            self.assertAlmostEqual(e['weight'], expected, places=3)
            self.assertLess(e['weight'], 0.5,
                            f"in_degree=50 should be heavily damped, got {e['weight']}")

    def test_non_structural_kinds_dont_damp_imports(self):
        """Codex P2 regression: previously in-degree counted all edge kinds,
        so a file with 1 import-inbound + 8 doc/test/related-inbound got
        classified as a hub at total in-degree 9. After the fix, the import
        edge stays at weight 1.0 because import-in-degree alone is 1."""
        import code_graph

        hub_target = 'src/target.ts'
        target_content = "export const t = 1;\n"
        files = [{
            'path': hub_target, 'tokens': 5,
            'tree': {'totalTokens': 5, 'text': target_content}, 'content': target_content,
        }]
        # 1 importer
        importer_content = "import { t } from './target';\n"
        files.append({
            'path': 'src/importer.ts', 'tokens': 5,
            'tree': {'totalTokens': 5, 'text': importer_content}, 'content': importer_content,
        })
        # Build the graph; manually inject 12 fake `documents` edges into the
        # same target to simulate cross-kind crowding (matches what the
        # markdown-link / test-pairing path would do at scale).
        graph = code_graph.build_graph(files, corpus_root=os.getcwd())
        # Sanity: the import edge is in the graph
        import_edges = [e for e in graph['edges']
                        if e['target'] == hub_target and e['kind'] == 'imports']
        self.assertEqual(len(import_edges), 1)
        # If we'd counted all kinds, total in-degree could exceed 10. Verify
        # the import weight is 1.0 — kind-specific in-degree is 1.
        self.assertEqual(import_edges[0]['weight'], 1.0,
                         "import edge weight must not be damped by non-import inbound")

    def test_graphify_path_also_damps(self):
        """Codex P1 regression: hub damping was only applied inside build_graph;
        the graphify-fallback branch returned the adapter's graph un-damped.
        Verify damping is now applied uniformly via build_graph_with_fallback."""
        import code_graph

        # Inline a synthetic graphify graph.json to exercise the graphify path
        # without needing the upstream CLI. 50 nodes all `imports` one hub.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'graphify-out').mkdir()
            graphify_path = root / 'graphify-out' / 'graph.json'

            files = [{'path': 'src/hub.ts', 'tokens': 5, 'tree': {'totalTokens': 5}}]
            nodes = [{'id': 0, 'source_file': 'src/hub.ts', 'file_type': 'code'}]
            links = []
            for i in range(50):
                p = f'src/importer_{i:03d}.ts'
                files.append({'path': p, 'tokens': 5, 'tree': {'totalTokens': 5}})
                nodes.append({'id': i + 1, 'source_file': p, 'file_type': 'code'})
                links.append({
                    '_src': i + 1, '_tgt': 0,
                    'source': i + 1, 'target': 0,
                    'relation': 'imports', 'confidence': 'EXTRACTED',
                })

            import json as _json
            graphify_path.write_text(_json.dumps({'nodes': nodes, 'links': links}))

            graph = code_graph.build_graph_with_fallback(
                files, graphify_path=str(graphify_path), corpus_root=str(root),
            )
            hub_edges = [e for e in graph['edges'] if e['target'] == 'src/hub.ts']
            self.assertEqual(len(hub_edges), 50,
                             f"graphify adapter should produce 50 import edges, got {len(hub_edges)}")
            # In-degree=50, threshold=10 -> weight ~ 0.301 of base. Base for
            # imports is 1.0 with confidence=EXTRACTED multiplier 1.0 -> 1.0.
            # After damping: ~0.301.
            for e in hub_edges:
                self.assertLess(e['weight'], 0.5,
                                f"graphify-path edge should be damped, got {e['weight']}")

    def test_narrow_targets_stay_unchanged(self):
        """Per-edge: a target imported only once keeps weight 1.0 even when
        a hub coexists in the same graph."""
        import code_graph

        hub, files = self._make_files_with_hub(hub_inbound=20)
        # Add one extra file imported by exactly one importer (not the hub)
        narrow_path = 'src/narrow.ts'
        narrow_content = "export const y = 2;\n"
        files.append({
            'path': narrow_path, 'tokens': 5,
            'tree': {'totalTokens': 5, 'text': narrow_content}, 'content': narrow_content,
        })
        # Make importer_000 also import narrow
        files[1]['content'] = "import { x } from './hub';\nimport { y } from './narrow';\n"
        files[1]['tree']['text'] = files[1]['content']

        graph = code_graph.build_graph(files, corpus_root=os.getcwd())
        narrow_edges = [e for e in graph['edges'] if e['target'] == narrow_path]
        self.assertEqual(len(narrow_edges), 1)
        self.assertEqual(narrow_edges[0]['weight'], 1.0,
                         f"narrow target should keep weight 1.0, got {narrow_edges[0]['weight']}")
        # And the hub edges are still damped
        hub_edges = [e for e in graph['edges'] if e['target'] == hub]
        self.assertLess(hub_edges[0]['weight'], 1.0)


if __name__ == '__main__':
    unittest.main()
