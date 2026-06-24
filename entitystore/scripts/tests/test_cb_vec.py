"""Unit tests for cb_vec.py — the VectorStore vector backend.

Covers the turbovec IdMapIndex tier, the numpy fallback tier, the
CE_DISABLE_TURBOVEC=1 kill switch, the persisted str<->uint64 id map,
content-hash invalidation via remove+add, batched apply(), allowlist
filtering, and the v2 token-named sidecar save/load lifecycle (staleness,
partial sets, crash ordering, cross-pairing, orphan sweep, path-traversal
refusal, fingerprint-as-data).

Engine-agnostic assertions run TWICE: once in the default mode (turbovec
when installed) and once under CE_DISABLE_TURBOVEC=1 via the subclass at
the bottom, so a single suite run covers both modes. All vectors are
seeded (np.random.default_rng(42)).

Run: python entitystore/scripts/tests/test_cb_vec.py  (or pytest).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import numpy as np  # noqa: E402

import cb_vec  # noqa: E402

try:
    import turbovec  # noqa: F401
    HAVE_TURBOVEC = True
except ImportError:
    HAVE_TURBOVEC = False

DIMS = 32
PROVIDER = {"name": "mistral", "model": "mistral-embed", "dims": DIMS}


def make_cache(n: int, dims: int = DIMS, seed: int = 42) -> dict:
    """Synthetic JSON-cache dict in the .cb_embed_cache.json shape."""
    rng = np.random.default_rng(seed)
    cache = {}
    for i in range(n):
        vec = rng.standard_normal(dims)
        vec /= np.linalg.norm(vec)
        cache[f"concept:e{i:03d}"] = {
            "hash": f"h{i:03d}",
            "identity": f"id: concept:e{i:03d}\nsummary: entity {i}",
            "embedding": [float(x) for x in vec],
            "provider": "mistral",
            "model": "mistral-embed",
            "dims": dims,
        }
    return cache


def write_json_cache(corpus_dir: pathlib.Path, cache: dict) -> None:
    (corpus_dir / cb_vec.JSON_NAME).write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def read_meta(corpus_dir: pathlib.Path) -> dict:
    """The committed sidecar meta — v2 data files are token-named, so
    tests resolve .npy/.tvim through the meta references, never by a
    fixed filename."""
    return json.loads(
        (corpus_dir / cb_vec.META_NAME).read_text(encoding="utf-8"))


@contextlib.contextmanager
def numpy_mode():
    """Force the numpy tier via the kill switch, restoring prior state."""
    prior = os.environ.get("CE_DISABLE_TURBOVEC")
    os.environ["CE_DISABLE_TURBOVEC"] = "1"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("CE_DISABLE_TURBOVEC", None)
        else:
            os.environ["CE_DISABLE_TURBOVEC"] = prior


class VectorStoreTests(unittest.TestCase):
    """Engine-agnostic assertions — re-run under CE_DISABLE_TURBOVEC=1 by
    NumpyModeVectorStoreTests below so one run covers both modes."""

    N = 40

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cb_vec_test_")
        self.dir = pathlib.Path(self.tmp)
        self.cache = make_cache(self.N)
        write_json_cache(self.dir, self.cache)
        # Mirror cb_embed: capture the JSON fingerprint at read/write time
        # and thread it in as data — save() never re-stats the JSON.
        self.fp = cb_vec.json_fingerprint(self.dir)
        self.store = cb_vec.build_from_cache(
            self.cache, PROVIDER, source_fp=self.fp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _qvec(self, eid: str) -> list[float]:
        return self.cache[eid]["embedding"]

    # ── search semantics ─────────────────────────────────────────

    def test_search_self_match_tops_results(self):
        eid = "concept:e005"
        hits = self.store.search(self._qvec(eid), top_k=10)
        self.assertTrue(hits)
        self.assertEqual(hits[0][0], eid)
        self.assertAlmostEqual(hits[0][1], 1.0, places=6)
        scores = [s for _, s in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertLessEqual(len(hits), 10)

    def test_search_min_score_floor(self):
        eid = "concept:e007"
        hits = self.store.search(self._qvec(eid), top_k=20, min_score=0.99)
        self.assertEqual([h[0] for h in hits], [eid])

    def test_search_top_k_cut(self):
        hits = self.store.search(
            self._qvec("concept:e000"), top_k=5, min_score=-1.0)
        self.assertEqual(len(hits), 5)

    def test_search_whole_index_when_allowlist_none(self):
        hits = self.store.search(
            self._qvec("concept:e000"), top_k=self.N, min_score=-1.0)
        self.assertEqual(len(hits), self.N)

    def test_search_rejects_bad_query(self):
        self.assertEqual(self.store.search([0.0] * DIMS, top_k=5), [])
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(
                self.store.search([1.0] * (DIMS + 3), top_k=5), [])
        # a dims mix-up must be loud (stderr), not read as "no matches"
        self.assertIn("shape", err.getvalue())
        self.assertEqual(
            self.store.search(self._qvec("concept:e000"), top_k=0), [])

    def test_over_fetch_k_policy(self):
        """10x top_k, min 100, capped at the allowed population."""
        self.assertEqual(cb_vec.over_fetch_k(1000, 5), 100)
        self.assertEqual(cb_vec.over_fetch_k(1000, 20), 200)
        self.assertEqual(cb_vec.over_fetch_k(50, 20), 50)
        self.assertEqual(cb_vec.over_fetch_k(150, 12), 120)
        self.assertEqual(cb_vec.over_fetch_k(0, 5), 0)

    # ── allowlist filtering ──────────────────────────────────────

    def test_allowlist_restricts_results(self):
        allow = {f"concept:e{i:03d}" for i in (2, 4, 8, 16, 32)}
        hits = self.store.search(
            self._qvec("concept:e004"), top_k=20, allowlist=allow,
            min_score=-1.0)
        self.assertEqual(len(hits), len(allow))
        self.assertTrue(set(h[0] for h in hits) <= allow)
        self.assertEqual(hits[0][0], "concept:e004")

    def test_allowlist_empty_short_circuits(self):
        self.assertEqual(
            self.store.search(
                self._qvec("concept:e000"), top_k=5, allowlist=set()), [])

    def test_allowlist_absent_ids_pre_intersected(self):
        hits = self.store.search(
            self._qvec("concept:e000"), top_k=5,
            allowlist={"concept:does-not-exist", "concept:nope"})
        self.assertEqual(hits, [])
        mixed = {"concept:does-not-exist", "concept:e003"}
        hits = self.store.search(
            self._qvec("concept:e003"), top_k=5, allowlist=mixed,
            min_score=-1.0)
        self.assertEqual([h[0] for h in hits], ["concept:e003"])

    # ── content-hash invalidation: remove + re-add, stable u64 ──

    def test_upsert_hash_change_keeps_u64_new_vector_wins(self):
        eid = "concept:e010"
        u_before = self.store._u64_of[eid]
        rng = np.random.default_rng(99)
        new_vec = rng.standard_normal(DIMS)
        new_vec /= np.linalg.norm(new_vec)
        self.store.upsert(eid, new_vec.tolist(), "h-new", "identity-new")
        self.assertEqual(self.store._u64_of[eid], u_before)
        self.assertEqual(self.store.hashes[eid], "h-new")
        self.assertEqual(self.store.identities[eid], "identity-new")
        hits = self.store.search(new_vec.tolist(), top_k=3)
        self.assertEqual(hits[0][0], eid)
        self.assertAlmostEqual(hits[0][1], 1.0, places=6)
        old_hits = self.store.search(self._qvec(eid), top_k=3, min_score=0.99)
        self.assertNotIn(eid, [h[0] for h in old_hits])

    def test_upsert_new_id_and_remove(self):
        rng = np.random.default_rng(7)
        vec = rng.standard_normal(DIMS)
        vec /= np.linalg.norm(vec)
        self.store.upsert("concept:fresh", vec.tolist(), "hf", "fresh identity")
        hits = self.store.search(vec.tolist(), top_k=3)
        self.assertEqual(hits[0][0], "concept:fresh")
        self.assertTrue(self.store.remove("concept:fresh"))
        self.assertFalse(self.store.remove("concept:fresh"))
        hits = self.store.search(vec.tolist(), top_k=3)
        self.assertNotIn("concept:fresh", [h[0] for h in hits])
        self.assertEqual(len(self.store.ids), self.N)

    def test_upsert_rejects_wrong_dims(self):
        with self.assertRaises(ValueError):
            self.store.upsert("concept:bad", [1.0] * (DIMS - 1), "h", "i")

    # ── batched apply() ──────────────────────────────────────────

    def test_apply_batch_equals_sequential(self):
        """One apply(removals, upserts) call must observably equal the
        same sequence of individual remove()/upsert() calls: same ids,
        same u64 map (stable across remove+re-add and replace), same
        hashes/identities, same search ids AND scores."""
        rng = np.random.default_rng(123)

        def unit() -> list[float]:
            v = rng.standard_normal(DIMS)
            return (v / np.linalg.norm(v)).tolist()

        removals = ["concept:e003", "concept:e007", "concept:absent"]
        upserts = [
            ("concept:new-a", unit(), "ha", "ia"),       # brand new id
            ("concept:e010", unit(), "h-re", "i-re"),    # replace existing
            ("concept:e003", unit(), "h-back", "i-back"),  # re-add removed
        ]
        seq = cb_vec.build_from_cache(self.cache, PROVIDER)
        u64_e003 = seq._u64_of["concept:e003"]
        u64_e010 = seq._u64_of["concept:e010"]
        self.assertTrue(seq.remove("concept:e003"))
        self.assertTrue(seq.remove("concept:e007"))
        self.assertFalse(seq.remove("concept:absent"))
        for up in upserts:
            seq.upsert(*up)
        bat = cb_vec.build_from_cache(self.cache, PROVIDER)
        bat.apply(removals, upserts)

        self.assertEqual(sorted(bat.ids), sorted(seq.ids))
        self.assertEqual(bat._u64_of, seq._u64_of)
        self.assertEqual(bat.hashes, seq.hashes)
        self.assertEqual(bat.identities, seq.identities)
        # u64 stability: re-added and replaced ids keep their u64
        self.assertEqual(bat._u64_of["concept:e003"], u64_e003)
        self.assertEqual(bat._u64_of["concept:e010"], u64_e010)
        self.assertNotIn("concept:e007", bat._u64_of)
        for q in (upserts[0][1], upserts[1][1], upserts[2][1],
                  self._qvec("concept:e000")):
            a = seq.search(q, top_k=self.N + 1, min_score=-1.0)
            b = bat.search(q, top_k=self.N + 1, min_score=-1.0)
            self.assertEqual([h[0] for h in a], [h[0] for h in b])
            for (_, sa), (_, sb) in zip(a, b):
                self.assertAlmostEqual(sa, sb, places=10)

    def test_apply_bad_vector_leaves_store_unmutated(self):
        """A wrong-shape vector anywhere in the batch raises ValueError
        BEFORE any mutation — removals included; search output is
        bit-identical before and after the failed call."""
        rng = np.random.default_rng(321)
        good = rng.standard_normal(DIMS)
        good = (good / np.linalg.norm(good)).tolist()
        q = self._qvec("concept:e000")
        before = self.store.search(q, top_k=self.N, min_score=-1.0)
        ids_before = list(self.store.ids)
        u64_before = dict(self.store._u64_of)
        hashes_before = dict(self.store.hashes)
        identities_before = dict(self.store.identities)
        with self.assertRaises(ValueError):
            self.store.apply(
                ["concept:e003"],
                [("concept:new-good", good, "hg", "ig"),
                 ("concept:bad", [1.0] * (DIMS - 1), "hb", "ib")])
        self.assertEqual(self.store.ids, ids_before)
        self.assertIn("concept:e003", self.store._row_of)  # removal undone
        self.assertNotIn("concept:new-good", self.store._row_of)
        self.assertEqual(self.store._u64_of, u64_before)
        self.assertEqual(self.store.hashes, hashes_before)
        self.assertEqual(self.store.identities, identities_before)
        after = self.store.search(q, top_k=self.N, min_score=-1.0)
        self.assertEqual(before, after)

    # ── save / load round-trip ───────────────────────────────────

    def test_save_load_roundtrip_identical_results(self):
        self.store.save(self.dir)
        loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertIsInstance(loaded.matrix, np.memmap)
        q = self._qvec("concept:e012")
        a = self.store.search(q, top_k=10, min_score=-1.0)
        b = loaded.search(q, top_k=10, min_score=-1.0)
        self.assertEqual([x[0] for x in a], [x[0] for x in b])
        for (_, sa), (_, sb) in zip(a, b):
            self.assertAlmostEqual(sa, sb, places=10)
        self.assertEqual(loaded.hashes, self.store.hashes)
        self.assertEqual(loaded.identities, self.store.identities)
        self.assertEqual(loaded._u64_of, self.store._u64_of)

    def test_load_without_provider_skips_provider_check(self):
        self.store.save(self.dir)
        self.assertIsNotNone(cb_vec.load(self.dir))

    def test_load_missing_or_partial_sidecars(self):
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))
        self.store.save(self.dir)
        meta_p = self.dir / cb_vec.META_NAME
        meta_bytes = meta_p.read_bytes()
        meta_p.unlink()
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))
        meta_p.write_bytes(meta_bytes)
        npy_name = json.loads(meta_bytes.decode("utf-8"))["npy"]
        (self.dir / npy_name).unlink()
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_load_stale_json_cache(self):
        self.store.save(self.dir)
        bigger = dict(self.cache)
        bigger["concept:later"] = {
            "hash": "hx", "identity": "later", "embedding": None,
            "provider": "mistral", "model": "mistral-embed", "dims": DIMS}
        write_json_cache(self.dir, bigger)
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_load_provider_model_dims_mismatch(self):
        self.store.save(self.dir)
        self.assertIsNone(cb_vec.load(
            self.dir, {"name": "openai", "model": "mistral-embed", "dims": DIMS}))
        self.assertIsNone(cb_vec.load(
            self.dir, {"name": "mistral", "model": "other-model", "dims": DIMS}))
        self.assertIsNone(cb_vec.load(
            self.dir, {"name": "mistral", "model": "mistral-embed",
                       "dims": DIMS * 2}))

    def test_load_version_mismatch(self):
        self.store.save(self.dir)
        meta_p = self.dir / cb_vec.META_NAME
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        meta["version"] = 999
        meta_p.write_text(json.dumps(meta), encoding="utf-8")
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_load_duplicate_u64_refused(self):
        self.store.save(self.dir)
        meta_p = self.dir / cb_vec.META_NAME
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        meta["rows"][1]["u64"] = meta["rows"][0]["u64"]
        meta_p.write_text(json.dumps(meta), encoding="utf-8")
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_crash_ordering_meta_written_last(self):
        """v2 crash window: an in-flight save wrote its token-named data
        files but crashed BEFORE the atomic meta replace (the commit
        point). load() must keep serving the OLD complete set — never a
        mixed set — and refuse outright once the JSON cache itself moved
        on (stale fingerprint). A completed save then recovers and sweeps
        the crash orphan."""
        self.store.save(self.dir)
        q = self._qvec("concept:e012")
        saved_hits = self.store.search(q, top_k=10, min_score=-1.0)
        fresh_cache = make_cache(self.N + 5, seed=43)
        fresh = cb_vec.build_from_cache(fresh_cache, PROVIDER)
        # crash window A: new data file on disk, meta still the old commit
        crash_npy = self.dir / f"{cb_vec.SIDECAR_PREFIX}deadbeefdeadbeef.npy"
        with open(crash_npy, "wb") as fh:
            np.save(fh, np.asarray(fresh.matrix, dtype=np.float64))
        loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.ids), self.N)  # OLD set, complete
        self.assertNotEqual(read_meta(self.dir)["npy"], crash_npy.name)
        hits = loaded.search(q, top_k=10, min_score=-1.0)
        self.assertEqual([h[0] for h in hits], [h[0] for h in saved_hits])
        for (_, sa), (_, sb) in zip(hits, saved_hits):
            self.assertAlmostEqual(sa, sb, places=10)
        del loaded  # release the npy memmap (Windows file lock)
        # crash window B: the JSON cache was also rewritten before the
        # crash — the committed meta's fingerprint is now stale: refuse.
        write_json_cache(self.dir, fresh_cache)
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))
        # recovery: a completed save commits the new set, sweeps orphans
        fresh.source_fp = cb_vec.json_fingerprint(self.dir)
        fresh.save(self.dir)
        reloaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(reloaded)
        self.assertEqual(len(reloaded.ids), self.N + 5)
        self.assertFalse(crash_npy.exists())

    def test_load_row_count_mismatch_with_npy(self):
        self.store.save(self.dir)
        with open(self.dir / read_meta(self.dir)["npy"], "wb") as fh:
            np.save(fh, np.asarray(self.store.matrix[: self.N - 3],
                                   dtype=np.float64))
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_concurrent_savers_cannot_cross_pair(self):
        """Two stores saving to the same corpus dir: the surviving meta's
        npy reference must resolve to ITS OWN matrix (token-per-save), so
        a loader sees the last saver's vectors, never a mix; the loser's
        orphaned data files are swept by the next completed save."""
        cache_b = make_cache(self.N, seed=77)  # same ids, different vectors
        store_b = cb_vec.build_from_cache(cache_b, PROVIDER,
                                          source_fp=self.fp)
        self.store.save(self.dir)   # saver A
        store_b.save(self.dir)      # saver B wins (meta replaced last)
        meta = read_meta(self.dir)
        self.assertEqual(
            sorted(p.name for p in self.dir.glob("*.npy")), [meta["npy"]])
        for p in self.dir.glob("*.tvim"):
            self.assertEqual(p.name, meta["tvim"])
        loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertTrue(np.array_equal(np.asarray(loaded.matrix),
                                       np.asarray(store_b.matrix)))
        for eid in ("concept:e000", "concept:e017"):
            hits = loaded.search(cache_b[eid]["embedding"], top_k=3)
            self.assertEqual(hits[0][0], eid)
            self.assertAlmostEqual(hits[0][1], 1.0, places=6)
            # A's vector for the same id must NOT self-match: no mixing
            mix = loaded.search(self._qvec(eid), top_k=3, min_score=0.99)
            self.assertNotIn(eid, [h[0] for h in mix])

    def test_save_never_restats_json(self):
        """save() stamps the source_fp captured when the JSON was read —
        a concurrent JSON rewrite must NOT get our stamp, so load()
        refuses (rebuild) instead of serving stale vectors as fresh."""
        original_fp = self.store.source_fp
        self.assertIsNotNone(original_fp)
        write_json_cache(self.dir, make_cache(self.N + 3, seed=9))
        self.assertNotEqual(cb_vec.json_fingerprint(self.dir), original_fp)
        self.store.save(self.dir)
        self.assertEqual(read_meta(self.dir)["source"], original_fp)
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_unstamped_store_save_refused_by_load(self):
        """source_fp=None saves source=None, which load() treats stale."""
        store = cb_vec.build_from_cache(self.cache, PROVIDER)  # no fp
        store.save(self.dir)
        self.assertIsNone(read_meta(self.dir)["source"])
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_meta_data_file_reference_path_traversal_refused(self):
        """meta is data: an npy/tvim reference must be a plain sidecar
        filename in the corpus dir — relative traversal, absolute paths,
        prefix-less or non-string names are all refused (load -> None)."""
        self.store.save(self.dir)
        meta_p = self.dir / cb_vec.META_NAME
        good = read_meta(self.dir)
        evil_refs = [
            "..\\..\\evil.npy",
            "../evil.npy",
            str(pathlib.Path(self.tmp).parent / "evil.npy"),  # absolute
            f"..\\{cb_vec.SIDECAR_PREFIX}x.npy",
            f"{cb_vec.SIDECAR_PREFIX}tok\\..\\evil.npy",
            f"sub/{cb_vec.SIDECAR_PREFIX}x.npy",
            "evil.npy",  # missing sidecar prefix
            None,
            42,
        ]
        for evil in evil_refs:
            m = dict(good, npy=evil)
            meta_p.write_text(json.dumps(m), encoding="utf-8")
            self.assertIsNone(cb_vec.load(self.dir, PROVIDER),
                              msg=f"npy={evil!r}")
        # the tvim reference is held to the same rule (None stays legal)
        m = dict(good, tvim="..\\..\\evil.tvim")
        meta_p.write_text(json.dumps(m), encoding="utf-8")
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_boundary_tie_broken_by_entity_id(self):
        """Several entities with IDENTICAL vectors straddling the top_k
        boundary: the k-th-boundary tie is broken by entity id, so the
        selected set is deterministic and identical in both tiers (this
        test re-runs under CE_DISABLE_TURBOVEC=1 via the subclass).

        One-hot tied vectors + one-hot query keep the cosine bit-exact:
        BLAS matmul accumulation order is row-position dependent, so
        identical RANDOM rows can rescore ulps apart between the
        full-matrix and pooled-submatrix paths, which would defeat the
        exact-tie detection this test targets."""
        cache = make_cache(12, seed=900)
        one_hot = [0.0] * DIMS
        one_hot[0] = 1.0
        tied_ids = [f"concept:tie-{c}" for c in "fdbace"]  # shuffled order
        for tid in tied_ids:
            cache[tid] = {
                "hash": "h", "identity": tid,
                "embedding": list(one_hot),
                "provider": "mistral", "model": "mistral-embed",
                "dims": DIMS}
        store = cb_vec.build_from_cache(cache, PROVIDER)
        top_k = 3  # 6 exact ties at score 1.0 straddle the k=3 boundary
        hits = store.search(one_hot, top_k=top_k, min_score=-1.0)
        expected = sorted(tied_ids)[:top_k]
        self.assertEqual([h[0] for h in hits], expected)
        for _, s in hits:
            self.assertEqual(s, 1.0)

    # ── id mapping ───────────────────────────────────────────────

    def test_u64_round_trip_every_row(self):
        for eid in self.store.ids:
            u = self.store._u64_of[eid]
            self.assertEqual(self.store._id_of[u], eid)
            self.assertTrue(0 <= u < 2 ** 64)

    def test_u64_persisted_map_is_authoritative(self):
        """load() must take u64s from meta.json, not re-hash."""
        self.store.save(self.dir)
        meta_p = self.dir / cb_vec.META_NAME
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        forced = 1234567890
        victim = meta["rows"][0]["id"]
        meta["rows"][0]["u64"] = forced
        meta_p.write_text(json.dumps(meta), encoding="utf-8")
        # In turbovec mode the forced u64 misaligns with the saved .tvim,
        # which is expected to warn and fall back to a lazy rebuild.
        with contextlib.redirect_stderr(io.StringIO()):
            loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded._u64_of[victim], forced)
        self.assertEqual(loaded._id_of[forced], victim)

    def test_collision_probe_deterministic(self):
        natural = cb_vec._assign_u64("concept:x", {})
        expected_natural = int.from_bytes(
            hashlib.blake2b(b"concept:x", digest_size=8).digest(), "big")
        self.assertEqual(natural, expected_natural)
        self.assertEqual(cb_vec._assign_u64("concept:x", {natural: "concept:x"}),
                         natural)
        probed = cb_vec._assign_u64("concept:x", {natural: "concept:other"})
        self.assertNotEqual(probed, natural)
        self.assertEqual(probed, cb_vec._assign_u64(
            "concept:x", {natural: "concept:other"}))
        expected_probe = int.from_bytes(
            hashlib.blake2b(b"concept:x#1", digest_size=8).digest(), "big")
        self.assertEqual(probed, expected_probe)

    # ── build_from_cache filtering ───────────────────────────────

    def test_build_skips_missing_embedding_and_dim_mismatch(self):
        cache = make_cache(4)
        cache["concept:noemb"] = {
            "hash": "h", "identity": "x", "embedding": None,
            "provider": "mistral", "model": "mistral-embed", "dims": DIMS}
        cache["concept:wrongdims"] = {
            "hash": "h", "identity": "x", "embedding": [0.1] * (DIMS + 1),
            "provider": "mistral", "model": "mistral-embed", "dims": DIMS + 1}
        store = cb_vec.build_from_cache(cache, PROVIDER)
        self.assertEqual(len(store.ids), 4)
        self.assertNotIn("concept:noemb", store.ids)
        self.assertNotIn("concept:wrongdims", store.ids)

    def test_build_skips_foreign_provenance_entries(self):
        """Same-dims entries embedded under another provider/model must be
        skipped, never relabeled with the current provider (the sidecar
        meta would otherwise poison the >10% auto_build self-heal)."""
        cache = make_cache(4)
        swapped = dict(cache["concept:e000"], model="other-model")
        cache["concept:swapped-model"] = swapped
        foreign = dict(cache["concept:e001"], provider="openai")
        cache["concept:swapped-provider"] = foreign
        store = cb_vec.build_from_cache(cache, PROVIDER)
        self.assertEqual(len(store.ids), 4)
        self.assertNotIn("concept:swapped-model", store.ids)
        self.assertNotIn("concept:swapped-provider", store.ids)

    def test_empty_store(self):
        write_json_cache(self.dir, {})
        store = cb_vec.build_from_cache(
            {}, PROVIDER, source_fp=cb_vec.json_fingerprint(self.dir))
        self.assertEqual(store.search([1.0] * DIMS, top_k=5), [])
        store.save(self.dir)
        loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.ids, [])
        self.assertEqual(loaded.search([1.0] * DIMS, top_k=5), [])


class NumpyModeVectorStoreTests(VectorStoreTests):
    """Re-run every engine-agnostic assertion with the kill switch on, so
    the suite passes in BOTH modes in one run (also the only mode on
    machines without turbovec)."""

    def setUp(self):
        self._prior = os.environ.get("CE_DISABLE_TURBOVEC")
        os.environ["CE_DISABLE_TURBOVEC"] = "1"
        super().setUp()

    def tearDown(self):
        super().tearDown()
        if self._prior is None:
            os.environ.pop("CE_DISABLE_TURBOVEC", None)
        else:
            os.environ["CE_DISABLE_TURBOVEC"] = self._prior

    def test_kill_switch_never_builds_index(self):
        self.store.search(self._qvec("concept:e001"), top_k=5)
        self.assertIsNone(self.store._index)
        self.store.save(self.dir)
        self.assertIsNone(read_meta(self.dir)["tvim"])
        self.assertEqual(list(self.dir.glob("*.tvim")), [])


@unittest.skipUnless(HAVE_TURBOVEC, "turbovec not installed")
class TurboVecTests(unittest.TestCase):
    """turbovec-tier specifics: numpy parity, .tvim lifecycle, in-index
    remove+re-add. N=80 keeps the over-fetch pool (min 100) covering the
    whole index, so turbovec-vs-numpy results are deterministic-identical."""

    N = 80

    def setUp(self):
        self._prior = os.environ.get("CE_DISABLE_TURBOVEC")
        os.environ.pop("CE_DISABLE_TURBOVEC", None)
        self.tmp = tempfile.mkdtemp(prefix="cb_vec_tv_")
        self.dir = pathlib.Path(self.tmp)
        self.cache = make_cache(self.N)
        write_json_cache(self.dir, self.cache)
        self.store = cb_vec.build_from_cache(
            self.cache, PROVIDER, source_fp=cb_vec.json_fingerprint(self.dir))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._prior is not None:
            os.environ["CE_DISABLE_TURBOVEC"] = self._prior

    def _qvec(self, eid: str) -> list[float]:
        return self.cache[eid]["embedding"]

    def test_parity_with_numpy_tier(self):
        q = self._qvec("concept:e033")
        tv = self.store.search(q, top_k=10)
        self.assertIsNotNone(self.store._index)
        with numpy_mode():
            np_hits = self.store.search(q, top_k=10)
        self.assertEqual([h[0] for h in tv], [h[0] for h in np_hits])
        for (_, sa), (_, sb) in zip(tv, np_hits):
            self.assertAlmostEqual(sa, sb, places=10)

    def test_parity_with_allowlist(self):
        rng = np.random.default_rng(42)
        allow = set(rng.choice(sorted(self.cache), size=30, replace=False))
        q = self._qvec("concept:e060")
        tv = self.store.search(q, top_k=10, allowlist=allow, min_score=-1.0)
        with numpy_mode():
            np_hits = self.store.search(q, top_k=10, allowlist=allow,
                                        min_score=-1.0)
        self.assertEqual([h[0] for h in tv], [h[0] for h in np_hits])
        for (_, sa), (_, sb) in zip(tv, np_hits):
            self.assertAlmostEqual(sa, sb, places=10)
        self.assertTrue(set(h[0] for h in tv) <= allow)

    def test_kill_switch_flips_at_call_time(self):
        with numpy_mode():
            self.store.search(self._qvec("concept:e001"), top_k=5)
            self.assertIsNone(self.store._index)
        self.store.search(self._qvec("concept:e001"), top_k=5)
        self.assertIsNotNone(self.store._index)

    def test_tvim_sidecar_lifecycle(self):
        self.store.save(self.dir)
        meta = read_meta(self.dir)
        self.assertIsNotNone(meta["tvim"])
        self.assertTrue((self.dir / meta["tvim"]).exists())
        loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertIsNotNone(loaded._index)
        # Release the loaded store's memmap before the next save sweeps
        # the old npy — Windows keeps memmapped files locked.
        del loaded
        with numpy_mode():
            self.store.save(self.dir)
        self.assertIsNone(read_meta(self.dir)["tvim"])
        self.assertEqual(list(self.dir.glob("*.tvim")), [])

    def test_upsert_reuses_index_without_full_rebuild(self):
        self.store.search(self._qvec("concept:e000"), top_k=5)
        index_obj = self.store._index
        self.assertIsNotNone(index_obj)
        eid = "concept:e022"
        rng = np.random.default_rng(5)
        new_vec = rng.standard_normal(DIMS)
        new_vec /= np.linalg.norm(new_vec)
        self.store.upsert(eid, new_vec.tolist(), "h-new", "i-new")
        self.assertIs(self.store._index, index_obj)
        hits = self.store.search(new_vec.tolist(), top_k=3)
        self.assertEqual(hits[0][0], eid)
        self.assertAlmostEqual(hits[0][1], 1.0, places=6)

    def test_corrupt_tvim_falls_back_to_lazy_rebuild(self):
        self.store.save(self.dir)
        (self.dir / read_meta(self.dir)["tvim"]).write_bytes(
            b"not a tvim file")
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):
            loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertIsNone(loaded._index)
        hits = loaded.search(self._qvec("concept:e009"), top_k=3)
        self.assertEqual(hits[0][0], "concept:e009")
        self.assertIsNotNone(loaded._index)

    def test_unsupported_dims_falls_back_to_exact_numpy(self):
        """IdMapIndex requires dims % 8 == 0; a runtime turbovec failure in
        search() must degrade to the exact numpy tier, not crash."""
        dims = 12
        cache = make_cache(10, dims=dims)
        store = cb_vec.build_from_cache(
            cache, {"name": "mistral", "model": "mistral-embed", "dims": dims})
        with contextlib.redirect_stderr(io.StringIO()) as err:
            hits = store.search(cache["concept:e003"]["embedding"], top_k=3)
        self.assertIn("turbovec search failed", err.getvalue())
        self.assertEqual(hits[0][0], "concept:e003")
        self.assertAlmostEqual(hits[0][1], 1.0, places=6)

    def test_partial_set_meta_deleted_tvim_kept(self):
        self.store.save(self.dir)
        (self.dir / cb_vec.META_NAME).unlink()
        self.assertIsNone(cb_vec.load(self.dir, PROVIDER))

    def test_tvim_entry_count_mismatch_rejected(self):
        """A .tvim whose entry count differs from the meta rows must be
        refused even when every meta u64 IS contained in it (extra stale
        u64s would KeyError on every search otherwise)."""
        self.store.save(self.dir)
        meta = read_meta(self.dir)
        # shrink meta rows + npy by one; the saved tvim keeps N entries,
        # so containment of the remaining N-1 u64s still holds
        meta["rows"] = meta["rows"][:-1]
        with open(self.dir / meta["npy"], "wb") as fh:
            np.save(fh, np.asarray(self.store.matrix[:-1],
                                   dtype=np.float64))
        (self.dir / cb_vec.META_NAME).write_text(
            json.dumps(meta), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            loaded = cb_vec.load(self.dir, PROVIDER)
        self.assertIsNotNone(loaded)
        self.assertIsNone(loaded._index)  # lazy in-memory rebuild instead
        self.assertIn("entries", err.getvalue())
        hits = loaded.search(self._qvec("concept:e009"), top_k=3)
        self.assertEqual(hits[0][0], "concept:e009")

    def test_approximate_retrieval_overlap_with_exact_tier(self):
        """N=500 >> over-fetch pool (100): retrieval is genuinely
        approximate. Self-query must still rank itself first, and the
        quantized-pool + exact-rescore results must overlap the exact
        numpy tier >= 9/10 with identical scores for common ids."""
        dims = 64  # dims % 8 == 0 (IdMapIndex requirement)
        n = 500
        cache = make_cache(n, dims=dims, seed=4242)
        provider = {"name": "mistral", "model": "mistral-embed",
                    "dims": dims}
        store = cb_vec.build_from_cache(cache, provider)
        self.assertLess(cb_vec.over_fetch_k(n, 10), n)
        q_eid = "concept:e123"
        q = cache[q_eid]["embedding"]
        tv = store.search(q, top_k=10, min_score=-1.0)
        self.assertEqual(len(tv), 10)
        self.assertEqual(tv[0][0], q_eid)
        self.assertAlmostEqual(tv[0][1], 1.0, places=6)
        with numpy_mode():
            ex = store.search(q, top_k=10, min_score=-1.0)
        self.assertEqual(ex[0][0], q_eid)
        tv_scores, ex_scores = dict(tv), dict(ex)
        common = set(tv_scores) & set(ex_scores)
        self.assertGreaterEqual(len(common), 9)
        for eid in common:
            self.assertAlmostEqual(tv_scores[eid], ex_scores[eid],
                                   places=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
