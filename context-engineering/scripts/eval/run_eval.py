"""
Full eval pipeline: index from fetched files, classify, score, measure recall.

Reads session/modular-patchbay-files.json (pre-fetched via GitHub API)
Builds index with knowledge types + feature clustering
Runs test cases at multiple budgets
Reports results

Usage: python3 run_eval.py
"""

import sys
import json
import re
import hashlib
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import (
    classify_knowledge_type, estimate_tokens, stem,
    tokenize_query, score_file, relevance_to_depth,
    pack_context, estimate_at_depth, DEPTH_NAMES, KNOWLEDGE_TYPES
)

# ── Parse trees ──

def parse_markdown_tree(source, content):
    lines = content.split('\n')
    heading_re = re.compile(r'^(#{1,6})\s+(.+)$')
    counter = [0]
    def mknode(title, depth):
        counter[0] += 1
        return {'nodeId': f'n{depth}-{counter[0]}', 'title': title, 'depth': depth,
                'text': '', 'tokens': 0, 'totalTokens': 0, 'children': [],
                'firstSentence': '', 'firstParagraph': ''}
    root = mknode(source, 0)
    stack = [root]
    cur = []
    def flush():
        text = '\n'.join(cur).strip()
        node = stack[-1]
        node['text'] = text
        node['tokens'] = estimate_tokens(text)
        if text:
            m = re.match(r'^[^\n]*?[.!?](?:\s|$)', text)
            node['firstSentence'] = m.group(0).strip()[:200] if m else text.split('\n')[0][:200]
            node['firstParagraph'] = text.split('\n\n')[0][:500]
        cur.clear()
    for line in lines:
        m = heading_re.match(line)
        if m:
            flush()
            lv = len(m.group(1))
            nd = mknode(m.group(2).strip(), lv)
            while len(stack) > 1 and stack[-1]['depth'] >= lv: stack.pop()
            stack[-1]['children'].append(nd)
            stack.append(nd)
        else:
            cur.append(line)
    flush()
    _totals(root)
    return root

def parse_code_tree(source, content, lang):
    if lang in ('typescript', 'javascript'):
        pat = re.compile(r'^export\s+(?:default\s+)?(?:async\s+)?(?:declare\s+)?'
                         r'(?:(?:abstract\s+)?class|interface|type|enum|function|(?:const|let|var))\s+(\w+)', re.M)
    elif lang == 'python':
        pat = re.compile(r'^(?:class|(?:async\s+)?def)\s+(\w+)', re.M)
    else:
        pat = None

    tokens = estimate_tokens(content)
    symbols = [m.group(1) for m in pat.finditer(content)] if pat else []

    root = {'nodeId': 'c0', 'title': source, 'depth': 0,
            'text': content, 'tokens': tokens, 'totalTokens': tokens,
            'children': [], 'firstSentence': f'{len(symbols)} exports',
            'firstParagraph': ', '.join(symbols[:15])}
    for i, sym in enumerate(symbols[:30]):
        root['children'].append({'nodeId': f'c{i+1}', 'title': sym, 'depth': 1,
                                  'text': '', 'tokens': 0, 'totalTokens': 0,
                                  'children': [], 'firstSentence': sym, 'firstParagraph': ''})
    _totals(root)
    return root

def _totals(node):
    t = node['tokens']
    for c in node['children']: t += _totals(c)
    node['totalTokens'] = t
    return t

def _headings(node, max_d=4):
    h = []
    if 0 < node.get('depth', 0) <= max_d and node.get('title'):
        h.append({'depth': node['depth'], 'title': node['title'], 'tokens': node['totalTokens']})
    for c in node.get('children', []): h.extend(_headings(c, max_d))
    return h

# ── Language detect ──
LANG_MAP = {'.ts': 'typescript', '.tsx': 'typescript', '.js': 'javascript',
            '.jsx': 'javascript', '.py': 'python', '.md': 'markdown', '.mdx': 'markdown',
            '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml'}

# ── Domain stem extraction (from modular repoIndexer) ──

GENERIC_DIRS = {'src','lib','app','server','client','api','services','service',
    'store','stores','components','component','pages','views','routes','route',
    'controllers','utils','util','helpers','common','shared','models','types',
    'interfaces','panels','tabs','layouts','middleware','config','hooks',
    'providers','__tests__','test','tests','docs','doc','bin','scripts'}

FUNC_SUFFIX = re.compile(r'(?:Service|Store|Controller|Route|Router|Panel|Component|View|'
    r'Page|Tab|Utils?|Helpers?|Handler|Manager|Provider|Config|Spec|Test|Mock|'
    r'Schema|Model|Types?|Index|Client|Api)$', re.I)

