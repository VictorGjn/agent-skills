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

EMBED_BATCH_SIZE = 100
CACHE_FILE = "cache/embeddings.json"
INDEX_FILE = "cache/workspace-index.json"
PENDING_FILE = "cache/pending_embeddings.json"
RESULTS_FILE = "cache/embedding_results.json"
QUERY_EMBEDDING_FILE = "cache/query_embedding.json"


def _resolve_provider() -> dict:
    """
    Resolve embedding provider from env. Auto-detects from API keys; override with EMBED_PROVIDER.
    Returns: {name, base_url, key_env, model, dims, send_dims, dims_param}
    """
    provider = os.environ.get('EMBED_PROVIDER', '').lower()
    if not provider:
        if os.environ.get('MISTRAL_API_KEY'):
            provider = 'mistral'
        elif os.environ.get('VOYAGE_API_KEY'):
            provider = 'voyage'
        else:
            provider = 'openai'

    if provider == 'external':
        # File-based handoff: vectors supplied by an orchestrator (e.g. agent calling MCP).
        # `dump-pending` writes identities; `apply-results` reads vectors from RESULTS_FILE.
        return {
            'name': 'external',
            'base_url': '',
            'key_env': '',
            'model': os.environ.get('EMBED_MODEL', 'mistral-embed'),
            'dims': int(os.environ.get('EMBED_DIMS', '1024')),
            'send_dims': False,
            'dims_param': '',
        }

    if provider == 'mistral':
        model = os.environ.get('EMBED_MODEL', 'codestral-embed')
        # codestral-embed: variable dims via `output_dimension` param (256/512/1024/1536/3072).
        # mistral-embed: fixed 1024, no dims param accepted.
        is_codestral = 'codestral' in model
        return {
            'name': 'mistral',
            'base_url': os.environ.get('EMBED_BASE_URL', 'https://api.mistral.ai/v1'),
            'key_env': 'MISTRAL_API_KEY',
            'model': model,
            'dims': int(os.environ.get('EMBED_DIMS', '1536' if is_codestral else '1024')),
            'send_dims': is_codestral,
            'dims_param': 'output_dimension',
        }

    if provider == 'voyage':
        return {
            'name': 'voyage',
            'base_url': os.environ.get('EMBED_BASE_URL', 'https://api.voyageai.com/v1'),
            'key_env': 'VOYAGE_API_KEY',
            'model': os.environ.get('EMBED_MODEL', 'voyage-code-3'),
            'dims': int(os.environ.get('EMBED_DIMS', '1024')),
            'send_dims': False,
            'dims_param': 'output_dimension',
        }

    # openai (and any OpenAI-compatible local server like Ollama via OPENAI_BASE_URL)
    return {
        'name': 'openai',
        'base_url': os.environ.get('EMBED_BASE_URL', os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')),
        'key_env': os.environ.get('EMBED_API_KEY_ENV', 'OPENAI_API_KEY'),
        'model': os.environ.get('EMBED_MODEL', 'text-embedding-3-small'),
        'dims': int(os.environ.get('EMBED_DIMS', '512')),
        'send_dims': os.environ.get('EMBED_SEND_DIMS', '1') == '1',
        'dims_param': 'dimensions',
    }


PROVIDER = _resolve_provider()
EMBED_MODEL = PROVIDER['model']
EMBED_DIMS = PROVIDER['dims']

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
    Embed a list of texts via the configured provider (OpenAI / Mistral / Voyage / OpenAI-compatible).
    Returns list of embedding vectors.
    """
    import requests

    if PROVIDER['name'] == 'external':
        print("Error: provider=external — use 'dump-pending' + agent-driven MCP embedding + 'apply-results' instead of direct embed.", file=sys.stderr)
        sys.exit(2)

    key = api_key or os.environ.get(PROVIDER['key_env'], '')
    if not key:
        print(f"Error: {PROVIDER['key_env']} not set (provider={PROVIDER['name']})", file=sys.stderr)
        sys.exit(1)

    url = PROVIDER['base_url'].rstrip('/') + '/embeddings'
    all_embeddings = []

    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]

        payload = {"model": EMBED_MODEL, "input": batch}
        if PROVIDER['send_dims']:
            payload[PROVIDER.get('dims_param', 'dimensions')] = EMBED_DIMS

        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
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
    if PROVIDER['name'] == 'external':
        # Query-time handoff: read precomputed query vector written by the agent.
        # Override path with EMBED_QUERY_FILE. Returns None if absent (callers fall back).
        qpath = os.environ.get('EMBED_QUERY_FILE', QUERY_EMBEDDING_FILE)
        p = Path(qpath)
        if not p.exists():
            print(f"Warning: provider=external but no query embedding at {qpath}. Skipping semantic.", file=sys.stderr)
            return None
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('embedding', data) if isinstance(data, dict) else data
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
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(cache: dict, cache_path: str):
    """Save embedding cache."""
    p = Path(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cache, f)
    print(f"Cache saved: {len(cache)} entries → {cache_path}", file=sys.stderr)


# ── External-provider handoff (agent drives MCP calls via files) ──

def dump_pending(index_path: str, cache_path: str = CACHE_FILE, pending_path: str = PENDING_FILE) -> int:
    """
    Compute which files need embedding (same logic as build_embeddings) and write them
    to pending_path. Returns count of pending entries.
    Format: {"model": str, "dims": int, "items": [{"path": str, "hash": str, "identity": str}, ...]}
    """
    with open(index_path, encoding='utf-8') as f:
        index = json.load(f)
    files = index.get('files', index) if isinstance(index, dict) else index
    if isinstance(files, dict):
        files = list(files.values())

    cache = load_cache(cache_path)
    items = []
    for entry in files:
        path = entry['path']
        content_hash = entry.get('contentHash', entry.get('hash', ''))
        cached = cache.get(path)
        if cached and cached.get('hash') == content_hash and cached.get('embedding'):
            continue
        identity = build_identity(entry)
        items.append({'path': path, 'hash': content_hash, 'identity': identity})

    p = Path(pending_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {'model': PROVIDER['model'], 'dims': PROVIDER['dims'], 'items': items}
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"Wrote {len(items)} pending -> {pending_path}", file=sys.stderr)
    return len(items)


def apply_results(index_path: str, cache_path: str = CACHE_FILE, results_path: str = RESULTS_FILE):
    """
    Read embedding vectors from results_path and merge into cache.
    Format: {"items": [{"path": str, "hash": str, "embedding": [float, ...]}, ...]}
    """
    with open(results_path, encoding='utf-8') as f:
        results = json.load(f)
    items = results.get('items', results) if isinstance(results, dict) else results

    with open(index_path, encoding='utf-8') as f:
        index = json.load(f)
    files = index.get('files', index) if isinstance(index, dict) else index
    if isinstance(files, dict):
        files = list(files.values())
    by_path = {e['path']: e for e in files}

    cache = load_cache(cache_path)
    applied = 0
    for item in items:
        path = item['path']
        emb = item.get('embedding')
        if not emb:
            continue
        entry = by_path.get(path)
        if not entry:
            continue
        identity = cache.get(path, {}).get('identity') or build_identity(entry)
        cache[path] = {
            'hash': item.get('hash', entry.get('contentHash', entry.get('hash', ''))),
            'identity': identity,
            'embedding': emb,
        }
        applied += 1

    # Remove stale entries (files no longer in index)
    index_paths = set(by_path.keys())
    stale = [p for p in cache if p not in index_paths]
    for p in stale:
        del cache[p]

    save_cache(cache, cache_path)
    print(f"Applied {applied} embeddings; cache now has {len(cache)} entries.", file=sys.stderr)


# ── Build embeddings ──

def build_embeddings(index_path: str, cache_path: str = CACHE_FILE, api_key: str = None):
    """
    Build or update embeddings for all files in the index.
    Only recomputes when file hash changes.
    """
    with open(index_path, encoding='utf-8') as f:
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
    if not query_embedding:
        return []

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
# Theoretical max RRF score: rank 1 in BOTH the keyword and semantic lists.
# Used to normalise raw RRF values into a 0..1 scale so downstream
# `relevance_to_depth` cutoffs (0.15/0.25/0.40/0.65) keep working.
_RRF_MAX = 2.0 / (RRF_K + 1)


def _rrf_score(rank: int, k: int = RRF_K) -> float:
    """Reciprocal Rank Fusion contribution for a given 1-based rank."""
    return 1.0 / (k + rank)


def resolve_hybrid(query: str, scored_files: list, cache_path: str = CACHE_FILE,
                   top_k: int = 15, semantic_weight: 'float | None' = None,
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

    # Anti-noise gates — match the original linear-blend's `combined < 0.1`
    # contract. Without these, weak hits fill `top_k` whenever the
    # complementary signal is missing (empty cache → keyword-only path,
    # or all-low-cosine → semantic-only path).
    SEM_MIN_COSINE = 0.15
    KW_MIN_RELEVANCE = 0.10

    semantic_pairs = []
    if query_embedding:
        for path, entry in cache.items():
            emb = entry.get('embedding')
            if emb:
                sim = cosine_similarity(query_embedding, emb)
                if sim >= SEM_MIN_COSINE:
                    semantic_pairs.append((path, sim))
    semantic_pairs.sort(key=lambda x: -x[1])
    semantic_rank = {p: i + 1 for i, (p, _) in enumerate(semantic_pairs)}
    semantic_raw = dict(semantic_pairs)

    # Keyword ranking — apply the same noise floor so weak keyword matches
    # don't pack at Detail/Summary depth when semantic is missing.
    keyword_pairs = sorted(
        ((sf['path'], sf['relevance']) for sf in scored_files
         if sf.get('relevance', 0) >= KW_MIN_RELEVANCE),
        key=lambda x: -x[1],
    )
    keyword_rank = {p: i + 1 for i, (p, _) in enumerate(keyword_pairs)}
    keyword_raw = dict(keyword_pairs)

    all_paths = set(keyword_rank) | set(semantic_rank)
    results = []
    for path in all_paths:
        kw_raw = keyword_raw.get(path, 0.0)
        sem_raw = semantic_raw.get(path, 0.0)

        # `confidence` MUST reflect actual match strength, because
        # downstream `pack_context_lib.relevance_to_depth` uses fixed
        # thresholds (0.15/0.25/0.40/0.65) to pick a depth band. Rank-fusion
        # values would inflate weak matches (rank 15 in both lists → ~0.81
        # via normalised RRF) and pack noise at Detail/Full.
        #
        # Use the raw scores. RRF (computed below) is kept as a tie-break
        # bonus so files confirmed in BOTH rankings sort above same-strength
        # files seen in only one — without inflating the absolute score.
        if path in keyword_rank and path in semantic_rank:
            confidence = max(kw_raw, sem_raw)
            rrf_bonus = _rrf_score(keyword_rank[path]) + _rrf_score(semantic_rank[path])
            reason = (f'hybrid (kw={kw_raw:.3f}#{keyword_rank[path]}, '
                      f'sem={sem_raw:.3f}#{semantic_rank[path]})')
        elif path in semantic_rank:
            confidence = sem_raw
            rrf_bonus = _rrf_score(semantic_rank[path])
            reason = f'semantic only (cos={sem_raw:.3f})'
        else:
            confidence = kw_raw
            rrf_bonus = _rrf_score(keyword_rank[path])
            reason = f'keyword only (rel={kw_raw:.3f})'

        results.append({
            'path': path,
            'confidence': round(confidence, 4),
            'keyword_score': round(kw_raw, 4),
            'semantic_score': round(sem_raw, 4),
            '_rrf_bonus': rrf_bonus,
            'reason': reason,
        })

    # Sort primarily by raw match strength, RRF as tie-break. Drop the
    # internal _rrf_bonus before returning so callers see a clean shape.
    results.sort(key=lambda x: (-x['confidence'], -x['_rrf_bonus']))
    for r in results:
        del r['_rrf_bonus']
    return results[:top_k]


# ── CLI ──

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  embed_resolve.py build [index_path]                         # Build/update via configured provider")
        print("  embed_resolve.py dump-pending [index_path] [pending_path]   # Write identities for external embedding")
        print("  embed_resolve.py apply-results [index_path] [results_path]  # Merge externally-supplied vectors")
        print("  embed_resolve.py resolve 'query' [--top N]                  # Semantic resolve")
        print("  embed_resolve.py resolve 'query' --hybrid                   # Hybrid resolve")
        print("  embed_resolve.py stats                                      # Show cache stats")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'build':
        index_path = sys.argv[2] if len(sys.argv) > 2 else INDEX_FILE
        cache = build_embeddings(index_path)
        print(f"\n[ok] {len(cache)} files embedded")

    elif cmd == 'dump-pending':
        index_path = sys.argv[2] if len(sys.argv) > 2 else INDEX_FILE
        pending_path = sys.argv[3] if len(sys.argv) > 3 else PENDING_FILE
        n = dump_pending(index_path, pending_path=pending_path)
        print(f"\n[ok] {n} pending identities -> {pending_path}")

    elif cmd == 'apply-results':
        index_path = sys.argv[2] if len(sys.argv) > 2 else INDEX_FILE
        results_path = sys.argv[3] if len(sys.argv) > 3 else RESULTS_FILE
        apply_results(index_path, results_path=results_path)

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
            with open(INDEX_FILE, encoding='utf-8') as f:
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
        print(f"  Provider: {PROVIDER['name']}")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
