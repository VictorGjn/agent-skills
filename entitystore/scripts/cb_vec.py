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

  .cb_embed_cache.<token>.npy   float64 matrix, row-aligned with meta rows
  .cb_embed_cache.<token>.tvim  serialized turbovec index (turbovec tier only)
  .cb_embed_cache.meta.json     written LAST as the commit point:
      {version, provider, model, dims,
       source: {size, mtime_ns} of .cb_embed_cache.json — captured by the
               caller when the JSON was read/written, NEVER re-stat'ed at
               save time (a concurrent JSON rewrite must not get our stamp),
       npy / tvim: the exact token-named data files of THIS save — a
               fresh token per save means concurrent savers cannot
               cross-pair each other's data files (last meta wins,
               orphans are swept on the next save),
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
import secrets
import sys

try:
    import numpy as np
except ImportError:
    np = None

JSON_NAME = ".cb_embed_cache.json"
SIDECAR_PREFIX = ".cb_embed_cache."
META_NAME = ".cb_embed_cache.meta.json"
META_VERSION = 2
# v1 fixed-name data files — no longer written or read, swept on save.
LEGACY_NPY_NAME = ".cb_embed_cache.npy"
LEGACY_TVIM_NAME = ".cb_embed_cache.tvim"


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


def json_fingerprint(corpus_dir: pathlib.Path) -> dict | None:
    """Fingerprint of the corpus JSON cache, for callers to capture at the
    moment they read or write it and thread into build_from_cache / save
    as data (save never re-stats — see the module docstring)."""
    return _fingerprint(pathlib.Path(corpus_dir) / JSON_NAME)


def over_fetch_k(n_pool: int, top_k: int) -> int:
    """Quantized-retrieval candidate-pool size: 10x top_k, min 100, capped
    at the allowed population. Shared with cb_vec_gate so the gate measures
    the exact policy production runs."""
    return min(n_pool, max(10 * top_k, 100))


