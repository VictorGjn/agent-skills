"""
Embedding Resolver — Semantic entry point resolution for context graph.

Fixes the lexical gap: when the query is "how does authentication work?" but
no file contains "auth" in its path/symbols, keyword matching returns nothing.
Embeddings bridge the vocabulary gap.

Architecture:
  1. Build a compact "identity string" per file (path + exports + headings + first sentence)
  2. Embed identities via OpenAI text-embedding-3-small (cheap, 1536 dims)
  3. At query time: embed query → cosine similarity → entry points
  4. Combine with keyword scores for hybrid resolution

Usage:
  # Generate embeddings for an existing index
  python3 embed_resolve.py build cache/workspace-index.json

  # Resolve entry points for a query
  python3 embed_resolve.py resolve "how does authentication work?" --top 10

  # Hybrid mode: combine keyword + semantic scores
  python3 embed_resolve.py resolve "authentication middleware" --hybrid --top 10

Embeddings are cached in cache/embeddings.json. Only recomputed when file hash changes.
"""

import json
import sys
import os
import math
from pathlib import Path

# ── Config ──

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 512  # request reduced dims (saves storage, good enough for file identity)
EMBED_BATCH_SIZE = 100  # OpenAI allows up to 2048 inputs per batch
CACHE_FILE = "cache/embeddings.json"
INDEX_FILE = "cache/workspace-index.json"

# Hybrid weights: how much to trust semantic vs keyword
SEMANTIC_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4


# ── Identity string builder ──

def build_identity(file_entry: dict) -> str:
    """
    Build a compact semantic identity for a file.
    This is what gets embedded — NOT the full content.

    Includes: path, exported symbols, heading titles, first sentence.
    ~50-200 tokens per file. Cheap to embed, captures what the file IS.
    """
    parts = []

    # Path (split camelCase for better semantic matching)
    path = file_entry['path']
    parts.append(f"File: {path}")

    # Knowledge type if available
    kt = file_entry.get('knowledge_type', '')
    if kt:
        parts.append(f"Type: {kt}")

    # Tree headings (recursive)
    tree = file_entry.get('tree', {})
    if tree:
        headings = _collect_headings(tree, max_depth=3)
        if headings:
            parts.append(f"Sections: {', '.join(headings)}")

        # First sentence (the file's purpose)
        first_sentence = tree.get('firstSentence', '')
        if first_sentence:
            parts.append(f"Purpose: {first_sentence}")

    # Exported symbols (from code files)
    symbols = file_entry.get('symbols', [])
    if symbols:
        exported = [s['name'] for s in symbols if s.get('isExported', True)][:20]
        if exported:
            parts.append(f"Exports: {', '.join(exported)}")

    return '\n'.join(parts)


def _collect_headings(tree: dict, max_depth: int = 3, current_depth: int = 0) -> list:
    """Recursively collect heading titles from tree."""
    if current_depth > max_depth:
        return []
    headings = []
    title = tree.get('title', '')
    if title and current_depth > 0:  # skip root title (usually file name)
        headings.append(title)
    for child in tree.get('children', []):
        headings.extend(_collect_headings(child, max_depth, current_depth + 1))
    return headings


# ── Embedding API ──