def domain_stem(filepath):
    parts = Path(filepath).parts
    fname = Path(filepath).stem
    s = FUNC_SUFFIX.sub('', fname)
    if not s: s = fname
    s = re.sub(r'([a-z])([A-Z])', r'\1_\2', s).lower().strip('_')
    s = re.sub(r'[-_]+', '_', s)
    if s in ('index', 'main', 'app', 'mod', 'init', 'types'):
        for part in reversed(parts[:-1]):
            if part.lower() not in GENERIC_DIRS:
                return part.lower()
    return s

# ── Build index ──

def build_index(raw_files):
    files = []
    total_tokens = 0
    for rf in raw_files:
        path = rf['path']
        content = rf['content']
        ext = Path(path).suffix.lower()
        lang = LANG_MAP.get(ext, 'unknown')

        if lang in ('markdown',):
            tree = parse_markdown_tree(path, content)
        elif lang in ('typescript', 'javascript', 'python'):
            tree = parse_code_tree(path, content, lang)
        else:
            tree = {'nodeId': 'n0', 'title': path, 'depth': 0,
                    'text': content[:500], 'tokens': estimate_tokens(content),
                    'totalTokens': estimate_tokens(content), 'children': [],
                    'firstSentence': content.split('\n')[0][:200],
                    'firstParagraph': content.split('\n\n')[0][:500]}

        headings = _headings(tree)
        headings_text = ' '.join(h['title'] for h in headings)
        kt = classify_knowledge_type(path, headings_text, tree.get('firstParagraph', ''))
        ds = domain_stem(path)

        fe = {
            'path': path, 'size': rf.get('size', len(content)),
            'tokens': tree['totalTokens'],
            'hash': hashlib.md5(content.encode()).hexdigest()[:12],
            'language': lang, 'knowledge_type': kt, 'domain_stem': ds,
            'headings': headings, 'tree': tree,
        }
        files.append(fe)
        total_tokens += tree['totalTokens']

    # Knowledge type distribution
    kt_dist = defaultdict(int)
    for f in files: kt_dist[f['knowledge_type']] += 1

    # Feature clusters
    clusters = defaultdict(list)
    for f in files: clusters[f['domain_stem']].append(f['path'])

    return {
        'root': 'victorgjn/modular-patchbay',
        'totalFiles': len(files),
        'totalTokens': total_tokens,
        'knowledgeTypes': dict(kt_dist),
        'featureClusters': {k: len(v) for k, v in clusters.items() if len(v) > 1},
        'files': files,
    }

# ── Test cases with ground truth ──
# For each query: which files SHOULD be found, which are CRITICAL (must be at depth ≤ 2)

TESTCASES = [
    {
        "query": "context graph engine traversal",
        "ground_truth": [
            "src/graph/index.ts", "src/graph/traverser.ts", "src/graph/types.ts",
            "src/graph/resolver.ts", "src/graph/packer.ts", "src/graph/db.ts",
            "src/graph/scanner.ts",
        ],
        "critical": ["src/graph/traverser.ts", "src/graph/types.ts", "src/graph/index.ts"],
    },
    {
        "query": "tree indexer depth filter markdown",
        "ground_truth": [
            "src/services/treeIndexer.ts", "src/services/treeNavigator.ts",
            "src/services/treeAwareRetriever.ts", "src/utils/depthFilter.ts",
            "src/utils/codeIndexer.ts",
        ],
        "critical": ["src/services/treeIndexer.ts"],
    },
    {
        "query": "knowledge pipeline retrieval provenance",
        "ground_truth": [
            "src/services/knowledgePipeline.ts", "src/services/treeAwareRetriever.ts",
            "src/services/provenanceService.ts", "src/services/adaptiveRetrieval.ts",
            "src/services/contrastiveRetrieval.ts", "src/services/pipeline.ts",
        ],
        "critical": ["src/services/knowledgePipeline.ts", "src/services/provenanceService.ts"],
    },
    {
        "query": "MCP server tools integration",
        "ground_truth": [
            "server/mcp/modular-server.ts", "server/mcp/manager.ts",
            "server/routes/mcp.ts", "src/store/mcpStore.ts",
            "bin/modular-mcp.ts",
        ],
        "critical": ["server/mcp/modular-server.ts", "server/mcp/manager.ts"],
    },
    {
        "query": "repo indexer feature clustering",
        "ground_truth": [
            "server/services/repoIndexer.ts", "server/services/githubIndexer.ts",
            "server/routes/repo-index.ts",
        ],
        "critical": ["server/services/repoIndexer.ts"],
    },
    {
        "query": "agent export Claude Code configuration",
        "ground_truth": [
            "src/utils/agentExport.ts", "src/utils/agentExportYaml.ts",
            "src/utils/agentImport.ts", "server/routes/claude-config.ts",
            "src/components/SaveAgentModal.tsx",
        ],
        "critical": ["src/utils/agentExport.ts", "server/routes/claude-config.ts"],
    },
    {
        "query": "embedding service semantic search",
        "ground_truth": [
            "server/services/embeddingService.ts", "server/routes/embeddings.ts",
            "src/services/treeAwareRetriever.ts",
        ],
        "critical": ["server/services/embeddingService.ts"],
    },
    {
        "query": "memory system fact extraction lessons",
        "ground_truth": [
            "src/services/memoryPipeline.ts", "server/services/factExtractor.ts",
            "server/services/lessonExtractor.ts", "server/services/memoryScorer.ts",
            "server/routes/memory.ts", "src/store/memoryStore.ts",
        ],
        "critical": ["src/services/memoryPipeline.ts", "server/services/factExtractor.ts"],
    },
    {
        "query": "metaprompt v2 pattern generation",
        "ground_truth": [
            "src/metaprompt/v2/index.ts", "src/metaprompt/v2/assembler.ts",
            "src/metaprompt/v2/pattern-selector.ts", "src/metaprompt/v2/parser.ts",
            "src/metaprompt/v2/researcher.ts",
        ],
        "critical": ["src/metaprompt/v2/index.ts", "src/metaprompt/v2/pattern-selector.ts"],
    },
    {
        "query": "connector notion slack github integration",
        "ground_truth": [
            "server/routes/connectors/notion.ts", "server/routes/connectors/slack.ts",
            "server/routes/connectors/github.ts", "server/routes/connectors/index.ts",
            "server/routes/connectors.ts",
        ],
        "critical": ["server/routes/connectors/notion.ts", "server/routes/connectors/slack.ts"],
    },
]

