"""Smoke server-side embedding via ce_index_github_repo on a small public repo.

Tests the Phase 5.5 B path: when MISTRAL_API_KEY is set on the server and
the repo is small enough to fit the 50s sync budget, the index call returns
with `embedded_count > 0` and a populated embeddings map. Then a `mode=semantic`
find call should produce real cosine-ranked results (not the synthetic
axis-vector stunt from smoke_semantic.py).

Picks `nicolargo/glances/issues` style — no, nicolargo/glances is too big.
Use a deliberately tiny public repo. Defaults to `octocat/Hello-World`.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


MCP_URL = os.environ.get("CE_MCP_URL", "https://ce-mcp-prod.vercel.app")
TOKEN = os.environ.get("CE_MCP_TOKEN") or os.environ.get("CE_MCP_BOOTSTRAP_TOKEN")
TARGET_REPO = os.environ.get("SMOKE_REPO", "octocat/Hello-World")
TARGET_BRANCH = os.environ.get("SMOKE_BRANCH", "master")

if not TOKEN:
    print("set CE_MCP_TOKEN", file=sys.stderr)
    sys.exit(2)


def _post(payload: dict, *, timeout: int = 120) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{MCP_URL}/api/mcp", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", errors="replace")[:1500]}


def main() -> int:
    print(f"# Indexing {TARGET_REPO}@{TARGET_BRANCH} via ce_index_github_repo (server embeds)…")
    idx = _post({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "ce_index_github_repo",
            "arguments": {
                "repo": TARGET_REPO,
                "branch": TARGET_BRANCH,
                "data_classification": "public",
            },
        },
    })
    if "_http_error" in idx:
        print("HTTP", idx["_http_error"], idx["_body"])
        return 1
    inner = idx.get("result", {}).get("structuredContent", {})
    if idx.get("result", {}).get("isError"):
        print("tool error:", json.dumps(inner, indent=2)[:600])
        return 1
    print("# index result:")
    print(json.dumps(inner, indent=2))
    corpus_id = inner["corpus_id"]
    embedded_count = inner["stats"]["embedded_count"]
    skipped = inner.get("embed_skipped")
    if skipped:
        print(f"# embed skipped: {skipped}")

    print(f"\n# ce_find_relevant_files mode=semantic on {corpus_id}…")
    find_resp = _post({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "ce_find_relevant_files",
            "arguments": {
                "query": "what does this repo demonstrate",
                "corpus_id": corpus_id,
                "mode": "semantic",
                "top_k": 5,
            },
        },
    })
    inner = find_resp.get("result", {}).get("structuredContent", {})
    if find_resp.get("result", {}).get("isError"):
        print("tool error:", json.dumps(inner, indent=2)[:600])
        return 1
    if embedded_count == 0:
        print("# (semantic falls back to keyword because corpus has no embeddings)")
    for f in inner.get("files", []):
        print(f"  {f.get('path'):30s}  rel={f.get('relevance'):.4f}  "
              f"semantic={f.get('semantic_score'):.4f}  reason={f.get('reason')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
