"""CSB pre-task hook — register a corpus for the task's repo.

Run this BEFORE the agent starts a CSB task. It:
1. Reads the task spec to get repo + branch (or accepts them via flags)
2. Calls ce_index_github_repo on the production CE MCP (or ce_upload_corpus
   for repos the server's GitHub App can't read)
3. Echoes the resulting corpus_id so the harness can export it as
   CE_CORPUS_ID in the agent shell

Usage:

    # Public repo, server-side index
    python setup_corpus.py --repo owner/name --branch main \\
                           --classification public \\
                           --mcp-url https://ce-mcp.vercel.app \\
                           --token $CE_MCP_TOKEN

    # Private/local repo, client-side upload
    python setup_corpus.py --upload --workspace ./repo \\
                           --corpus-id local-foo-main \\
                           --classification internal \\
                           --mcp-url ... --token ...

The script prints `CE_CORPUS_ID=<id>` to stdout on success. The harness
captures and sets it for the agent.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error


def _post(url: str, token: str, payload: dict, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", errors="replace")}


def index_via_server(mcp_url: str, token: str, repo: str, branch: str,
                     classification: str) -> str:
    """Call ce_index_github_repo. Returns corpus_id on success, raises on failure."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "ce_index_github_repo",
            "arguments": {
                "repo": repo, "branch": branch,
                "data_classification": classification,
                "async": False,
            },
        },
    }
    resp = _post(f"{mcp_url}/api/mcp", token, payload, timeout=180)
    if "_http_error" in resp:
        raise RuntimeError(f"index_github_repo HTTP {resp['_http_error']}: {resp['_body'][:300]}")
    if "error" in resp:
        raise RuntimeError(f"index_github_repo JSON-RPC error: {resp['error']}")
    result = resp.get("result", {}).get("structuredContent", {})
    if result.get("isError"):
        raise RuntimeError(f"index_github_repo tool error: {result}")
    cid = result.get("corpus_id")
    if not cid:
        raise RuntimeError(f"index_github_repo returned no corpus_id: {result}")
    return cid


