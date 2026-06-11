#!/usr/bin/env python3
"""Recall-gate readiness harness for the turbovec semantic backend (cb_vec).

Builds a TurboVec 4-bit IdMapIndex over seeded synthetic vectors (or real
corpus vectors), retrieves an over-fetched candidate pool, exact-rescores it
in float64, and gates measured recall@k against the exact float cosine
baseline (queries are never quantized — asymmetric, like cb_vec.search).
When turbovec is unavailable (or CE_DISABLE_TURBOVEC=1) the numpy
brute-force tier runs instead, which is exact by definition (recall 1.0);
pass --require-turbovec to make that an error instead of a green run.

Clones the shrink-vector-store/scripts/quantize_embeddings.py gate pattern
(that skill is not touched). Dependency: numpy; turbovec optional.

Gated metrics (each must be >= 1.0 - --recall-tolerance):
  pool      pre-rescore candidate-pool recall@k — the binding metric,
            it upper-bounds what exact rescore can recover
  rescored  final top-k recall@k after exact float64 rescore
  allowlist rescored recall@k restricted to a seeded 25%% id subset
            (the provenance-scoped search the scribes pipeline needs)

Exit codes (mirrors quantize_embeddings.py, must be preserved):
  0  success, recall gate passed
  2  bad arguments / unmet precondition (shape mismatch, k out of range,
     turbovec unavailable with --require-turbovec, missing corpus cache)
  3  provenance / id-map misalignment (--ids length != vector count,
     u64 round-trip failure, allowlist leak)
  4  recall gate failed (recall@k < 1.0 - recall_tolerance)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np


def _die(code: int, msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _load_npy(path: str, what: str, **kw) -> np.ndarray:
    """np.load with the exit-2 'bad arguments' contract for missing/corrupt files."""
    try:
        return np.load(path, **kw)
    except (OSError, ValueError) as exc:
        _die(2, f"cannot load {what} from {path}: {exc}")


def _turbovec_index_cls():
    """Call-time engine selection: turbovec iff importable and not disabled."""
    if os.environ.get("CE_DISABLE_TURBOVEC") == "1":
        return None
    try:
        from turbovec import IdMapIndex
        return IdMapIndex
    except ImportError:
        return None


def str_to_u64(eid: str, taken: set[int]) -> int:
    """Deterministic blake2b-64 of the entity id, with collision probe.

    Mirrors the cb_vec.py id-mapping strategy: the probed value is what
    gets persisted, so collisions are detected AND resolved.
    """
    h = int.from_bytes(hashlib.blake2b(eid.encode("utf-8"), digest_size=8).digest(), "big")
    i = 0
    while h in taken:
        i += 1
        probe = f"{eid}#{i}".encode("utf-8")
        h = int.from_bytes(hashlib.blake2b(probe, digest_size=8).digest(), "big")
    return h


def build_id_maps(ids: list[str]) -> tuple[dict[str, int], dict[int, str], int]:
    """str->u64 and u64->str maps; returns (fwd, rev, n_collisions). Exit 3 on
    any internal inconsistency or round-trip failure."""
    fwd: dict[str, int] = {}
    rev: dict[int, str] = {}
    collisions = 0
    taken: set[int] = set()
    for eid in ids:
        u = str_to_u64(eid, taken)
        base = int.from_bytes(hashlib.blake2b(eid.encode("utf-8"), digest_size=8).digest(), "big")
        if u != base:
            collisions += 1
        fwd[eid] = u
        rev[u] = eid
        taken.add(u)
    if len(fwd) != len(ids) or len(rev) != len(ids):
        _die(3, f"id-map misalignment: {len(ids)} ids -> {len(fwd)} fwd / {len(rev)} rev entries")
    for eid in ids:  # str -> u64 -> str round-trip, every row
        if rev[fwd[eid]] != eid:
            _die(3, f"u64 round-trip failed for id {eid!r}")
    return fwd, rev, collisions


def l2_normalize(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms > 0.0, norms, 1.0)


def exact_topk(q64: np.ndarray, v64: np.ndarray, k: int) -> np.ndarray:
    """Ground-truth top-k row indices by exact float cosine (inputs normalized)."""
    return np.argsort(-(q64 @ v64.T), axis=1)[:, :k]


def recall_at_k(pred, truth, k: int) -> float:
    hits = sum(len(set(p[:k]) & set(t[:k])) for p, t in zip(pred, truth))
    return hits / (len(truth) * k)


def pool_recall_at_k(pools, truth, k: int) -> float:
    """Fraction of ground-truth top-k present anywhere in the candidate pool."""
    hits = sum(len(set(t[:k]) & set(p)) for p, t in zip(pools, truth))
    return hits / (len(truth) * k)


def load_corpus_vectors(corpus_dir: str) -> tuple[np.ndarray, list[str]]:
    """Real vectors from the .npy sidecar (when present with its meta) or the
    JSON embedding cache. Entries with missing embeddings or off-modal dims
    are skipped, matching cb_vec.build_from_cache."""
    d = Path(corpus_dir)
    npy, meta = d / ".cb_embed_cache.npy", d / ".cb_embed_cache.meta.json"
    if npy.exists() and meta.exists():
        try:
            rows = json.loads(meta.read_text(encoding="utf-8")).get("rows", [])
        except (OSError, json.JSONDecodeError) as exc:
            _die(2, f"cannot read {meta}: {exc}")
        mat = _load_npy(str(npy), "sidecar matrix").astype(np.float32)
        if len(rows) != len(mat):
            _die(3, f"sidecar misalignment: {len(rows)} meta rows vs {len(mat)} npy rows")
        return mat, [r["id"] for r in rows]
    cache_path = d / ".cb_embed_cache.json"
    if not cache_path.exists():
        _die(2, f"no embedding cache in {corpus_dir} (.cb_embed_cache.json missing)")
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(2, f"cannot read {cache_path}: {exc}")
    dims = [len(e["embedding"]) for e in cache.values() if e.get("embedding")]
    if not dims:
        _die(2, f"embedding cache in {corpus_dir} has no embedded entries")
    modal = max(set(dims), key=dims.count)
    ids = sorted(eid for eid, e in cache.items()
                 if e.get("embedding") and len(e["embedding"]) == modal)
    mat = np.array([cache[eid]["embedding"] for eid in ids], dtype=np.float32)
    return mat, ids


def turbovec_search(index, rev, u64_to_row, q32, q64, v64, k, kprime, allow_u64=None):
    """Retrieval-only turbovec + exact float64 rescore (the cb_vec contract).

    Returns (pools, preds): per-query candidate row-index pools and final
    rescored top-k row indices. Exit 3 if an allowlisted search leaks ids."""
    allow = None
    if allow_u64 is not None:
        allow = np.asarray(sorted(allow_u64), dtype=np.uint64)
        kprime = min(kprime, len(allow))
    _, ids = index.search(q32, kprime, allowlist=allow)
    pools, preds = [], []
    for qi in range(len(q32)):
        cand_u64 = [int(u) for u in ids[qi]]
        if allow_u64 is not None:
            leaked = [u for u in cand_u64 if u not in allow_u64]
            if leaked:
                _die(3, f"allowlist leak: {len(leaked)} returned ids outside the "
                        f"allowlist (e.g. {rev.get(leaked[0], leaked[0])!r})")
        rows = [u64_to_row[u] for u in cand_u64 if u in u64_to_row]
        if len(rows) != len(cand_u64):
            _die(3, "index returned u64 ids absent from the id map")
        sims = q64[qi] @ v64[rows].T
        order = np.argsort(-sims)
        pools.append(rows)
        preds.append([rows[j] for j in order[:k]])
    return pools, preds


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Gate turbovec 4-bit retrieval recall vs the exact float baseline.")
    ap.add_argument("--synthetic", type=int, default=2000,
                    help="number of synthetic DB vectors (default 2000)")
    ap.add_argument("--dim", type=int, default=1024, help="vector dim (default 1024)")
    ap.add_argument("--queries", type=int, default=50,
                    help="number of query vectors (default 50)")
    ap.add_argument("--seed", type=int, default=42, help="np.random.default_rng seed")
    ap.add_argument("--corpus", default=None,
                    help="corpus dir: use real vectors from .cb_embed_cache.json/.npy")
    ap.add_argument("--vectors-npy", "--vectors", dest="vectors_npy", default=None,
                    help=".npy float32 (n, d) DB vector override (real corpus re-runs)")
    ap.add_argument("--queries-npy", dest="queries_npy", default=None,
                    help=".npy float32 (q, d) query vector override")
    ap.add_argument("--ids", default=None,
                    help="optional .npy provenance ids aligned to the DB vectors")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--over-fetch-factor", type=int, default=10,
                    help="candidate pool k' = min(n, max(factor*k, 100))")
    ap.add_argument("--recall-tolerance", type=float, default=0.02,
                    help="max allowed recall@k drop vs exact float (default 2%%)")
    ap.add_argument("--require-turbovec", action="store_true",
                    help="exit 2 instead of running numpy-only when turbovec is absent")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)

    # --- DB vectors + ids ---
    if args.vectors_npy:
        mode = "npy"
        vecs = _load_npy(args.vectors_npy, "--vectors-npy").astype(np.float32)
        if vecs.ndim != 2:
            _die(2, f"--vectors-npy must be 2-D, got shape {vecs.shape}")
        ids = [f"vec:{i:06d}" for i in range(len(vecs))]
    elif args.corpus:
        mode = "corpus"
        vecs, ids = load_corpus_vectors(args.corpus)
    else:
        mode = "synthetic"
        if args.synthetic < 2 or args.dim < 1:
            _die(2, f"bad synthetic spec: n={args.synthetic} dim={args.dim}")
        vecs = rng.standard_normal((args.synthetic, args.dim), dtype=np.float32)
        ids = [f"syn:{i:06d}" for i in range(len(vecs))]
    if args.ids:
        loaded = _load_npy(args.ids, "--ids", allow_pickle=False)
        if len(loaded) != len(vecs):
            _die(3, f"provenance misalignment: {len(loaded)} ids vs {len(vecs)} vectors")
        ids = [str(x) for x in loaded]
    n, d = vecs.shape

    # --- queries (never quantized — R1 asymmetric invariant) ---
    if args.queries_npy:
        queries = _load_npy(args.queries_npy, "--queries-npy").astype(np.float32)
        if queries.ndim != 2 or queries.shape[1] != d:
            _die(2, f"shape mismatch: vectors {vecs.shape}, queries {queries.shape}")
    elif args.queries < 1:
        _die(2, f"--queries must be >= 1 (got {args.queries})")
    elif mode == "synthetic":
        queries = rng.standard_normal((args.queries, d), dtype=np.float32)
    else:  # seeded sample of the DB vectors
        pick = rng.choice(n, size=min(args.queries, n), replace=False)
        queries = vecs[pick].copy()
    if len(queries) == 0:
        _die(2, "need at least one query vector")

    k = args.k
    if not (1 <= k <= n):
        _die(2, f"--k must satisfy 1 <= k <= n (k={k}, n={n})")
    gate = 1.0 - args.recall_tolerance
    kprime = min(n, max(args.over_fetch_factor * k, 100))

    fwd, rev, collisions = build_id_maps(ids)
    u64s = np.array([fwd[eid] for eid in ids], dtype=np.uint64)
    u64_to_row = {int(u): i for i, u in enumerate(u64s)}

    v32 = l2_normalize(vecs)                 # what the index stores
    v64 = v32.astype(np.float64)             # exact-rescore matrix
    q32 = l2_normalize(queries)
    q64 = q32.astype(np.float64)
    truth = exact_topk(q64, v64, k)          # exact float cosine baseline

    # numpy tier sanity: the brute-force fallback is exact by definition.
    numpy_pred = exact_topk(q64, v64, k)
    numpy_rec = recall_at_k(numpy_pred, truth, k)
    assert numpy_rec == 1.0, f"numpy exact tier recall must be 1.0, got {numpy_rec}"

    # seeded allowlist spot-check subset: 25% of ids.
    allow_rows = sorted(rng.choice(n, size=max(k, n // 4), replace=False).tolist())
    allow_u64 = {int(u64s[r]) for r in allow_rows}
    allow_truth = [[allow_rows[j] for j in np.argsort(-(q64[qi] @ v64[allow_rows].T))[:k]]
                   for qi in range(len(q64))]

    index_cls = _turbovec_index_cls()
    if index_cls is None:
        if args.require_turbovec:
            _die(2, "turbovec unavailable (not installed or CE_DISABLE_TURBOVEC=1) "
                    "and --require-turbovec set")
        engine = "numpy"
        pools, preds = [list(t) for t in truth], [list(t) for t in truth]
        allow_preds = allow_truth
    else:
        engine = "turbovec"
        index = index_cls(dim=d, bit_width=4)
        index.add_with_ids(v32, u64s)
        pools, preds = turbovec_search(index, rev, u64_to_row, q32, q64, v64, k, kprime)
        _, allow_preds = turbovec_search(index, rev, u64_to_row, q32, q64, v64, k,
                                         kprime, allow_u64=allow_u64)

    pool_rec = pool_recall_at_k(pools, truth, k)
    rescored_rec = recall_at_k(preds, truth, k)
    allow_rec = recall_at_k(allow_preds, allow_truth, k)

    print(f"engine={engine} bit_width=4 rescore=exact-float64 mode={mode} seed={args.seed}")
    print(f"vectors={n} dim={d} queries={len(queries)} "
          f"float_MB={v32.nbytes / 1e6:.1f} over_fetch_k'={kprime}")
    print(f"idmap: {n} ids, u64 unique, round-trip OK, collisions={collisions}")
    print(f"recall@{k} numpy=1.0000 (sanity: exact tier)")
    print(f"recall@{k} pool={pool_rec:.4f} (gate: >= {gate:.4f}, binding metric)")
    print(f"recall@{k} rescored={rescored_rec:.4f} (gate: >= {gate:.4f})")
    print(f"recall@{k} allowlist={allow_rec:.4f} "
          f"(gate: >= {gate:.4f}, subset n={len(allow_rows)})")

    failed = [(name, r) for name, r in
              (("pool", pool_rec), ("rescored", rescored_rec), ("allowlist", allow_rec))
              if r < gate]
    if failed:
        worst = ", ".join(f"{name}={r:.4f}" for name, r in failed)
        _die(4, f"recall gate FAILED: {worst} < {gate:.4f}. Increase "
                f"--over-fetch-factor or fall back to the numpy tier; do not ship.")
    print("recall gate PASSED")


if __name__ == "__main__":
    main()
