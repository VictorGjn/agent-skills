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
import urllib.parse
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
    # P2.1 (v1.2): broaden source coverage for the IR bench. .c/.cc/.cpp/.h
    # cover the C/C++ tier (linux, ceph, postgres, kafka native bindings).
    # .cs covers .NET (aspnetcore, roslyn). .scala covers Strata. .j2 covers
    # ansible Jinja templates. 11/70 CSB SDLC tasks were unreachable on
    # v1.0 because their GT files lived in these extensions.
    '.c', '.cc', '.cpp', '.h', '.hpp',
    '.cs',
    '.scala',
    '.j2',
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

# P2.3 (v1.2): split caps by indexing path. Sync indexer is bounded by
# Vercel's 280s function budget — at ~200ms/file, 2000 files is the
# upper limit before BUDGET_EXCEEDED becomes likely. Async path uses
# chunked fetching (Phase B.2) and can scale to 10k candidates without
# bumping into per-tick budget. The legacy MAX_FILES_TO_FETCH alias
# stays at the sync value for any external callers that imported it
# pre-v1.2 (the codebase calls it via fetch_tree's max_files arg).
MAX_FILES_TO_FETCH_SYNC = 2000
MAX_FILES_TO_FETCH_ASYNC = 10_000
MAX_FILES_TO_FETCH = MAX_FILES_TO_FETCH_SYNC  # legacy alias

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


def encode_path(path: str) -> str:
    """URL-encode a path for use in GitHub `/contents/<path>` URLs.

    P2.5 (v1.2): path components may contain spaces or other characters that
    `urllib.request.urlopen` rejects with `InvalidURL`. `microsoft/vscode` and
    `dotnet/roslyn` both have files at paths with literal spaces — these
    raised at fetch time on v1.0 with no graceful degradation. `safe='/'`
    preserves the directory separators while encoding spaces and other
    reserved characters.
    """
    return urllib.parse.quote(path, safe='/')


def resolve_default_branch(owner: str, repo: str, token: str = None) -> str | None:
    """Look up the repo's current default_branch via the GitHub repo API.

    P2.4 (v1.2): `fetch_tree` originally raised `RuntimeError` when the
    caller-provided branch didn't exist on the upstream — costing 13/70 CSB
    tasks on the v1.0 bench (apache/beam=master, ansible=devel, kafka=trunk,
    etc., all baked branch=main in their spec). This helper lets the caller
    auto-resolve once on 404 without hardcoding a static map.

    Returns None on lookup failure (rate-limited, repo missing, etc.) so the
    caller can decide whether to surface the original error.
    """
    url = f'https://api.github.com/repos/{owner}/{repo}'
    try:
        data = github_get(url, token)
    except HTTPError:
        return None
    return data.get('default_branch')


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

