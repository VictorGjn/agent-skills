#!/usr/bin/env python3
"""Quantize an embedding store (int8 or binary) with an asymmetric float
rescore pass, then gate on measured recall@10 vs the exact float baseline.

Dependency: numpy only. (No FAISS required — brute-force is fine for the
validation sample; production indexing is out of scope for this script.)

Exit codes:
  0  success, recall gate passed
  2  bad arguments / unmet precondition (e.g. binary without rescore)
  3  provenance assertion failed (row/id misalignment)
  4  recall gate failed (drop exceeds tolerance)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def _die(code: int, msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def int8_quantize(vecs: np.ndarray):
    """Per-dimension affine int8. Returns (codes uint8, lo, hi)."""
    lo = vecs.min(axis=0)
    hi = vecs.max(axis=0)
    span = np.where(hi > lo, hi - lo, 1.0)
    codes = np.round((vecs - lo) / span * 255.0).astype(np.uint8)
    return codes, lo, hi


def int8_dequantize(codes: np.ndarray, lo: np.ndarray, hi: np.ndarray):
    span = np.where(hi > lo, hi - lo, 1.0)
    return lo + codes.astype(np.float32) / 255.0 * span


def binary_quantize(vecs: np.ndarray):
    """Sign bits, packed. Returns packed uint8 of shape (n, ceil(d/8))."""
    bits = (vecs >= 0.0).astype(np.uint8)
    return np.packbits(bits, axis=1), vecs.shape[1]


def binary_signs(packed: np.ndarray, dim: int):
    bits = np.unpackbits(packed, axis=1)[:, :dim]
    return bits.astype(np.float32) * 2.0 - 1.0  # {0,1} -> {-1,+1}


def exact_topk(queries, vecs, k):
    """Ground-truth top-k by exact float inner product."""
    return np.argsort(-(queries @ vecs.T), axis=1)[:, :k]


def recall_at_k(pred, truth, k):
    hits = sum(len(set(p[:k]) & set(t[:k])) for p, t in zip(pred, truth))
    return hits / (len(truth) * k)


def main() -> None:
    ap = argparse.ArgumentParser(description="Shrink a vector store with a recall gate.")
    ap.add_argument("--vectors", required=True, help=".npy float32 (n, d) DB vectors")
    ap.add_argument("--queries", required=True, help=".npy float32 (q, d) query vectors")
    ap.add_argument("--precision", choices=("int8", "binary"), default="int8")
    ap.add_argument("--rescore", action="store_true",
                    help="keep an int8 copy and reorder coarse candidates by it")
    ap.add_argument("--matryoshka", type=int, default=0,
                    help="optional: truncate to first N dims before quantizing")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--rescore-factor", type=int, default=10,
                    help="coarse candidate pool = factor * k")
    ap.add_argument("--recall-tolerance", type=float, default=0.02,
                    help="max allowed recall@k drop vs exact float (default 2%%)")
    ap.add_argument("--ids", default=None, help="optional .npy provenance ids to align")
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args()

    vecs = np.load(args.vectors).astype(np.float32)
    queries = np.load(args.queries).astype(np.float32)
    if vecs.ndim != 2 or queries.ndim != 2 or vecs.shape[1] != queries.shape[1]:
        _die(2, f"shape mismatch: vectors {vecs.shape}, queries {queries.shape}")

    # R2: binary is unusable without a rescore pass.
    if args.precision == "binary" and not args.rescore:
        _die(2, "binary precision requires --rescore (recall collapses otherwise)")

    # R3: provenance alignment is a hard precondition.
    ids = None
    if args.ids:
        ids = np.load(args.ids)
        if len(ids) != len(vecs):
            _die(3, f"provenance misalignment: {len(ids)} ids vs {len(vecs)} vectors")

    if args.matryoshka:
        if args.matryoshka >= vecs.shape[1]:
            _die(2, f"--matryoshka {args.matryoshka} >= dim {vecs.shape[1]}")
        vecs = vecs[:, : args.matryoshka]
        queries = queries[:, : args.matryoshka]

    n, d = vecs.shape
    k = args.k
    if not (1 <= k <= n):
        _die(2, f"--k must satisfy 1 <= k <= n (k={k}, n={n})")
    truth = exact_topk(queries, vecs, k)  # exact float baseline = ground truth

    # --- coarse quantized scoring (asymmetric: query stays float) ---
    if args.precision == "int8":
        codes, lo, hi = int8_quantize(vecs)
        coarse = queries @ int8_dequantize(codes, lo, hi).T
        store_bytes = codes.nbytes + lo.nbytes + hi.nbytes
    else:
        packed, dim = binary_quantize(vecs)
        coarse = queries @ binary_signs(packed, dim).T
        store_bytes = packed.nbytes

    pool = min(n, max(k, args.rescore_factor * k))
    cand = np.argpartition(-coarse, pool - 1, axis=1)[:, :pool]

    # --- asymmetric rescore: reorder the coarse pool by a higher-precision copy ---
    if args.rescore:
        if args.precision == "int8":
            rs_codes, rs_lo, rs_hi = codes, lo, hi  # reuse coarse store, no copy
            rescore_bytes = 0
        else:
            rs_codes, rs_lo, rs_hi = int8_quantize(vecs)  # int8 reorder copy
            rescore_bytes = rs_codes.nbytes + rs_lo.nbytes + rs_hi.nbytes
        pred = np.empty((len(queries), k), dtype=np.int64)
        for i in range(len(queries)):
            c = cand[i]
            recon = int8_dequantize(rs_codes[c], rs_lo, rs_hi)
            order = np.argsort(-(queries[i] @ recon.T))
            pred[i] = c[order[:k]]
    else:
        rescore_bytes = 0
        pred = np.take_along_axis(cand, np.argsort(-np.take_along_axis(
            coarse, cand, axis=1), axis=1), axis=1)[:, :k]

    rec = recall_at_k(pred, truth, k)
    float_bytes = vecs.astype(np.float32).nbytes
    total = store_bytes + rescore_bytes
    shrink = float_bytes / total if total else float("inf")

    print(f"precision={args.precision} rescore={args.rescore} "
          f"matryoshka={args.matryoshka or 'off'}")
    print(f"vectors={n} dim={d} float_MB={float_bytes/1e6:.1f} "
          f"quant_MB={total/1e6:.1f} shrink={shrink:.1f}x")
    print(f"recall@{k}={rec:.4f} (gate: >= {1.0 - args.recall_tolerance:.4f})")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.precision == "int8":
        np.save(out / "codes.npy", codes)
        np.save(out / "lo.npy", lo)
        np.save(out / "hi.npy", hi)
    else:
        np.save(out / "packed.npy", packed)
        if args.rescore:  # binary store is unusable without the int8 rescore copy
            np.save(out / "rescore_codes.npy", rs_codes)
            np.save(out / "rescore_lo.npy", rs_lo)
            np.save(out / "rescore_hi.npy", rs_hi)
    if ids is not None:
        np.save(out / "ids.npy", ids)  # provenance carried through unchanged
    print(f"written to {out}/")

    if rec < 1.0 - args.recall_tolerance:
        _die(4, f"recall gate FAILED: {rec:.4f} < {1.0 - args.recall_tolerance:.4f}. "
                f"Use higher precision (int8 if binary; disable matryoshka) and re-run.")
    print("recall gate PASSED")


if __name__ == "__main__":
    main()
