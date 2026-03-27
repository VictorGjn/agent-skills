"""
Context Packer — Query-driven depth-packed context from workspace index.

Given a query and token budget, returns depth-packed markdown:
- Highly relevant files at Full/Detail depth
- Related files at Summary/Headlines depth
- Peripheral files at Mention depth

Usage:
  python3 pack-context.py "query string" [--budget 8000] [--top 30]
  python3 pack-context.py "query" --quality     # fewer files, better depth
  python3 pack-context.py "query" --json         # JSON output
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import (
    tokenize_query, score_file, pack_context,
    DEPTH_NAMES, DEPTH_COST_RATIO, KNOWLEDGE_TYPES,
)

INDEX_PATH = Path('skills/sauna/depth-packing/cache/workspace-index.json')

# ── Content rendering at depth levels ──

def render_at_depth(tree: dict, depth_level: int, file_path: str) -> str:
    if depth_level == 4:  # Mention
        kt = ''  # could add knowledge type tag here
        return f"- `{file_path}` ({tree.get('totalTokens', 0)} tok)"

    if depth_level == 3:  # Headlines
        lines = [f"### {file_path}"]
        for h in _collect_headings(tree, max_depth=3):
            indent = '  ' * max(0, h['depth'] - 1)
            lines.append(f"{indent}- {h['title']} ({h['tokens']} tok)")
        return '\n'.join(lines)

    if depth_level == 2:  # Summary
        lines = [f"### {file_path}"]
        for node in _walk_nodes(tree):
            if node['depth'] > 0 and node['title']:
                prefix = '#' * min(node['depth'] + 2, 6)
                lines.append(f"{prefix} {node['title']}")
            if node.get('firstSentence'):
                lines.append(node['firstSentence'])
                lines.append('')
        return '\n'.join(lines)

    if depth_level == 1:  # Detail
        lines = [f"### {file_path}"]
        for node in _walk_nodes(tree):
            if node['depth'] > 0 and node['title']:
                prefix = '#' * min(node['depth'] + 2, 6)
                lines.append(f"{prefix} {node['title']}")
            if node.get('firstParagraph'):
                lines.append(node['firstParagraph'])
                lines.append('')
        return '\n'.join(lines)

    # depth_level == 0: Full
    lines = [f"### {file_path}"]
    for node in _walk_nodes(tree):
        if node['depth'] > 0 and node['title']:
            prefix = '#' * min(node['depth'] + 2, 6)
            lines.append(f"{prefix} {node['title']}")
        if node.get('text'):
            lines.append(node['text'])
            lines.append('')
    return '\n'.join(lines)

def _collect_headings(node, max_depth=3):
    headings = []
    if node.get('depth', 0) > 0 and node['depth'] <= max_depth:
        headings.append({'depth': node['depth'], 'title': node.get('title', ''),
                         'tokens': node.get('totalTokens', 0)})
    for child in node.get('children', []):
        headings.extend(_collect_headings(child, max_depth))
    return headings

def _walk_nodes(node):
    yield node
    for child in node.get('children', []):
        yield from _walk_nodes(child)

# ── Main ──

def main():
    parser = argparse.ArgumentParser(description='Pack workspace context for a query')
    parser.add_argument('query', help='Search query')
    parser.add_argument('--budget', type=int, default=8000, help='Token budget (default: 8000)')
    parser.add_argument('--top', type=int, default=None, help='Max files to consider')
    parser.add_argument('--quality', action='store_true',
                        help='Quality mode: fewer files (15), better depth allocation')
    parser.add_argument('--json', action='store_true', help='Output JSON instead of markdown')
    parser.add_argument('--index', type=str, default=str(INDEX_PATH), help='Path to workspace index')
    args = parser.parse_args()

    # Quality mode: fewer files, better depth
    if args.quality:
        if args.top is None:
            args.top = 15

    if args.top is None:
        args.top = 30

    # Load index
    index_path = Path(args.index)
    if not index_path.exists():
        print(f'Index not found at {index_path}. Run index-workspace.py first.', file=sys.stderr)
        sys.exit(1)

    with open(index_path) as f:
        index = json.load(f)

    query_tokens = tokenize_query(args.query)
    query_lower = args.query.lower()

    if not query_tokens:
        print('Empty query', file=sys.stderr)
        sys.exit(1)

    # Score all files
    scored = []
    for file_entry in index['files']:
        rel = score_file(file_entry, query_tokens, query_lower)
        if rel > 0:
            scored.append({
                'path': file_entry['path'],
                'relevance': rel,
                'tokens': file_entry['tokens'],
                'tree': file_entry.get('tree'),
                'knowledge_type': file_entry.get('knowledge_type', 'evidence'),
            })

    scored.sort(key=lambda x: x['relevance'], reverse=True)
    scored = scored[:args.top]

    if not scored:
        print(f'No files matched query: "{args.query}"', file=sys.stderr)
        sys.exit(0)

    packed = pack_context(scored, args.budget)

    if args.json:
        output = [{
            'path': it['path'],
            'relevance': round(it['relevance'], 3),
            'depth': it['depth'],
            'depthName': DEPTH_NAMES[it['depth']],
            'tokens': it['tokens'],
            'knowledge_type': it.get('knowledge_type', 'evidence'),
        } for it in packed]
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # Render markdown
    sections = {'Full': [], 'Detail': [], 'Summary': [], 'Headlines': [], 'Mention': []}
    total_tokens = 0

    for item in packed:
        depth_name = DEPTH_NAMES[item['depth']]
        if item.get('tree'):
            rendered = render_at_depth(item['tree'], item['depth'], item['path'])
        else:
            rendered = f"- `{item['path']}` ({item['tokens']} tok)"
        sections[depth_name].append(rendered)
        total_tokens += item['tokens']

    mode = 'quality' if args.quality else 'standard'
    print(f'<!-- Depth-packed context for: "{args.query}" [{mode}] -->')
    print(f'<!-- Budget: {args.budget} tok | Used: ~{total_tokens} tok | Files: {len(packed)} -->')
    print()

    for depth_name in ['Full', 'Detail', 'Summary', 'Headlines', 'Mention']:
        items = sections[depth_name]
        if items:
            print(f'## {depth_name} ({len(items)} files)\n')
            print('\n\n'.join(items))
            print()

if __name__ == '__main__':
    main()
