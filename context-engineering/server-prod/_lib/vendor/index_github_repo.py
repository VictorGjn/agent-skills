"""
GitHub Repo Indexer — Fetch a repo via API and build a workspace-index.json

Fetches the file tree, then content of indexable files (.md, .ts, .py, etc.)
Builds the same index format as index-workspace.py but from remote repos.

Usage: python3 index-github-repo.py owner/repo [--branch main] [--token GITHUB_TOKEN]
       python3 index-github-repo.py owner/repo --output cache/repo-index.json

Respects rate limits, batches requests, skips large/binary files.
"""

import sys
import os
import json
import re
import hashlib
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import classify_knowledge_type, estimate_tokens

# ── Config ──

INDEXABLE_EXTENSIONS = {
    '.md', '.mdx', '.txt', '.rst',
    '.ts', '.tsx', '.js', '.jsx', '.mjs',
    '.py', '.pyi',
    '.rs', '.go', '.java', '.kt', '.swift', '.rb',
    '.vue', '.svelte', '.astro',
    '.yaml', '.yml', '.json', '.toml',
    '.css', '.scss',
    '.sh', '.bash',
    '.sql',
    '.graphql', '.gql',
    '.proto',
    '.env.example', '.env.sample',
    'Dockerfile', 'Makefile',
}

SKIP_PATTERNS = [
    r'node_modules/', r'\.git/', r'dist/', r'build/', r'\.next/',
    r'__pycache__/', r'\.cache/', r'coverage/', r'\.turbo/',
    r'package-lock\.json$', r'yarn\.lock$', r'pnpm-lock\.yaml$',
    r'\.min\.(js|css)$', r'\.map$', r'\.d\.ts$',
    r'\.png$', r'\.jpg$', r'\.jpeg$', r'\.gif$', r'\.svg$', r'\.ico$',
    r'\.woff', r'\.ttf', r'\.eot',
]

MAX_FILE_SIZE = 80_000  # 80KB
MAX_FILES_TO_FETCH = 300  # rate limit safety

# ── GitHub API ──

def github_get(url: str, token: str = None) -> dict:
    """Make a GitHub API request with optional auth."""
    headers = {'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'depth-packing-eval'}
    if token:
        headers['Authorization'] = f'Bearer {token}'

    req = Request(url, headers=headers)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 403:
            print(f'Rate limited. Waiting 60s...', file=sys.stderr)
            time.sleep(60)
            with urlopen(req) as resp:
                return json.loads(resp.read().decode())
        raise


def github_get_raw(url: str, token: str = None) -> str:
    """Fetch raw file content from GitHub."""
    headers = {'Accept': 'application/vnd.github.v3.raw', 'User-Agent': 'depth-packing-eval'}
    if token:
        headers['Authorization'] = f'Bearer {token}'

    req = Request(url, headers=headers)
    try:
        with urlopen(req) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except HTTPError as e:
        if e.code == 404:
            return ''
        raise


def should_index(path: str) -> bool:
    """Check if a file should be indexed."""
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, path):
            return False

    # Check extension
    p = Path(path)
    ext = p.suffix.lower()
    name = p.name

    if ext in INDEXABLE_EXTENSIONS:
        return True
    if name in ('Dockerfile', 'Makefile', '.gitignore', '.env.example'):
        return True

    return False


# ── Tree parsing (reused from index-workspace.py) ──

def content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]

def first_sentence(text: str) -> str:
    m = re.match(r'^[^\n]*?[.!?](?:\s|$)', text)
    if m:
        return m.group(0).strip()[:200]
    return text.split('\n')[0][:200]

def first_paragraph(text: str) -> str:
    para = text.split('\n\n')[0]
    return para.strip()[:500] if para else ''

def parse_markdown_tree(source: str, content: str) -> dict:
    lines = content.split('\n')
    heading_re = re.compile(r'^(#{1,6})\s+(.+)$')
    counter = [0]

    def make_node(title, depth):
        counter[0] += 1
        return {
            'nodeId': f'n{depth}-{counter[0]}', 'title': title, 'depth': depth,
            'text': '', 'tokens': 0, 'totalTokens': 0, 'children': [],
            'firstSentence': '', 'firstParagraph': '',
        }

    root = make_node(source, 0)
    stack = [root]
    current_text = []

    def flush():
        text = '\n'.join(current_text).strip()
        cur = stack[-1]
        cur['text'] = text
        cur['tokens'] = estimate_tokens(text)
        if text:
            cur['firstSentence'] = first_sentence(text)
            cur['firstParagraph'] = first_paragraph(text)
        current_text.clear()

    for line in lines:
        m = heading_re.match(line)
        if m:
            flush()
            level = len(m.group(1))
            node = make_node(m.group(2).strip(), level)
            while len(stack) > 1 and stack[-1]['depth'] >= level:
                stack.pop()
            stack[-1]['children'].append(node)
            stack.append(node)
        else:
            current_text.append(line)

    flush()
    compute_totals(root)
    return root


