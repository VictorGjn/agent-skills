"""Minimal depth-aware packer for the YC-demo stub.

This is intentionally tiny — keyword scoring + 3 depth bands. Production
v1 (scripts/pack_context_lib.py) has 5 bands, RRF hybrid, semantic mode,
graph traversal, and knowledge_type ranking.

The stub is built to demonstrate the wire shape (query, corpus_id, budget
in → markdown out), not to compete on retrieval quality.
"""
import re
from collections import Counter

# ── Token estimation (rough, char-based — production uses tiktoken) ──
_CHARS_PER_TOKEN = 4


def _est_tokens(text):
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


# ── Depth bands ──
DEPTH_FULL = "full"
DEPTH_SUMMARY = "summary"
DEPTH_MENTION = "mention"

# Approximate token budgets per band (caps within budget)
BAND_FRACTIONS = {
    DEPTH_FULL: 0.65,      # most relevant files: full content
    DEPTH_SUMMARY: 0.25,   # supporting: heading tree + first sentences
    DEPTH_MENTION: 0.10,   # context: one-line each
}


# ── Query → token bag (for keyword scoring) ──
def _tokenize(query):
    parts = re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", query.lower())
    # Split camelCase / snake_case / kebab-case into word fragments too
    expanded = []
    for p in parts:
        expanded.append(p)
        for sub in re.findall(r"[a-z][a-z0-9]+", re.sub(r"([A-Z])", r" \1", p).lower()):
            if sub not in expanded:
                expanded.append(sub)
    return expanded


# ── Score one file ──
def _score_file(file_entry, query_tokens):
    """Return a relevance score in [0..1] based on token presence in path + tree text."""
    haystack_parts = [file_entry.get("path", "")]
    tree = file_entry.get("tree", {}) or {}
    if isinstance(tree, dict):
        haystack_parts.append(tree.get("title", ""))
        haystack_parts.append(tree.get("firstSentence", ""))
        for sym in (file_entry.get("symbols") or []):
            haystack_parts.append(sym.get("name", ""))
    haystack = " ".join(haystack_parts).lower()

    if not haystack.strip():
        return 0.0

    counts = Counter()
    for tok in query_tokens:
        if tok in haystack:
            counts[tok] += haystack.count(tok)

    if not counts:
        return 0.0

    coverage = len(counts) / max(len(query_tokens), 1)  # how many query tokens hit
    density = sum(counts.values()) / max(len(haystack.split()), 1)
    return min(1.0, coverage * 0.7 + min(density * 100, 0.3))


# ── Render one file at a depth ──
def _render(file_entry, depth, max_tokens):
    path = file_entry.get("path", "")
    tree = file_entry.get("tree") or {}

    if depth == DEPTH_MENTION:
        return f"- `{path}`"

    if depth == DEPTH_SUMMARY:
        lines = [f"### {path}"]
        if isinstance(tree, dict):
            if tree.get("firstSentence"):
                lines.append(tree["firstSentence"])
            for child in (tree.get("children") or [])[:8]:
                title = child.get("title")
                if title:
                    lines.append(f"- {title}")
        out = "\n".join(lines)
        return _truncate(out, max_tokens)

    # DEPTH_FULL
    lines = [f"### {path}"]
    if isinstance(tree, dict):
        text = tree.get("text") or tree.get("firstParagraph") or tree.get("firstSentence") or ""
        if text:
            lines.append(text)
        else:
            # fall back to walking children
            for child in (tree.get("children") or []):
                if child.get("title"):
                    lines.append(f"#### {child['title']}")
                if child.get("text"):
                    lines.append(child["text"])
                elif child.get("firstParagraph"):
                    lines.append(child["firstParagraph"])
    out = "\n\n".join(lines)
    return _truncate(out, max_tokens)


def _truncate(text, max_tokens):
    """Truncate to approx max_tokens. Adds '…' marker."""
    cap = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "\n\n… [truncated to fit budget]"


