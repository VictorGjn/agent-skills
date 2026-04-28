"""
Context Packer — Query-driven depth-packed context from any indexed file collection.

The agent (or you) types ONE thing:

    pack "users getting 401 on refresh tokens"

and gets back ~12 files at mixed depth, ~95% of an 8K budget. The skill auto-
decides mode (keyword/semantic/graph) and task preset (fix/review/explain/...)
from the query shape — no flags needed unless you want to override.

User-facing flags (3):
  --budget N        token budget (default 8000)
  --mode M          auto | deep | wide | keyword | semantic | graph
  --task T          fix | review | explain | build | document | research

Useful extras:
  --why             Print the trace: mode reason, entries, budget
  --json            Structured output instead of markdown
  --no-auto-index   Refuse to index automatically (require an existing index)

Back-compat flags (kept working): --graph --semantic --topic-filter --confidence
                                  --quality --top --index --graphify-path
"""

import os
import re
import sys
import json
import time
import hashlib
import argparse
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import (
    tokenize_query, score_file, pack_context, filter_by_topic, confidence_check,
    DEPTH_NAMES, KNOWLEDGE_TYPES,
)

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
INDEX_PATH = _SCRIPT_DIR / 'cache' / 'workspace-index.json'
EMBED_CACHE_PATH = _SCRIPT_DIR / 'cache' / 'embeddings.json'


# ── Auto-mode + auto-task detection ────────────────────────────────────────

_QUESTION_LEADS = ('how ', 'why ', 'what ', 'when ', 'where ', 'explain ',
                   'describe ', 'tell me ')

_TASK_HINTS = {
    'fix': ('fix', 'bug', 'broken', 'error', 'crash', 'fails?', 'failing',
            'regression', '401', '403', '404', '500', 'traceback'),
    'review': ('review', 'pr', 'pull request', 'changes', 'diff', 'merge'),
    'explain': ('explain', 'how does', 'what is', 'walk me through', 'understand'),
    'build': ('build', 'implement', 'add', 'create', 'wire up', 'introduce'),
    'document': ('document', 'docs', 'readme', 'write up'),
    'research': ('research', 'investigate', 'explore', 'compare', 'evaluate',
                 'options for'),
}
_TASK_PATTERNS = {
    task: re.compile(r'\b(' + '|'.join(hints) + r')\b', re.IGNORECASE)
    for task, hints in _TASK_HINTS.items()
}


def detect_mode(query: str, semantic_available: bool) -> tuple:
    """Return (effective_mode, reason). modes: keyword | semantic | graph."""
    q = query.strip()
    ql = q.lower()
    first = q.split(' ')[0] if q else ''

    has_camel = (any(c.isupper() for c in first[1:])
                 and any(c.islower() for c in first))
    has_snake = '_' in first and first.upper() != first
    has_proper = (first and first[:1].isupper() and len(first) > 2
                  and not first.endswith('?'))
    if has_camel or has_snake or has_proper:
        return 'graph', f'identifier "{first}" → graph'

    if any(ql.startswith(lead) for lead in _QUESTION_LEADS):
        if semantic_available:
            return 'semantic', 'question form → semantic'
        return 'keyword', 'question form, no OPENAI_API_KEY → keyword'

    return 'keyword', 'default keyword scan'


def detect_task(query: str) -> tuple:
    """Word-boundary regex match. Returns (task or None, reason)."""
    for task, pat in _TASK_PATTERNS.items():
        m = pat.search(query)
        if m:
            return task, f'matched "{m.group(1)}" → task:{task}'
    return None, 'no task hint detected'


# ── Telemetry ──────────────────────────────────────────────────────────────

