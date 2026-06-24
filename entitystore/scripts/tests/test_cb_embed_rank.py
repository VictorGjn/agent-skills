"""Contract tests for cb_embed.semantic_rank / build_embeddings wiring.

Pins the external contract that cb_engine, cb_mcp and entity-review rely
on — return shape, ordering, min_score floor, candidate-dict filtering,
top_k cut, RuntimeError on missing provider, the >10% auto_build trigger —
across the cb_vec sidecar backend, the pure-Python fallback, and the
JSON-only-cache migration path. The JSON cache must stay byte-identical
when semantic_rank merely builds sidecars from it.

All embedding calls are monkeypatched (no network); vectors are controlled
one-hots plus seeded extras (np.random.default_rng(42)). Engine-agnostic
assertions run TWICE via the CE_DISABLE_TURBOVEC=1 subclass at the bottom.

Run: python entitystore/scripts/tests/test_cb_embed_rank.py  (or pytest).
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import numpy as np  # noqa: E402

import cb_embed  # noqa: E402
import cb_vec  # noqa: E402

DIMS = 8
PROVIDER = {"name": "fake", "base_url": "", "key": "k",
            "model": "fake-embed", "dims": DIMS, "send_dims": False}


def onehot(i: int) -> list[float]:
    v = [0.0] * DIMS
    v[i] = 1.0
    return v


def make_entity(eid: str, summary: str) -> dict:
    kind, slug = eid.split(":", 1)
    return {"id": eid, "kind": kind, "names": [slug],
            "summary": summary, "topics": ["testing"]}


# Query = onehot(0), so each controlled vector's cosine is its first
# component: alpha 1.0, beta 0.7071, epsilon 0.3, gamma 0.2, delta 0.0.
CONTROLLED = {
    "concept:alpha": onehot(0),
    "concept:beta": [1 / math.sqrt(2), 1 / math.sqrt(2)] + [0.0] * (DIMS - 2),
    "concept:epsilon": [0.3, math.sqrt(1 - 0.09)] + [0.0] * (DIMS - 2),
    "concept:gamma": [0.2, math.sqrt(1 - 0.04)] + [0.0] * (DIMS - 2),
    "concept:delta": onehot(1),
}
QUERY_VEC = onehot(0)


class RankContractTests(unittest.TestCase):
    """semantic_rank/build_embeddings contract, default engine mode."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(
            prefix="cb_embed_rank_", ignore_cleanup_errors=True)
        self.corpus = pathlib.Path(self.td.name)
        self.vecs: dict[str, list[float]] = {
            eid: list(v) for eid, v in CONTROLLED.items()}
        rng = np.random.default_rng(42)
        for i in range(5):
            v = rng.standard_normal(DIMS)
            v /= np.linalg.norm(v)
            self.vecs[f"concept:r{i}"] = [float(x) for x in v]
        self.entities = {eid: make_entity(eid, f"summary of {eid}")
                         for eid in self.vecs}
        self.embed_calls: list[list[str]] = []

        self._orig = (cb_embed._resolve_provider, cb_embed.embed_texts,
                      cb_embed.embed_query)
        cb_embed._resolve_provider = lambda: dict(PROVIDER)
        cb_embed.embed_texts = self._fake_embed_texts
        cb_embed.embed_query = lambda q, p: list(QUERY_VEC) if q else None

    def tearDown(self):
        (cb_embed._resolve_provider, cb_embed.embed_texts,
         cb_embed.embed_query) = self._orig
        self.td.cleanup()

    def _fake_embed_texts(self, texts: list[str], provider: dict):
        """Deterministic embedder keyed on the identity's 'id:' line."""
        self.embed_calls.append(list(texts))
        out = []
        for text in texts:
            eid = text.splitlines()[0].removeprefix("id: ")
            out.append(list(self.vecs[eid]))
        return out

    def build(self) -> dict:
        return cb_embed.build_embeddings(self.corpus, self.entities)

    def rank(self, **kw) -> list[dict]:
        return cb_embed.semantic_rank("q", self.corpus, self.entities, **kw)

    # ── return-shape / ordering / floors ─────────────────────────

    def test_shape_ordering_and_scores(self):
        self.build()
        res = self.rank(top_k=20)
        self.assertTrue(res)
        for r in res:
            self.assertEqual(set(r), {"id", "score", "identity"})
            self.assertEqual(r["identity"],
                             cb_embed.entity_identity(self.entities[r["id"]]))
            self.assertEqual(r["score"], round(r["score"], 4))
        scores = [r["score"] for r in res]
        self.assertEqual(scores, sorted(scores, reverse=True))
        by_id = {r["id"]: r["score"] for r in res}
        self.assertEqual(by_id["concept:alpha"], 1.0)
        self.assertEqual(by_id["concept:beta"], 0.7071)
        self.assertEqual(by_id["concept:epsilon"], 0.3)
        self.assertEqual(by_id["concept:gamma"], 0.2)
        self.assertNotIn("concept:delta", by_id)  # 0.0 < 0.15 floor

    def test_min_score_floor(self):
        self.build()
        res = self.rank(min_score=0.25)
        self.assertTrue(all(r["score"] >= 0.25 for r in res))
        ids = {r["id"] for r in res}
        self.assertIn("concept:epsilon", ids)   # 0.3
        self.assertNotIn("concept:gamma", ids)  # 0.2

    def test_top_k_cut(self):
        self.build()
        res = self.rank(top_k=2)
        self.assertEqual([r["id"] for r in res],
                         ["concept:alpha", "concept:beta"])

    def test_candidate_dict_filters_results(self):
        self.build()
        subset = {eid: self.entities[eid]
                  for eid in ("concept:beta", "concept:gamma")}
        res = cb_embed.semantic_rank("q", self.corpus, subset)
        self.assertEqual([r["id"] for r in res],
                         ["concept:beta", "concept:gamma"])

    def test_runtime_error_without_provider(self):
        cb_embed._resolve_provider = lambda: None
        with self.assertRaises(RuntimeError):
            self.rank()
        with self.assertRaises(RuntimeError):
            self.build()

    # ── auto_build trigger ───────────────────────────────────────

    def test_auto_build_over_10_percent(self):
        self.build()  # 10 cached
        self.vecs["concept:zeta"] = [0.5, math.sqrt(0.75)] + [0.0] * (DIMS - 2)
        self.vecs["concept:eta"] = [0.4, math.sqrt(0.84)] + [0.0] * (DIMS - 2)
        entities = dict(self.entities)
        entities["concept:zeta"] = make_entity("concept:zeta", "new one")
        entities["concept:eta"] = make_entity("concept:eta", "newer one")
        self.embed_calls.clear()
        res = cb_embed.semantic_rank("q", self.corpus, entities)  # 2/12 > 10%
        embedded = {t.splitlines()[0].removeprefix("id: ")
                    for call in self.embed_calls for t in call}
        self.assertEqual(embedded, {"concept:zeta", "concept:eta"})
        by_id = {r["id"]: r["score"] for r in res}
        self.assertEqual(by_id["concept:zeta"], 0.5)
        self.assertEqual(by_id["concept:eta"], 0.4)

    def test_no_auto_build_under_threshold(self):
        self.build()  # 10 cached
        self.vecs["concept:zeta"] = [0.5, math.sqrt(0.75)] + [0.0] * (DIMS - 2)
        entities = dict(self.entities)
        entities["concept:zeta"] = make_entity("concept:zeta", "new one")
        self.embed_calls.clear()
        res = cb_embed.semantic_rank("q", self.corpus, entities)  # 1/11 < 10%
        self.assertEqual(self.embed_calls, [])
        self.assertNotIn("concept:zeta", {r["id"] for r in res})

    def test_auto_build_false_never_builds(self):
        res = self.rank(auto_build=False)  # no cache at all
        self.assertEqual(res, [])
        self.assertEqual(self.embed_calls, [])

    def test_empty_candidates_no_results_no_prune(self):
        self.build()
        before = cb_embed._cache_path(self.corpus).read_bytes()
        res = cb_embed.semantic_rank("q", self.corpus, {})
        self.assertEqual(res, [])
        self.assertEqual(cb_embed._cache_path(self.corpus).read_bytes(), before)

    # ── provider compatibility ───────────────────────────────────

    def test_provider_swap_triggers_rebuild(self):
        self.build()
        provider2 = dict(PROVIDER, name="fake2", model="fake2-embed")
        cb_embed._resolve_provider = lambda: dict(provider2)
        self.embed_calls.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = self.rank()
        self.assertNotIn("sidecar sync failed", err.getvalue())
        self.assertTrue(self.embed_calls)  # everything re-embedded
        self.assertEqual(res[0]["id"], "concept:alpha")
        cache = cb_embed.load_cache(self.corpus)
        self.assertTrue(all(e["provider"] == "fake2" for e in cache.values()))
        meta = json.loads((self.corpus / cb_vec.META_NAME)
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["provider"], "fake2")

    def test_same_dims_model_swap_never_poisons_sidecars(self):
        """Regression: a same-dims model swap over a JSON-only cache must
        never stamp the new model over the old vectors in the sidecar meta
        (build_from_cache used to ignore per-entry provenance), and the
        next auto_build=True call must re-embed everything."""
        self._write_json_only_cache()  # built under model fake-embed
        provider2 = dict(PROVIDER, name="fake2", model="fake2-embed")
        cb_embed._resolve_provider = lambda: dict(provider2)
        self.embed_calls.clear()
        res = self.rank(auto_build=False)
        self.assertEqual(res, [])  # no cross-model garbage scores
        self.assertEqual(self.embed_calls, [])
        meta_p = self.corpus / cb_vec.META_NAME
        if meta_p.exists():  # bootstrap store must not claim foreign vectors
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            self.assertEqual(meta["rows"], [])
        res = self.rank(auto_build=True)  # self-heals: full re-embed
        self.assertTrue(self.embed_calls)
        self.assertEqual(res[0]["id"], "concept:alpha")
        cache = cb_embed.load_cache(self.corpus)
        self.assertTrue(all(e["model"] == "fake2-embed" for e in cache.values()))
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        self.assertEqual(meta["model"], "fake2-embed")
        self.assertEqual(len(meta["rows"]), len(self.entities))

    # ── JSON-only cache migration ────────────────────────────────

    def _write_json_only_cache(self) -> bytes:
        cache = {
            eid: {"hash": cb_embed.entity_content_hash(e),
                  "identity": cb_embed.entity_identity(e),
                  "embedding": list(self.vecs[eid]),
                  "provider": PROVIDER["name"], "model": PROVIDER["model"],
                  "dims": DIMS}
            for eid, e in self.entities.items()
        }
        cb_embed.save_cache(self.corpus, cache)
        return cb_embed._cache_path(self.corpus).read_bytes()

    def test_json_only_cache_migrates_byte_identically(self):
        before = self._write_json_only_cache()
        self.assertFalse((self.corpus / cb_vec.META_NAME).exists())
        res = self.rank()
        self.assertEqual(res[0], {"id": "concept:alpha", "score": 1.0,
                                  "identity": cb_embed.entity_identity(
                                      self.entities["concept:alpha"])})
        self.assertTrue((self.corpus / cb_vec.META_NAME).exists())
        meta = self._meta()
        self.assertTrue((self.corpus / meta["npy"]).exists())
        self.assertEqual(meta["tvim"] is not None, cb_vec._turbovec_enabled())
        if meta["tvim"] is not None:
            self.assertTrue((self.corpus / meta["tvim"]).exists())
        self.assertEqual(cb_embed._cache_path(self.corpus).read_bytes(), before)

    def test_second_call_serves_from_sidecars(self):
        self._write_json_only_cache()
        first = self.rank()
        store = cb_vec.load(self.corpus)
        self.assertIsNotNone(store)
        del store  # release the memmap (Windows file lock)
        self.assertEqual(self.rank(), first)

    # ── degraded (pure-Python) parity ────────────────────────────

    def test_pure_python_path_identical(self):
        self.build()
        res_backend = self.rank()
        orig_vec, orig_np = cb_embed.cb_vec, cb_embed.np
        cb_embed.cb_vec = None
        cb_embed.np = None
        try:
            res_pure = self.rank()
        finally:
            cb_embed.cb_vec, cb_embed.np = orig_vec, orig_np
        self.assertEqual(res_backend, res_pure)

    def test_exact_tie_ordering_identical_across_engines(self):
        """Exact-score ties order by id in both the cb_vec backend and the
        pure-Python fallback (insertion order is id-descending here to
        catch a stable-sort-only fallback)."""
        for eid in ("concept:zz-tie", "concept:aa-tie"):
            self.vecs[eid] = list(QUERY_VEC)  # cosine 1.0, tied with alpha
            self.entities[eid] = make_entity(eid, "tied")
        self.build()
        res_backend = self.rank(top_k=20)
        ids = [r["id"] for r in res_backend]
        self.assertEqual(ids[:3],
                         ["concept:aa-tie", "concept:alpha", "concept:zz-tie"])
        orig_vec, orig_np = cb_embed.cb_vec, cb_embed.np
        cb_embed.cb_vec = None
        cb_embed.np = None
        try:
            res_pure = self.rank(top_k=20)
        finally:
            cb_embed.cb_vec, cb_embed.np = orig_vec, orig_np
        self.assertEqual(res_backend, res_pure)

    # ── build_embeddings incremental sidecar sync ────────────────

    def _meta(self) -> dict:
        return json.loads((self.corpus / cb_vec.META_NAME)
                          .read_text(encoding="utf-8"))

    def _meta_rows(self) -> list[dict]:
        return self._meta()["rows"]

    def test_incremental_sync_stable_u64_and_pruning(self):
        self.build()
        u64_before = {r["id"]: r["u64"] for r in self._meta_rows()}
        # hash change for beta (new summary + new vector), gamma deleted,
        # zeta added
        self.entities["concept:beta"]["summary"] = "rewritten summary"
        self.vecs["concept:beta"] = [0.6, 0.8] + [0.0] * (DIMS - 2)
        del self.entities["concept:gamma"]
        self.vecs["concept:zeta"] = [0.5, math.sqrt(0.75)] + [0.0] * (DIMS - 2)
        self.entities["concept:zeta"] = make_entity("concept:zeta", "new one")
        self.build()
        rows = self._meta_rows()
        ids = [r["id"] for r in rows]
        self.assertNotIn("concept:gamma", ids)
        self.assertIn("concept:zeta", ids)
        u64_after = {r["id"]: r["u64"] for r in rows}
        self.assertEqual(u64_after["concept:beta"], u64_before["concept:beta"])
        mat = np.load(self.corpus / self._meta()["npy"])
        np.testing.assert_allclose(mat[ids.index("concept:beta")],
                                   self.vecs["concept:beta"])
        by_id = {r["id"]: r["score"] for r in self.rank()}
        self.assertEqual(by_id["concept:beta"], 0.6)
        self.assertEqual(by_id["concept:zeta"], 0.5)

    def test_force_rebuild_refreshes_sidecar_vectors(self):
        """build_embeddings(force=True) re-embeds unchanged identities; the
        sidecar matrix must pick up the new vectors (regression: the
        incremental hash-keyed sync used to keep the old matrix)."""
        self.build()
        # Provider-side vectors change behind the same model name; content
        # hashes are unchanged.
        self.vecs = {eid: [float(x) for x in np.roll(v, 1)]
                     for eid, v in self.vecs.items()}
        self.embed_calls.clear()
        cache = cb_embed.build_embeddings(self.corpus, self.entities, force=True)
        self.assertTrue(self.embed_calls)
        rows = self._meta_rows()
        mat = np.load(self.corpus / self._meta()["npy"])
        self.assertEqual(len(rows), len(self.entities))
        for i, r in enumerate(rows):
            np.testing.assert_allclose(mat[i], cache[r["id"]]["embedding"])
            np.testing.assert_allclose(mat[i], self.vecs[r["id"]])

    def test_sidecar_failure_never_blocks_json_write(self):
        class _Boom:
            META_NAME = cb_vec.META_NAME

            @staticmethod
            def json_fingerprint(corpus_dir):
                return None

            @staticmethod
            def load(corpus_dir, provider=None):
                raise OSError("sidecar load boom")

            @staticmethod
            def build_from_cache(cache, provider, source_fp=None):
                raise OSError("sidecar build boom")

        orig = cb_embed.cb_vec
        cb_embed.cb_vec = _Boom
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                cache = self.build()
        finally:
            cb_embed.cb_vec = orig
        self.assertIn("sidecar sync failed", err.getvalue())
        on_disk = cb_embed.load_cache(self.corpus)
        self.assertEqual(set(on_disk), set(self.entities))
        self.assertTrue(all(e["embedding"] for e in on_disk.values()))
        self.assertEqual(cache, on_disk)

    def test_wrong_vector_count_writes_nothing(self):
        cb_embed.embed_texts = lambda texts, provider: []
        with self.assertRaises(RuntimeError):
            self.build()
        self.assertFalse(cb_embed._cache_path(self.corpus).exists())