# ── Main packer ──
def pack(query, index, budget, why=False):
    files = index.get("files") or []
    if isinstance(files, dict):
        files = list(files.values())

    query_tokens = _tokenize(query)
    if not query_tokens:
        return {
            "markdown": f"<!-- empty query token bag for {query!r} -->",
            "tokens_used": 0,
            "tokens_budget": budget,
            "files": [],
            "trace": "tokenization yielded no tokens" if why else None,
        }

    scored = [
        {**f, "_score": _score_file(f, query_tokens)}
        for f in files
        if _score_file(f, query_tokens) > 0
    ]
    scored.sort(key=lambda f: -f["_score"])

    if not scored:
        return {
            "markdown": f"<!-- no files matched query -->",
            "tokens_used": 0,
            "tokens_budget": budget,
            "files": [],
            "trace": f"scored 0/{len(files)} files for tokens={query_tokens}" if why else None,
        }

    # Allocate slots
    full_budget = int(budget * BAND_FRACTIONS[DEPTH_FULL])
    summary_budget = int(budget * BAND_FRACTIONS[DEPTH_SUMMARY])
    mention_budget = budget - full_budget - summary_budget

    # Greedy fill at FULL
    out_files = []
    used = 0
    full_chunks = []
    summary_chunks = []
    mention_chunks = []

    for f in scored:
        f_tokens = f.get("tokens", 1000)
        # FULL fill
        if used + f_tokens <= full_budget:
            chunk = _render(f, DEPTH_FULL, f_tokens)
            full_chunks.append(chunk)
            chunk_tokens = _est_tokens(chunk)
            used += chunk_tokens
            out_files.append({
                "path": f["path"], "depth": DEPTH_FULL,
                "tokens": chunk_tokens, "relevance": round(f["_score"], 4),
            })
            continue
        break

    # SUMMARY fill (next batch)
    summary_used = 0
    for f in scored[len(out_files):]:
        chunk = _render(f, DEPTH_SUMMARY, max(200, _est_tokens(f.get("tree", {}).get("firstSentence", ""))))
        chunk_tokens = _est_tokens(chunk)
        if summary_used + chunk_tokens > summary_budget:
            break
        summary_chunks.append(chunk)
        summary_used += chunk_tokens
        out_files.append({
            "path": f["path"], "depth": DEPTH_SUMMARY,
            "tokens": chunk_tokens, "relevance": round(f["_score"], 4),
        })
    used += summary_used

    # MENTION fill (rest, until budget exhausted)
    mention_used = 0
    for f in scored[len(out_files):]:
        chunk = _render(f, DEPTH_MENTION, 50)
        chunk_tokens = _est_tokens(chunk)
        if mention_used + chunk_tokens > mention_budget:
            break
        mention_chunks.append(chunk)
        mention_used += chunk_tokens
        out_files.append({
            "path": f["path"], "depth": DEPTH_MENTION,
            "tokens": chunk_tokens, "relevance": round(f["_score"], 4),
        })
    used += mention_used

    # ── Assemble markdown ──
    parts = []
    if full_chunks:
        parts.append("## Full\n\n" + "\n\n---\n\n".join(full_chunks))
    if summary_chunks:
        parts.append("## Summary\n\n" + "\n\n---\n\n".join(summary_chunks))
    if mention_chunks:
        parts.append("## Mention\n\n" + "\n".join(mention_chunks))

    markdown = "\n\n".join(parts) if parts else f"<!-- no content packed for {query!r} -->"

    trace = None
    if why:
        trace = (
            f"tokens={query_tokens}\n"
            f"scored {len(scored)}/{len(files)} files\n"
            f"packed {len(full_chunks)} full + {len(summary_chunks)} summary + {len(mention_chunks)} mention\n"
            f"budget {budget}, used ~{used}"
        )

    return {
        "markdown": markdown,
        "tokens_used": used,
        "tokens_budget": budget,
        "files": out_files,
        "trace": trace,
    }