def parse_code_tree(source: str, content: str, language: str) -> dict:
    """Parse code into a simple tree: file root -> exported symbols."""
    counter = [0]

    def make_node(title, depth, text=''):
        counter[0] += 1
        return {
            'nodeId': f'c{depth}-{counter[0]}', 'title': title, 'depth': depth,
            'text': text, 'tokens': estimate_tokens(text), 'totalTokens': 0,
            'children': [], 'firstSentence': first_sentence(text) if text else '',
            'firstParagraph': first_paragraph(text) if text else '',
        }

    root = make_node(source, 0, '')
    lines = content.split('\n')

    # Extract exports/definitions as children
    if language in ('typescript', 'javascript'):
        pattern = re.compile(
            r'^export\s+(?:default\s+)?(?:async\s+)?(?:declare\s+)?'
            r'(?:(?:abstract\s+)?class|interface|type|enum|function|(?:const|let|var))\s+(\w+)'
        )
    elif language == 'python':
        pattern = re.compile(r'^(?:class|(?:async\s+)?def)\s+(\w+)')
    else:
        pattern = None

    if pattern:
        for i, line in enumerate(lines):
            m = pattern.match(line)
            if m:
                name = m.group(1)
                # Grab signature (next few lines until empty line or new definition)
                sig_lines = [line]
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip() == '' or pattern.match(lines[j]):
                        break
                    sig_lines.append(lines[j])
                sig = '\n'.join(sig_lines)
                child = make_node(name, 1, sig)
                root['children'].append(child)

    # Root text = full content (for Full depth)
    root['text'] = content
    root['tokens'] = estimate_tokens(content)
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

def extract_headings(node: dict, max_depth: int = 3) -> list:
    headings = []
    if node['depth'] <= max_depth and node['title']:
        headings.append({'depth': node['depth'], 'title': node['title'], 'tokens': node['totalTokens']})
    for child in node['children']:
        headings.extend(extract_headings(child, max_depth))
    return headings


# ── Language detection ──

LANG_MAP = {
    '.ts': 'typescript', '.tsx': 'typescript', '.js': 'javascript', '.jsx': 'javascript',
    '.mjs': 'javascript', '.py': 'python', '.pyi': 'python',
    '.rs': 'rust', '.go': 'go', '.java': 'java', '.rb': 'ruby',
    '.vue': 'typescript', '.svelte': 'typescript',
    '.md': 'markdown', '.mdx': 'markdown', '.rst': 'markdown', '.txt': 'text',
    '.yaml': 'yaml', '.yml': 'yaml', '.json': 'json', '.toml': 'toml',
    '.css': 'css', '.scss': 'css',
    '.sql': 'sql', '.graphql': 'graphql', '.gql': 'graphql',
    '.sh': 'bash', '.bash': 'bash',
}


def detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    return LANG_MAP.get(ext, 'unknown')


# ── Main indexer ──