def _unlink_quiet(p: pathlib.Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _sweep_orphans(corpus_dir: pathlib.Path, keep: set[str]) -> None:
    """Best-effort removal of sidecar data files not referenced by the
    just-written meta: loser halves of concurrent saves and pre-v2
    fixed-name files. Quiet on failure (Windows holds a lock on memmapped
    npy files). May delete another saver's in-flight data files — its
    meta then points at a missing npy, which load() refuses (rebuild),
    never serves wrong."""
    for pat in (f"{SIDECAR_PREFIX}*.npy", f"{SIDECAR_PREFIX}*.tvim"):
        for p in corpus_dir.glob(pat):
            if p.name not in keep:
                _unlink_quiet(p)
    _unlink_quiet(corpus_dir / LEGACY_NPY_NAME)
    _unlink_quiet(corpus_dir / LEGACY_TVIM_NAME)


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
        # {size, mtime_ns} of the JSON cache this store was built from —
        # set by the caller (via build_from_cache / load); save() stamps
        # exactly this value, never a fresh stat.
        self.source_fp: dict | None = None

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
        k_prime = over_fetch_k(n_pool, top_k)
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
            if len(index) != len(self._id_of):
                # extra stale u64s would surface as KeyErrors on every
                # search — containment alone is a one-way check
                raise ValueError(
                    f"tvim has {len(index)} entries, meta has {len(self._id_of)}")
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
            # warn, don't raise: a provider dims mix-up must degrade like
            # the other paths, but silently would read as "no matches"
            print(f"cb_vec: query has shape {q.shape}, store expects "
                  f"({self.dims},) — returning no matches", file=sys.stderr)
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
            kth = sims[part].min()
            if np.count_nonzero(sims == kth) > 1:
                # exact score ties at the k-th boundary (realistic with
                # duplicate-content entities): argpartition picks members
                # in array order, which differs between tiers — break by
                # id. Best-effort across tiers: BLAS may put bit-identical
                # rows ulps apart depending on matrix position, in which
                # case they are not "tied" here at all
                strict = np.flatnonzero(sims > kth)
                tied = np.flatnonzero(sims == kth)
                tied = tied[np.argsort(
                    np.asarray([self.ids[int(rows[i])] for i in tied]))]
                part = np.concatenate([strict, tied])[:top_k]
            rows, sims = rows[part], sims[part]
        out = [(self.ids[int(r)], float(s)) for r, s in zip(rows, sims)]
        out.sort(key=lambda t: (-t[1], t[0]))
        return out

    def remove(self, entity_id: str) -> bool:
        """Drop one entity. Returns False when it was not present."""
        present = entity_id in self._row_of
        if present:
            self.apply([entity_id], [])
        return present

    def upsert(self, entity_id: str, vector, content_hash: str, identity: str) -> None:
        """Add a new entity or replace a changed one (content-hash change).

        An existing id keeps its u64: remove-first, then add_with_ids with
        the SAME u64 (add_with_ids raises ValueError on a duplicate id) —
        no full rebuild on re-embed.
        """
        self.apply([], [(entity_id, vector, content_hash, identity)])

    def apply(self, removals, upserts) -> None:
        """Batched remove + upsert — the scribe-scale sync path.

        Per-entity remove()/upsert() each copy the full matrix (O(n*dims)),
        so a sync touching m entities costs O(m*n*dims); this does one
        masked drop + one vstack regardless of m. All upsert vectors are
        validated BEFORE any mutation (a bad one leaves the store intact).

        removals: iterable of entity ids (absent ids are ignored).
        upserts:  iterable of (entity_id, vector, content_hash, identity);
                  a duplicated id keeps the last tuple.
        """
        drop_ids = [e for e in dict.fromkeys(removals) if e in self._row_of]
        ups: dict[str, tuple] = {}
        for eid, vector, content_hash, identity in upserts:
            vec = np.asarray(list(vector), dtype=np.float64)
            if vec.shape != (self.dims,):
                raise ValueError(
                    f"vector has shape {vec.shape}, expected ({self.dims},)")
            ups[eid] = (vec, float(np.linalg.norm(vec)), content_hash, identity)
        if not drop_ids and not ups:
            return
        self._materialize()
        if drop_ids:
            drop_set = set(drop_ids)
            keep = np.asarray(
                [i for i, eid in enumerate(self.ids) if eid not in drop_set],
                dtype=np.int64)
            self.matrix = self.matrix[keep]
            self.norms = self.norms[keep]
            self.ids = [e for e in self.ids if e not in drop_set]
            for eid in drop_ids:
                u = self._u64_of.pop(eid)
                del self._id_of[u]
                self.hashes.pop(eid, None)
                self.identities.pop(eid, None)
                if self._index is not None:
                    self._index.remove(u)
            self._row_of = {eid: i for i, eid in enumerate(self.ids)}
        new_vecs: list = []
        new_norms: list[float] = []
        index_adds: list[tuple] = []  # (unit float32 vec, u64)
        for eid, (vec, norm, content_hash, identity) in ups.items():
            row = self._row_of.get(eid)
            if row is None:
                u = _assign_u64(eid, self._id_of)
                self.ids.append(eid)
                self._row_of[eid] = len(self.ids) - 1
                self._u64_of[eid] = u
                self._id_of[u] = eid
                new_vecs.append(vec)
                new_norms.append(norm)
            else:
                u = self._u64_of[eid]
                self.matrix[row] = vec
                self.norms[row] = norm
                if self._index is not None:
                    self._index.remove(u)
            self.hashes[eid] = content_hash
            self.identities[eid] = identity
            if self._index is not None:
                index_adds.append(((vec / (norm if norm else 1.0))
                                   .astype(np.float32), u))
        if new_vecs:
            self.matrix = np.vstack(
                [self.matrix, np.asarray(new_vecs, dtype=np.float64)])
            self.norms = np.append(self.norms, np.asarray(new_norms))
        if index_adds and self._index is not None:
            self._index.add_with_ids(
                np.ascontiguousarray(np.vstack([v[None, :] for v, _ in index_adds])),
                np.asarray([u for _, u in index_adds], dtype=np.uint64))

    def save(self, corpus_dir: pathlib.Path) -> None:
        """Write the sidecars under a fresh per-save token.

        Data files (.npy / .tvim) go directly to unique token names — an
        unguessable name nothing reads until .meta.json, replaced
        atomically LAST as the commit point, references it. A crash
        mid-save leaves the previous complete set untouched (plus swept-
        later orphans); concurrent savers cannot cross-pair data files.

        The source fingerprint stamped is `self.source_fp` — captured by
        the caller when the JSON cache was read or written, never a fresh
        stat (a concurrent JSON rewrite must not get our stamp; an
        unstamped store saves source=None, which load() refuses as stale).
        """
        corpus_dir = pathlib.Path(corpus_dir)
        token = secrets.token_hex(8)
        self._materialize()

        npy_name = f"{SIDECAR_PREFIX}{token}.npy"
        tvim_name = (f"{SIDECAR_PREFIX}{token}.tvim"
                     if _turbovec_enabled() and self.ids else None)
        try:
            with open(corpus_dir / npy_name, "wb") as fh:
                np.save(fh, np.ascontiguousarray(self.matrix, dtype=np.float64))
            if tvim_name is not None:
                self._ensure_index().write(str(corpus_dir / tvim_name))

            meta = {
                "version": META_VERSION,
                "provider": self.provider,
                "model": self.model,
                "dims": self.dims,
                "source": self.source_fp,
                "npy": npy_name,
                "tvim": tvim_name,
                "rows": [
                    {"id": eid,
                     "u64": self._u64_of[eid],
                     "hash": self.hashes.get(eid, ""),
                     "identity": self.identities.get(eid, "")}
                    for eid in self.ids
                ],
            }
            meta_p = corpus_dir / META_NAME
            tmp = meta_p.with_name(meta_p.name + f".{token}.tmp")
            try:
                tmp.write_text(json.dumps(meta, ensure_ascii=False),
                               encoding="utf-8")
                os.replace(tmp, meta_p)
            except Exception:
                _unlink_quiet(tmp)
                raise
        except Exception:
            _unlink_quiet(corpus_dir / npy_name)
            if tvim_name is not None:
                _unlink_quiet(corpus_dir / tvim_name)
            raise
        _sweep_orphans(corpus_dir, keep={npy_name, tvim_name or ""})


def build_from_cache(cache: dict, provider_meta: dict,
                     source_fp: dict | None = None) -> VectorStore:
    """Build an in-memory store from the JSON cache (read-only input —
    this module never writes .cb_embed_cache.json). `source_fp` is the
    fingerprint the caller captured when it read/wrote that JSON; without
    it a later save() is stamped source=None and load() treats it stale.

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
    store.source_fp = source_fp
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
    if not meta_p.exists():
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or meta.get("version") != META_VERSION:
        return None

    def _safe_name(n) -> bool:
        # meta is data: a data-file reference must be a plain sidecar
        # filename in this directory, never a path
        return (isinstance(n, str) and n.startswith(SIDECAR_PREFIX)
                and pathlib.PurePath(n).name == n)

    npy_name = meta.get("npy")
    if not _safe_name(npy_name):
        return None
    npy_p = corpus_dir / npy_name
    # All-or-nothing: meta referencing a missing npy is refused.
    if not npy_p.exists():
        return None
    tvim_name = meta.get("tvim")
    if tvim_name is not None and not _safe_name(tvim_name):
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
        matrix = np.load(npy_p, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError):
        return None
    if matrix.ndim != 2 or matrix.shape != (len(rows), dims):
        return None
    store = VectorStore(meta.get("provider", ""), meta.get("model", ""), dims)
    store.source_fp = meta.get("source")
    store.ids = ids
    store.matrix = matrix
    store.norms = np.linalg.norm(matrix, axis=1).astype(np.float64)
    store._u64_of = dict(zip(ids, u64s))
    store._id_of = dict(zip(u64s, ids))
    store._row_of = {eid: i for i, eid in enumerate(ids)}
    store.hashes = {r["id"]: r.get("hash", "") for r in rows}
    store.identities = {r["id"]: r.get("identity", "") for r in rows}
    if _turbovec_enabled() and tvim_name is not None:
        tvim_p = corpus_dir / tvim_name
        if tvim_p.exists():
            store._load_tvim(tvim_p)
    return store
