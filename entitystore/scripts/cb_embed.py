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
    p = _cache_path(corpus_dir)
    p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


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
        for eid, vec in zip(to_embed_ids, vecs):
            cache[eid]["embedding"] = vec
        print(f"embedded in {time.time() - t0:.1f}s", file=sys.stderr)

    save_cache(corpus_dir, cache)
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

    cache = load_cache(corpus_dir)
    # Auto-build if the cache is empty or has many missing entries.
    missing = sum(1 for eid in entities if eid not in cache
                  or not cache.get(eid, {}).get("embedding"))
    if auto_build and (not cache or missing > len(entities) * 0.1):
        cache = build_embeddings(corpus_dir, entities)

    qvec = embed_query(query, provider)
    if not qvec:
        return []

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
    scored.sort(key=lambda x: -x["score"])
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
