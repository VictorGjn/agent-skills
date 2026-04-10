"""
MCP Server for Context Engineering.

Exposes depth-packed context loading as MCP tools.
Any AI agent that speaks MCP can index repos and pack context.

Install: uv add "mcp[cli]"
Run:     uv run fastmcp dev mcp_server.py
         uv run python mcp_server.py              # stdio (local)
         uv run python mcp_server.py --http 8000  # remote
"""

import json
import sys
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from pack_context_lib import (
    tokenize_query, score_file, pack_context, classify_knowledge_type,
    estimate_tokens, DEPTH_NAMES,
)

mcp = FastMCP(
    "context-engineering",
    version="1.0.0",
    description="Depth-packed context loading for codebases. Pack 40+ files at 5 depth levels into any token budget.",
)

SCRIPT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = SCRIPT_DIR / "cache"
INDEX_PATH = CACHE_DIR / "workspace-index.json"
EMBED_CACHE_PATH = CACHE_DIR / "embeddings.json"


def _load_index(index_path: str = None) -> dict:
    p = Path(index_path) if index_path else INDEX_PATH
    if not p.exists():
        raise FileNotFoundError(f"No index at {p}. Run index_workspace or index_github_repo first.")
    with open(p) as f:
        return json.load(f)


def _render_at_depth(tree: dict, depth_level: int, file_path: str) -> str:
    """Render file content at a given depth level."""
    if not tree:
        return f"- `{file_path}`"
    if depth_level == 4:
        return f"- `{file_path}` ({tree.get('totalTokens', 0)} tok)"
    if depth_level == 3:
        lines = [f"### {file_path}"]
        for h in _collect_headings(tree, max_depth=3):
            indent = "  " * max(0, h["depth"] - 1)
            lines.append(f"{indent}- {h['title']} ({h['tokens']} tok)")
        return "\n".join(lines)
    if depth_level == 2:
        lines = [f"### {file_path}"]
        for node in _walk(tree):
            if node["depth"] > 0 and node.get("title"):
                lines.append(f"{'#' * min(node['depth'] + 2, 6)} {node['title']}")
            if node.get("firstSentence"):
                lines.append(node["firstSentence"])
                lines.append("")
        return "\n".join(lines)
    if depth_level <= 1:
        key = "firstParagraph" if depth_level == 1 else "text"
        lines = [f"### {file_path}"]
        for node in _walk(tree):
            if node["depth"] > 0 and node.get("title"):
                lines.append(f"{'#' * min(node['depth'] + 2, 6)} {node['title']}")
            if node.get(key):
                lines.append(node[key])
                lines.append("")
        return "\n".join(lines)
    return f"- `{file_path}`"


def _collect_headings(node, max_depth=3):
    h = []
    if node.get("depth", 0) > 0 and node["depth"] <= max_depth:
        h.append({"depth": node["depth"], "title": node.get("title", ""), "tokens": node.get("totalTokens", 0)})
    for c in node.get("children", []):
        h.extend(_collect_headings(c, max_depth))
    return h


def _walk(node):
    yield node
    for c in node.get("children", []):
        yield from _walk(c)


# ── Tools ──