class RankContractTestsNumpyMode(RankContractTests):
    """Re-run every contract assertion with turbovec disabled."""

    def setUp(self):
        self._prev_env = os.environ.get("CE_DISABLE_TURBOVEC")
        os.environ["CE_DISABLE_TURBOVEC"] = "1"
        super().setUp()

    def tearDown(self):
        super().tearDown()
        if self._prev_env is None:
            os.environ.pop("CE_DISABLE_TURBOVEC", None)
        else:
            os.environ["CE_DISABLE_TURBOVEC"] = self._prev_env


class FileLocationImportTests(unittest.TestCase):
    """cb_embed's sibling-import fallback must survive being loaded via
    importlib.util.spec_from_file_location (how entity-review loads
    cb_engine), where the scripts dir is not on sys.path."""

    def test_spec_from_file_location_finds_cb_vec(self):
        code = textwrap.dedent(f"""
            import importlib.util, json, pathlib
            p = pathlib.Path({str(SCRIPTS)!r}) / "cb_embed.py"
            spec = importlib.util.spec_from_file_location("cb_embed_fileloc", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            print(json.dumps({{"cb_vec": mod.cb_vec is not None,
                               "np": mod.np is not None}}))
        """)
        with tempfile.TemporaryDirectory() as td:  # cwd without cb_vec.py
            # stdin=DEVNULL: under pytest's fd capture on Windows the
            # inherited stdin handle is invalid (WinError 6).
            r = subprocess.run([sys.executable, "-c", code], cwd=td,
                               capture_output=True, text=True, timeout=120,
                               stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"cb_vec": True, "np": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
