"""Bridge to the production CE pack engine.

The engine ships the canonical scoring + packing pipeline (5 depth bands,
knowledge-type weighting, 3-phase pack/demote/promote). The transport layer
turns its outputs into the SPEC § 3.1 / § 3.2 wire shape.

We import a vendored copy at `_lib.vendor.pack_context_lib` rather than the
canonical `scripts/pack_context_lib.py` because Vercel's function bundler
can't reach parent directories outside the function root. The vendor copy
is sha-checked against canonical via the test_phase5 sync check.
"""
from __future__ import annotations


def _import_lib():
    """Lazy import — keeps cold-start cheap when tools that don't need it are called."""
    from .vendor import pack_context_lib  # type: ignore
    return pack_context_lib


# Map engine depth ints (0..4) to SPEC § 3.1 depth strings.
# Engine: 0=Full, 1=Detail, 2=Summary, 3=Headlines, 4=Mention
# SPEC depth enum: Full | Detail | Summary | Structure | Mention
# Headlines ↔ Structure are equivalent (the engine label predates the spec
# rename); we emit the SPEC-canonical name on the wire.
_DEPTH_NAMES = {
    0: "Full",
    1: "Detail",
    2: "Summary",
    3: "Structure",
    4: "Mention",
}


def score_corpus(query: str, files: list[dict], top: int = 100) -> list[dict]:
    """Keyword-score every file in a corpus and return the top-N as scored items.

    Each scored item carries: path, relevance, tokens, tree, knowledge_type.
    Items with relevance == 0 are dropped before truncation.
    """
    lib = _import_lib()
    tokens = lib.tokenize_query(query)
    if not tokens:
        return []
    q_lower = query.lower()
    scored = []
    for f in files:
        rel = lib.score_file(f, tokens, q_lower)
        if rel <= 0:
            continue
        scored.append({
            "path": f["path"],
            "relevance": rel,
            "tokens": int(f.get("tokens", 0) or 0),
            "tree": f.get("tree"),
            "knowledge_type": f.get("knowledge_type", "evidence"),
        })
    scored.sort(key=lambda x: -x["relevance"])
    return scored[:top]


def pack(scored: list[dict], budget: int) -> list[dict]:
    """Run the engine's depth-aware packer. Returns packed items per file."""
    lib = _import_lib()
    return lib.pack_context(scored, budget)


def depth_name(depth_int: int) -> str:
    return _DEPTH_NAMES.get(depth_int, "Mention")


def render_at_depth(tree: dict | None, depth_int: int, file_path: str) -> str:
    """Render a file's tree at the assigned depth.

    Mirror of mcp_server._render_at_depth, kept self-contained so we don't pull
    that file's full surface (FastMCP, embedding lock, brain wiki tools) in.
    """
    if not tree:
        return f"- `{file_path}`"
    if depth_int == 4:
        return f"- `{file_path}` ({tree.get('totalTokens', 0)} tok)"
    if depth_int == 3:
        lines = [f"### {file_path}"]
        for h in _collect_headings(tree, max_depth=3):
            indent = "  " * max(0, h["depth"] - 1)
            lines.append(f"{indent}- {h['title']} ({h['tokens']} tok)")
        return "\n".join(lines)
    if depth_int == 2:
        lines = [f"### {file_path}"]
        for node in _walk(tree):
            if node.get("depth", 0) > 0 and node.get("title"):
                lines.append(f"{'#' * min(node['depth'] + 2, 6)} {node['title']}")
            if node.get("firstSentence"):
                lines.append(node["firstSentence"])
                lines.append("")
        return "\n".join(lines)
    # 0 or 1
    key = "firstParagraph" if depth_int == 1 else "text"
    lines = [f"### {file_path}"]
    for node in _walk(tree):
        if node.get("depth", 0) > 0 and node.get("title"):
            lines.append(f"{'#' * min(node['depth'] + 2, 6)} {node['title']}")
        if node.get(key):
            lines.append(node[key])
            lines.append("")
    return "\n".join(lines)


def _collect_headings(node: dict, max_depth: int = 3) -> list[dict]:
    out: list[dict] = []
    if node.get("depth", 0) > 0 and node.get("depth", 0) <= max_depth:
        out.append({
            "depth": node.get("depth", 0),
            "title": node.get("title", ""),
            "tokens": node.get("totalTokens", 0),
        })
    for c in node.get("children", []):
        out.extend(_collect_headings(c, max_depth))
    return out


def _walk(node: dict) -> list[dict]:
    out = [node]
    for c in node.get("children", []):
        out.extend(_walk(c))
    return out


def estimate_tokens(text: str) -> int:
    return _import_lib().estimate_tokens(text)


def assemble_markdown(query: str, mode: str, packed: list[dict], total_tokens: int) -> str:
    """Format depth-banded sections per the local stdio MCP convention."""
    sections = {"Full": [], "Detail": [], "Summary": [], "Structure": [], "Mention": []}
    for item in packed:
        name = depth_name(item["depth"])
        sections[name].append(render_at_depth(item.get("tree"), item["depth"], item["path"]))
    out = [
        f'<!-- depth-packed [{mode}] query={query!r} budget=~{total_tokens} files={len(packed)} -->',
        "",
    ]
    for name in ["Full", "Detail", "Summary", "Structure", "Mention"]:
        chunks = sections[name]
        if chunks:
            out.append(f"## {name} ({len(chunks)} files)\n")
            out.append("\n\n".join(chunks))
            out.append("")
    return "\n".join(out)
