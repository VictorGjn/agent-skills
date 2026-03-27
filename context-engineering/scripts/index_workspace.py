"""
Workspace Indexer — Scans documents/ into a heading-tree JSON index.

Output: cache/workspace-index.json
Each file gets a tree of {title, depth, tokens, totalTokens, children, firstSentence}

Usage: python3 index-workspace.py [root_dir]
Default root: documents/
"""

import os
import sys
import json
import re
import hashlib
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import classify_knowledge_type

# ── Config ──

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.cache', 'assets', 'screenshots'}
MAX_FILE_SIZE = 200_000  # 200KB

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

# ── Scanner ──

def scan_directory(root_dir: str) -> dict:
    root = Path(root_dir)
    files = []
    total_tokens = 0
    
    for md_path in sorted(root.rglob('*.md')):
        rel = str(md_path.relative_to(root))
        
        # Skip directories
        if any(part in SKIP_DIRS for part in md_path.parts):
            continue
        
        # Skip large files
        size = md_path.stat().st_size
        if size > MAX_FILE_SIZE:
            continue
        if size == 0:
            continue
        
        try:
            content = md_path.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        
        tree = parse_markdown_tree(rel, content)
        headings = extract_headings(tree, max_depth=3)
        
        # Classify knowledge type
        headings_text = ' '.join(h['title'] for h in headings)
        kt = classify_knowledge_type(rel, headings_text, tree.get('firstParagraph', ''))
        
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
        'totalFiles': len(files),
        'totalTokens': total_tokens,
        'knowledgeTypeDistribution': dict(kt_dist),
        'directories': sorted(dirs),
        'files': files,
    }

# ── Main ──

if __name__ == '__main__':
    root_dir = sys.argv[1] if len(sys.argv) > 1 else 'documents/'
    
    print(f'Scanning {root_dir}...', file=sys.stderr)
    index = scan_directory(root_dir)
    
    # Save full index
    cache_dir = Path('skills/sauna/depth-packing/cache')
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    index_path = cache_dir / 'workspace-index.json'
    with open(index_path, 'w') as f:
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
    with open(light_path, 'w') as f:
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
