#!/usr/bin/env python3
"""
companybrain — vector backend for cb_embed.semantic_rank.

VectorStore wraps the per-corpus embedding matrix behind a three-tier
engine chain, selected at CALL time (never frozen at import) so the
CE_DISABLE_TURBOVEC=1 kill switch and the tests can flip modes within
one process:

  1. turbovec IdMapIndex (4-bit quantized) — retrieval ONLY: over-fetch
     a candidate pool, then exact float64 rescore against the matrix.
     Quantized 4-bit self-similarity errs by ~1e-2, which would corrupt
     the 0.15 min_score floor if returned raw, so quantized scores are
     always discarded. Active iff `import turbovec` succeeds and
     os.environ CE_DISABLE_TURBOVEC != "1".
  2. numpy brute-force — one normalized float64 matmul over the
     (memmapped) matrix. Exact by definition; identical API and scores.
  3. (in cb_embed.semantic_rank) the pure-Python cosine loop, kept
     verbatim there for zero-optional-dep degradation; this module is
     only used when numpy is available.

Sidecars (derived, disposable — `.cb_embed_cache.json` stays the byte-
identical source of truth and is NEVER written here; deleting all
sidecars is always safe, they rebuild on the next semantic call):

  .cb_embed_cache.npy        float64 matrix, row-aligned with meta rows
  .cb_embed_cache.tvim       serialized turbovec index (turbovec tier only)
  .cb_embed_cache.meta.json  written LAST as the commit point:
      {version, provider, model, dims,
       source: {size, mtime_ns} of .cb_embed_cache.json,
       rows: [{id, u64, hash, identity}]}

load() refuses partial / stale / version- or provider-mismatched sidecar
sets (returns None) and the caller rebuilds from the JSON cache.

str -> uint64 id mapping: blake2b-64 of the entity id — deterministic
across machines and rebuilds; collisions resolved by deterministically
probing blake2b("{id}#{i}"). The PERSISTED map in meta.json — not the
hash function — is authoritative at load time, so the .tvim stays
interpretable even if the hash scheme changes. A content-hash change
re-uses the SAME u64 (IdMapIndex.remove then add_with_ids), so identity
is stable across re-embeds without a full rebuild.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys

try:
    import numpy as np
except ImportError:
    np = None

JSON_NAME = ".cb_embed_cache.json"
NPY_NAME = ".cb_embed_cache.npy"
TVIM_NAME = ".cb_embed_cache.tvim"
META_NAME = ".cb_embed_cache.meta.json"
META_VERSION = 1


def _turbovec_enabled() -> bool:
    """Engine selection at call time — kill switch wins over availability."""
    if os.environ.get("CE_DISABLE_TURBOVEC") == "1":
        return False
    try:
        import turbovec  # noqa: F401
    except ImportError:
        return False
    return True


def _assign_u64(entity_id: str, reverse: dict[int, str]) -> int:
    """Deterministic blake2b-64 id; probe '{id}#{i}' on collision.

    `reverse` maps already-assigned u64 -> entity_id; the value returned
    here is what gets persisted in meta.json, which is authoritative
    thereafter.
    """
    i = 0
    while True:
        key = entity_id if i == 0 else f"{entity_id}#{i}"
        u = int.from_bytes(
            hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "big")
        if reverse.get(u) in (None, entity_id):
            return u
        i += 1


def _fingerprint(json_cache: pathlib.Path) -> dict | None:
    """stat-based (size + mtime_ns) staleness check of the JSON cache.

    Hashing the 54MB JSON per query would negate the perf win; the >10%
    auto_build heuristic in semantic_rank is the second net for the
    theoretical same-size same-mtime rewrite.
    """
    try:
        st = json_cache.stat()
    except OSError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _unlink_quiet(p: pathlib.Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


class VectorStore:
    """Embedding matrix + persisted str<->uint64 id map + lazy turbovec index."""

    def __init__(self, provider: str, model: str, dims: int):
        self.provider = provider
        self.model = model
        self.dims = dims
        self.ids: list[str] = []
        self.matrix = np.zeros((0, dims), dtype=np.float64)
        self.norms = np.zeros(0, dtype=np.float64)
        self.hashes: dict[str, str] = {}
        self.identities: dict[str, str] = {}
        self._u64_of: dict[str, int] = {}
        self._id_of: dict[int, str] = {}
        self._row_of: dict[str, int] = {}
        self._index = None

    # ── engine internals ─────────────────────────────────────────

    def _materialize(self) -> None:
        """Swap the read-only memmap for an in-memory copy before mutation
        (also releases the Windows file lock so save() can os.replace)."""
        if isinstance(self.matrix, np.memmap):
            self.matrix = np.array(self.matrix)

    def _ensure_index(self):
        """Build (or return) the turbovec index over L2-normalized rows so
        quantized inner product approximates cosine for retrieval."""
        if self._index is None:
            import turbovec
            index = turbovec.IdMapIndex(dim=self.dims, bit_width=4)
            if self.ids:
                norms = np.where(self.norms == 0.0, 1.0, self.norms)
                mat32 = np.ascontiguousarray(
                    (np.asarray(self.matrix, dtype=np.float64)
                     / norms[:, None]).astype(np.float32))
                index.add_with_ids(
                    mat32,
                    np.asarray([self._u64_of[e] for e in self.ids],
                               dtype=np.uint64))
            self._index = index
        return self._index

    def _rescore(self, rows, q, qn):
        """Exact float64 cosine for the given row indices."""
        sub = np.asarray(self.matrix[rows], dtype=np.float64)
        denom = self.norms[rows] * qn
        denom = np.where(denom == 0.0, 1.0, denom)
        return (sub @ q) / denom

    def _pool_turbovec(self, q, top_k: int, allowed_rows):
        """Quantized retrieval of an over-fetched candidate pool (10x top_k,
        min 100, capped at the allowed population) — scores discarded, the
        pool is exact-rescored by the caller."""
        index = self._ensure_index()
        n_pool = int(allowed_rows.size) if allowed_rows is not None else len(self.ids)
        k_prime = min(n_pool, max(10 * top_k, 100))
        q32 = q.astype(np.float32).reshape(1, -1)
        if allowed_rows is not None:
            allow_u64 = np.asarray(
                [self._u64_of[self.ids[int(r)]] for r in allowed_rows],
                dtype=np.uint64)
            _, ids = index.search(q32, k_prime, allowlist=allow_u64)
        else:
            _, ids = index.search(q32, k_prime)
        return np.asarray(
            [self._row_of[self._id_of[int(u)]] for u in ids[0]], dtype=np.int64)

    def _load_tvim(self, tvim_p: pathlib.Path) -> None:
        """Best-effort: the .tvim is a derived accelerator. On any failure
        or misalignment with the meta rows, fall back to a lazy in-memory
        rebuild from the npy matrix — never serve from a stale index."""
        try:
            import turbovec
            index = turbovec.IdMapIndex.load(str(tvim_p))
            if index.dim != self.dims:
                raise ValueError(f"tvim dim {index.dim} != {self.dims}")
            for u in self._id_of:
                if not index.contains(u):
                    raise ValueError("tvim id-map misaligned with meta rows")
            self._index = index
        except Exception as exc:
            print(f"cb_vec: ignoring .tvim sidecar ({exc}) — index will be "
                  f"rebuilt in memory", file=sys.stderr)
            self._index = None

    # ── public API ───────────────────────────────────────────────

    def search(
        self,
        qvec,
        top_k: int = 20,
        allowlist: set[str] | None = None,
        min_score: float = 0.15,
    ) -> list[tuple[str, float]]:
        """Top-k (entity_id, exact_cosine) for `qvec`, sorted desc.

        allowlist=None searches the whole index. An entity-id subset
        restricts results to it; ids absent from the store are silently
        dropped (same semantics as the old 'eid not in cache -> skip'),
        and an empty / fully-absent allowlist short-circuits to []
        before touching turbovec (it raises ValueError on empty).
        """
        if top_k <= 0 or not self.ids:
            return []
        q = np.asarray(list(qvec), dtype=np.float64)
        if q.shape != (self.dims,):
            return []
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        allowed_rows = None
        if allowlist is not None:
            keep = sorted(self._row_of[e] for e in allowlist if e in self._row_of)
            if not keep:
                return []
            allowed_rows = np.asarray(keep, dtype=np.int64)
        rows = None
        if _turbovec_enabled():
            try:
                rows = self._pool_turbovec(q, top_k, allowed_rows)
            except Exception as exc:
                # e.g. IdMapIndex requires dims % 8 == 0 — degrade to the
                # exact numpy tier instead of crashing the rank call
                # (matching the _load_tvim best-effort style).
                print(f"cb_vec: turbovec search failed ({exc}) — falling "
                      f"back to exact numpy search", file=sys.stderr)
        if rows is None:
            if allowed_rows is not None:
                rows = allowed_rows
            else:
                rows = np.arange(len(self.ids), dtype=np.int64)
        sims = self._rescore(rows, q, qn)
        mask = sims >= min_score
        rows, sims = rows[mask], sims[mask]
        if rows.size > top_k:
            part = np.argpartition(-sims, top_k - 1)[:top_k]
            rows, sims = rows[part], sims[part]
        out = [(self.ids[int(r)], float(s)) for r, s in zip(rows, sims)]
        out.sort(key=lambda t: (-t[1], t[0]))
        return out

    def remove(self, entity_id: str) -> bool:
        """Drop one entity. Returns False when it was not present."""
        row = self._row_of.get(entity_id)
        if row is None:
            return False
        self._materialize()
        self.matrix = np.delete(self.matrix, row, axis=0)
        self.norms = np.delete(self.norms, row)
        self.ids.pop(row)
        u = self._u64_of.pop(entity_id)
        del self._id_of[u]
        del self.hashes[entity_id]
        del self.identities[entity_id]
        self._row_of = {eid: i for i, eid in enumerate(self.ids)}
        if self._index is not None:
            self._index.remove(u)
        return True

    def upsert(self, entity_id: str, vector, content_hash: str, identity: str) -> None:
        """Add a new entity or replace a changed one (content-hash change).

        An existing id keeps its u64: remove-first, then add_with_ids with
        the SAME u64 (add_with_ids raises ValueError on a duplicate id) —
        no full rebuild on re-embed.
        """
        vec = np.asarray(list(vector), dtype=np.float64)
        if vec.shape != (self.dims,):
            raise ValueError(
                f"vector has shape {vec.shape}, expected ({self.dims},)")
        self._materialize()
        norm = float(np.linalg.norm(vec))
        row = self._row_of.get(entity_id)
        if row is None:
            u = _assign_u64(entity_id, self._id_of)
            self.ids.append(entity_id)
            self._row_of[entity_id] = len(self.ids) - 1
            self._u64_of[entity_id] = u
            self._id_of[u] = entity_id
            self.matrix = np.vstack([self.matrix, vec[None, :]])
            self.norms = np.append(self.norms, norm)
        else:
            u = self._u64_of[entity_id]
            self.matrix[row] = vec
            self.norms[row] = norm
            if self._index is not None:
                self._index.remove(u)
        self.hashes[entity_id] = content_hash
        self.identities[entity_id] = identity
        if self._index is not None:
            unit = vec / (norm if norm else 1.0)
            self._index.add_with_ids(
                np.ascontiguousarray(unit.astype(np.float32)[None, :]),
                np.asarray([u], dtype=np.uint64))

    def save(self, corpus_dir: pathlib.Path) -> None:
        """Write the sidecars. All writes atomic (pid tmp + os.replace).

        Order matters: .npy first, then .tvim (turbovec tier only), then
        .meta.json LAST as the commit point — a crash mid-save leaves a
        stale-fingerprint set that load() refuses, never a consistent-
        looking wrong one.
        """
        corpus_dir = pathlib.Path(corpus_dir)
        pid = os.getpid()
        self._materialize()

        npy_p = corpus_dir / NPY_NAME
        tmp = npy_p.with_name(npy_p.name + f".{pid}.tmp")
        try:
            with open(tmp, "wb") as fh:
                np.save(fh, np.ascontiguousarray(self.matrix, dtype=np.float64))
            os.replace(tmp, npy_p)
        except Exception:
            _unlink_quiet(tmp)
            raise

        tvim_p = corpus_dir / TVIM_NAME
        if _turbovec_enabled() and self.ids:
            tmp = tvim_p.with_name(tvim_p.name + f".{pid}.tmp")
            try:
                self._ensure_index().write(str(tmp))
                os.replace(tmp, tvim_p)
            except Exception:
                _unlink_quiet(tmp)
                raise
        else:
            # A .tvim from an earlier turbovec-mode save must not survive a
            # numpy-mode rewrite of the matrix — it would be stale.
            _unlink_quiet(tvim_p)

        meta = {
            "version": META_VERSION,
            "provider": self.provider,
            "model": self.model,
            "dims": self.dims,
            "source": _fingerprint(corpus_dir / JSON_NAME),
            "rows": [
                {"id": eid,
                 "u64": self._u64_of[eid],
                 "hash": self.hashes.get(eid, ""),
                 "identity": self.identities.get(eid, "")}
                for eid in self.ids
            ],
        }
        meta_p = corpus_dir / META_NAME
        tmp = meta_p.with_name(meta_p.name + f".{pid}.tmp")
        try:
            tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, meta_p)
        except Exception:
            _unlink_quiet(tmp)
            raise


def build_from_cache(cache: dict, provider_meta: dict) -> VectorStore:
    """Build an in-memory store from the JSON cache (read-only input —
    this module never writes .cb_embed_cache.json).

    Entries with a missing embedding, mismatched dims, or per-entry
    provider/model/dims provenance disagreeing with provider_meta are
    skipped (mirroring semantic_rank's cache-path checks) — the store
    must never label foreign vectors with the current provider; a
    same-dims model swap would otherwise poison the sidecar meta and
    permanently defeat the >10% auto_build self-heal.
    """
    if np is None:
        raise RuntimeError("cb_vec requires numpy")
    dims = provider_meta["dims"]
    store = VectorStore(provider_meta["name"], provider_meta["model"], dims)
    rows: list[list[float]] = []
    for eid, entry in cache.items():
        emb = entry.get("embedding")
        if (not emb or len(emb) != dims
                or entry.get("provider") != provider_meta["name"]
                or entry.get("model") != provider_meta["model"]
                or entry.get("dims") != dims):
            continue
        u = _assign_u64(eid, store._id_of)
        store.ids.append(eid)
        store._row_of[eid] = len(store.ids) - 1
        store._u64_of[eid] = u
        store._id_of[u] = eid
        store.hashes[eid] = entry.get("hash", "")
        store.identities[eid] = entry.get("identity", "")
        rows.append(emb)
    if rows:
        store.matrix = np.asarray(rows, dtype=np.float64)
        store.norms = np.linalg.norm(store.matrix, axis=1)
    return store


def load(corpus_dir: pathlib.Path, provider: dict | None = None) -> VectorStore | None:
    """Load the sidecars, or None when they are missing / partial / stale /
    version- or provider-mismatched — the caller rebuilds from the JSON
    cache. The persisted u64 map (not the hash function) is authoritative.
    The matrix is memmapped (np.load mmap_mode='r') on this hot path.
    """
    if np is None:
        return None
    corpus_dir = pathlib.Path(corpus_dir)
    meta_p = corpus_dir / META_NAME
    npy_p = corpus_dir / NPY_NAME
    # All-or-nothing: meta without npy (or vice versa) is refused.
    if not meta_p.exists() or not npy_p.exists():
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or meta.get("version") != META_VERSION:
        return None
    if provider is not None and (
            meta.get("provider") != provider["name"]
            or meta.get("model") != provider["model"]
            or meta.get("dims") != provider["dims"]):
        return None
    src_fp = _fingerprint(corpus_dir / JSON_NAME)
    if src_fp is None or meta.get("source") != src_fp:
        return None
    rows = meta.get("rows")
    dims = meta.get("dims")
    if not isinstance(rows, list) or not isinstance(dims, int):
        return None
    try:
        ids = [r["id"] for r in rows]
        u64s = [int(r["u64"]) for r in rows]
    except (KeyError, TypeError, ValueError):
        return None
    if len(set(u64s)) != len(rows) or len(set(ids)) != len(rows):
        return None
    try:
        matrix = np.load(npy_p, mmap_mode="r")
    except (OSError, ValueError):
        return None
    if matrix.ndim != 2 or matrix.shape != (len(rows), dims):
        return None
    store = VectorStore(meta.get("provider", ""), meta.get("model", ""), dims)
    store.ids = ids
    store.matrix = matrix
    store.norms = np.linalg.norm(matrix, axis=1).astype(np.float64)
    store._u64_of = dict(zip(ids, u64s))
    store._id_of = dict(zip(u64s, ids))
    store._row_of = {eid: i for i, eid in enumerate(ids)}
    store.hashes = {r["id"]: r.get("hash", "") for r in rows}
    store.identities = {r["id"]: r.get("identity", "") for r in rows}
    tvim_p = corpus_dir / TVIM_NAME
    if _turbovec_enabled() and tvim_p.exists():
        store._load_tvim(tvim_p)
    return store