@mcp.tool()
def pack(
    query: str,
    budget: int = 8000,
    mode: str = "keyword",
    top: int = 30,
    graphify_path: str = None,
) -> str:
    """
    Pack depth-graded context for a query within a token budget.

    Args:
        query: Natural language query (e.g., "how does authentication work?")
        budget: Token budget (default 8000). Use 4000 for quick scans, 16000 for deep dives.
        mode: Resolution mode. "keyword" (free, fast), "semantic" (hybrid embedding+keyword),
              "graph" (follows imports/deps), "semantic+graph" (full pipeline).
        top: Maximum files to consider before packing.
        graphify_path: Optional path to graphify graph.json. Auto-discovers at
                       {workspace}/graphify-out/graph.json when None and mode includes "graph".

    Returns:
        Depth-packed markdown context with files at Full/Detail/Summary/Headlines/Mention levels.
    """
    index = _load_index()
    query_tokens = tokenize_query(query)
    query_lower = query.lower()

    if not query_tokens:
        return "Empty query."

    if "semantic" in mode:
        from embed_resolve import resolve_hybrid
        keyword_scored = []
        for f in index["files"]:
            rel = score_file(f, query_tokens, query_lower)
            keyword_scored.append({
                "path": f["path"], "relevance": rel,
                "tokens": f["tokens"], "tree": f.get("tree"),
                "knowledge_type": f.get("knowledge_type", "evidence"),
            })
        kw_with_score = [s for s in keyword_scored if s["relevance"] > 0]
        hybrid = resolve_hybrid(query, kw_with_score, cache_path=str(EMBED_CACHE_PATH), top_k=top)
        file_index = {f["path"]: f for f in index["files"]}
        scored = []
        for hr in hybrid:
            fi = file_index.get(hr["path"])
            if fi:
                scored.append({
                    "path": hr["path"], "relevance": hr["confidence"],
                    "tokens": fi["tokens"], "tree": fi.get("tree"),
                    "knowledge_type": fi.get("knowledge_type", "evidence"),
                })
    else:
        scored = []
        for f in index["files"]:
            rel = score_file(f, query_tokens, query_lower)
            if rel > 0:
                scored.append({
                    "path": f["path"], "relevance": rel, "tokens": f["tokens"],
                    "tree": f.get("tree"), "knowledge_type": f.get("knowledge_type", "evidence"),
                })
        scored.sort(key=lambda x: x["relevance"], reverse=True)
        scored = scored[:top]

    if "graph" in mode:
        from code_graph import build_graph_with_fallback, traverse_from, find_entry_points

        # Auto-discover graphify graph.json
        _gp = graphify_path
        if _gp is None:
            workspace_root = index.get("metadata", {}).get("root") or str(INDEX_PATH.parent)
            candidate = Path(workspace_root) / "graphify-out" / "graph.json"
            if candidate.exists():
                _gp = str(candidate)

        graph = build_graph_with_fallback(index["files"], graphify_path=_gp)
        entry_points = find_entry_points(scored[:10], threshold=0.2)
        if entry_points:
            traversed = traverse_from(entry_points, graph, max_depth=3, max_files=top)
            file_index = {f["path"]: f for f in index["files"]}
            merged = {s["path"]: s for s in scored}
            for t in traversed:
                path = t["path"]
                if path not in merged:
                    fi = file_index.get(path)
                    if fi:
                        merged[path] = {
                            "path": path, "relevance": t["relevance"],
                            "tokens": fi["tokens"], "tree": fi.get("tree"),
                            "knowledge_type": fi.get("knowledge_type", "evidence"),
                        }
                else:
                    merged[path]["relevance"] = min(1.0, max(merged[path]["relevance"], t["relevance"]))
            scored = sorted(merged.values(), key=lambda x: x["relevance"], reverse=True)[:top]

    if not scored:
        return f'No files matched: "{query}"'

    packed = pack_context(scored, budget)
    total_tokens = sum(it["tokens"] for it in packed)

    sections = {"Full": [], "Detail": [], "Summary": [], "Headlines": [], "Mention": []}
    for item in packed:
        dn = DEPTH_NAMES[item["depth"]]
        rendered = _render_at_depth(item.get("tree"), item["depth"], item["path"])
        sections[dn].append(rendered)

    out = [f'<!-- depth-packed [{mode}] query="{query}" budget={budget} used=~{total_tokens} files={len(packed)} -->']
    out.append("")
    for dn in ["Full", "Detail", "Summary", "Headlines", "Mention"]:
        if sections[dn]:
            out.append(f"## {dn} ({len(sections[dn])} files)\n")
            out.append("\n\n".join(sections[dn]))
            out.append("")

    return "\n".join(out)


