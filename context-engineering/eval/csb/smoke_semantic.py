"""End-to-end smoke for Phase 5.5 semantic mode against the deployed CE MCP.

Uploads a tiny synthetic corpus (3 files, 4-d vectors carefully chosen so a
known query embedding direction has a known winner). Then calls
ce_find_relevant_files with mode=semantic and rerank=mmr to confirm the
production server:
  1) embeds the query via its own MISTRAL_API_KEY (the test query mentions
     "authentication bearer token" — codestral will produce a real vector
     here, so the synthetic file vectors must be aligned with the right
     conceptual direction in 1536-d, NOT 4-d).

Because we cannot align synthetic 4-d test vectors with codestral's 1536-d
embedding space, this smoke uses a different strategy: upload with the
SERVER's expected embedding shape (1536-d) by setting all file vectors to
zero (so cosine = 0 for everything → semantic ranks nothing → soft fallback
to keyword fires). That's enough to prove the request path doesn't crash on
semantic mode in production.

For a real semantic smoke that actually exercises cosine ranking, need
MISTRAL_API_KEY in local env to embed file content via setup_corpus.py.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


MCP_URL = os.environ.get("CE_MCP_URL", "https://ce-mcp-prod.vercel.app")
TOKEN = os.environ.get("CE_MCP_TOKEN") or os.environ.get("CE_MCP_BOOTSTRAP_TOKEN")

if not TOKEN:
    print("set CE_MCP_TOKEN (or CE_MCP_BOOTSTRAP_TOKEN)", file=sys.stderr)
    sys.exit(2)


def _post(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{MCP_URL}/api/mcp", data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"_http_error": e.code, "_body": body[:1500]}


def main() -> int:
    # 1. Upload a tiny corpus with synthetic 1536-d vectors. We use a corpus_id
    #    that won't collide with real ones.
    corpus_id = "smoke-phase-5-5-semantic"
    DIMS = 1536
    files = [
        {"path": f"f{i}.py", "contentHash": f"h{i}", "tokens": 100,
         "tree": {"text": f"file {i}", "children": [],
                  "title": f"f{i}.py", "firstSentence": f"file {i}",
                  "firstParagraph": f"file {i}", "totalTokens": 5},
         "knowledge_type": "evidence"}
        for i in range(3)
    ]
    # Three orthogonal-ish 1536-d vectors: f0 has 1.0 in slot 0, f1 in slot 100, f2 in slot 200
    def axis(slot: int) -> list[float]:
        v = [0.0] * DIMS
        v[slot] = 1.0
        return v
    vectors = [axis(0), axis(100), axis(200)]

    upload = _post({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "ce_upload_corpus",
            "arguments": {
                "corpus_id": corpus_id,
                "source": {"type": "local_workspace", "uri": "smoke://phase-5-5"},
                "data_classification": "public",
                "embedding": {"provider": "mistral", "model": "codestral-embed", "dims": DIMS},
                "files": files,
                "embeddings": {
                    "format": "json",
                    "paths": [f["path"] for f in files],
                    "hashes": [f["contentHash"] for f in files],
                    "vectors": vectors,
                },
            },
        },
    })
    print("upload result:", json.dumps(upload.get("result", upload), indent=2)[:600])

    # 2. Query in semantic mode. The server will embed the query via Mistral
    #    (which produces a real 1536-d vector) and cosine-rank against our
    #    axis-aligned synthetics. The result will be near-random since
    #    codestral's natural geometry isn't aligned with axis(0) etc., but
    #    the dispatch path will fire and return non-zero semantic_score on
    #    at least the top file.
    find_resp = _post({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "ce_find_relevant_files",
            "arguments": {
                "query": "authentication bearer token validation",
                "corpus_id": corpus_id,
                "mode": "semantic",
                "top_k": 3,
            },
        },
    })
    print("\nfind (mode=semantic):")
    inner = find_resp.get("result", {}).get("structuredContent", {})
    for f in inner.get("files", []):
        print(f"  {f.get('path'):20s}  rel={f.get('relevance'):.4f}  "
              f"semantic={f.get('semantic_score'):.4f}  reason={f.get('reason')}")

    # 3. Same query with rerank=mmr to verify the rerank path validates and runs.
    mmr_resp = _post({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "ce_find_relevant_files",
            "arguments": {
                "query": "authentication bearer token validation",
                "corpus_id": corpus_id,
                "mode": "semantic",
                "top_k": 3,
                "rerank": "mmr",
            },
        },
    })
    print("\nfind (mode=semantic, rerank=mmr):")
    inner = mmr_resp.get("result", {}).get("structuredContent", {})
    for f in inner.get("files", []):
        print(f"  {f.get('path'):20s}  rel={f.get('relevance'):.4f}  "
              f"semantic={f.get('semantic_score'):.4f}  reason={f.get('reason')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