def fetch_tree(owner: str, repo: str, branch: str = 'main',
               token: str = None,
               *,
               max_files: int = MAX_FILES_TO_FETCH_SYNC,
               extension_priority: str = 'source-first',
               auto_resolve_branch: bool = True) -> tuple[list[dict], str]:
    """Phase 1 of indexing: fetch the GitHub tree, filter to indexable
    files, sort + cap. Pure data — no per-file content fetches yet.
    Idempotent and cheap (~1 GitHub API call). Run ONCE per job.

    Returns a tuple of (candidates, resolved_branch). `resolved_branch` is
    the same as the input `branch` unless auto_resolve_branch fired (see
    P2.4 below). Callers MUST use `resolved_branch` when fetching content
    via `index_chunk` — otherwise content URLs use the wrong ref and every
    file fetch returns 404 / empty (Codex P1 catch on PR #54: silent empty
    corpora despite successful tree discovery).

    Phase B.2: this is the first of three split functions. The async
    cron worker calls this on the first tick and stores the candidates
    list in KV; subsequent ticks call index_chunk on slices of it.

    P2.2 (v1.2): `extension_priority` controls cap-time sort order.
    'source-first' (default): source code (.py/.ts/.go/.rs/.c/.cpp/.cs/etc.)
    appears first; markdown/docs after. This is the bench-honest priority —
    the v1.0 sort had .md first, which on multi-thousand-file repos filled
    the cap with READMEs before any source code was reached. 'docs-first'
    preserves the legacy behavior for callers that explicitly want it.

    P2.3 (v1.2): `max_files` is now a parameter (default = sync cap of 2000).
    Async callers should pass `max_files=MAX_FILES_TO_FETCH_ASYNC` (10k);
    the chunked indexer can scale beyond the sync 280s function budget.

    P2.4 (v1.2): on 404 / no-tree, `auto_resolve_branch=True` looks up the
    repo's current default_branch via the GitHub repo API and retries once.
    The resolved branch is the second tuple element. Caller can disable
    for stricter handling (e.g. test fixtures where a silent branch
    substitution would mask a real bug).
    """
    # P2.2: validate extension_priority eagerly — not only in the cap branch
    # below — so a bogus value fails fast even when the corpus fits the cap.
    if extension_priority not in ('source-first', 'docs-first'):
        raise ValueError(
            f"extension_priority must be 'source-first' or 'docs-first', "
            f"got {extension_priority!r}"
        )
    print(f'Fetching tree for {owner}/{repo}@{branch}...', file=sys.stderr)
    tree_url = f'https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1'
    try:
        tree_data = github_get(tree_url, token)
    except HTTPError as e:
        if e.code == 404 and auto_resolve_branch:
            real = resolve_default_branch(owner, repo, token)
            if real and real != branch:
                print(f'Branch {branch!r} not found; auto-resolved to default_branch={real!r}',
                      file=sys.stderr)
                tree_url = f'https://api.github.com/repos/{owner}/{repo}/git/trees/{real}?recursive=1'
                tree_data = github_get(tree_url, token)
                branch = real  # for the SystemExit log below
            else:
                raise
        else:
            raise

    if 'tree' not in tree_data:
        # SystemExit (from sys.exit) is BaseException — escapes tool
        # handler's `except Exception` and aborts the function instead
        # of mapping to SOURCE_NOT_FOUND. Raise a regular exception.
        msg = tree_data.get('message', 'unknown')
        raise RuntimeError(f'GitHub returned no tree for {owner}/{repo}@{branch}: {msg}')

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

    if len(candidates) > max_files:
        # P2.2: source-first puts code in the first slot, docs in the third.
        # docs-first inverts. Anything outside the explicit tiers lands in
        # the middle to preserve relative ordering.
        SOURCE_EXTS = {
            '.py', '.pyi',
            '.ts', '.tsx', '.js', '.jsx', '.mjs',
            '.go', '.rs', '.java', '.kt', '.swift', '.rb',
            '.c', '.cc', '.cpp', '.h', '.hpp',
            '.cs', '.scala',
            '.vue', '.svelte', '.astro',
        }
        DOC_EXTS = {'.md', '.mdx', '.txt', '.rst'}

        # extension_priority validated at function entry — only two branches reachable here.
        if extension_priority == 'source-first':
            source_tier, doc_tier = 0, 2
        else:  # 'docs-first' (validated above)
            source_tier, doc_tier = 2, 0

        def sort_key(item):
            ext = Path(item['path']).suffix.lower()
            if ext in SOURCE_EXTS:
                return (source_tier, item['path'])
            if ext in DOC_EXTS:
                return (doc_tier, item['path'])
            return (1, item['path'])
        candidates.sort(key=sort_key)
        candidates = candidates[:max_files]
        print(f'Capped to {max_files} files (priority={extension_priority})', file=sys.stderr)

    return candidates, branch


