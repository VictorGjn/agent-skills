"""
Workspace Indexer — Scans a directory into a heading-tree JSON index.

Indexes:
  - Markdown/MDX/RST/TXT files via heading parser
  - Code files (TS/JS/Python/Go/Rust/etc) via AST symbol extraction (ast_extract)

Output: cache/workspace-index.json
Each file gets a tree of {title, depth, tokens, totalTokens, children, firstSentence}.
For code files, top-level symbols (functions/classes/methods) become tree children
so the packer can render them at Headlines/Summary/Detail/Full depth levels.

Usage: python3 index_workspace.py [root_dir]
"""

import os
import sys
import json
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import classify_knowledge_type
from ast_extract import extract_symbols, lang_from_path, EXT_TO_LANG

# ── Config ──

SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', '.cache', 'assets', 'screenshots',
    '.next', '.turbo', 'dist', 'build', 'out', 'coverage', '.vscode', '.idea',
    'vendor', 'target',
}
MAX_FILE_SIZE = 200_000  # 200KB

DOC_EXTENSIONS = {'.md', '.mdx', '.rst', '.txt'}
CODE_EXTENSIONS = set(EXT_TO_LANG.keys())
INDEXABLE_EXTENSIONS = DOC_EXTENSIONS | CODE_EXTENSIONS

# ── Token estimation ──

def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    code_blocks = re.findall(r'```[\s\S]*?```', text)
    code_chars = sum(len(b) for b in code_blocks)
    prose_chars = len(text) - code_chars
    return max(1, int(prose_chars / 4 + code_chars / 2.5))

def content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]

# ── First sentence / paragraph extraction ──

def first_sentence(text: str) -> str:
    m = re.match(r'^[^\n]*?[.!?](?:\s|$)', text)
    if m:
        return m.group(0).strip()[:200]
    return text.split('\n')[0][:200]

def first_paragraph(text: str) -> str:
    para = text.split('\n\n')[0]
    return para.strip()[:500] if para else ''

# ── Markdown tree parser ──

def parse_markdown_tree(source: str, content: str) -> dict:
    lines = content.split('\n')
    heading_re = re.compile(r'^(#{1,6})\s+(.+)$')
    
    node_counter = [0]
    
    def make_node(title, depth, node_id=None):
        node_counter[0] += 1
        return {
            'nodeId': f'n{depth}-{node_counter[0]}',
            'title': title,
            'depth': depth,
            'text': '',
            'tokens': 0,
            'totalTokens': 0,
            'children': [],
            'firstSentence': '',
            'firstParagraph': '',
        }
    
    root = make_node(source, 0)
    stack = [root]
    current_text = []
    
    def flush_text():
        text = '\n'.join(current_text).strip()
        current = stack[-1]
        current['text'] = text
        current['tokens'] = estimate_tokens(text)
        if text:
            current['firstSentence'] = first_sentence(text)
            current['firstParagraph'] = first_paragraph(text)
        current_text.clear()
    
    for line in lines:
        m = heading_re.match(line)
        if m:
            flush_text()
            level = len(m.group(1))
            title = m.group(2).strip()
            node = make_node(title, level)
            
            while len(stack) > 1 and stack[-1]['depth'] >= level:
                stack.pop()
            stack[-1]['children'].append(node)
            stack.append(node)
        else:
            current_text.append(line)
    
    flush_text()
    compute_totals(root)
    
    return root

def compute_totals(node: dict) -> int:
    total = node['tokens']
    for child in node['children']:
        total += compute_totals(child)
    node['totalTokens'] = total
    return total

def count_nodes(node: dict) -> int:
    c = 1
    for child in node['children']:
        c += count_nodes(child)
    return c

# ── Headings extraction (for lightweight index) ──

def extract_headings(node: dict, max_depth: int = 3) -> list:
    """Extract heading titles up to max_depth for quick overview."""
    headings = []
    if node['depth'] <= max_depth and node['title']:
        headings.append({'depth': node['depth'], 'title': node['title'], 'tokens': node['totalTokens']})
    for child in node['children']:
        headings.extend(extract_headings(child, max_depth))
    return headings


# ── Code tree parser ──

