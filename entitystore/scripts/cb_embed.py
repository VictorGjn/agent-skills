#!/usr/bin/env python3
"""
companybrain — semantic resolver over JSON entities.

Ports the embedding/caching/cosine logic from CE's embed_resolve.py and
specializes it for JSON entity stores instead of file-tree workspaces.

What it does:
  1. Build a short "identity string" per entity (id + names + summary +
     concept.statement + topics).
  2. Embed each identity via Mistral / OpenAI (whichever key is set).
  3. Cache embeddings per-corpus to `.cb_embed_cache.json` (gitignored).
  4. At query time: embed query, cosine-rank entities, return top-K.

Schema-injection still holds — this module never embeds entity schemas;
it embeds the identity string we synthesize at runtime.

Cache invalidation: entity content_hash (sha256 of id|names|summary|
concept.statement|topics). When any of those change the entity gets
re-embedded; everything else stays cached.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import sys
import time
from typing import Iterable

# numpy and the cb_vec sidecar backend are optional — without them
# semantic_rank degrades to the pure-Python cosine loop below.
try:
    import numpy as np
except ImportError:
    np = None

try:
    import cb_vec
except ImportError:
    # cb_embed may itself be loaded via importlib.spec_from_file_location
    # (entity-review does this to cb_engine) — make the sibling importable.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    try:
        import cb_vec
    except ImportError:
        cb_vec = None

# ──────────────────────────────────────────────────────────────────
# Provider resolution (Mistral preferred — Syroco default)
# ──────────────────────────────────────────────────────────────────

EMBED_BATCH_SIZE = 64


def _resolve_provider() -> dict | None:
    """Returns provider config dict, or None if no API key available."""
    explicit = os.environ.get("CB_EMBED_PROVIDER", "").lower()
    if not explicit:
        if os.environ.get("MISTRAL_API_KEY"):
            explicit = "mistral"
        elif os.environ.get("OPENAI_API_KEY"):
            explicit = "openai"
        else:
            return None

    if explicit == "mistral":
        return {
            "name": "mistral",
            "base_url": "https://api.mistral.ai/v1",
            "key": os.environ.get("MISTRAL_API_KEY", ""),
            "model": os.environ.get("CB_EMBED_MODEL", "mistral-embed"),
            "dims": 1024,
            "send_dims": False,
        }
    if explicit == "openai":
        return {
            "name": "openai",
            "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "key": os.environ.get("OPENAI_API_KEY", ""),
            "model": os.environ.get("CB_EMBED_MODEL", "text-embedding-3-small"),
            "dims": 512,
            "send_dims": True,
        }
    return None


# ──────────────────────────────────────────────────────────────────
# Identity + hash
# ──────────────────────────────────────────────────────────────────


def entity_identity(entity: dict) -> str:
    """Compact semantic identity for an entity. ~30-80 tokens."""
    parts: list[str] = []
    parts.append(f"id: {entity.get('id', '')}")
    parts.append(f"kind: {entity.get('kind', '')}")
    names = entity.get("names") or []
    if names:
        parts.append(f"names: {', '.join(names)}")
    summary = entity.get("summary") or ""
    if summary:
        parts.append(f"summary: {summary}")
    concept = entity.get("concept") or {}
    stmt = concept.get("statement") or ""
    if stmt:
        parts.append(f"statement: {stmt}")
    topics = entity.get("topics") or []
    if topics:
        parts.append(f"topics: {', '.join(topics)}")
    return "\n".join(parts)


def entity_content_hash(entity: dict) -> str:
    """Stable hash of the fields that affect embedding identity."""
    h = hashlib.sha256()
    h.update(entity_identity(entity).encode("utf-8"))
    return h.hexdigest()[:16]


# ──────────────────────────────────────────────────────────────────
# Cache I/O
# ──────────────────────────────────────────────────────────────────


def _cache_path(corpus_dir: pathlib.Path) -> pathlib.Path:
    return corpus_dir / ".cb_embed_cache.json"


def load_cache(corpus_dir: pathlib.Path) -> dict:
    """Cache shape: {entity_id: {hash, identity, embedding[], provider, model, dims}}."""
    p = _cache_path(corpus_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(corpus_dir: pathlib.Path, cache: dict) -> None:
    """Atomic write — load_cache silently returns {} on a truncated cache."""
    p = _cache_path(corpus_dir)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


# ──────────────────────────────────────────────────────────────────
# API call
# ──────────────────────────────────────────────────────────────────


def embed_texts(texts: list[str], provider: dict) -> list[list[float]]:
    """Batched embedding call. Returns one vector per input text."""
    import requests

    url = provider["base_url"].rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {provider['key']}",
        "Content-Type": "application/json",
    }
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i: i + EMBED_BATCH_SIZE]
        payload: dict = {"model": provider["model"], "input": batch}
        if provider.get("send_dims"):
            payload["dimensions"] = provider["dims"]
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(
                f"embedding API error {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        out.extend(item["embedding"] for item in data["data"])
        print(f"  embedded batch {i // EMBED_BATCH_SIZE + 1}: {len(batch)} entities "
              f"({data.get('usage', {}).get('total_tokens', '?')} tokens)",
              file=sys.stderr)
    return out


def embed_query(query: str, provider: dict) -> list[float] | None:
    if not query:
        return None
    vecs = embed_texts([query], provider)
    return vecs[0] if vecs else None


# ──────────────────────────────────────────────────────────────────
# Cosine similarity
# ──────────────────────────────────────────────────────────────────


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    a = list(a)
    b = list(b)
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ──────────────────────────────────────────────────────────────────
# Vector sidecars (derived, disposable — JSON cache stays the truth)
# ──────────────────────────────────────────────────────────────────


def _sidecar_store(corpus_dir: pathlib.Path):
    """Load the cb_vec sidecars, or None (backend missing / sidecars
    missing / stale / mismatched). Best-effort — never raises."""
    if cb_vec is None or np is None:
        return None
    try:
        return cb_vec.load(corpus_dir)
    except Exception:
        return None


def _sync_sidecars(corpus_dir: pathlib.Path, cache: dict, provider: dict,
                   store) -> None:
    """Best-effort sidecar sync after a JSON cache write.

    `store` must be loaded BEFORE save_cache (its fingerprint still matches
    the old JSON) so the sync can be incremental: stale ids removed,
    changed-hash ids re-added with the same u64, new ids added. Any failure
    warns to stderr and never blocks the JSON write (the source of truth).
    """
    if cb_vec is None or np is None:
        return
    try:
        if store is not None:
            # Copy the matrix in-memory: releases the npy memmap so the
            # save() below can os.replace the file (Windows holds a lock
            # on memmapped files).
            store._materialize()
        if (store is None
                or store.provider != provider["name"]
                or store.model != provider["model"]
                or store.dims != provider["dims"]):
            store = cb_vec.build_from_cache(cache, provider)
        else:
            for eid in [e for e in store.ids if e not in cache]:
                store.remove(eid)
            for eid, entry in cache.items():
                emb = entry.get("embedding")
                if (not emb or len(emb) != store.dims
                        or entry.get("provider") != store.provider
                        or entry.get("model") != store.model):
                    continue
                if store.hashes.get(eid) != entry.get("hash"):
                    store.upsert(eid, emb, entry.get("hash", ""),
                                 entry.get("identity", ""))
        store.save(corpus_dir)
    except Exception as ex:  # noqa: BLE001 — sidecars are disposable
        print(f"cb_embed: sidecar sync failed ({ex}) — JSON cache remains "
              f"the source of truth", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────
# Build + resolve
# ──────────────────────────────────────────────────────────────────


def build_embeddings(
    corpus_dir: pathlib.Path,
    entities: dict[str, dict],
    force: bool = False,
) -> dict:
    """Incrementally (re)build the embedding cache for a corpus.

    Only re-embeds entities whose content_hash changed. Removes entries for
    deleted entities. Returns the updated cache.
    """
    provider = _resolve_provider()
    if provider is None:
        raise RuntimeError(
            "no embedding provider available — set MISTRAL_API_KEY or OPENAI_API_KEY"
        )

    cache = {} if force else load_cache(corpus_dir)
    to_embed_ids: list[str] = []
    to_embed_texts: list[str] = []

    for eid, e in entities.items():
        ch = entity_content_hash(e)
        cached = cache.get(eid)
        if (cached
                and cached.get("hash") == ch
                and cached.get("provider") == provider["name"]
                and cached.get("model") == provider["model"]
                and cached.get("embedding")):
            continue
        ident = entity_identity(e)
        cache[eid] = {
            "hash": ch,
            "identity": ident,
            "embedding": None,
            "provider": provider["name"],
            "model": provider["model"],
            "dims": provider["dims"],
        }
        to_embed_ids.append(eid)
        to_embed_texts.append(ident)

    # Remove stale (deleted) entries.
    stale = [eid for eid in cache if eid not in entities]
    for eid in stale:
        del cache[eid]

    if to_embed_ids:
        print(f"embedding {len(to_embed_ids)} new/changed entities "
              f"({len(cache) - len(to_embed_ids)} cached) via "
              f"{provider['name']}/{provider['model']}...", file=sys.stderr)
        t0 = time.time()
        vecs = embed_texts(to_embed_texts, provider)
        # Refuse partial cache: zip would silently drop the unmatched tail.
        if len(vecs) != len(to_embed_texts):
            raise RuntimeError(
                f"embedding API returned {len(vecs)} vectors for "
                f"{len(to_embed_texts)} inputs — refusing to write partial "
                f"cache. provider={provider['name']} model={provider['model']}"
            )
        for eid, vec in zip(to_embed_ids, vecs):
            cache[eid]["embedding"] = vec
        print(f"embedded in {time.time() - t0:.1f}s", file=sys.stderr)

    # Load existing sidecars BEFORE the JSON write — their fingerprint still
    # matches the old JSON, which is what makes the sync incremental. force
    # bypasses them: content hashes are unchanged, so an incremental sync
    # would keep the old vectors; a None store rebuilds from the new JSON.
    store = None if force else _sidecar_store(corpus_dir)
    save_cache(corpus_dir, cache)
    _sync_sidecars(corpus_dir, cache, provider, store)
    return cache


def semantic_rank(
    query: str,
    corpus_dir: pathlib.Path,
    entities: dict[str, dict],
    top_k: int = 20,
    min_score: float = 0.15,
    auto_build: bool = True,
) -> list[dict]:
    """Embed `query`, cosine-rank entities, return top-K with scores.

    Returns: [{id, score, identity}] sorted by score desc.
    Raises RuntimeError if no provider configured.
    """
    provider = _resolve_provider()
    if provider is None:
        raise RuntimeError(
            "no embedding provider available — set MISTRAL_API_KEY or OPENAI_API_KEY"
        )

    use_backend = cb_vec is not None and np is not None
    # Fresh sidecars carry per-id hashes and the provider/model/dims meta, so
    # the hot path never parses the (multi-MB) JSON cache.
    store = _sidecar_store(corpus_dir)
    cache = None if store is not None else load_cache(corpus_dir)

    # Incompatibility = missing OR built under a different provider/model/dims.
    # Counting only `embedding is None` lets a provider swap (Mistral 1024d ->
    # OpenAI 512d) silently fly through; cosine() then returns 0 for every
    # dim-mismatch and the caller sees "no matches" instead of "stale cache".
    def _incompatible(eid: str) -> bool:
        if store is not None:
            return (
                eid not in store.hashes
                or store.provider != provider["name"]
                or store.model != provider["model"]
                or store.dims != provider["dims"]
            )
        e = cache.get(eid)
        if not e or not e.get("embedding"):
            return True
        return (
            e.get("provider") != provider["name"]
            or e.get("model") != provider["model"]
            or e.get("dims") != provider["dims"]
        )
    # An empty cache/store makes every candidate incompatible, so the >10%
    # trigger subsumes the old `not cache` one; with zero candidates a rebuild
    # could only prune the cache, so it never fires.
    incompatible = sum(1 for eid in entities if _incompatible(eid))
    if auto_build and incompatible > len(entities) * 0.1:
        store = None  # drop the memmap so the rebuild can replace the sidecars
        cache = build_embeddings(corpus_dir, entities)
        store = _sidecar_store(corpus_dir)

    qvec = embed_query(query, provider)
    if not qvec:
        return []

    if use_backend and store is None:
        if cache is None:
            cache = load_cache(corpus_dir)
        store = cb_vec.build_from_cache(cache, provider)
        try:
            store.save(corpus_dir)
        except Exception as ex:  # noqa: BLE001 — sidecars are disposable
            print(f"cb_embed: sidecar save failed ({ex}) — serving search "
                  f"from memory", file=sys.stderr)

    if store is not None:
        hits = store.search(qvec, top_k=top_k, allowlist=set(entities),
                            min_score=min_score)
        return [{"id": eid, "score": round(s, 4),
                 "identity": store.identities.get(eid, "")}
                for eid, s in hits]

    # Pure-Python fallback — numpy/cb_vec unavailable.
    scored: list[dict] = []
    for eid, entry in cache.items():
        if eid not in entities:
            continue
        emb = entry.get("embedding")
        if not emb:
            continue
        s = cosine(qvec, emb)
        if s >= min_score:
            scored.append({
                "id": eid,
                "score": round(s, 4),
                "identity": entry.get("identity", ""),
            })
    # id tie-break keeps exact-tie ordering identical to VectorStore.search.
    scored.sort(key=lambda x: (-x["score"], x["id"]))
    return scored[:top_k]


def provider_status() -> dict:
    p = _resolve_provider()
    if p is None:
        return {"available": False, "reason": "no_api_key"}
    return {
        "available": True,
        "provider": p["name"],
        "model": p["model"],
        "dims": p["dims"],
    }
