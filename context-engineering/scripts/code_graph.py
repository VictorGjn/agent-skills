"""
Code Graph — Import/dependency graph with BFS traversal and task-type presets.

Builds a relation graph from indexed files (imports, exports, tests, docs, links).
Traverses from entry points with relevance decay to find structurally related files.

Sources: agent-skills (core), modular-patchbay (relation kinds, task presets, bidirectional)
Used by pack_context.py --graph mode.
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional
from collections import defaultdict

from tsconfig_resolver import TsconfigResolver

# Module-level singleton: tsconfig walks + parses are expensive and idempotent
# (tsconfig.json changes at path X stay valid until path X changes). Reusing
# across build_graph calls saves the per-request re-walk in MCP-server mode.
# Restart the process to pick up tsconfig.json changes.
_TS_RESOLVER = TsconfigResolver()

# Hard cap on edges built during graph construction. Prevents the silent
# truncation pattern that produced `total_relations=12175` metadata while only
# 5000 edges were actually stored. Configurable via env for large monorepos.
# Bare int() at import time would crash the whole graph path on a typo
# (e.g. `CONTEXT_ENG_MAX_RELATIONS=50k`) — fall back to the default with a
# stderr warning instead.
_DEFAULT_MAX_RELATIONS = 50_000
try:
    MAX_RELATIONS = int(os.environ.get('CONTEXT_ENG_MAX_RELATIONS', _DEFAULT_MAX_RELATIONS))
    if MAX_RELATIONS <= 0:
        raise ValueError('must be positive')
except (TypeError, ValueError) as _e:
    print(f"<!-- code_graph: invalid CONTEXT_ENG_MAX_RELATIONS={os.environ.get('CONTEXT_ENG_MAX_RELATIONS')!r} "
          f"({_e}); falling back to {_DEFAULT_MAX_RELATIONS}. -->", file=sys.stderr)
    MAX_RELATIONS = _DEFAULT_MAX_RELATIONS

# ── Relation types (expanded from modular-patchbay's 17 kinds) ──

RELATION_KINDS = {
    # Code relations
    'imports':       1.0,    # A imports B
    'extends':       0.9,    # A extends class from B
    'implements':    0.85,   # A implements interface from B
    'calls':         0.7,    # A calls function from B
    'uses_type':     0.7,    # A uses type from B
    'tested_by':     0.6,    # A is tested by B
    'tests':         0.6,    # A tests B
    'configured_by': 0.5,   # A is configured by B
    # Doc relations
    'documents':     0.5,    # A (doc) documents B (code)
    'links_to':      0.5,    # Markdown link A → B
    'references':    0.4,    # Markdown reference
    'depends_on':    0.4,    # Explicit dependency
    'defined_in':    0.4,    # Symbol defined in
    'continues':     0.3,    # Doc versioning
    'supersedes':    0.3,    # Doc versioning
    'related':       0.3,    # Semantic relation
    'co_located':    0.3,    # Same directory
}

DECAY = 0.65  # relevance decay per hop

# ── Task-type presets (from modular-patchbay traverser.ts) ──

TASK_PRESETS = {
    'fix': {
        'max_depth': 3, 'max_files': 15,
        'follow_kinds': {'imports', 'extends', 'implements', 'calls', 'uses_type',
                         'tested_by', 'tests', 'configured_by'},
        'follow_callers': False, 'min_weight': 0.4,
    },
    'review': {
        'max_depth': 2, 'max_files': 25,
        'follow_kinds': {'imports', 'extends', 'implements', 'calls', 'uses_type',
                         'tested_by', 'tests', 'documents', 'links_to'},
        'follow_callers': True, 'min_weight': 0.3,
    },
    'explain': {
        'max_depth': 4, 'max_files': 20,
        'follow_kinds': {'imports', 'extends', 'calls', 'documents', 'links_to',
                         'references', 'related'},
        'follow_callers': False, 'min_weight': 0.3,
    },
    'build': {
        'max_depth': 2, 'max_files': 15,
        'follow_kinds': {'imports', 'extends', 'implements', 'uses_type',
                         'documents', 'configured_by'},
        'follow_callers': False, 'min_weight': 0.5,
    },
    'document': {
        'max_depth': 3, 'max_files': 20,
        'follow_kinds': {'imports', 'extends', 'calls', 'tested_by', 'tests',
                         'documents', 'links_to', 'references', 'related'},
        'follow_callers': True, 'min_weight': 0.3,
    },
    'research': {
        'max_depth': 4, 'max_files': 30,
        'follow_kinds': {'documents', 'links_to', 'references', 'related',
                         'continues', 'supersedes'},
        'follow_callers': False, 'min_weight': 0.2,
    },
}


def detect_task_type(query: str) -> str:
    """Auto-detect task type from query keywords (from modular-patchbay)."""
    q = query.lower()
    if re.search(r'fix|bug|error|crash|broken|issue', q): return 'fix'
    if re.search(r'\bresearch\b|find|look.*up|what.*about', q): return 'research'
    if re.search(r'\breview\b|pr\b|pull request|diff|changes', q): return 'review'
    if re.search(r'explain|how does|what is|understand|walk.*through', q): return 'explain'
    if re.search(r'add|build|create|implement|feature|new', q): return 'build'
    if re.search(r'document|readme|write.*doc|api.*doc', q): return 'document'
    return 'explain'  # safe default


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

# Markdown link extraction (for doc→doc relations)
MD_LINK = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

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
        for fp in file_index:
            if fp.endswith('/' + c) or fp == c:
                return fp

    return None


def _resolve_md_link(link_target: str, source_dir: str, file_index: dict) -> str:
    """Resolve a markdown link target to an indexed file."""
    if link_target.startswith('http://') or link_target.startswith('https://'):
        return None
    # Strip anchors
    target = link_target.split('#')[0]
    if not target:
        return None
    # Resolve relative to source dir
    if not target.startswith('/'):
        full = str(Path(source_dir) / target)
    else:
        full = target.lstrip('/')
    # Normalize to forward slashes — file_index keys are forward-slash on Windows
    full = str(Path(full)).replace('\\', '/')
    if full in file_index:
        return full
    for fp in file_index:
        if fp.endswith('/' + full) or fp == full:
            return fp
    return None


# ── Build graph ──

def build_graph(files: list, corpus_root: Optional[str] = None) -> dict:
    """Build a relation graph from indexed files.

    Args:
        files: indexed file entries
        corpus_root: absolute path the indexed paths are relative to. Defaults to
            os.getcwd(). Required for TS tsconfig.json path-alias resolution —
            indexed paths are relative, but tsconfig walks need absolute paths.

    Returns:
        {nodes, edges, outgoing, incoming, stats}
    """
    if corpus_root is None:
        corpus_root = os.getcwd()

    # Normalize paths to forward slashes (Windows indexes store backslashes)
    file_index = {}
    for f in files:
        file_index[f['path'].replace('\\', '/')] = f

    nodes = {}
    edges = []
    outgoing = defaultdict(list)
    incoming = defaultdict(list)

    # Phase 1: Extract node metadata
    for f in files:
        path = f['path'].replace('\\', '/')
        ext = Path(path).suffix.lower()
        content = f.get('content', '')
        if not content and f.get('tree'):
            content = f['tree'].get('text', '')

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

    _truncated = [False]

    def _add_edge(source, target, kind):
        # Hard cap to prevent the silent truncation pattern that left
        # `metadata.total_relations: 12175` mismatched with `relations[]` of
        # 5000. Past the cap we stop appending and warn once on stderr.
        if len(edges) >= MAX_RELATIONS:
            if not _truncated[0]:
                _truncated[0] = True
                print(f"<!-- code_graph: hit MAX_RELATIONS={MAX_RELATIONS}, "
                      f"edges truncated. Set CONTEXT_ENG_MAX_RELATIONS to raise. -->",
                      file=sys.stderr)
            return
        edge = {'source': source, 'target': target, 'kind': kind,
                'weight': RELATION_KINDS.get(kind, 0.3)}
        edges.append(edge)
        outgoing[source].append(edge)
        incoming[target].append(edge)

    # Phase 2: Extract relations
    for f in files:
        path = f['path'].replace('\\', '/')
        ext = Path(path).suffix.lower()
        content = f.get('content', '')
        if not content and f.get('tree'):
            content = f['tree'].get('text', '')
        source_dir = str(Path(path).parent).replace('\\', '/')

        # Code imports
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
                if ext in ('.ts', '.tsx', '.js', '.jsx', '.mjs'):
                    if not import_path.startswith('.') and not import_path.startswith('/'):
                        # Try tsconfig.json path-alias resolution before skipping.
                        # Aliases like `@/auth/middleware` (defined in compilerOptions.paths)
                        # are silently dropped today; resolver maps them to real files.
                        abs_source = os.path.join(corpus_root, path)
                        resolved_abs = _TS_RESOLVER.resolve_alias(import_path, abs_source)
                        if resolved_abs:
                            try:
                                rel = os.path.relpath(resolved_abs, corpus_root).replace('\\', '/')
                            except ValueError:
                                rel = None  # different drives on Windows
                            if rel and rel in file_index and rel != path:
                                _add_edge(path, rel, 'imports')
                        continue

                target = _resolve_import(import_path, source_dir, file_index)
                if target and target != path:
                    _add_edge(path, target, 'imports')

        # Markdown links (doc → doc or doc → code)
        if ext in ('.md', '.mdx'):
            for m in MD_LINK.finditer(content):
                link_target = m.group(2)
                resolved = _resolve_md_link(link_target, source_dir, file_index)
                if resolved and resolved != path:
                    _add_edge(path, resolved, 'links_to')

        # Test ↔ Source relations
        if nodes[path]['is_test']:
            test_name = Path(path).stem.lower()
            test_name = re.sub(r'\.(test|spec)$', '', test_name)
            test_name = re.sub(r'^test[_-]', '', test_name)
            test_name = re.sub(r'[_-]test$', '', test_name)

            for other_path, other_node in nodes.items():
                if other_path == path:
                    continue
                other_stem = Path(other_path).stem.lower()
                if other_stem == test_name and other_node['is_code']:
                    _add_edge(path, other_path, 'tests')
                    _add_edge(other_path, path, 'tested_by')

        # Doc ↔ Code relations (by filename matching)
        if nodes[path]['is_doc']:
            doc_stem = Path(path).stem.lower().replace('-', '').replace('_', '')
            for other_path, other_node in nodes.items():
                if other_path == path or not other_node['is_code']:
                    continue
                code_stem = Path(other_path).stem.lower().replace('-', '').replace('_', '')
                if doc_stem == code_stem or doc_stem in code_stem or code_stem in doc_stem:
                    _add_edge(path, other_path, 'documents')

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
                  follow_callers: bool = False,
                  follow_kinds: set = None,
                  min_weight: float = 0.05) -> list:
    """BFS from entry points, following relations with relevance decay.

    Args:
        entry_points: [{path, confidence}]
        graph: output of build_graph()
        max_depth: how many hops to follow
        max_files: cap on returned files
        follow_kinds: explicit set of relation kinds to follow (overrides flags)
        follow_callers: also follow incoming edges (who imports/calls this?)
        min_weight: minimum relevance to keep traversing

    Returns:
        [{path, relevance, distance, reason}] sorted by relevance desc
    """
    visited = {}
    queue = []

    # Determine which relation kinds to follow
    if follow_kinds is not None:
        allowed_kinds = follow_kinds
    else:
        allowed_kinds = {'imports', 'extends', 'implements', 'calls', 'uses_type', 'configured_by'}
        if follow_tests:
            allowed_kinds.update({'tested_by', 'tests'})
        if follow_docs:
            allowed_kinds.update({'documents', 'links_to', 'references'})

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
            if edge['kind'] not in allowed_kinds:
                continue

            new_rel = current['relevance'] * edge['weight'] * DECAY
            if new_rel < min_weight:
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
                if edge['kind'] not in ('imports', 'calls', 'extends', 'implements'):
                    continue

                new_rel = current['relevance'] * edge['weight'] * DECAY * 0.7
                if new_rel < min_weight:
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

    results = sorted(visited.values(), key=lambda x: -x['relevance'])
    return results[:max_files]


def traverse_for_task(query: str, entry_points: list, graph: dict,
                      task_type: str = None) -> list:
    """Convenience: auto-detect task type and traverse with preset."""
    task = task_type or detect_task_type(query)
    preset = TASK_PRESETS.get(task, TASK_PRESETS['explain'])
    return traverse_from(
        entry_points, graph,
        max_depth=preset['max_depth'],
        max_files=preset['max_files'],
        follow_callers=preset['follow_callers'],
        follow_kinds=preset['follow_kinds'],
        min_weight=preset['min_weight'],
    )


def build_graph_with_fallback(files: list, graphify_path: Optional[str] = None, corpus_root: Optional[str] = None) -> dict:
    """Build graph from Graphify graph.json if available, else import-only fallback.

    `corpus_root` is forwarded to build_graph for tsconfig path-alias resolution
    on the fallback path. Graphify-driven path runs upstream and produces its own
    edges; alias resolution there would have to happen at graphify-extraction time.
    """
    if graphify_path:
        from graphify_adapter import load_graphify_graph, adapt_to_code_graph

        graphify_data = load_graphify_graph(graphify_path)
        if graphify_data is not None:
            indexed_paths = {f['path'] for f in files}
            graph = adapt_to_code_graph(graphify_data, indexed_paths)
            if graph['edges']:
                print(f"<!-- Graph source: graphify ({graph['stats']['total_nodes']} nodes, "
                      f"{graph['stats']['total_edges']} edges) -->", file=sys.stderr)
                return graph

    graph = build_graph(files, corpus_root=corpus_root)
    print(f"<!-- Graph source: import-only ({graph['stats']['total_nodes']} nodes, "
          f"{graph['stats']['total_edges']} edges) -->", file=sys.stderr)
    return graph


def find_entry_points(query_scored: list, threshold: float = 0.3) -> list:
    """Convert scored files (from keyword matching) to graph entry points."""
    return [
        {'path': s['path'], 'confidence': s['relevance'], 'reason': 'keyword match'}
        for s in query_scored
        if s['relevance'] >= threshold
    ]