# ── Eval engine ──

DEPTH_WEIGHTS = {0: 1.0, 1: 0.8, 2: 0.5, 3: 0.3, 4: 0.15}

def run_eval(index, testcases, budgets):
    results = []
    for tc in testcases:
        query = tc['query']
        gt = [g.lower() for g in tc['ground_truth']]
        critical = [c.lower() for c in tc.get('critical', [])]
        qt = tokenize_query(query)
        ql = query.lower()

        for budget in budgets:
            scored = []
            for f in index['files']:
                rel = score_file(f, qt, ql)
                if rel > 0:
                    scored.append({'path': f['path'], 'relevance': rel,
                                   'tokens': f['tokens'], 'tree': f.get('tree'),
                                   'knowledge_type': f.get('knowledge_type', 'evidence')})
            scored.sort(key=lambda x: x['relevance'], reverse=True)
            scored = scored[:50]
            packed = pack_context(scored, budget)

            # Map packed paths
            packed_map = {}
            for p in packed:
                packed_map[p['path'].lower()] = p['depth']

            # Recall
            gt_found = []
            gt_missed = []
            wsum = 0
            for g in gt:
                hit = False
                for pp, d in packed_map.items():
                    if g == pp or g in pp or pp in g or Path(g).name == Path(pp).name:
                        gt_found.append((g, d))
                        wsum += DEPTH_WEIGHTS.get(d, 0.1)
                        hit = True
                        break
                if not hit:
                    gt_missed.append(g)

            recall = len(gt_found) / len(gt) if gt else 0
            wrecall = wsum / len(gt) if gt else 0

            # Precision
            rel_packed = 0
            for pp in packed_map:
                for g in gt:
                    if g == pp or g in pp or pp in g or Path(g).name == Path(pp).name:
                        rel_packed += 1
                        break
            precision = rel_packed / len(packed_map) if packed_map else 0

            # Critical hits (depth <= 2)
            crit_found = []
            crit_missed = []
            for c in critical:
                hit = False
                for pp, d in packed_map.items():
                    if (c == pp or c in pp or pp in c or Path(c).name == Path(pp).name) and d <= 2:
                        crit_found.append((c, d))
                        hit = True
                        break
                if not hit:
                    crit_missed.append(c)
            crit_rate = len(crit_found) / len(critical) if critical else 1.0

            tok_used = sum(p['tokens'] for p in packed)

            results.append({
                'query': query, 'budget': budget,
                'recall': round(recall, 3), 'weighted_recall': round(wrecall, 3),
                'precision': round(precision, 3), 'critical_hit_rate': round(crit_rate, 3),
                'files_packed': len(packed), 'tokens_used': tok_used,
                'gt_found': gt_found, 'gt_missed': gt_missed,
                'crit_found': crit_found, 'crit_missed': crit_missed,
            })

    return results

