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
import re
import sys
import os
from pathlib import Path

# Embedding provider lock — MCP server runs in hosted mode (Vercel, Fly, etc.)
# where local BGE inference is the wrong shape (130MB model, slow CPU,
# bundle-size blowout). Refuse to start without a hosted API key, and lock
# EMBED_PROVIDER to whichever is set so embed_resolve's BGE auto-detection
# can never win — even if sentence-transformers somehow ends up in the bundle.
def _lock_embedding_provider() -> None:
    if os.environ.get('EMBED_PROVIDER', '').lower() == 'bge':
        print('FATAL: EMBED_PROVIDER=bge is not supported in MCP server mode. '
              'Use MISTRAL_API_KEY / OPENAI_API_KEY / VOYAGE_API_KEY.', file=sys.stderr)
        sys.exit(1)
    explicit = os.environ.get('EMBED_PROVIDER', '').lower()
    if explicit in ('mistral', 'openai', 'voyage', 'external'):
        return  # operator was explicit, trust them
    if os.environ.get('MISTRAL_API_KEY'):
        os.environ['EMBED_PROVIDER'] = 'mistral'
    elif os.environ.get('VOYAGE_API_KEY'):
        os.environ['EMBED_PROVIDER'] = 'voyage'
    elif os.environ.get('OPENAI_API_KEY'):
        os.environ['EMBED_PROVIDER'] = 'openai'
    else:
        print('FATAL: no embedding API key set. MCP server requires one of '
              'MISTRAL_API_KEY / OPENAI_API_KEY / VOYAGE_API_KEY (BGE local '
              'inference is intentionally disabled in MCP mode).',
              file=sys.stderr)
        sys.exit(1)


_lock_embedding_provider()

from mcp.server.fastmcp import FastMCP

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from pack_context_lib import (
    tokenize_query, score_file, pack_context, classify_knowledge_type,
    estimate_tokens, DEPTH_NAMES,
)

