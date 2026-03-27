"""
Code Graph — Import/dependency graph with BFS traversal.

Builds a relation graph from indexed files (imports, exports, tests, docs).
Traverses from entry points with relevance decay to find structurally related files.

Used by pack_context.py --graph mode.
"""

import re
from pathlib import Path
from collections import defaultdict

# ── Relation types ──

RELATION_KINDS = {
    'imports': 1.0,       # A imports B
    'calls': 0.7,         # A calls function from B (via exports)
    'extends': 0.9,       # A extends class from B
    'tested_by': 0.6,     # A is tested by B
    'tests': 0.6,         # A tests B
    'documents': 0.5,     # A (doc) documents B (code)
    'configured_by': 0.5, # A is configured by B
    'co_located': 0.3,    # same directory
}

DECAY = 0.65  # relevance decay per hop

# ── Import extraction ──

TS_IMPORT = re.compile(
    r"""import\s+(?:(?:type\s+)?(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)"""
    r"""(?:\s*,\s*(?:\{[^}]*\}|\*\s+as\s+\w+|\w+))*\s+from\s+)?['"]([^'"]+)['"]""")
TS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
TS_DYNAMIC = re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""")
PY_FROM = re.compile(r'^from\s+([\w.]+)\s+import\b', re.M)
PY_IMPORT = re.compile(r'^import\s+([\w.]+)', re.M)

# Export extraction
TS_EXPORT = re.compile(
    r'^export\s+(?:default\s+)?(?:async\s+)?(?:declare\s+)?'
    r'(?:(?:abstract\s+)?class|interface|type|enum|function|(?:const|let|var))\s+(\w+)', re.M)
PY_DEF = re.compile(r'^(?:class|(?:async\s+)?def)\s+(\w+)', re.M)

# Test detection
TEST_PATTERNS = [
    re.compile(r'\.test\.[tj]sx?$'),
    re.compile(r'\.spec\.[tj]sx?$'),
    re.compile(r'__tests__/'),
    re.compile(r'test_\w+\.py$'),
    re.compile(r'\w+_test\.py$'),
    re.compile(r'^tests?/'),
]

DOC_EXTENSIONS = {'.md', '.mdx', '.rst', '.txt'}
CODE_EXTENSIONS = {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.py', '.rs', '.go'}


def _is_test(path: str) -> bool:
    return any(p.search(path) for p in TEST_PATTERNS)


def _is_doc(path: str) -> bool:
    return Path(path).suffix.lower() in DOC_EXTENSIONS


def _is_code(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS


# ── Resolve import path to file ──

def _resolve_import(import_path: str, source_dir: str, file_index: dict) -> str:
    """Try to match an import path to an indexed file."""
    if import_path.startswith('.'):
        parts = source_dir.split('/')
        for seg in import_path.split('/'):
            if seg == '.':
                continue
            if seg == '..':
                if parts:
                    parts.pop()
            else:
                parts.append(seg)
        normalized = '/'.join(parts)
    else:
        normalized = import_path

    # Remove .js/.ts extension suffix if present
    normalized = re.sub(r'\.(js|ts|tsx|jsx|mjs)$', '', normalized)

    candidates = [
        normalized,
        normalized + '.ts', normalized + '.tsx',
        normalized + '.js', normalized + '.jsx', normalized + '.mjs',
        normalized + '.py',
        normalized + '/index.ts', normalized + '/index.js',
        normalized + '/index.tsx',
        normalized + '/__init__.py',
    ]

    for c in candidates:
        if c in file_index:
            return c
        # Try matching just the end of path
        for fp in file_index:
            if fp.endswith('/' + c) or fp == c:
                return fp

    return None


# ── Build graph ──

def build_graph(files: list) -> dict:
    """
    Build a relation graph from indexed files.

    Args:
        files: list of {path, content, language, symbols?, ...}
              'content' is needed for import extraction.
              If content not available, pass tree's firstParagraph.

    Returns:
        {
            'nodes': {path: {exports, is_test, is_doc, ...}},
            'edges': [{source, target, kind, weight}],
            'outgoing': {path: [edges]},
            'incoming': {path: [edges]},
        }
    """
    # Build path index
    file_index = {}
    for f in files:
        file_index[f['path']] = f

    nodes = {}
    edges = []
    outgoing = defaultdict(list)
    incoming = defaultdict(list)

    # Phase 1: Extract node metadata
    for f in files:
        path = f['path']
        ext = Path(path).suffix.lower()
        content = f.get('content', '')
        # If no raw content, use tree text
        if not content and f.get('tree'):
            content = f['tree'].get('text', '')

        # Extract exports
        exports = []
        if ext in ('.ts', '.tsx', '.js', '.jsx', '.mjs'):
            exports = [m.group(1) for m in TS_EXPORT.finditer(content)]
        elif ext == '.py':
            exports = [m.group(1) for m in PY_DEF.finditer(content)]

        nodes[path] = {
            'exports': exports,
            'is_test': _is_test(path),
            'is_doc': _is_doc(path),
            'is_code': _is_code(path),
            'dir': str(Path(path).parent),
        }

    # Phase 2: Extract relations
    for f in files:
        path = f['path']
        ext = Path(path).suffix.lower()
        content = f.get('content', '')
        if not content and f.get('tree'):
            content = f['tree'].get('text', '')
        source_dir = str(Path(path).parent)

        # Imports
        import_patterns = []
        if ext in ('.ts', '.tsx', '.js', '.jsx', '.mjs'):
            import_patterns = [TS_IMPORT, TS_REQUIRE, TS_DYNAMIC]
        elif ext == '.py':
            import_patterns = [PY_FROM, PY_IMPORT]

        for pat in import_patterns:
            for m in pat.finditer(content):
                import_path = m.group(1)
                if not import_path:
                    continue
                # Skip external packages
                if ext in ('.ts', '.tsx', '.js', '.jsx', '.mjs'):
                    if not import_path.startswith('.') and not import_path.startswith('/'):
                        continue

                target = _resolve_import(import_path, source_dir, file_index)
                if target and target != path:
                    edge = {'source': path, 'target': target, 'kind': 'imports',
                            'weight': RELATION_KINDS['imports']}
                    edges.append(edge)
                    outgoing[path].append(edge)
                    incoming[target].append(edge)

        # Test ↔ Source relations
        if nodes[path]['is_test']:
            # Find what this test file tests (by filename matching)
            test_name = Path(path).stem.lower()
            test_name = re.sub(r'\.(test|spec)$', '', test_name)
            test_name = re.sub(r'^test[_-]', '', test_name)
            test_name = re.sub(r'[_-]test$', '', test_name)

            for other_path, other_node in nodes.items():
                if other_path == path:
                    continue
                other_stem = Path(other_path).stem.lower()
                if other_stem == test_name and other_node['is_code']:
                    edge = {'source': path, 'target': other_path, 'kind': 'tests',
                            'weight': RELATION_KINDS['tests']}
                    edges.append(edge)
                    outgoing[path].append(edge)
                    incoming[other_path].append(edge)
                    # Reverse relation
                    rev = {'source': other_path, 'target': path, 'kind': 'tested_by',
                           'weight': RELATION_KINDS['tested_by']}
                    edges.append(rev)
                    outgoing[other_path].append(rev)
                    incoming[path].append(rev)

        # Doc ↔ Code relations (by filename/stem matching)
        if nodes[path]['is_doc']:
            doc_stem = Path(path).stem.lower().replace('-', '').replace('_', '')
            for other_path, other_node in nodes.items():
                if other_path == path or not other_node['is_code']:
                    continue
                code_stem = Path(other_path).stem.lower().replace('-', '').replace('_', '')
                if doc_stem == code_stem or doc_stem in code_stem or code_stem in doc_stem:
                    edge = {'source': path, 'target': other_path, 'kind': 'documents',
                            'weight': RELATION_KINDS['documents']}
                    edges.append(edge)
                    outgoing[path].append(edge)
                    incoming[other_path].append(edge)

    return {
        'nodes': nodes,
        'edges': edges,
        'outgoing': dict(outgoing),
        'incoming': dict(incoming),
        'stats': {
            'total_nodes': len(nodes),
            'total_edges': len(edges),
            'code_files': sum(1 for n in nodes.values() if n['is_code']),
            'test_files': sum(1 for n in nodes.values() if n['is_test']),
            'doc_files': sum(1 for n in nodes.values() if n['is_doc']),
        },
    }


# ── Graph Traversal ──

def traverse_from(entry_points: list, graph: dict,
                  max_depth: int = 3, max_files: int = 30,
                  follow_tests: bool = True, follow_docs: bool = True,
                  follow_callers: bool = False) -> list:
    """
    BFS from entry points, following relations with relevance decay.

    Args:
        entry_points: [{path, confidence}]
        graph: output of build_graph()
        max_depth: how many hops to follow
        max_files: cap on returned files

    Returns:
        [{path, relevance, distance, reason}] sorted by relevance desc
    """
    visited = {}  # path → {relevance, distance, reason}
    queue = []

    # Allowed relation kinds for forward traversal
    follow_kinds = {'imports', 'extends', 'calls', 'configured_by'}
    if follow_tests:
        follow_kinds.add('tested_by')
        follow_kinds.add('tests')
    if follow_docs:
        follow_kinds.add('documents')

    # Seed with entry points
    for ep in entry_points:
        item = {
            'path': ep['path'],
            'relevance': ep.get('confidence', 1.0),
            'distance': 0,
            'reason': f"entry: {ep.get('reason', 'direct match')}",
        }
        queue.append(item)
        visited[ep['path']] = item

    # BFS
    while queue:
        queue.sort(key=lambda x: -x['relevance'])
        current = queue.pop(0)

        if current['distance'] >= max_depth:
            continue

        # Follow outgoing edges
        for edge in graph.get('outgoing', {}).get(current['path'], []):
            if edge['kind'] not in follow_kinds:
                continue

            new_rel = current['relevance'] * edge['weight'] * DECAY
            if new_rel < 0.05:
                continue

            target = edge['target']
            existing = visited.get(target)
            if existing and existing['relevance'] >= new_rel:
                continue

            item = {
                'path': target,
                'relevance': new_rel,
                'distance': current['distance'] + 1,
                'reason': f"{edge['kind']} from {Path(current['path']).name}",
            }
            visited[target] = item
            queue.append(item)

        # Optionally follow incoming edges (callers)
        if follow_callers:
            for edge in graph.get('incoming', {}).get(current['path'], []):
                if edge['kind'] not in ('imports', 'calls', 'extends'):
                    continue

                new_rel = current['relevance'] * edge['weight'] * DECAY * 0.7
                if new_rel < 0.05:
                    continue

                source = edge['source']
                existing = visited.get(source)
                if existing and existing['relevance'] >= new_rel:
                    continue

                item = {
                    'path': source,
                    'relevance': new_rel,
                    'distance': current['distance'] + 1,
                    'reason': f"called by {Path(source).name}",
                }
                visited[source] = item
                queue.append(item)

    # Sort by relevance, cap at max_files
    results = sorted(visited.values(), key=lambda x: -x['relevance'])
    return results[:max_files]


def find_entry_points(query_scored: list, threshold: float = 0.3) -> list:
    """Convert scored files (from keyword matching) to graph entry points."""
    return [
        {'path': s['path'], 'confidence': s['relevance'], 'reason': 'keyword match'}
        for s in query_scored
        if s['relevance'] >= threshold
    ]