def parse_code_tree(source: str, content: str, lang: str) -> dict:
    """Build a heading-tree-compatible structure from code via AST symbols.

    Root node contains a content preview. Each top-level symbol (function, class,
    interface, type, etc.) becomes a depth-1 child with its signature as title and
    docstring as text. This makes code files renderable at all 5 depth levels.
    """
    counter = [0]

    def make_node(title, depth, text='', tok=0):
        counter[0] += 1
        return {
            'nodeId': f'n{depth}-{counter[0]}',
            'title': title, 'depth': depth,
            'text': text, 'tokens': tok, 'totalTokens': tok,
            'children': [],
            'firstSentence': first_sentence(text) if text else '',
            'firstParagraph': first_paragraph(text) if text else '',
        }

    # Preview = first ~400 chars (skip blank lines/imports)
    preview_lines = []
    for line in content.split('\n')[:50]:
        s = line.strip()
        if not s or s.startswith(('import ', 'from ', '//', '#')):
            continue
        preview_lines.append(s)
        if len(' '.join(preview_lines)) >= 400:
            break
    preview = ' '.join(preview_lines)[:500]

    # Root stores a truncated preview (full-depth render caps at 1000 chars),
    # so its token count must match that truncated text — not the whole file.
    # Otherwise totalTokens is inflated multi-fold and the packer demotes large
    # code files under fixed budgets.
    root_text = content[:1000]
    root_tokens = estimate_tokens(root_text)
    root = make_node(source, 0, text=root_text, tok=root_tokens)
    root['firstParagraph'] = preview
    root['firstSentence'] = first_sentence(preview) if preview else ''

    try:
        symbols = extract_symbols(lang, content, source)
    except Exception:
        symbols = []

    # Sort by line, dedupe by (name, kind, line)
    seen = set()
    unique_symbols = []
    for sym in sorted(symbols, key=lambda s: s.get('line', 0)):
        key = (sym.get('name'), sym.get('kind'), sym.get('line'))
        if key in seen or sym.get('kind') == 'import':
            continue
        seen.add(key)
        unique_symbols.append(sym)

    for sym in unique_symbols:
        name = sym.get('name', '')
        kind = sym.get('kind', '')
        sig = sym.get('signature', '') or name
        doc = sym.get('docstring', '')
        # Title = "kind name" (so keyword scoring matches both kind and name)
        title = f'{kind} {name}' if kind and kind not in ('export', 'function', 'method') else name
        # Symbol text = signature + docstring
        sym_text = sig
        if doc:
            sym_text += '\n' + doc
        sym_tokens = estimate_tokens(sym_text)
        node = make_node(title, 1, text=sym_text, tok=sym_tokens)
        root['children'].append(node)
        root['totalTokens'] += sym_tokens

    return root

# ── Scanner ──

def scan_directory(root_dir: str) -> dict:
    # Always resolve to absolute. Otherwise `index_workspace.py .` writes
    # root='.' which is meaningless once the index is loaded from a
    # different cwd (TS path-alias resolution silently no-ops, downstream
    # `index.get('root')` lookups land in the wrong directory).
    root = Path(root_dir).resolve()
    files = []
    total_tokens = 0
    skipped = 0

    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped dirs in-place so os.walk does not descend into them
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext in INDEXABLE_EXTENSIONS:
                candidates.append(Path(dirpath) / name)
    candidates.sort()

    for path in candidates:
        ext = path.suffix.lower()

        try:
            size = path.stat().st_size
            if size == 0 or size > MAX_FILE_SIZE:
                skipped += 1
                continue
            content = path.read_text(encoding='utf-8', errors='replace')
        except Exception:
            skipped += 1
            continue

        rel = str(path.relative_to(root))

        if ext in DOC_EXTENSIONS:
            tree = parse_markdown_tree(rel, content)
            headings_text = ' '.join(h['title'] for h in extract_headings(tree, max_depth=3))
            kt = classify_knowledge_type(rel, headings_text, tree.get('firstParagraph', ''))
        else:
            lang = lang_from_path(rel)
            tree = parse_code_tree(rel, content, lang)
            kt = 'ground_truth'

        headings = extract_headings(tree, max_depth=3)

        file_entry = {
            'path': rel,
            'size': size,
            'tokens': tree['totalTokens'],
            'hash': content_hash(content),
            'nodeCount': count_nodes(tree),
            'knowledge_type': kt,
            'headings': headings,
            'tree': tree,
        }

        files.append(file_entry)
        total_tokens += tree['totalTokens']
    
    # Directory structure (for path-based relevance)
    dirs = set()
    for f in files:
        parts = Path(f['path']).parts
        for i in range(1, len(parts)):
            dirs.add('/'.join(parts[:i]))
    
    # Knowledge type distribution
    kt_dist = defaultdict(int)
    for f in files:
        kt_dist[f.get('knowledge_type', 'evidence')] += 1
    
    return {
        'root': str(root),
        'indexer_version': '1.0',
        'indexer': 'index_workspace',
        'indexed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'totalFiles': len(files),
        'totalTokens': total_tokens,
        'skipped': skipped,
        'knowledgeTypeDistribution': dict(kt_dist),
        'directories': sorted(dirs),
        'files': files,
    }

# ── Main ──

if __name__ == '__main__':
    root_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    
    print(f'Scanning {root_dir}...', file=sys.stderr)
    index = scan_directory(root_dir)
    
    # Save index next to the script (skill cache dir)
    script_dir = Path(__file__).resolve().parent.parent
    cache_dir = script_dir / 'cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    index_path = cache_dir / 'workspace-index.json'
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    # Also save a lightweight version (no tree, just headings + metadata)
    light_index = {
        'root': index['root'],
        'totalFiles': index['totalFiles'],
        'totalTokens': index['totalTokens'],
        'directories': index['directories'],
        'files': [{
            'path': f['path'],
            'tokens': f['tokens'],
            'nodeCount': f['nodeCount'],
            'headings': f['headings'],
        } for f in index['files']],
    }

    light_path = cache_dir / 'workspace-index-light.json'
    with open(light_path, 'w', encoding='utf-8') as f:
        json.dump(light_index, f, indent=2, ensure_ascii=False)
    
    # Print summary
    print(f'\nIndexed {index["totalFiles"]} files, {index["totalTokens"]:,} tokens total')
    print(f'Full index: {index_path} ({os.path.getsize(index_path):,} bytes)')
    print(f'Light index: {light_path} ({os.path.getsize(light_path):,} bytes)')
    
    # Top 10 largest files
    by_tokens = sorted(index['files'], key=lambda f: f['tokens'], reverse=True)
    print(f'\nTop 10 by tokens:')
    for f in by_tokens[:10]:
        print(f'  {f["tokens"]:>6} tok  {f["path"]}')
