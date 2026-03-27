"""
Context Packer — Query-driven depth-packed context from any indexed file collection.

Modes:
  keyword (default): Score files by keyword/stem matching, pack by budget
  graph:             Find entry points by keyword, then traverse import graph, pack by budget

Usage:
  python3 pack_context.py "query" --budget 8000
  python3 pack_context.py "query" --budget 8000 --graph     # graph-enhanced
  python3 pack_context.py "query" --quality                  # fewer files, better depth
  python3 pack_context.py "query" --json                     # structured output
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import (
    tokenize_query, score_file, pack_context,
    DEPTH_NAMES, KNOWLEDGE_TYPES,
)

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
INDEX_PATH = _SCRIPT_DIR / 'cache' / 'workspace-index.json'

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

def score_with_graph(index: dict, query_tokens: list, query_lower: str, top: int) -> list:
    """Score files by keyword first, then expand via import graph."""
    from code_graph import build_graph, traverse_from, find_entry_points

    # Phase 1: Keyword scoring to find entry points
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

    # Phase 2: Build graph from ALL indexed files
    graph = build_graph(index['files'])
    print(f"<!-- Graph: {graph['stats']['total_nodes']} nodes, {graph['stats']['total_edges']} edges -->",
          file=sys.stderr)

    # Phase 3: Find entry points from top keyword matches
    entry_points = find_entry_points(keyword_scored[:10], threshold=0.2)
    if not entry_points:
        return keyword_scored[:top]

    # Phase 4: Traverse graph from entry points
    traversed = traverse_from(entry_points, graph, max_depth=3, max_files=top,
                               follow_tests=True, follow_docs=True)

    # Phase 5: Merge keyword scores with graph scores
    # Graph traversal gives structural relevance; keyword gives lexical relevance
    # Final score = max(keyword, graph) with bonus if both match
    merged = {}
    for s in keyword_scored:
        merged[s['path']] = {**s, 'keyword_rel': s['relevance'], 'graph_rel': 0}
    for t in traversed:
        path = t['path']
        if path in merged:
            merged[path]['graph_rel'] = t['relevance']
            # Boost: found by both keyword AND graph
            merged[path]['relevance'] = min(1.0,
                max(merged[path]['keyword_rel'], t['relevance']) +
                min(merged[path]['keyword_rel'], t['relevance']) * 0.3)
            merged[path]['reason'] = t.get('reason', '')
        else:
            # Found only by graph (structural discovery)
            file_entry = next((f for f in index['files'] if f['path'] == path), None)
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

    results = sorted(merged.values(), key=lambda x: x['relevance'], reverse=True)
    return results[:top]

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
    parser.add_argument('--json', action='store_true', help='JSON output')
    parser.add_argument('--index', type=str, default=str(INDEX_PATH), help='Path to index')
    args = parser.parse_args()

    if args.quality and args.top is None:
        args.top = 15
    if args.top is None:
        args.top = 30

    index_path = Path(args.index)
    if not index_path.exists():
        print(f'Index not found at {index_path}. Run index_workspace.py first.', file=sys.stderr)
        sys.exit(1)

    with open(index_path) as f:
        index = json.load(f)

    query_tokens = tokenize_query(args.query)
    query_lower = args.query.lower()
    if not query_tokens:
        print('Empty query', file=sys.stderr); sys.exit(1)

    # Score files
    if args.graph:
        scored = score_with_graph(index, query_tokens, query_lower, args.top)
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

    packed = pack_context(scored, args.budget)
    mode = 'graph+quality' if args.graph and args.quality else 'graph' if args.graph else 'quality' if args.quality else 'keyword'

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

    print(f'<!-- depth-packed [{mode}] query="{args.query}" budget={args.budget} used=~{total_tokens} files={len(packed)} -->')
    print()
    for dn in ['Full', 'Detail', 'Summary', 'Headlines', 'Mention']:
        if sections[dn]:
            print(f'## {dn} ({len(sections[dn])} files)\n')
            print('\n\n'.join(sections[dn])); print()

if __name__ == '__main__':
    main()