@mcp.tool()
def index_workspace(path: str) -> str:
    """
    Index a local directory for context packing.

    Args:
        path: Absolute path to the directory to index.

    Returns:
        Summary of indexed files and token counts.
    """
    import subprocess
    script = str(Path(__file__).parent / "index_workspace.py")
    result = subprocess.run(
        [sys.executable, script, path],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return result.stdout or f"Indexed. Check {INDEX_PATH}"


@mcp.tool()
def index_github_repo(repo: str, branch: str = "main") -> str:
    """
    Index a GitHub repository for context packing.

    Args:
        repo: GitHub repo in "owner/name" format.
        branch: Branch to index (default "main").

    Returns:
        Summary of indexed files.
    """
    import subprocess
    script = str(Path(__file__).parent / "index_github_repo.py")
    result = subprocess.run(
        [sys.executable, script, repo, "--branch", branch],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return result.stdout or f"Indexed {repo}. Check {INDEX_PATH}"


@mcp.tool()
def build_embeddings() -> str:
    """
    Build or refresh the embedding cache for semantic resolution.
    Only re-embeds files whose content hash changed. Costs ~$0.01 per 500 files.

    Returns:
        Summary of embedding cache state.
    """
    from embed_resolve import build_embeddings as _build
    cache = _build(str(INDEX_PATH), str(EMBED_CACHE_PATH))
    return f"Embedding cache: {len(cache)} files. Saved to {EMBED_CACHE_PATH}"


@mcp.tool()
def resolve(query: str, mode: str = "hybrid", top: int = 10) -> str:
    """
    Find the most relevant files for a query without packing.
    Useful for debugging or understanding what the packer would select.

    Args:
        query: Natural language query.
        mode: "keyword", "semantic", or "hybrid" (default).
        top: Max results.

    Returns:
        Ranked list of files with scores.
    """
    index = _load_index()
    query_tokens = tokenize_query(query)
    query_lower = query.lower()

    if mode in ("semantic", "hybrid"):
        from embed_resolve import resolve_hybrid, resolve_semantic
        kw_scored = []
        for f in index["files"]:
            rel = score_file(f, query_tokens, query_lower)
            if rel > 0:
                kw_scored.append({"path": f["path"], "relevance": rel, "tokens": f.get("tokens", 0)})

        if mode == "hybrid":
            results = resolve_hybrid(query, kw_scored, cache_path=str(EMBED_CACHE_PATH), top_k=top)
        else:
            results = resolve_semantic(query, cache_path=str(EMBED_CACHE_PATH), top_k=top)

        lines = [f"Query: {query}", f"Mode: {mode}", f"Results ({len(results)}):", ""]
        for r in results:
            kw = r.get("keyword_score", 0)
            sem = r.get("semantic_score", r.get("confidence", 0))
            lines.append(f"  {r.get('confidence', sem):.3f}  {r['path']}  (kw={kw:.3f} sem={sem:.3f})  {r.get('reason', '')}")
        return "\n".join(lines)
    else:
        scored = []
        for f in index["files"]:
            rel = score_file(f, query_tokens, query_lower)
            if rel > 0:
                scored.append({"path": f["path"], "relevance": rel})
        scored.sort(key=lambda x: x["relevance"], reverse=True)
        lines = [f"Query: {query}", f"Mode: keyword", f"Results ({min(len(scored), top)}):", ""]
        for s in scored[:top]:
            lines.append(f"  {s['relevance']:.3f}  {s['path']}")
        return "\n".join(lines)


@mcp.tool()
def stats() -> str:
    """
    Show index and embedding cache statistics.

    Returns:
        Summary of indexed files, token counts, and embedding cache state.
    """
    lines = []

    if INDEX_PATH.exists():
        with open(INDEX_PATH) as f:
            index = json.load(f)
        files = index.get("files", [])
        total_tokens = sum(f.get("tokens", 0) for f in files)
        lines.append(f"Index: {len(files)} files, {total_tokens:,} tokens total")
        lines.append(f"  Path: {INDEX_PATH}")

        kt_counts = {}
        for f in files:
            kt = f.get("knowledge_type", "unknown")
            kt_counts[kt] = kt_counts.get(kt, 0) + 1
        for kt, count in sorted(kt_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {kt}: {count} files")
    else:
        lines.append("No index found. Run index_workspace or index_github_repo.")

    lines.append("")

    if EMBED_CACHE_PATH.exists():
        with open(EMBED_CACHE_PATH) as f:
            cache = json.load(f)
        total = len(cache)
        with_emb = sum(1 for v in cache.values() if v.get("embedding"))
        lines.append(f"Embeddings: {with_emb}/{total} files cached")
        lines.append(f"  Path: {EMBED_CACHE_PATH}")
    else:
        lines.append("No embedding cache. Run build_embeddings for semantic mode.")

    return "\n".join(lines)


if __name__ == "__main__":
    if "--http" in sys.argv:
        idx = sys.argv.index("--http")
        port = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 8000
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        mcp.run()