def embed_texts(texts: list, api_key: str = None) -> list:
    """
    Embed a list of texts using OpenAI API.
    Returns list of embedding vectors.
    """
    import requests

    key = api_key or os.environ.get('OPENAI_API_KEY', '')
    if not key:
        print("Error: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    all_embeddings = []

    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]

        resp = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": EMBED_MODEL,
                "input": batch,
                "dimensions": EMBED_DIMS,
            },
            timeout=60,
        )

        if resp.status_code != 200:
            print(f"Embedding API error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        batch_embeddings = [item['embedding'] for item in data['data']]
        all_embeddings.extend(batch_embeddings)

        tokens_used = data.get('usage', {}).get('total_tokens', 0)
        print(f"  Embedded batch {i//EMBED_BATCH_SIZE + 1}: {len(batch)} files, {tokens_used} tokens", file=sys.stderr)

    return all_embeddings


def embed_single(text: str, api_key: str = None) -> list:
    """Embed a single text. Returns one vector."""
    return embed_texts([text], api_key)[0]


# ── Vector math ──

def cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Cache management ──

def load_cache(cache_path: str) -> dict:
    """Load embedding cache. Returns {path: {hash, embedding, identity}}."""
    p = Path(cache_path)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict, cache_path: str):
    """Save embedding cache."""
    p = Path(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w') as f:
        json.dump(cache, f)
    print(f"Cache saved: {len(cache)} entries → {cache_path}", file=sys.stderr)


# ── Build embeddings ──

def build_embeddings(index_path: str, cache_path: str = CACHE_FILE, api_key: str = None):
    """
    Build or update embeddings for all files in the index.
    Only recomputes when file hash changes.
    """
    with open(index_path) as f:
        index = json.load(f)

    files = index.get('files', index) if isinstance(index, dict) else index
    if isinstance(files, dict):
        files = list(files.values())

    cache = load_cache(cache_path)

    # Find files that need (re)embedding
    to_embed = []
    to_embed_paths = []

    for entry in files:
        path = entry['path']
        content_hash = entry.get('contentHash', entry.get('hash', ''))

        cached = cache.get(path)
        if cached and cached.get('hash') == content_hash and cached.get('embedding'):
            continue  # still fresh

        identity = build_identity(entry)
        to_embed.append(identity)
        to_embed_paths.append(path)

        # Store identity + hash (embedding will be added after API call)
        cache[path] = {
            'hash': content_hash,
            'identity': identity,
            'embedding': None,
        }

    # Remove stale entries (files no longer in index)
    index_paths = {e['path'] for e in files}
    stale = [p for p in cache if p not in index_paths]
    for p in stale:
        del cache[p]

    if not to_embed:
        print(f"All {len(cache)} files already embedded. No API calls needed.", file=sys.stderr)
        save_cache(cache, cache_path)
        return cache

    print(f"Embedding {len(to_embed)} files ({len(cache) - len(to_embed)} cached)...", file=sys.stderr)

    embeddings = embed_texts(to_embed, api_key)

    for path, embedding in zip(to_embed_paths, embeddings):
        cache[path]['embedding'] = embedding

    save_cache(cache, cache_path)
    return cache


# ── Resolve ──

def resolve_semantic(query: str, cache_path: str = CACHE_FILE,
                     top_k: int = 10, min_score: float = 0.15,
                     api_key: str = None) -> list:
    """
    Resolve entry points using semantic similarity.

    Returns: [{path, confidence, reason, identity}] sorted by confidence desc.
    """
    cache = load_cache(cache_path)
    if not cache:
        print("No embedding cache found. Run 'build' first.", file=sys.stderr)
        return []

    query_embedding = embed_single(query, api_key)

    results = []
    for path, entry in cache.items():
        emb = entry.get('embedding')
        if not emb:
            continue

        sim = cosine_similarity(query_embedding, emb)
        if sim >= min_score:
            results.append({
                'path': path,
                'confidence': round(sim, 4),
                'reason': 'semantic match',
                'identity': entry.get('identity', ''),
            })

    results.sort(key=lambda x: -x['confidence'])
    return results[:top_k]


RRF_K = 60  # Cormack 2009; canonical RRF default. Insensitive in [10..100].


def _rrf_score(rank: int, k: int = RRF_K) -> float:
    """Reciprocal Rank Fusion contribution for a given 1-based rank."""
    return 1.0 / (k + rank)


def resolve_hybrid(query: str, scored_files: list, cache_path: str = CACHE_FILE,
                   top_k: int = 15, semantic_weight: float = None,
                   api_key: str = None) -> list:
    """
    Hybrid resolution via Reciprocal Rank Fusion (RRF).

    Replaces the previous tuned linear blend (semantic_weight × sem +
    keyword_weight × kw). RRF is parameter-free, robust to score-scale
    mismatches between keyword/semantic, and beats tuned linear fusion on
    most TREC-style benchmarks.

    `semantic_weight` is accepted for backward compatibility but ignored —
    pass it if you must, but it does nothing in this implementation.

    Returns: [{path, confidence, keyword_score, semantic_score, reason}]
    sorted by confidence desc. `confidence` is the RRF score (small absolute
    number; only the ranking is meaningful).
    """
    cache = load_cache(cache_path)
    query_embedding = embed_single(query, api_key) if cache else None

    # Semantic ranking
    semantic_pairs = []
    if query_embedding:
        for path, entry in cache.items():
            emb = entry.get('embedding')
            if emb:
                semantic_pairs.append((path, cosine_similarity(query_embedding, emb)))
    semantic_pairs.sort(key=lambda x: -x[1])
    semantic_rank = {p: i + 1 for i, (p, _) in enumerate(semantic_pairs)}
    semantic_raw = dict(semantic_pairs)

    # Keyword ranking
    keyword_pairs = sorted(
        ((sf['path'], sf['relevance']) for sf in scored_files if sf.get('relevance', 0) > 0),
        key=lambda x: -x[1],
    )
    keyword_rank = {p: i + 1 for i, (p, _) in enumerate(keyword_pairs)}
    keyword_raw = dict(keyword_pairs)

    all_paths = set(keyword_rank) | set(semantic_rank)
    results = []
    for path in all_paths:
        rrf = 0.0
        if path in keyword_rank:
            rrf += _rrf_score(keyword_rank[path])
        if path in semantic_rank:
            rrf += _rrf_score(semantic_rank[path])

        kw_raw = keyword_raw.get(path, 0.0)
        sem_raw = semantic_raw.get(path, 0.0)

        if path in keyword_rank and path in semantic_rank:
            reason = f'rrf hybrid (kw#{keyword_rank[path]}, sem#{semantic_rank[path]})'
        elif path in semantic_rank:
            reason = f'semantic only (rank #{semantic_rank[path]})'
        else:
            reason = f'keyword only (rank #{keyword_rank[path]})'

        results.append({
            'path': path,
            'confidence': round(rrf, 6),
            'keyword_score': round(kw_raw, 4),
            'semantic_score': round(sem_raw, 4),
            'reason': reason,
        })

    results.sort(key=lambda x: -x['confidence'])
    return results[:top_k]


# ── CLI ──

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  embed_resolve.py build [index_path]          # Build/update embeddings")
        print("  embed_resolve.py resolve 'query' [--top N]   # Semantic resolve")
        print("  embed_resolve.py resolve 'query' --hybrid    # Hybrid resolve")
        print("  embed_resolve.py stats                       # Show cache stats")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'build':
        index_path = sys.argv[2] if len(sys.argv) > 2 else INDEX_FILE
        cache = build_embeddings(index_path)
        print(f"\n✓ {len(cache)} files embedded")

    elif cmd == 'resolve':
        if len(sys.argv) < 3:
            print("Usage: embed_resolve.py resolve 'query' [--top N] [--hybrid]", file=sys.stderr)
            sys.exit(1)

        query = sys.argv[2]
        top_k = 10
        hybrid = False

        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == '--top' and i + 1 < len(sys.argv):
                top_k = int(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == '--hybrid':
                hybrid = True
                i += 1
            else:
                i += 1

        if hybrid:
            # Load index and do keyword scoring first
            from pack_context_lib import score_file, tokenize_query
            with open(INDEX_FILE) as f:
                index = json.load(f)
            files = index.get('files', index) if isinstance(index, dict) else index
            if isinstance(files, dict):
                files = list(files.values())
            tokens = tokenize_query(query)
            scored = [{'path': f['path'], 'relevance': score_file(f, tokens, query.lower()),
                       'tokens': f.get('tokens', 0)} for f in files]
            scored = [s for s in scored if s['relevance'] > 0]

            results = resolve_hybrid(query, scored)
        else:
            results = resolve_semantic(query, top_k=top_k)

        if not results:
            print("No results found.")
            sys.exit(0)

        print(f"\nQuery: {query}")
        print(f"Results ({len(results)}):\n")
        for r in results:
            if hybrid:
                print(f"  {r['confidence']:.3f}  {r['path']}")
                print(f"         kw={r['keyword_score']:.3f}  sem={r['semantic_score']:.3f}  ({r['reason']})")
            else:
                print(f"  {r['confidence']:.3f}  {r['path']}  ({r['reason']})")

    elif cmd == 'stats':
        cache = load_cache(CACHE_FILE)
        if not cache:
            print("No cache found.")
            sys.exit(0)

        total = len(cache)
        with_emb = sum(1 for v in cache.values() if v.get('embedding'))
        avg_identity_len = sum(len(v.get('identity', '')) for v in cache.values()) / max(total, 1)

        print(f"Cache: {CACHE_FILE}")
        print(f"  Files: {total}")
        print(f"  With embeddings: {with_emb}")
        print(f"  Avg identity length: {avg_identity_len:.0f} chars")
        print(f"  Embedding dims: {EMBED_DIMS}")
        print(f"  Model: {EMBED_MODEL}")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