def print_report(index, results, budgets):
    print(f"\n{'='*80}")
    print(f"DEPTH PACKING EVAL — {index['root']}")
    print(f"{'='*80}")
    print(f"Files: {index['totalFiles']} | Tokens: {index['totalTokens']:,}")
    print(f"Knowledge types: {json.dumps(index['knowledgeTypes'])}")
    print(f"Feature clusters (>1 file): {len(index['featureClusters'])}")
    print(f"Test cases: {len(TESTCASES)}")
    print()

    # Summary table
    print(f"{'Budget':>8} | {'Recall':>7} | {'W.Recal':>7} | {'Precis.':>7} | {'CritHit':>7} | {'Files':>5} | {'TokUsed':>7}")
    print(f"{'-'*8}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*5}-+-{'-'*7}")

    for budget in budgets:
        br = [r for r in results if r['budget'] == budget]
        n = len(br)
        avg = lambda key: sum(r[key] for r in br) / n
        print(f"{budget:>8} | {avg('recall'):>7.3f} | {avg('weighted_recall'):>7.3f} | "
              f"{avg('precision'):>7.3f} | {avg('critical_hit_rate'):>7.3f} | "
              f"{avg('files_packed'):>5.0f} | {avg('tokens_used'):>7.0f}")

    # Per-query detail at 4K and 16K
    print(f"\n{'─'*80}")
    print("PER-QUERY DETAIL (4K and 16K budgets)")
    print(f"{'─'*80}")

    for tc in TESTCASES:
        query = tc['query']
        print(f"\n  Q: \"{query}\"")
        print(f"  GT: {len(tc['ground_truth'])} files, Critical: {len(tc.get('critical', []))}")

        for budget in [4000, 16000]:
            r = next((r for r in results if r['query'] == query and r['budget'] == budget), None)
            if not r: continue

            status = '✓' if r['critical_hit_rate'] >= 0.8 and r['recall'] >= 0.5 else '△' if r['recall'] >= 0.3 else '✗'
            found_str = ', '.join(f"{Path(p).name}@{DEPTH_NAMES[d]}" for p, d in r['gt_found'][:6])
            missed_str = ', '.join(Path(p).name for p in r['gt_missed'][:4])

            print(f"    {status} @{budget:>5}tok: R={r['recall']:.2f} WR={r['weighted_recall']:.2f} "
                  f"P={r['precision']:.2f} CH={r['critical_hit_rate']:.2f} ({r['files_packed']}f/{r['tokens_used']}t)")
            if found_str: print(f"      Found: {found_str}")
            if missed_str: print(f"      Missed: {missed_str}")

    # Knowledge type analysis
    print(f"\n{'─'*80}")
    print("KNOWLEDGE TYPE DISTRIBUTION")
    print(f"{'─'*80}")
    for kt, count in sorted(index['knowledgeTypes'].items(), key=lambda x: -x[1]):
        label = KNOWLEDGE_TYPES.get(kt, {}).get('label', kt)
        bonus = KNOWLEDGE_TYPES.get(kt, {}).get('depth_bonus', 0)
        print(f"  {label:>15}: {count:>3} files  (bonus: {bonus:+.2f})")

# ── Main ──

if __name__ == '__main__':
    print("Loading fetched files...", file=sys.stderr)
    with open('session/modular-patchbay-files.json') as f:
        raw = json.load(f)

    print(f"Building index from {len(raw)} files...", file=sys.stderr)
    index = build_index(raw)

    budgets = [2000, 4000, 8000, 16000, 32000]

    print(f"Running {len(TESTCASES)} test cases at {len(budgets)} budget levels...", file=sys.stderr)
    results = run_eval(index, TESTCASES, budgets)

    print_report(index, results, budgets)

    # Save results
    out = {
        'repo': index['root'],
        'totalFiles': index['totalFiles'],
        'totalTokens': index['totalTokens'],
        'knowledgeTypes': index['knowledgeTypes'],
        'featureClusters': index['featureClusters'],
        'testcases': len(TESTCASES),
        'budgets': budgets,
        'results': [{k: v for k, v in r.items()
                      if k not in ('gt_found','gt_missed','crit_found','crit_missed')}
                     for r in results],
        'per_query': [{
            'query': r['query'], 'budget': r['budget'],
            'gt_found': [(p, DEPTH_NAMES.get(d,'?')) for p, d in r['gt_found']],
            'gt_missed': r['gt_missed'],
            'crit_missed': r['crit_missed'],
        } for r in results],
    }
    with open('skills/sauna/depth-packing/cache/eval-modular-patchbay.json', 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("\nResults saved to cache/eval-modular-patchbay.json", file=sys.stderr)