def log_usage(*, query: str, mode: str, task, files_packed: int, tokens_used: int,
              budget: int, time_ms: int, ok: bool, error: str = '') -> None:
    """Append one JSON line per pack call to <cache>/usage.jsonl. Logs metadata
    only — no query content, no file contents. Never breaks the pack call."""
    try:
        cache_dir = _SCRIPT_DIR / 'cache'
        cache_dir.mkdir(parents=True, exist_ok=True)
        line = {
            'ts': int(time.time()),
            'cwd_hash': hashlib.md5(str(Path.cwd().resolve()).encode()).hexdigest()[:10],
            'query_len': len(query),
            'mode': mode,
            'task': task,
            'files_packed': files_packed,
            'tokens_used': tokens_used,
            'budget': budget,
            'budget_used_pct': round(tokens_used / max(1, budget), 3),
            'time_ms': time_ms,
            'ok': ok,
        }
        if error:
            line['error'] = error[:200]
        with open(cache_dir / 'usage.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(line) + '\n')
    except Exception:
        pass

# ── Content rendering at depth levels ──

def render_at_depth(tree: dict, depth_level: int, file_path: str) -> str:
    if depth_level == 4:
        return f"- `{file_path}` ({tree.get('totalTokens', 0)} tok)"
    if depth_level == 3:
        lines = [f"### {file_path}"]
        for h in _collect_headings(tree, max_depth=3):
            indent = '  ' * max(0, h['depth'] - 1)
            lines.append(f"{indent}- {h['title']} ({h['tokens']} tok)")
        return '\n'.join(lines)
    if depth_level == 2:
        lines = [f"### {file_path}"]
        for node in _walk_nodes(tree):
            if node['depth'] > 0 and node['title']:
                prefix = '#' * min(node['depth'] + 2, 6)
                lines.append(f"{prefix} {node['title']}")
            if node.get('firstSentence'):
                lines.append(node['firstSentence']); lines.append('')
        return '\n'.join(lines)
    if depth_level == 1:
        lines = [f"### {file_path}"]
        for node in _walk_nodes(tree):
            if node['depth'] > 0 and node['title']:
                prefix = '#' * min(node['depth'] + 2, 6)
                lines.append(f"{prefix} {node['title']}")
            if node.get('firstParagraph'):
                lines.append(node['firstParagraph']); lines.append('')
        return '\n'.join(lines)
    # Full
    lines = [f"### {file_path}"]
    for node in _walk_nodes(tree):
        if node['depth'] > 0 and node['title']:
            prefix = '#' * min(node['depth'] + 2, 6)
            lines.append(f"{prefix} {node['title']}")
        if node.get('text'):
            lines.append(node['text']); lines.append('')
    return '\n'.join(lines)

def _collect_headings(node, max_depth=3):
    h = []
    if node.get('depth', 0) > 0 and node['depth'] <= max_depth:
        h.append({'depth': node['depth'], 'title': node.get('title', ''), 'tokens': node.get('totalTokens', 0)})
    for c in node.get('children', []):
        h.extend(_collect_headings(c, max_depth))
    return h

def _walk_nodes(node):
    yield node
    for c in node.get('children', []):
        yield from _walk_nodes(c)

# ── Graph-enhanced scoring ──

def score_with_graph(index: dict, query_tokens: list, query_lower: str, top: int,
                     entry_point_source: list = None, task_type: str = None,
                     graphify_path: str = None) -> list:
    """Score files by keyword (or provided entry points), then expand via import graph."""
    from code_graph import build_graph_with_fallback, traverse_from, traverse_for_task, find_entry_points

    # Phase 1: Entry points (from keyword or from caller)
    if entry_point_source is not None:
        keyword_scored = entry_point_source
    else:
        keyword_scored = []
        for f in index['files']:
            rel = score_file(f, query_tokens, query_lower)
            if rel > 0:
                keyword_scored.append({
                    'path': f['path'], 'relevance': rel,
                    'tokens': f['tokens'], 'tree': f.get('tree'),
                    'knowledge_type': f.get('knowledge_type', 'evidence'),
                })
        keyword_scored.sort(key=lambda x: x['relevance'], reverse=True)

    # Phase 2: Build graph from ALL indexed files (with graphify fallback)
    graph = build_graph_with_fallback(index['files'], graphify_path=graphify_path)

    # Phase 3: Find entry points from top matches
    entry_points = find_entry_points(keyword_scored[:10], threshold=0.2)
    if not entry_points:
        return keyword_scored[:top]

    # Phase 4: Traverse graph (task-aware or default)
    if task_type:
        traversed = traverse_for_task(query_lower, entry_points, graph, task_type=task_type)
        print(f"<!-- Task preset: {task_type} -->", file=sys.stderr)
    else:
        traversed = traverse_from(entry_points, graph, max_depth=3, max_files=top,
                                   follow_tests=True, follow_docs=True)

    # Phase 5: Merge keyword scores with graph scores.
    # Preserves keyword winners — graph traversal ADDS files, never displaces
    # high-scoring keyword matches out of the top-N. This avoids the regression
    # where structurally-related files push keyword-found files out of results.
    file_map = {f['path']: f for f in index['files']}
    merged = {}
    for s in keyword_scored:
        merged[s['path']] = {**s, 'keyword_rel': s['relevance'], 'graph_rel': 0}
    for t in traversed:
        path = t['path']
        if path in merged:
            merged[path]['graph_rel'] = t['relevance']
            merged[path]['relevance'] = min(1.0,
                max(merged[path]['keyword_rel'], t['relevance']) +
                min(merged[path]['keyword_rel'], t['relevance']) * 0.3)
            merged[path]['reason'] = t.get('reason', '')
        else:
            file_entry = file_map.get(path)
            if file_entry:
                merged[path] = {
                    'path': path,
                    'relevance': t['relevance'],
                    'tokens': file_entry['tokens'],
                    'tree': file_entry.get('tree'),
                    'knowledge_type': file_entry.get('knowledge_type', 'evidence'),
                    'keyword_rel': 0,
                    'graph_rel': t['relevance'],
                    'reason': t.get('reason', ''),
                }

    # Preserve keyword ranking — graph traversal can BOOST relevance (better depth)
    # but never displace keyword winners. Sort primarily by original keyword_rel
    # (keeping pure-keyword ordering) with boosted relevance as tiebreaker.
    keyword_winners = sorted(
        (merged[s['path']] for s in keyword_scored if s['relevance'] > 0),
        key=lambda x: (x['keyword_rel'], x['relevance']), reverse=True,
    )
    graph_only = sorted(
        (v for v in merged.values() if v.get('keyword_rel', 0) == 0),
        key=lambda x: x['relevance'], reverse=True,
    )

    # Reserve a small quota for graph-only neighbors so --semantic --graph
    # (where entry_point_source is already capped at top) still benefits from
    # graph expansion. Keyword winners always keep the majority of slots.
    if graph_only and len(keyword_winners) >= top:
        graph_quota = min(len(graph_only), max(1, top // 5))
        kw_keep = top - graph_quota
        return keyword_winners[:kw_keep] + graph_only[:graph_quota]

    results = list(keyword_winners[:top])
    remaining = top - len(results)
    if remaining > 0:
        results.extend(graph_only[:remaining])
    return results


# ── Semantic-enhanced scoring ──

def score_with_semantic(index: dict, query_tokens: list, query_lower: str,
                        query_raw: str, top: int) -> list:
    """Hybrid scoring: keyword + embedding similarity."""
    from embed_resolve import resolve_hybrid

    keyword_scored = []
    for f in index['files']:
        rel = score_file(f, query_tokens, query_lower)
        keyword_scored.append({
            'path': f['path'], 'relevance': rel,
            'tokens': f['tokens'], 'tree': f.get('tree'),
            'knowledge_type': f.get('knowledge_type', 'evidence'),
        })

    keyword_with_score = [s for s in keyword_scored if s['relevance'] > 0]

    hybrid_results = resolve_hybrid(
        query_raw,
        keyword_with_score,
        cache_path=str(EMBED_CACHE_PATH),
        top_k=top,
    )

    if not hybrid_results:
        keyword_with_score.sort(key=lambda x: x['relevance'], reverse=True)
        return keyword_with_score[:top]

    file_index = {f['path']: f for f in index['files']}
    results = []
    for hr in hybrid_results:
        path = hr['path']
        f = file_index.get(path)
        if not f:
            continue
        results.append({
            'path': path,
            'relevance': hr['confidence'],
            'tokens': f['tokens'],
            'tree': f.get('tree'),
            'knowledge_type': f.get('knowledge_type', 'evidence'),
            'keyword_rel': hr.get('keyword_score', 0),
            'semantic_rel': hr.get('semantic_score', 0),
            'reason': hr.get('reason', ''),
        })

    semantic_only = [r for r in results if r.get('keyword_rel', 0) == 0 and r.get('semantic_rel', 0) > 0]
    if semantic_only:
        print(f"<!-- Semantic discovered {len(semantic_only)} files invisible to keyword search:", file=sys.stderr)
        for s in semantic_only[:5]:
            print(f"     {s['path']} (sem={s['semantic_rel']:.3f}) -->", file=sys.stderr)

    return results


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description='Pack context for a query')
    parser.add_argument('query', help='Search query')
    parser.add_argument('--budget', type=int, default=8000, help='Token budget (default: 8000)')
    parser.add_argument('--top', type=int, default=None, help='Max files to consider')
    parser.add_argument('--quality', action='store_true',
                        help='Quality mode: fewer files (15), better depth')
    parser.add_argument('--graph', action='store_true',
                        help='Graph-enhanced: follow imports/deps from entry points')
    parser.add_argument('--semantic', action='store_true',
                        help='Semantic-enhanced: hybrid keyword + embedding similarity')
    parser.add_argument('--task', type=str, default=None,
                        choices=['fix', 'review', 'explain', 'build', 'document', 'research'],
                        help='Task-type preset for graph traversal (auto-detects if not set with --graph)')
    parser.add_argument('--topic-filter', action='store_true',
                        help='Remove off-topic results before packing (anti-hallucination)')
    parser.add_argument('--confidence', action='store_true',
                        help='Print confidence signal when results are weak')
    parser.add_argument('--mode', choices=['auto', 'deep', 'wide',
                                           'keyword', 'semantic', 'graph'],
                        default='auto',
                        help='auto (default — picks by query shape), deep (semantic+graph), '
                             'wide (keyword broad-scan), or explicit mode')
    parser.add_argument('--why', action='store_true',
                        help='Print the trace: how mode/task/files were chosen')
    parser.add_argument('--no-auto-index', action='store_true',
                        help='Refuse to index automatically (error if no index found)')
    parser.add_argument('--json', action='store_true', help='JSON output')
    parser.add_argument('--index', type=str, default=str(INDEX_PATH), help='Path to index')
    parser.add_argument('--graphify-path', type=str, default=None,
                        help='Path to graphify graph.json (auto-discovers at {workspace}/graphify-out/graph.json)')
    args = parser.parse_args()

    t_start = time.time()
    why_trace = {}
    semantic_available = bool(os.environ.get('OPENAI_API_KEY'))

    # ── Resolve mode ───────────────────────────────────────────────────────
    explicit_mode = None
    if args.graph and args.semantic:
        explicit_mode = 'deep'
    elif args.graph:
        explicit_mode = 'graph'
    elif args.semantic:
        explicit_mode = 'semantic'
    elif args.mode != 'auto':
        explicit_mode = args.mode

    if explicit_mode:
        effective_mode = explicit_mode
        why_trace['mode_reason'] = f'explicit --mode {explicit_mode}'
    else:
        effective_mode, why_trace['mode_reason'] = detect_mode(args.query, semantic_available)
    why_trace['mode'] = effective_mode

    # `deep` and `wide` are macros over the existing dispatch flags
    if effective_mode == 'deep':
        args.semantic = True
        args.graph = True
        if args.top is None: args.top = 30
    elif effective_mode == 'wide':
        args.semantic = False
        args.graph = False
        if args.top is None: args.top = 50
    elif effective_mode == 'graph':
        args.graph = True
    elif effective_mode == 'semantic':
        args.semantic = True
    # 'keyword' → leave both False

    if args.quality and args.top is None:
        args.top = 15
    if args.top is None:
        args.top = 30

    # ── Resolve task (auto-detect if not given) ───────────────────────────
    if not args.task:
        detected_task, task_reason = detect_task(args.query)
        if detected_task:
            args.task = detected_task
        why_trace['task_reason'] = task_reason
    else:
        why_trace['task_reason'] = f'explicit --task {args.task}'
    why_trace['task'] = args.task

    # ── Resolve / auto-build index ────────────────────────────────────────
    index_path = Path(args.index)
    if not index_path.exists():
        if args.no_auto_index:
            print(f'No index at {index_path}. Run `index_workspace.py {Path.cwd()}` first, '
                  f'or omit --no-auto-index to build one now.', file=sys.stderr)
            log_usage(query=args.query, mode=effective_mode, task=args.task,
                      files_packed=0, tokens_used=0, budget=args.budget,
                      time_ms=int((time.time() - t_start) * 1000),
                      ok=False, error='no_index')
            sys.exit(1)
        # Auto-index the current working directory
        try:
            from index_workspace import build_index as _build_workspace_index
            print(f'No index at {index_path}. Auto-indexing {Path.cwd()}...', file=sys.stderr)
            built = _build_workspace_index(Path.cwd())
            index_path.parent.mkdir(parents=True, exist_ok=True)
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(built, f, ensure_ascii=False)
            why_trace['index_source'] = 'auto_indexed'
            print(f'Indexed {built.get("totalFiles", "?")} files', file=sys.stderr)
        except Exception as e:
            print(f'Auto-index failed: {e}. Run `index_workspace.py {Path.cwd()}` manually.',
                  file=sys.stderr)
            sys.exit(1)
    else:
        why_trace['index_source'] = 'cached'
    why_trace['index_path'] = str(index_path)

    with open(index_path, encoding='utf-8') as f:
        index = json.load(f)
    why_trace['index_files'] = index.get('totalFiles', len(index.get('files', [])))

    query_tokens = tokenize_query(args.query)
    query_lower = args.query.lower()
    if not query_tokens:
        print('Empty query', file=sys.stderr); sys.exit(1)

    # Auto-discover graphify graph.json when --graph is used
    graphify_path = args.graphify_path
    if graphify_path is None and args.graph:
        workspace_root = index.get('root') or str(index_path.parent)
        candidate = Path(workspace_root) / 'graphify-out' / 'graph.json'
        if candidate.exists():
            graphify_path = str(candidate)

    # Score files based on mode
    task_type = args.task  # explicit task type, or None
    if args.semantic and args.graph:
        semantic_scored = score_with_semantic(index, query_tokens, query_lower, args.query, args.top)
        scored = score_with_graph(index, query_tokens, query_lower, args.top,
                                  entry_point_source=semantic_scored, task_type=task_type,
                                  graphify_path=graphify_path)
    elif args.semantic:
        scored = score_with_semantic(index, query_tokens, query_lower, args.query, args.top)
    elif args.graph:
        scored = score_with_graph(index, query_tokens, query_lower, args.top,
                                  task_type=task_type, graphify_path=graphify_path)
    else:
        scored = []
        for f in index['files']:
            rel = score_file(f, query_tokens, query_lower)
            if rel > 0:
                scored.append({'path': f['path'], 'relevance': rel, 'tokens': f['tokens'],
                               'tree': f.get('tree'), 'knowledge_type': f.get('knowledge_type', 'evidence')})
        scored.sort(key=lambda x: x['relevance'], reverse=True)
        scored = scored[:args.top]

    if not scored:
        print(f'No files matched: "{args.query}"', file=sys.stderr); sys.exit(0)

    # Anti-hallucination: topic filter
    if args.topic_filter:
        before = len(scored)
        scored = filter_by_topic(scored, args.query)
        after = len(scored)
        if before != after:
            print(f"<!-- Topic filter: {before} → {after} files -->", file=sys.stderr)

    # Confidence check
    if args.confidence:
        conf = confidence_check(scored)
        if conf['is_low']:
            print(f"<!-- CONFIDENCE WARNING: {conf['signal']} -->", file=sys.stderr)

    if not scored:
        print(f'No files matched after filtering: "{args.query}"', file=sys.stderr); sys.exit(0)

    packed = pack_context(scored, args.budget)

    # Determine mode label
    modes = []
    if args.semantic: modes.append('semantic')
    if args.graph: modes.append('graph')
    if args.task: modes.append(f'task:{args.task}')
    if args.topic_filter: modes.append('topic-filtered')
    if args.quality: modes.append('quality')
    if not modes: modes.append('keyword')
    mode = '+'.join(modes)

    if args.json:
        output = [{
            'path': it['path'], 'relevance': round(it['relevance'], 3),
            'depth': it['depth'], 'depthName': DEPTH_NAMES[it['depth']],
            'tokens': it['tokens'], 'knowledge_type': it.get('knowledge_type', 'evidence'),
        } for it in packed]
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    sections = {'Full': [], 'Detail': [], 'Summary': [], 'Headlines': [], 'Mention': []}
    total_tokens = 0
    for item in packed:
        depth_name = DEPTH_NAMES[item['depth']]
        rendered = render_at_depth(item['tree'], item['depth'], item['path']) if item.get('tree') \
            else f"- `{item['path']}` ({item['tokens']} tok)"
        sections[depth_name].append(rendered)
        total_tokens += item['tokens']

    why_trace['files_packed'] = len(packed)
    why_trace['tokens_used'] = total_tokens
    why_trace['budget_used_pct'] = round(total_tokens / max(1, args.budget), 3)

    if args.why:
        _print_why_trace(args.query, why_trace, args.budget)

    print(f'<!-- depth-packed [{mode}] query="{args.query}" budget={args.budget} used=~{total_tokens} files={len(packed)} -->')
    print()
    for dn in ['Full', 'Detail', 'Summary', 'Headlines', 'Mention']:
        if sections[dn]:
            print(f'## {dn} ({len(sections[dn])} files)\n')
            print('\n\n'.join(sections[dn])); print()

    log_usage(query=args.query, mode=effective_mode, task=args.task,
              files_packed=len(packed), tokens_used=total_tokens,
              budget=args.budget,
              time_ms=int((time.time() - t_start) * 1000), ok=True)


def _print_why_trace(query: str, why: dict, budget: int) -> None:
    print('## Why this context\n')
    print(f'- **Query**: `{query}`')
    print(f'- **Mode**: `{why.get("mode")}` — {why.get("mode_reason", "")}')
    if why.get('task'):
        print(f'- **Task**: `{why["task"]}` — {why.get("task_reason", "")}')
    else:
        print(f'- **Task**: none — {why.get("task_reason", "")}')
    print(f'- **Index**: {why.get("index_source")} ({why.get("index_files", "?")} files) — `{why.get("index_path", "")}`')
    print(f'- **Budget**: {why.get("tokens_used", 0):,} / {budget:,} tokens '
          f'({why.get("budget_used_pct", 0)*100:.1f}%) on {why.get("files_packed", 0)} files')
    print()


if __name__ == '__main__':
    main()