def _embed_files_mistral(workspace: str, files: list[dict]
                         ) -> tuple[list[dict], list[list[float]]]:
    """Read each file's content from disk and call Mistral codestral-embed in batches.

    Returns (kept_files, kept_vectors) — drops files that can't be embedded
    (empty content, OSError, contains null bytes after replace-decode) from
    BOTH lists in lockstep so callers can build a payload where len(files) ==
    len(vectors) and every vector has the right dim. Sending `[]` placeholders
    would fail server-side EMBEDDING_MISMATCH validation (dims=1536 vs len=0).
    """
    from pathlib import Path

    here = Path(__file__).resolve()
    server_lib = here.parent.parent.parent / "server-prod" / "_lib"
    if str(server_lib.parent) not in sys.path:
        sys.path.insert(0, str(server_lib.parent))
    from _lib import embed as _embed  # type: ignore

    ws = Path(workspace)
    kept_files: list[dict] = []
    kept_texts: list[str] = []
    skipped = 0
    for f in files:
        path = ws / f["path"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        if not text.strip() or "\x00" in text:
            skipped += 1
            continue
        kept_files.append(f)
        kept_texts.append(text)

    if skipped:
        print(f"  skipped {skipped} unembeddable files (empty/binary/unreadable)",
              file=sys.stderr)
    print(f"  embedding {len(kept_texts)} files via Mistral codestral-embed…",
          file=sys.stderr)
    vectors = _embed.embed_batch(kept_texts) if kept_texts else []
    return kept_files, vectors


def upload_via_local_index(mcp_url: str, token: str, workspace: str,
                           corpus_id: str, classification: str,
                           embed_provider: str = "auto") -> str:
    """Index a local workspace via scripts/index_workspace.scan_directory, then ce_upload_corpus.

    `embed_provider`:
    - `"auto"` (default): use Mistral codestral-embed when MISTRAL_API_KEY is
      set in the local env, else upload with no vectors (keyword-only corpus).
    - `"mistral"`: require MISTRAL_API_KEY; fail fast if missing.
    - `"none"`: skip embedding entirely (cheap, keyword-only).

    In-process import is cleaner than subprocess: index_workspace.py's main
    block writes to a hardcoded `cache/workspace-index.json` path with no
    --output flag, so the prior subprocess approach failed (Codex P1). The
    underlying scan_directory function returns the index dict directly.
    """
    import os
    from pathlib import Path

    here = Path(__file__).resolve()
    scripts = here.parent.parent.parent / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import index_workspace as _iw  # type: ignore

    idx = _iw.scan_directory(workspace)
    files = idx.get("files", [])
    # Normalize legacy `hash` field — scripts/* emit `hash`, server reads `contentHash`.
    for f in files:
        if "contentHash" not in f and "hash" in f:
            f["contentHash"] = f["hash"]

    # ── Embeddings ──
    use_mistral = embed_provider == "mistral" or (
        embed_provider == "auto" and os.environ.get("MISTRAL_API_KEY")
    )
    if embed_provider == "mistral" and not os.environ.get("MISTRAL_API_KEY"):
        raise RuntimeError("--embed-provider=mistral requires MISTRAL_API_KEY in env")

    if use_mistral:
        embedding_meta = {"provider": "mistral", "model": "codestral-embed", "dims": 1536}
        # Filter files to only those we successfully embedded — server rejects
        # any zero-length vector when dims>0, so payload arrays must be aligned
        # (same length, same indices, same files).
        files, vectors = _embed_files_mistral(workspace, files)
    else:
        embedding_meta = {"provider": "none", "model": "n/a", "dims": 0}
        vectors = [[] for _ in files]

    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "ce_upload_corpus",
            "arguments": {
                "corpus_id": corpus_id,
                "source": {"type": "local_workspace", "uri": workspace},
                "data_classification": classification,
                "embedding": embedding_meta,
                "files": files,
                "embeddings": {
                    "format": "json",
                    "paths": [f["path"] for f in files],
                    "hashes": [f.get("contentHash", "") for f in files],
                    "vectors": vectors,
                },
            },
        },
    }
    resp = _post(f"{mcp_url}/api/mcp", token, payload, timeout=300)
    if "_http_error" in resp:
        raise RuntimeError(f"upload_corpus HTTP {resp['_http_error']}: {resp['_body'][:300]}")
    if "error" in resp:
        raise RuntimeError(f"upload_corpus JSON-RPC error: {resp['error']}")
    result = resp.get("result", {}).get("structuredContent", {})
    if result.get("isError"):
        raise RuntimeError(f"upload_corpus tool error: {result}")
    cid = result.get("corpus_id")
    if not cid:
        raise RuntimeError(f"upload_corpus returned no corpus_id: {result}")
    return cid


def main() -> int:
    p = argparse.ArgumentParser(description="Register a corpus for a CSB task on the CE MCP.")
    p.add_argument("--mcp-url", required=True, help="Base URL of the CE MCP server (e.g. https://ce-mcp.vercel.app)")
    p.add_argument("--token", required=True, help="Bearer token for the CE MCP")
    p.add_argument("--classification", default="public",
                   choices=["public", "internal", "confidential", "restricted"])

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--repo", help="GitHub repo owner/name (server-side index)")
    mode.add_argument("--upload", action="store_true", help="Index a local workspace + upload")

    p.add_argument("--branch", default="main", help="Branch (server-side index only)")
    p.add_argument("--workspace", help="Path to local workspace (--upload mode only)")
    p.add_argument("--corpus-id", help="Explicit corpus_id (defaults to server-derived)")
    p.add_argument("--embed-provider", choices=["auto", "mistral", "none"], default="auto",
                   help="Embedding provider for --upload mode. auto = Mistral if MISTRAL_API_KEY set, else none.")

    args = p.parse_args()

    try:
        if args.repo:
            cid = index_via_server(args.mcp_url, args.token, args.repo, args.branch, args.classification)
        else:
            if not args.workspace:
                p.error("--upload requires --workspace")
            cid = args.corpus_id or "local-csb-task"
            cid = upload_via_local_index(args.mcp_url, args.token, args.workspace, cid,
                                         args.classification, embed_provider=args.embed_provider)
    except Exception as e:
        print(f"setup_corpus FAILED: {e}", file=sys.stderr)
        return 2

    # Stdout is captured by the harness — keep it parsable.
    print(f"CE_CORPUS_ID={cid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