mcp = FastMCP(
    "context-engineering",
    instructions="Depth-packed context loading for codebases. Pack 40+ files at 5 depth levels into any token budget. Wiki/EntityStore tools (wiki.ask / wiki.add / wiki.audit) close the Anabasis loop.",
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
            workspace_root = index.get("root") or str(INDEX_PATH.parent)
            candidate = Path(workspace_root) / "graphify-out" / "graph.json"
            if candidate.exists():
                _gp = str(candidate)

        graph = build_graph_with_fallback(index["files"], graphify_path=_gp, corpus_root=workspace_root)
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


# ──────────────────────────────────────────────────────────────────
# P5 — Wiki / EntityStore MCP tools (PRD M7 + S2 + S3 + S4)
# ──────────────────────────────────────────────────────────────────

import time
from datetime import datetime, timezone


def _emit_telemetry(event: str, **fields) -> None:
    """Emit one JSONL telemetry record to stderr per SPEC-mcp.md §9.

    Events: entity.consolidated, entity.superseded, audit.flagged,
    freshness.expired, tool.call. Stderr keeps stdout clean for tool
    response payloads.
    """
    rec = {
        "ts": int(time.time()),
        "event": event,
        **fields,
    }
    try:
        sys.stderr.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # never break a tool call on telemetry
        pass


def _resolve_brain_path(brain: str | None) -> Path:
    """Resolve the brain directory: explicit arg > env > cwd default."""
    if brain:
        return Path(brain).resolve()
    env_brain = os.environ.get("CE_BRAIN_DIR")
    if env_brain:
        return Path(env_brain).resolve()
    return Path.cwd() / "brain"


def _read_page_with_scope(path: Path) -> tuple[str | None, str]:
    """Read a wiki page; return (scope, content).

    scope=None means the file isn't a valid entity page (no frontmatter,
    no `scope:` line, or unreadable). Codex P2 fix: malformed pages must
    NOT silently default to scope="default" — that leaks arbitrary
    markdown into default-scope queries.

    A page qualifies if AND only if it has a closed `---` frontmatter
    block AND that block contains a `scope:` line. Pages missing either
    return (None, "") so callers skip them.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None, ""
    in_frontmatter = False
    frontmatter_closed = False
    scope: str | None = None
    for line in content.splitlines():
        if line.startswith("---"):
            if in_frontmatter:
                frontmatter_closed = True
                break
            in_frontmatter = True
            continue
        if in_frontmatter and line.startswith("scope:"):
            raw = line.split(":", 1)[1].strip().strip('"\'')
            scope = raw if raw else None
    if not frontmatter_closed or scope is None:
        return None, ""
    return scope, content


@mcp.tool(name="wiki.ask")
def wiki_ask(
    query: str,
    scope: str | None = None,
    brain: str | None = None,
    budget: int = 8000,
) -> str:
    """Read entity pages from brain/wiki/, filter by scope, return as markdown.

    M7 from plan/prd-closed-loop.md — implements the namespace primitive
    landed in PR #16 schema. Absent ``scope`` defaults to ``default`` corpus
    only; explicit ``scope`` filters to matching entity pages.

    V0.1 surface: simple read + filter. Full pack-style depth-band selection
    over wiki pages lands in Phase 2 (`pack --wiki`).

    Args:
        query: free-text query (currently used for substring filter only;
               full semantic resolution is Phase 2).
        scope: corpus scope; defaults to "default".
        brain: brain root directory; falls back to CE_BRAIN_DIR env or ./brain.
        budget: soft token budget for the returned markdown.
    """
    target_scope = scope or "default"
    brain_dir = _resolve_brain_path(brain)
    wiki_dir = brain_dir / "wiki"

    _emit_telemetry("tool.call", tool="wiki.ask", scope=target_scope,
                    brain=str(brain_dir))

    if not wiki_dir.exists():
        _emit_telemetry("tool.result", tool="wiki.ask", status="ok",
                        matched=0, reason="no_wiki_dir")
        return f"<!-- wiki.ask: no wiki/ directory at {brain_dir} -->"

    # F1 fix: gate every page on validate_page so stale-schema pages
    # (e.g., a brain still at schema_version=1.0 after the 1.0 -> 1.1 bump)
    # don't silently leak through wiki.ask. Skipped pages emit a structured
    # warning telemetry event so operators can tell why a brain looks empty.
    from wiki.validate_page import validate_page, ValidationError

    matched: list[tuple[str, str]] = []
    skipped = 0
    for path in sorted(wiki_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        # Codex P2 (#28): read the page once; OSError must not escape the
        # loop or one bad file takes down the whole wiki.ask request.
        # Also avoids the double-read (validate_page would re-read for us).
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            skipped += 1
            _emit_telemetry("tool.skip", tool="wiki.ask",
                            reason="io_error", page=path.name,
                            error=str(e)[:200])
            continue
        try:
            fm = validate_page(path, text=content)
        except ValidationError as e:
            skipped += 1
            _emit_telemetry("tool.skip", tool="wiki.ask",
                            reason="validation", page=path.name,
                            error=str(e)[:200])
            continue
        page_scope = fm.get("scope")
        if page_scope != target_scope:
            continue
        # Lightweight query filter: case-insensitive substring on whole content.
        # Real semantic + multi-hop is Phase 2.
        if query and query.strip() and query.lower() not in content.lower():
            continue
        matched.append((path.name, content))

    if not matched:
        _emit_telemetry("tool.result", tool="wiki.ask", status="ok",
                        matched=0, skipped=skipped)
        return (f"<!-- wiki.ask: no entities in scope={target_scope!r} "
                f"matched query={query!r}"
                + (f"; {skipped} pages skipped on validation" if skipped else "")
                + " -->")

    # Pack within budget — naive: concatenate until ~budget chars (≈ tokens × 4).
    char_cap = budget * 4
    out: list[str] = [
        f"<!-- wiki.ask scope={target_scope} matched {len(matched)} entities -->",
        "",
    ]
    used = 0
    truncated_at = None
    for i, (name, content) in enumerate(matched):
        chunk = f"## {name}\n\n{content}\n"
        if used + len(chunk) > char_cap:
            # L4 fix: track i explicitly; remaining = total - already-included.
            remaining = len(matched) - i
            out.append(f"<!-- wiki.ask: truncated at budget; "
                       f"{remaining} more not shown -->")
            truncated_at = i
            break
        out.append(chunk)
        used += len(chunk)
    _emit_telemetry("tool.result", tool="wiki.ask", status="ok",
                    matched=len(matched), skipped=skipped,
                    truncated=(truncated_at is not None))
    return "\n".join(out)


@mcp.tool(name="wiki.add")
def wiki_add(
    events: list[dict],
    brain: str | None = None,
) -> str:
    """Append events to the brain via EventStreamSource — the runtime-facing
    alias for skills emitting findings back into the brain.

    S2 from plan/prd-closed-loop.md. Each event dict needs `source_type`,
    `source_ref`, `file_id`, `claim`; optional `entity_hint`, `embedding_id`,
    `ts` (auto-stamped if absent). Returns the count of events appended
    plus the events file path.

    Closes the loop: a non-Python skill (Claude Code, n8n, cron) calls this
    MCP verb instead of importing `EventStreamSource`.
    """
    from wiki.source_adapter import EventStreamSource

    brain_dir = _resolve_brain_path(brain)
    events_dir = brain_dir / "events"

    requested = len(events) if events else 0
    _emit_telemetry("tool.call", tool="wiki.add", brain=str(brain_dir),
                    n_events=requested)

    if not events:
        # M2 fix: tool.result fires on every exit path. Prior code emitted
        # tool.call only, so empty/error exits left dangling spans in the
        # telemetry stream and operators couldn't tell appended-count from
        # requested-count.
        _emit_telemetry("tool.result", tool="wiki.add", status="ok",
                        appended=0, requested=0)
        return json.dumps({"appended": 0, "events_dir": str(events_dir)})

    src = EventStreamSource(events_dir=events_dir)
    try:
        n = src.emit_events(events)
    except ValueError as e:
        # M2 fix: report appended-before-error count so the caller can
        # resume from the right index. EventStreamSource carries this
        # in the message tail (see source_adapter.py M3 fix).
        _emit_telemetry("tool.result", tool="wiki.add", status="error",
                        error_kind="INVALID_EVENT",
                        appended=getattr(e, "appended_before_error", 0),
                        requested=requested)
        return json.dumps({
            "error": "INVALID_EVENT", "message": str(e),
            "appended_before_error": getattr(e, "appended_before_error", 0),
        })

    today = time.strftime("%Y-%m-%d", time.gmtime())
    _emit_telemetry("tool.result", tool="wiki.add", status="ok",
                    appended=n, requested=requested)
    return json.dumps({
        "appended": n,
        "events_file": str(events_dir / f"{today}.jsonl"),
    })


@mcp.tool(name="wiki.audit")
def wiki_audit(
    brain: str | None = None,
    refresh: bool = False,
) -> str:
    """Read the latest audit/proposals.md, optionally re-running audit.py first.

    S3 from plan/prd-closed-loop.md. The CLI runner `audit.py` is the
    cron-driven primary; this MCP verb is a thin reader by default
    (returns whatever audit.py last wrote). Pass refresh=True to force a
    re-audit before reading — useful for ad-hoc operator queries.
    """
    brain_dir = _resolve_brain_path(brain)
    proposals_path = brain_dir / "audit" / "proposals.md"

    _emit_telemetry("tool.call", tool="wiki.audit", brain=str(brain_dir),
                    refresh=refresh)

    if refresh:
        from wiki.audit import run_audit
        result = run_audit(brain_dir)
        if result["stale_supersessions"]:
            for f in result["stale_supersessions"]:
                _emit_telemetry("audit.flagged", rule="stale-supersession",
                                source=f["source_slug"], target=f["target_slug"])
        if result["freshness_expired"]:
            for f in result["freshness_expired"]:
                _emit_telemetry("freshness.expired", slug=f["slug"],
                                score=f["score"], elapsed_days=f["elapsed_days"])

    if not proposals_path.exists():
        _emit_telemetry("tool.result", tool="wiki.audit", status="ok",
                        proposals_exists=False, refreshed=refresh)
        return (f"<!-- wiki.audit: no audit/proposals.md at {brain_dir}. "
                f"Run with refresh=true or invoke `audit.py --brain {brain_dir}` first. -->")

    body = proposals_path.read_text(encoding="utf-8")
    _emit_telemetry("tool.result", tool="wiki.audit", status="ok",
                    proposals_exists=True, refreshed=refresh,
                    bytes_returned=len(body))
    return body


@mcp.tool(name="wiki.impact_of")
def wiki_impact_of(
    entity: str,
    brain: str | None = None,
    max_hops: int = 3,
    relation_kinds: list[str] | None = None,
    min_weight: float = 0.0,
    budget: int = 8000,
    include_hubs: bool = False,
) -> str:
    """Return the entities affected by `entity` (impact closure).

    Answers cross-corpus jobs: "what does this change touch?" (engineer),
    "what's the fallout of this decision?" (exec), "which contracts use
    this deprecated feature?" (CSM). Same primitive, same shape — verb
    name domain-neutral on purpose.

    Algorithm: BFS from `entity` through `[[wiki-link]]` body mentions and
    `superseded_by` frontmatter chains, capped at `max_hops`. Hub entities
    (>10 inbound mentions by default; override via CE_IMPACT_HUB_THRESHOLD)
    are reported as 1-hop affected but not fanned through, to keep the
    output useful on real brains. Pass `include_hubs=True` to bypass.

    Recall: 100% when no hubs trip; "best-effort" when they do (excluded
    list surfaces in the output).

    Args:
        entity: slug, id, or case-insensitive title.
        brain: brain root; falls back to CE_BRAIN_DIR or ./brain.
        max_hops: BFS depth cap (default 3).
        relation_kinds: filter to ["mentions"] or ["supersedes"] or both
            (None = all).
        min_weight: drop entities with risk_score below this.
        budget: soft markdown char budget × 4.
        include_hubs: traverse through hub entities anyway.

    Per ``plan/proposals/wiki-impact-of.md``.
    """
    from wiki.impact_of import compute_impact, render_markdown

    brain_dir = _resolve_brain_path(brain)
    _emit_telemetry("tool.call", tool="wiki.impact_of", brain=str(brain_dir),
                    entity=entity, max_hops=max_hops,
                    relation_kinds=relation_kinds, include_hubs=include_hubs)

    result = compute_impact(
        entity, brain_dir,
        max_hops=max_hops,
        relation_kinds=relation_kinds,
        min_weight=min_weight,
        include_hubs=include_hubs,
    )

    _emit_telemetry(
        "tool.result", tool="wiki.impact_of",
        status="error" if result.error else "ok",
        error=result.error,
        affected_count=len(result.affected),
        skipped_hubs=len(result.skipped_hubs),
        recall=result.recall,
    )
    return render_markdown(result, budget=budget)


# ──────────────────────────────────────────────────────────────────
# Phase 4 (CE x lat.md): five new MCP tools mapping lat.md's verb surface
# (locate / section / refs / search / expand) onto CE primitives. Each
# tool is a thin wrapper over wikiref + code_index + existing wiki
# helpers. Brings MCP surface from 9 -> 15 tools (incl. wiki.impact_of).
# ──────────────────────────────────────────────────────────────────


def _resolve_code_index_path(brain: str | None) -> Path:
    """Resolve the code_index.json path: <brain>/../cache/code_index.json
    by default. Callers can override by setting CE_CODE_INDEX in env.
    """
    env_idx = os.environ.get("CE_CODE_INDEX")
    if env_idx:
        return Path(env_idx).resolve()
    return _resolve_brain_path(brain).parent / "cache" / "code_index.json"


def _slice_heading_section(body: str, anchor: str) -> str | None:
    """Extract the body slice from heading ``anchor`` to the next heading
    at the same or higher level. Case-insensitive + slug-normalized match.
    Returns None when no heading matches.
    """
    import re as _re
    heading_re = _re.compile(r"^(#{1,6})\s+(.+?)\s*$", _re.MULTILINE)
    matches = list(heading_re.finditer(body))
    if not matches:
        return None
    norm_anchor = anchor.lower().strip()
    anchor_slug = _re.sub(r"[^a-z0-9]+", "-", norm_anchor).strip("-")
    found_idx: int | None = None
    found_level = 0
    for i, m in enumerate(matches):
        title = m.group(2).strip()
        title_slug = _re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        if title == anchor or title.lower() == norm_anchor or title_slug == anchor_slug:
            found_idx = i
            found_level = len(m.group(1))
            break
    if found_idx is None:
        return None
    start_pos = matches[found_idx].start()
    end_pos = len(body)
    for j in range(found_idx + 1, len(matches)):
        if len(matches[j].group(1)) <= found_level:
            end_pos = matches[j].start()
            break
    return body[start_pos:end_pos].rstrip() + "\n"


def _slice_symbol_range(repo_root: Path, rel_path: str, line: int, end_line: int) -> str | None:
    """Read the source file and return the [line, end_line] slice, fenced
    in a code block. Returns None on read failure.
    """
    p = repo_root / rel_path
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    lo = max(0, line - 1)
    hi = min(len(lines), end_line)
    body = "\n".join(lines[lo:hi])
    return f"```\n{body}\n```\n"


def _parse_ref_argument(ref: str):
    """Accept either ``[[foo#bar]]`` or ``foo#bar`` and return a ``WikiRef``.
    """
    from wiki.wikiref import parse_wikiref  # noqa: E402
    inside = ref.strip()
    if inside.startswith("[[") and inside.endswith("]]"):
        inside = inside[2:-2]
    return parse_wikiref(inside)


@mcp.tool(name="lat.locate")
def lat_locate(
    ref: str,
    brain: str | None = None,
) -> str:
    """Resolve a wiki/code ref to its location.

    Phase 4 of CE x lat.md interop. Mirrors lat.md's ``lat locate``.

    Accepts either ``[[slug#section]]`` or the bare ``slug#section`` form
    (caller may strip the brackets). Returns a one-line markdown record:
    file path, line range (for code refs), or "not found" with the
    parsed ref shape for diagnostics.
    """
    parsed = _parse_ref_argument(ref)
    _emit_telemetry("tool.call", tool="lat.locate", ref=ref)

    if parsed is None:
        _emit_telemetry("tool.result", tool="lat.locate", status="error", reason="malformed_ref")
        return f"<!-- lat.locate: malformed ref `{ref}` -->"

    brain_dir = _resolve_brain_path(brain)

    if parsed.kind in ("slug", "section"):
        page = brain_dir / "wiki" / f"{parsed.target}.md"
        if not page.exists():
            _emit_telemetry("tool.result", tool="lat.locate", status="ok", found=False, kind=parsed.kind)
            return f"<!-- lat.locate: page `{parsed.target}` not found in {brain_dir / 'wiki'} -->"
        loc = f"- **file**: `{page.relative_to(brain_dir.parent) if brain_dir.parent in page.parents else page}`"
        if parsed.anchor:
            loc += f"\n- **section**: `{parsed.anchor}`"
            if parsed.sub_anchor:
                loc += f" / `{parsed.sub_anchor}`"
        _emit_telemetry("tool.result", tool="lat.locate", status="ok", found=True, kind=parsed.kind)
        return loc + "\n"

    # kind == "code"
    code_index_path = _resolve_code_index_path(brain)
    try:
        from wiki.code_index import load_code_index, resolve_symbol
        idx = load_code_index(code_index_path)
    except (FileNotFoundError, ValueError) as e:
        _emit_telemetry("tool.result", tool="lat.locate", status="error", reason="no_code_index")
        return f"<!-- lat.locate: no code index ({e}) -->"
    file_entry = idx.get("files", {}).get(parsed.target)
    if file_entry is None:
        _emit_telemetry("tool.result", tool="lat.locate", status="ok", found=False, kind="code")
        return f"<!-- lat.locate: file `{parsed.target}` not in code index -->"
    if not parsed.anchor:
        loc = f"- **file**: `{parsed.target}` (no symbol anchor)"
    else:
        matches = resolve_symbol(idx, parsed.target, parsed.anchor)
        if not matches:
            _emit_telemetry("tool.result", tool="lat.locate", status="ok", found=False, kind="code")
            return f"<!-- lat.locate: symbol `{parsed.anchor}` not in `{parsed.target}` -->"
        s = matches[0]
        loc = f"- **file**: `{parsed.target}`\n- **symbol**: `{parsed.anchor}` ({s['kind']}, lines {s['line']}-{s['end_line']})"
    _emit_telemetry("tool.result", tool="lat.locate", status="ok", found=True, kind="code")
    return loc + "\n"


@mcp.tool(name="lat.section")
def lat_section(
    ref: str,
    brain: str | None = None,
    budget: int = 4000,
) -> str:
    """Return the body slice for a given ref.

    Phase 4 of CE x lat.md interop. Mirrors lat.md's ``lat section``.

    - Slug ref: returns the entire page body (frontmatter stripped).
    - Section ref: returns the section under that heading (down to the
      next same-or-higher heading).
    - Code ref: returns the symbol's line range as a code block.

    Output is hard-capped at ``budget * 4`` characters (a token-to-char
    rough conversion) so a 50KB page doesn't tank a small budget.
    """
    parsed = _parse_ref_argument(ref)
    _emit_telemetry("tool.call", tool="lat.section", ref=ref, budget=budget)

    if parsed is None:
        _emit_telemetry("tool.result", tool="lat.section", status="error", reason="malformed_ref")
        return f"<!-- lat.section: malformed ref `{ref}` -->"

    brain_dir = _resolve_brain_path(brain)
    char_cap = budget * 4

    if parsed.kind in ("slug", "section"):
        page = brain_dir / "wiki" / f"{parsed.target}.md"
        if not page.exists():
            _emit_telemetry("tool.result", tool="lat.section", status="ok", found=False)
            return f"<!-- lat.section: page `{parsed.target}` not found -->"
        text = page.read_text(encoding="utf-8")
        body_match = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL).match(text)
        body = text[body_match.end():] if body_match else text
        if parsed.kind == "section" and parsed.anchor:
            slice_ = _slice_heading_section(body, parsed.anchor)
            if slice_ is None:
                _emit_telemetry("tool.result", tool="lat.section", status="ok", found=False)
                return f"<!-- lat.section: heading `{parsed.anchor}` not found in `{parsed.target}` -->"
            body = slice_
        if len(body) > char_cap:
            body = body[:char_cap] + f"\n<!-- truncated at budget*4={char_cap} chars -->"
        _emit_telemetry("tool.result", tool="lat.section", status="ok", found=True, kind=parsed.kind)
        return body

    # kind == "code"
    code_index_path = _resolve_code_index_path(brain)
    try:
        from wiki.code_index import load_code_index, resolve_symbol
        idx = load_code_index(code_index_path)
    except (FileNotFoundError, ValueError) as e:
        _emit_telemetry("tool.result", tool="lat.section", status="error", reason="no_code_index")
        return f"<!-- lat.section: no code index ({e}) -->"
    file_entry = idx.get("files", {}).get(parsed.target)
    if file_entry is None:
        _emit_telemetry("tool.result", tool="lat.section", status="ok", found=False)
        return f"<!-- lat.section: file `{parsed.target}` not in code index -->"
    matches = resolve_symbol(idx, parsed.target, parsed.anchor) if parsed.anchor else []
    if not matches:
        _emit_telemetry("tool.result", tool="lat.section", status="ok", found=False)
        return f"<!-- lat.section: symbol `{parsed.anchor}` not in `{parsed.target}` -->"
    s = matches[0]
    repo_root = Path(idx.get("root", ".")).resolve()
    snippet = _slice_symbol_range(repo_root, parsed.target, s["line"], s["end_line"])
    if snippet is None:
        return f"<!-- lat.section: could not read `{parsed.target}` -->"
    if len(snippet) > char_cap:
        snippet = snippet[:char_cap] + f"\n<!-- truncated at budget*4={char_cap} chars -->"
    _emit_telemetry("tool.result", tool="lat.section", status="ok", found=True, kind="code")
    return snippet


@mcp.tool(name="lat.refs")
def lat_refs(
    target: str,
    brain: str | None = None,
) -> str:
    """List every wiki page that references ``target``.

    Phase 4 of CE x lat.md interop. Mirrors lat.md's ``lat refs``.

    Walks ``brain/wiki/*.md``, parses every wikiref in each page body,
    and returns one bullet per (source_page, ref) pair where the ref's
    target equals the requested target (or matches as a code-ref slug).
    """
    # Codex P1 (PR #32): docs claim bracketed and bare refs both work for
    # the lat.* tools, but lat.refs only stripped whitespace. Strip the
    # `[[...]]` envelope here so `lat.refs("[[token-store]]")` matches the
    # same inbound refs as `lat.refs("token-store")`.
    target = target.strip()
    if target.startswith("[[") and target.endswith("]]"):
        target = target[2:-2].strip()
    _emit_telemetry("tool.call", tool="lat.refs", target=target)

    brain_dir = _resolve_brain_path(brain)
    wiki_dir = brain_dir / "wiki"
    if not wiki_dir.exists():
        _emit_telemetry("tool.result", tool="lat.refs", status="ok", found=0)
        return f"<!-- lat.refs: no wiki/ at {brain_dir} -->"

    from wiki.wikiref import parse_wikirefs
    seen: set[tuple[str, str]] = set()
    hits: list[tuple[str, str, str]] = []
    for page in sorted(wiki_dir.glob("*.md")):
        if page.name.startswith("_"):
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            continue
        for r in parse_wikirefs(text):
            if r.target == target or r.slug == target:
                key = (page.stem, r.raw)
                if key in seen:
                    continue
                seen.add(key)
                hits.append((page.stem, r.kind, r.raw))

    _emit_telemetry("tool.result", tool="lat.refs", status="ok", found=len(hits))
    if not hits:
        return f"<!-- lat.refs: no inbound refs to `{target}` -->"
    lines = [f"# Inbound refs to `{target}` ({len(hits)} found)", ""]
    for src, kind, raw in hits:
        lines.append(f"- `{src}` -> {raw} (kind={kind})")
    lines.append("")
    return "\n".join(lines)


@mcp.tool(name="lat.search")
def lat_search(
    query: str,
    brain: str | None = None,
    budget: int = 4000,
) -> str:
    """Substring search across wiki page bodies AND code-index symbols.

    Phase 4 of CE x lat.md interop. Mirrors lat.md's ``lat search``.

    Case-insensitive substring match. Returns a single markdown blob
    with two sections: matching wiki pages (one bullet per page) and
    matching code symbols (one bullet per symbol). Caps total output
    at ``budget * 4`` characters.
    """
    query = query.strip()
    _emit_telemetry("tool.call", tool="lat.search", query=query)
    if not query:
        # Codex P2 (PR #32): every `tool.call` must have a matching
        # `tool.result` per SPEC-mcp.md §9. Empty-query early return
        # previously left a dangling invocation in telemetry pipelines.
        _emit_telemetry("tool.result", tool="lat.search", status="error",
                        reason="empty_query")
        return "<!-- lat.search: empty query -->"

    brain_dir = _resolve_brain_path(brain)
    char_cap = budget * 4
    needle = query.lower()

    wiki_hits: list[tuple[str, str]] = []
    wiki_dir = brain_dir / "wiki"
    if wiki_dir.exists():
        for page in sorted(wiki_dir.glob("*.md")):
            if page.name.startswith("_"):
                continue
            try:
                text = page.read_text(encoding="utf-8")
            except OSError:
                continue
            if needle in text.lower():
                # Use the first matching line as the snippet preview.
                for line in text.splitlines():
                    if needle in line.lower():
                        wiki_hits.append((page.stem, line.strip()[:120]))
                        break

    symbol_hits: list[tuple[str, dict]] = []
    code_index_path = _resolve_code_index_path(brain)
    try:
        from wiki.code_index import load_code_index
        idx = load_code_index(code_index_path)
        for path, entry in idx.get("files", {}).items():
            for s in entry["symbols"]:
                if needle in s["name"].lower():
                    symbol_hits.append((path, s))
    except (FileNotFoundError, ValueError):
        pass  # No code_index = wiki-only search.

    out: list[str] = [f"# Search results for `{query}`", ""]
    out.append(f"## Wiki pages ({len(wiki_hits)})")
    out.append("")
    for slug, preview in wiki_hits[:50]:
        out.append(f"- `{slug}`: {preview}")
    if not wiki_hits:
        out.append("_(none)_")
    out.append("")
    out.append(f"## Code symbols ({len(symbol_hits)})")
    out.append("")
    for path, s in symbol_hits[:50]:
        ex = "*" if s.get("exported") else " "
        out.append(f"- {ex}`{path}#{s['name']}` ({s['kind']}, lines {s['line']}-{s['end_line']})")
    if not symbol_hits:
        out.append("_(none)_")
    out.append("")
    body = "\n".join(out)
    if len(body) > char_cap:
        body = body[:char_cap] + f"\n<!-- truncated at budget*4={char_cap} chars -->"
    _emit_telemetry("tool.result", tool="lat.search", status="ok",
                    wiki_hits=len(wiki_hits), symbol_hits=len(symbol_hits))
    return body


@mcp.tool(name="lat.expand")
def lat_expand(
    ref: str,
    brain: str | None = None,
    depth: int = 1,
    budget: int = 8000,
) -> str:
    """Recursive expansion: fetch ``ref`` plus everything it links to (up to ``depth`` hops).

    Phase 4 of CE x lat.md interop. Mirrors lat.md's ``lat expand``.

    BFS over wikirefs. Each node's content (via ``lat.section`` semantics)
    is concatenated into the output. Visited refs are deduplicated.
    Hard-capped at ``budget * 4`` characters; further refs are noted but
    not expanded once the cap is hit.
    """
    parsed = _parse_ref_argument(ref)
    _emit_telemetry("tool.call", tool="lat.expand", ref=ref, depth=depth, budget=budget)
    if parsed is None:
        return f"<!-- lat.expand: malformed ref `{ref}` -->"

    char_cap = budget * 4

    # Helper: fetch one section by ref, reusing lat.section logic. Returning
    # raw markdown without the headers we add below.
    def _fetch(ref_str: str) -> str:
        return lat_section(ref=ref_str, brain=brain, budget=budget)

    from wiki.wikiref import parse_wikirefs
    visited: set[str] = set()
    out_parts: list[str] = []
    queue: list[tuple[int, str]] = [(0, parsed.raw or f"[[{ref.strip()}]]")]

    while queue:
        d, ref_raw = queue.pop(0)
        if ref_raw in visited:
            continue
        visited.add(ref_raw)
        section = _fetch(ref_raw)
        out_parts.append(f"### {ref_raw} (depth={d})\n\n{section}\n")
        # Stop expansion once budget exhausted.
        if sum(len(p) for p in out_parts) >= char_cap:
            out_parts.append(f"<!-- lat.expand: char cap {char_cap} reached at depth {d} -->")
            break
        if d >= depth:
            continue
        for inner_ref in parse_wikirefs(section):
            if inner_ref.raw not in visited:
                queue.append((d + 1, inner_ref.raw))

    body = "\n".join(out_parts)
    if len(body) > char_cap:
        body = body[:char_cap] + f"\n<!-- truncated at budget*4={char_cap} chars -->"
    _emit_telemetry("tool.result", tool="lat.expand", status="ok",
                    refs_visited=len(visited))
    return body


# ──────────────────────────────────────────────────────────────────


def _list_tools() -> list[str]:
    """Return the registered MCP tool names. Used by --list-tools below.

    The fastmcp framework keeps its registry private; we scan the source
    file for the two decorator shapes used in this module:

    - explicit name form: at-mcp dot tool with a name= keyword argument
    - default-name form:  at-mcp dot tool with no arguments, name = def

    The placeholder above intentionally avoids the literal regex pattern
    so this docstring isn't matched by the scanner itself.

    Filters out the ellipsis token that might appear when the decorator
    pattern shows up inside a docstring example -- ``...`` is never a
    valid MCP tool name.
    """
    import re as _re
    src_path = Path(__file__)
    src = src_path.read_text(encoding="utf-8")
    explicit = _re.findall(r'@mcp\.tool\(name="([^"]+)"\)', src)
    default_names = _re.findall(
        r'@mcp\.tool\(\)\s*\n\s*def\s+(\w+)',
        src,
    )
    names = set(explicit) | set(default_names)
    # Defensive filter: a docstring example accidentally matching the
    # decorator regex would surface as `...` or other non-identifier text;
    # MCP tool names must be `<segment>(.<segment>)*` of word chars.
    valid = _re.compile(r'^\w+(?:\.\w+)*$')
    return sorted(n for n in names if valid.match(n))


if __name__ == "__main__":
    if "--list-tools" in sys.argv:
        # Phase 4 (CE x lat.md PRD AC4): a simple inventory call so CI /
        # operators can verify the surface count without spinning up the
        # full MCP transport. Stdout one tool per line.
        for name in _list_tools():
            print(name)
        sys.exit(0)
    if "--http" in sys.argv:
        idx = sys.argv.index("--http")
        port = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 8000
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        mcp.run()
