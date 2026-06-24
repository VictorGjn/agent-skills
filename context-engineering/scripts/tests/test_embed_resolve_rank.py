"""Unit tests for embed_resolve ranking (resolve_semantic / resolve_hybrid).

First direct coverage of the ranking paths, pinning the numpy matmul
vectorization as a pure refactor: every assertion runs with numpy present
AND with embed_resolve.np monkeypatched to None (the pure-Python fallback),
and the outputs must be identical.

Also pins the one deliberate behaviour change: cache entries whose
embedding dims differ from the query's are excluded in BOTH paths
(previously they got zip-truncated garbage scores).

No network: embed_single is stubbed via module attribute patch.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import embed_resolve  # noqa: E402

DIM = 8


def unit_vec(cos: float, dim: int = DIM) -> list:
    """Unit vector whose cosine against the query axis [1,0,...] is `cos`."""
    v = [0.0] * dim
    v[0] = cos
    v[1] = math.sqrt(max(0.0, 1.0 - cos * cos))
    return v


QUERY_VEC = unit_vec(1.0)

# Fixture cache: known cosines against QUERY_VEC.
FIXTURE_CACHE = {
    'docs/auth.md': {'hash': 'h1', 'identity': 'File: docs/auth.md',
                     'embedding': unit_vec(0.9)},
    'src/login.py': {'hash': 'h2', 'identity': 'File: src/login.py',
                     'embedding': unit_vec(0.5)},
    'src/util.py': {'hash': 'h3', 'identity': 'File: src/util.py',
                    'embedding': unit_vec(0.2)},
    'README.md': {'hash': 'h4', 'identity': 'File: README.md',
                  'embedding': unit_vec(0.1)},          # below 0.15 floor
    'bad/dims.md': {'hash': 'h5', 'identity': 'File: bad/dims.md',
                    'embedding': unit_vec(0.99, dim=4)},  # dim mismatch
    'bad/empty.md': {'hash': 'h6', 'identity': 'File: bad/empty.md',
                     'embedding': None},                  # never embedded
}


class RankTestBase(unittest.TestCase):
    """Shared fixtures; subclass flips embed_resolve.np to exercise both paths."""

    use_numpy = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_path = str(Path(self._tmp.name) / 'embeddings.json')
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump(FIXTURE_CACHE, f)
        self._orig_np = embed_resolve.np
        self._orig_embed_single = embed_resolve.embed_single
        if not self.use_numpy:
            embed_resolve.np = None
        embed_resolve.embed_single = lambda text, api_key=None: list(QUERY_VEC)

    def tearDown(self):
        embed_resolve.np = self._orig_np
        embed_resolve.embed_single = self._orig_embed_single
        self._tmp.cleanup()

    # ── resolve_semantic ──

    def test_semantic_shape_and_ordering(self):
        results = embed_resolve.resolve_semantic('auth', cache_path=self.cache_path)
        self.assertEqual([r['path'] for r in results],
                         ['docs/auth.md', 'src/login.py', 'src/util.py'])
        for r in results:
            self.assertEqual(set(r), {'path', 'confidence', 'reason', 'identity'})
            self.assertEqual(r['reason'], 'semantic match')
            self.assertEqual(r['confidence'], round(r['confidence'], 4))
        self.assertEqual(results[0]['confidence'], 0.9)
        self.assertEqual(results[0]['identity'], 'File: docs/auth.md')

    def test_semantic_min_score_floor_before_truncation(self):
        # README.md (cos 0.1) is below the default 0.15 floor — never appears,
        # even with a generous top_k.
        results = embed_resolve.resolve_semantic('q', cache_path=self.cache_path,
                                                 top_k=50)
        self.assertNotIn('README.md', [r['path'] for r in results])
        # Raising the floor drops more.
        results = embed_resolve.resolve_semantic('q', cache_path=self.cache_path,
                                                 min_score=0.45)
        self.assertEqual([r['path'] for r in results],
                         ['docs/auth.md', 'src/login.py'])

    def test_semantic_top_k_truncation(self):
        results = embed_resolve.resolve_semantic('q', cache_path=self.cache_path,
                                                 top_k=1)
        self.assertEqual([r['path'] for r in results], ['docs/auth.md'])

    def test_semantic_excludes_mismatched_dims_and_null_embeddings(self):
        paths = [r['path'] for r in
                 embed_resolve.resolve_semantic('q', cache_path=self.cache_path,
                                                min_score=0.0, top_k=50)]
        self.assertNotIn('bad/dims.md', paths)
        self.assertNotIn('bad/empty.md', paths)

    def test_semantic_empty_cache_returns_empty(self):
        missing = str(Path(self._tmp.name) / 'nope.json')
        self.assertEqual(embed_resolve.resolve_semantic('q', cache_path=missing), [])

    def test_semantic_no_query_embedding_returns_empty(self):
        embed_resolve.embed_single = lambda text, api_key=None: None
        self.assertEqual(
            embed_resolve.resolve_semantic('q', cache_path=self.cache_path), [])

    # ── resolve_hybrid ──

    def test_hybrid_shape_fusion_and_floors(self):
        scored = [
            {'path': 'docs/auth.md', 'relevance': 0.3, 'tokens': 100},  # both
            {'path': 'docs/kw_only.md', 'relevance': 0.7},              # kw only
            {'path': 'docs/weak_kw.md', 'relevance': 0.05},             # < 0.10 floor
        ]
        results = embed_resolve.resolve_hybrid('q', scored,
                                               cache_path=self.cache_path)
        by_path = {r['path']: r for r in results}
        for r in results:
            self.assertEqual(set(r), {'path', 'confidence', 'keyword_score',
                                      'semantic_score', 'reason'})

        # KW_MIN_RELEVANCE floor.
        self.assertNotIn('docs/weak_kw.md', by_path)
        # SEM_MIN_COSINE floor (README.md cos 0.1, no keyword score).
        self.assertNotIn('README.md', by_path)

        # confidence is the RAW match strength: max(kw, sem) when in both
        # rankings, the single raw score otherwise — never the RRF value.
        auth = by_path['docs/auth.md']
        self.assertEqual(auth['confidence'], 0.9)
        self.assertEqual(auth['keyword_score'], 0.3)
        self.assertEqual(auth['semantic_score'], 0.9)
        self.assertTrue(auth['reason'].startswith('hybrid (kw=0.300#'))

        kw_only = by_path['docs/kw_only.md']
        self.assertEqual(kw_only['confidence'], 0.7)
        self.assertEqual(kw_only['reason'], 'keyword only (rel=0.700)')

        sem_only = by_path['src/login.py']
        self.assertEqual(sem_only['confidence'], 0.5)
        self.assertEqual(sem_only['reason'], 'semantic only (cos=0.500)')

        # Sorted by confidence desc.
        confs = [r['confidence'] for r in results]
        self.assertEqual(confs, sorted(confs, reverse=True))

    def test_hybrid_top_k_and_semantic_weight_ignored(self):
        scored = [{'path': f'kw{i}.md', 'relevance': 0.2 + i * 0.01}
                  for i in range(10)]
        results = embed_resolve.resolve_hybrid('q', scored,
                                               cache_path=self.cache_path,
                                               top_k=3, semantic_weight=0.99)
        self.assertEqual(len(results), 3)

    def test_hybrid_empty_cache_keyword_only(self):
        missing = str(Path(self._tmp.name) / 'nope.json')
        results = embed_resolve.resolve_hybrid(
            'q', [{'path': 'a.md', 'relevance': 0.5}], cache_path=missing)
        self.assertEqual(results[0]['path'], 'a.md')
        self.assertEqual(results[0]['reason'], 'keyword only (rel=0.500)')

    def test_hybrid_no_candidates_returns_empty(self):
        missing = str(Path(self._tmp.name) / 'nope.json')
        self.assertEqual(embed_resolve.resolve_hybrid('q', [], cache_path=missing), [])


class RankFallbackTests(RankTestBase):
    """Re-run every assertion through the pure-Python path (np = None)."""

    use_numpy = False

    def test_fallback_is_active(self):
        self.assertIsNone(embed_resolve.np)


class NumpyVsFallbackParityTests(unittest.TestCase):
    """Numpy and pure-Python paths must produce identical outputs (seeded)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_path = str(Path(self._tmp.name) / 'embeddings.json')
        rng = numpy.random.default_rng(42)
        dim = 32
        cache = {}
        for i in range(50):
            v = rng.standard_normal(dim)
            v = v / numpy.linalg.norm(v)
            cache[f'file_{i:02d}.md'] = {
                'hash': f'h{i}', 'identity': f'File: file_{i:02d}.md',
                'embedding': v.tolist(),
            }
        # Poison a few entries with mismatched dims — both paths must
        # exclude them identically.
        for i in (3, 17):
            cache[f'file_{i:02d}.md']['embedding'] = \
                cache[f'file_{i:02d}.md']['embedding'][:dim // 2]
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f)

        q = rng.standard_normal(dim)
        self.query_vec = (q / numpy.linalg.norm(q)).tolist()
        self.scored = [{'path': f'file_{i:02d}.md', 'relevance': float(r)}
                       for i, r in enumerate(rng.uniform(0, 1, 30))]

        self._orig_np = embed_resolve.np
        self._orig_embed_single = embed_resolve.embed_single
        embed_resolve.embed_single = lambda text, api_key=None: list(self.query_vec)

    def tearDown(self):
        embed_resolve.np = self._orig_np
        embed_resolve.embed_single = self._orig_embed_single
        self._tmp.cleanup()

    def _both_paths(self, fn):
        self.assertIsNotNone(embed_resolve.np)
        with_np = fn()
        embed_resolve.np = None
        try:
            without_np = fn()
        finally:
            embed_resolve.np = self._orig_np
        return with_np, without_np

    def test_resolve_semantic_parity(self):
        with_np, without_np = self._both_paths(
            lambda: embed_resolve.resolve_semantic(
                'q', cache_path=self.cache_path, top_k=20, min_score=0.0))
        self.assertEqual(with_np, without_np)
        self.assertTrue(with_np)  # not vacuous
        paths = [r['path'] for r in with_np]
        self.assertNotIn('file_03.md', paths)
        self.assertNotIn('file_17.md', paths)

    def test_resolve_hybrid_parity(self):
        with_np, without_np = self._both_paths(
            lambda: embed_resolve.resolve_hybrid(
                'q', self.scored, cache_path=self.cache_path, top_k=25))
        self.assertEqual(with_np, without_np)
        self.assertTrue(with_np)
        # Mixed-dim entries may only surface via the keyword ranking,
        # never the semantic one.
        for r in with_np:
            if r['path'] in ('file_03.md', 'file_17.md'):
                self.assertEqual(r['semantic_score'], 0.0)
                self.assertTrue(r['reason'].startswith('keyword only'))

    def test_rank_cosine_parity_raw(self):
        cache = embed_resolve.load_cache(self.cache_path)
        with_np, without_np = self._both_paths(
            lambda: embed_resolve._rank_cosine(self.query_vec, cache, -1.0))
        self.assertEqual([p for p, _ in with_np], [p for p, _ in without_np])
        for (_, a), (_, b) in zip(with_np, without_np):
            self.assertAlmostEqual(a, b, places=12)
        self.assertEqual(len(with_np), 48)  # 50 entries minus 2 dim-poisoned


if __name__ == '__main__':
    unittest.main()