def index_github_repo(owner: str, repo: str, branch: str = 'main',
                       token: str = None) -> dict:
    """Fetch and index a GitHub repository."""

    print(f'Fetching tree for {owner}/{repo}@{branch}...', file=sys.stderr)
    tree_url = f'https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1'
    tree_data = github_get(tree_url, token)

    if 'tree' not in tree_data:
        print(f'Error: {tree_data.get("message", "unknown")}', file=sys.stderr)
        sys.exit(1)

    # Filter indexable files
    candidates = []
    for item in tree_data['tree']:
        if item['type'] != 'blob':
            continue
        if not should_index(item['path']):
            continue
        size = item.get('size', 0)
        if size > MAX_FILE_SIZE or size == 0:
            continue
        candidates.append(item)

    print(f'Found {len(candidates)} indexable files (of {len(tree_data["tree"])} total)', file=sys.stderr)

    # Limit for rate safety
    if len(candidates) > MAX_FILES_TO_FETCH:
        # Prioritize: .md first, then code, then config
        def sort_key(item):
            ext = Path(item['path']).suffix.lower()
            if ext in ('.md', '.mdx'): return (0, item['path'])
            if ext in ('.ts', '.tsx', '.py', '.rs', '.go'): return (1, item['path'])
            return (2, item['path'])
        candidates.sort(key=sort_key)
        candidates = candidates[:MAX_FILES_TO_FETCH]
        print(f'Capped to {MAX_FILES_TO_FETCH} files', file=sys.stderr)

    # Fetch content
    files = []
    total_tokens = 0

    for i, item in enumerate(candidates):
        path = item['path']
        language = detect_language(path)

        content_url = f'https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}'
        content = github_get_raw(content_url, token)

        if not content:
            continue

        # Parse into tree
        if language in ('markdown', 'text'):
            tree = parse_markdown_tree(path, content)
        elif language in ('typescript', 'javascript', 'python', 'rust', 'go', 'java', 'ruby'):
            tree = parse_code_tree(path, content, language)
        else:
            # Simple flat tree for config/other
            tree = {
                'nodeId': 'n0-1', 'title': path, 'depth': 0,
                'text': content, 'tokens': estimate_tokens(content),
                'totalTokens': estimate_tokens(content), 'children': [],
                'firstSentence': first_sentence(content),
                'firstParagraph': first_paragraph(content),
            }

        headings = extract_headings(tree, max_depth=3)
        headings_text = ' '.join(h['title'] for h in headings)

        # Classify knowledge type
        kt = classify_knowledge_type(path, headings_text, tree.get('firstParagraph', ''))

        file_entry = {
            'path': path,
            'size': item.get('size', 0),
            'tokens': tree['totalTokens'],
            'hash': content_hash(content),
            'nodeCount': count_nodes(tree),
            'language': language,
            'knowledge_type': kt,
            'headings': headings,
            'tree': tree,
        }

        files.append(file_entry)
        total_tokens += tree['totalTokens']

        if (i + 1) % 50 == 0:
            print(f'  Indexed {i+1}/{len(candidates)} files...', file=sys.stderr)

    # Build directory list
    dirs = set()
    for f in files:
        parts = Path(f['path']).parts
        for j in range(1, len(parts)):
            dirs.add('/'.join(parts[:j]))

    # Knowledge type distribution
    kt_dist = {}
    for f in files:
        kt = f['knowledge_type']
        kt_dist[kt] = kt_dist.get(kt, 0) + 1

    return {
        'root': f'{owner}/{repo}@{branch}',
        'indexer_version': '1.0',
        'indexer': 'index_github_repo',
        'indexed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'totalFiles': len(files),
        'totalTokens': total_tokens,
        'directories': sorted(dirs),
        'knowledgeTypeDistribution': kt_dist,
        'files': files,
    }


# ── Main ──

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Index a GitHub repo for depth packing eval')
    parser.add_argument('repo', help='owner/repo (e.g., victorgjn/modular-patchbay)')
    parser.add_argument('--branch', default='master', help='Branch to index')
    parser.add_argument('--token', default=os.environ.get('GITHUB_TOKEN', ''),
                        help='GitHub token (or set GITHUB_TOKEN env)')
    parser.add_argument('--output', default=None, help='Output path for index JSON')
    args = parser.parse_args()

    parts = args.repo.split('/')
    if len(parts) != 2:
        print('Repo must be owner/repo format', file=sys.stderr)
        sys.exit(1)

    owner, repo = parts
    index = index_github_repo(owner, repo, args.branch, args.token or None)

    # Default output path
    script_dir = Path(__file__).resolve().parent.parent
    default_cache = script_dir / 'cache'
    default_cache.mkdir(parents=True, exist_ok=True)
    output = args.output or str(default_cache / f'{repo}-index.json')
    Path(output).parent.mkdir(parents=True, exist_ok=True)

    with open(output, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    print(f'\nIndexed {index["totalFiles"]} files, {index["totalTokens"]:,} tokens', file=sys.stderr)
    print(f'Knowledge types: {json.dumps(index["knowledgeTypeDistribution"])}', file=sys.stderr)
    print(f'Saved to {output}', file=sys.stderr)