def index_chunk(owner: str, repo: str, branch: str,
                token: str | None,
                candidates: list[dict],
                *,
                start_idx: int,
                max_files: int = 50,
                time_budget_s: float = 50.0) -> dict:
    """Phase 2 of indexing: fetch + parse `candidates[start_idx:start_idx+max_files]`.

    Wall-time-bounded — stops mid-batch if the budget runs out and returns
    `done=False` with `next_idx` pointing at the unprocessed remainder.
    The async cron worker re-queues the job; the next tick resumes from
    `next_idx` (idempotent re-fetch is fine — GitHub content is the same).

    Returns:
        files:       list[dict] — newly indexed file entries (may be < max_files)
        next_idx:    int        — where the next chunk should resume
        done:        bool       — True when next_idx >= len(candidates)
        time_used_s: float
    """
    start_time = time.time()
    end_idx = min(start_idx + max_files, len(candidates))
    files: list[dict] = []
    cur = start_idx

    while cur < end_idx:
        elapsed = time.time() - start_time
        if elapsed > time_budget_s:
            # Out of wall-time. Return partial chunk; caller re-queues.
            break

        item = candidates[cur]
        path = item['path']
        language = detect_language(path)

        # P2.5 (v1.2): URL-encode path so files like
        # 'extensions/markdown-language-features/test-workspace/sub with space/file.md'
        # don't raise InvalidURL inside urllib. safe='/' preserves the
        # directory separators while encoding spaces and other reserved chars.
        content_url = f'https://api.github.com/repos/{owner}/{repo}/contents/{encode_path(path)}?ref={urllib.parse.quote(branch, safe="")}'
        content = github_get_raw(content_url, token)
        cur += 1

        if not content:
            continue

        if language in ('markdown', 'text'):
            tree = parse_markdown_tree(path, content)
        elif language in ('typescript', 'javascript', 'python', 'rust', 'go', 'java', 'ruby'):
            tree = parse_code_tree(path, content, language)
        else:
            tree = {
                'nodeId': 'n0-1', 'title': path, 'depth': 0,
                'text': content, 'tokens': estimate_tokens(content),
                'totalTokens': estimate_tokens(content), 'children': [],
                'firstSentence': first_sentence(content),
                'firstParagraph': first_paragraph(content),
            }

        headings = extract_headings(tree, max_depth=3)
        headings_text = ' '.join(h['title'] for h in headings)
        kt = classify_knowledge_type(path, headings_text, tree.get('firstParagraph', ''))

        files.append({
            'path': path,
            'size': item.get('size', 0),
            'tokens': tree['totalTokens'],
            'hash': content_hash(content),
            'nodeCount': count_nodes(tree),
            'language': language,
            'knowledge_type': kt,
            'headings': headings,
            'tree': tree,
        })

    return {
        'files': files,
        'next_idx': cur,
        'done': cur >= len(candidates),
        'time_used_s': round(time.time() - start_time, 3),
    }


def finalize(files: list[dict], owner: str, repo: str,
             branch: str = 'main') -> dict:
    """Phase 3 of indexing: build the manifest from accumulated files.
    Pure aggregation (directories, knowledge-type distribution, totals).
    Same shape as the previous monolithic `index_github_repo()` returned.
    """
    total_tokens = sum(f['tokens'] for f in files)

    dirs = set()
    for f in files:
        parts = Path(f['path']).parts
        for j in range(1, len(parts)):
            dirs.add('/'.join(parts[:j]))

    kt_dist: dict = {}
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


def index_github_repo(owner: str, repo: str, branch: str = 'main',
                       token: str = None) -> dict:
    """Sync wrapper: fetch_tree → loop index_chunk → finalize. Same return
    shape as the pre-Phase-B.2 monolithic version. Used by the local CLI
    and by the synchronous (`async=false`) `ce_index_github_repo` path.
    """
    # P2.4 fix (Codex P1 on PR #54): use resolved_branch for downstream
    # content fetches. fetch_tree may have auto-resolved a 404 to the
    # repo's actual default_branch; passing the original branch through
    # to index_chunk would point content URLs at a non-existent ref and
    # return empty for every file.
    candidates, resolved_branch = fetch_tree(owner, repo, branch, token)
    files: list[dict] = []
    next_idx = 0
    while next_idx < len(candidates):
        chunk = index_chunk(
            owner, repo, resolved_branch, token, candidates,
            start_idx=next_idx,
            max_files=MAX_FILES_TO_FETCH,  # no chunking in sync mode
            time_budget_s=999_999,         # no wall-time cap in sync mode
        )
        files.extend(chunk['files'])
        if next_idx % 50 == 0 or chunk['done']:
            print(f'  Indexed {len(files)}/{len(candidates)} files...', file=sys.stderr)
        next_idx = chunk['next_idx']
        if chunk['done']:
            break
    # finalize records the source branch in the index manifest — use the
    # resolved branch so the manifest reflects what was actually indexed.
    return finalize(files, owner, repo, resolved_branch)


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
