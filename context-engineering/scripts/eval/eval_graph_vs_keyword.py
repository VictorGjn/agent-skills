"""
Eval: graph-enhanced vs keyword-only on modular-patchbay.
Measures whether import graph traversal improves precision and weighted recall.
"""
import sys, json, re, hashlib
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import (
    classify_knowledge_type, estimate_tokens, stem, split_camel,
    tokenize_query, score_file, relevance_to_depth,
    pack_context, estimate_at_depth, DEPTH_NAMES, KNOWLEDGE_TYPES
)
from code_graph import build_graph, traverse_from, find_entry_points

# ── Tree parsers (minimal) ──
def parse_md(src, content):
    lines = content.split('\n'); hr = re.compile(r'^(#{1,6})\s+(.+)$'); ctr = [0]
    def mk(t, d):
        ctr[0] += 1
        return {'nodeId': f'n{d}-{ctr[0]}', 'title': t, 'depth': d, 'text': '', 'tokens': 0,
                'totalTokens': 0, 'children': [], 'firstSentence': '', 'firstParagraph': ''}
    root = mk(src, 0); stk = [root]; cur = []
    def flush():
        txt = '\n'.join(cur).strip(); n = stk[-1]; n['text'] = txt; n['tokens'] = estimate_tokens(txt)
        if txt:
            m = re.match(r'^[^\n]*?[.!?](?:\s|$)', txt)
            n['firstSentence'] = m.group(0).strip()[:200] if m else txt.split('\n')[0][:200]
            n['firstParagraph'] = txt.split('\n\n')[0][:500]
        cur.clear()
    for line in lines:
        m = hr.match(line)
        if m:
            flush(); lv = len(m.group(1)); nd = mk(m.group(2).strip(), lv)
            while len(stk) > 1 and stk[-1]['depth'] >= lv: stk.pop()
            stk[-1]['children'].append(nd); stk.append(nd)
        else: cur.append(line)
    flush(); _tot(root); return root

def parse_code(src, content, lang):
    if lang in ('typescript', 'javascript'):
        pat = re.compile(r'^export\s+(?:default\s+)?(?:async\s+)?(?:declare\s+)?'
                         r'(?:(?:abstract\s+)?class|interface|type|enum|function|(?:const|let|var))\s+(\w+)', re.M)
    elif lang == 'python':
        pat = re.compile(r'^(?:class|(?:async\s+)?def)\s+(\w+)', re.M)
    else: pat = None
    tokens = estimate_tokens(content)
    syms = [m.group(1) for m in pat.finditer(content)] if pat else []
    root = {'nodeId': 'c0', 'title': src, 'depth': 0, 'text': content, 'tokens': tokens,
            'totalTokens': tokens, 'children': [], 'firstSentence': f'{len(syms)} exports',
            'firstParagraph': ', '.join(syms[:15])}
    for i, s in enumerate(syms[:30]):
        root['children'].append({'nodeId': f'c{i+1}', 'title': s, 'depth': 1, 'text': '',
                                  'tokens': 0, 'totalTokens': 0, 'children': [],
                                  'firstSentence': s, 'firstParagraph': ''})
    _tot(root); return root

def _tot(n):
    t = n['tokens']
    for c in n['children']: t += _tot(c)
    n['totalTokens'] = t; return t

def _hd(n, d=4):
    h = []
    if 0 < n.get('depth', 0) <= d and n.get('title'):
        h.append({'depth': n['depth'], 'title': n['title'], 'tokens': n['totalTokens']})
    for c in n.get('children', []): h.extend(_hd(c, d))
    return h

LMAP = {'.ts': 'typescript', '.tsx': 'typescript', '.js': 'javascript', '.jsx': 'javascript',
        '.py': 'python', '.md': 'markdown', '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml'}

def build_index(raw):
    files = []; total = 0
    for rf in raw:
        p = rf['path']; c = rf['content']; ext = Path(p).suffix.lower()
        lang = LMAP.get(ext, 'unknown')
        if lang == 'markdown': tree = parse_md(p, c)
        elif lang in ('typescript', 'javascript', 'python'): tree = parse_code(p, c, lang)
        else:
            tree = {'nodeId': 'n0', 'title': p, 'depth': 0, 'text': c[:500],
                    'tokens': estimate_tokens(c), 'totalTokens': estimate_tokens(c),
                    'children': [], 'firstSentence': c.split('\n')[0][:200],
                    'firstParagraph': c.split('\n\n')[0][:500]}
        hd = _hd(tree); ht = ' '.join(h['title'] for h in hd)
        kt = classify_knowledge_type(p, ht, tree.get('firstParagraph', ''))
        fe = {'path': p, 'tokens': tree['totalTokens'], 'language': lang,
              'knowledge_type': kt, 'headings': hd, 'tree': tree,
              'content': c}  # keep content for graph building
        files.append(fe); total += tree['totalTokens']
    kt_d = defaultdict(int)
    for f in files: kt_d[f['knowledge_type']] += 1
    return {'root': 'victorgjn/modular-patchbay', 'totalFiles': len(files),
            'totalTokens': total, 'knowledgeTypes': dict(kt_d), 'files': files}

# ── Test cases ──
TESTCASES = [
    {"query": "context graph engine traversal",
     "ground_truth": ["src/graph/index.ts", "src/graph/traverser.ts", "src/graph/types.ts",
                       "src/graph/resolver.ts", "src/graph/packer.ts", "src/graph/db.ts", "src/graph/scanner.ts"],
     "critical": ["src/graph/traverser.ts", "src/graph/types.ts", "src/graph/index.ts"]},
    {"query": "tree indexer depth filter markdown",
     "ground_truth": ["src/services/treeIndexer.ts", "src/services/treeNavigator.ts",
                       "src/services/treeAwareRetriever.ts", "src/utils/depthFilter.ts", "src/utils/codeIndexer.ts"],
     "critical": ["src/services/treeIndexer.ts"]},
    {"query": "knowledge pipeline retrieval provenance",
     "ground_truth": ["src/services/knowledgePipeline.ts", "src/services/treeAwareRetriever.ts",
                       "src/services/provenanceService.ts", "src/services/adaptiveRetrieval.ts",
                       "src/services/contrastiveRetrieval.ts", "src/services/pipeline.ts"],
     "critical": ["src/services/knowledgePipeline.ts", "src/services/provenanceService.ts"]},
    {"query": "MCP server tools integration",
     "ground_truth": ["server/mcp/modular-server.ts", "server/mcp/manager.ts",
                       "server/routes/mcp.ts", "src/store/mcpStore.ts", "bin/modular-mcp.ts"],
     "critical": ["server/mcp/modular-server.ts", "server/mcp/manager.ts"]},
    {"query": "connector notion slack github integration",
     "ground_truth": ["server/routes/connectors/notion.ts", "server/routes/connectors/slack.ts",
                       "server/routes/connectors/github.ts", "server/routes/connectors/index.ts",
                       "server/routes/connectors.ts"],
     "critical": ["server/routes/connectors/notion.ts", "server/routes/connectors/slack.ts"]},
]

DEPTH_WEIGHTS = {0: 1.0, 1: 0.8, 2: 0.5, 3: 0.3, 4: 0.15}

def eval_mode(index, testcases, budgets, use_graph=False, graph=None):
    results = []
    for tc in testcases:
        query = tc['query']; gt = [g.lower() for g in tc['ground_truth']]
        critical = [c.lower() for c in tc.get('critical', [])]
        qt = tokenize_query(query); ql = query.lower()

        # Score
        keyword_scored = []
        for f in index['files']:
            rel = score_file(f, qt, ql)
            if rel > 0:
                keyword_scored.append({'path': f['path'], 'relevance': rel, 'tokens': f['tokens'],
                                       'tree': f.get('tree'), 'knowledge_type': f.get('knowledge_type', 'evidence')})
        keyword_scored.sort(key=lambda x: x['relevance'], reverse=True)

        for budget in budgets:
            if use_graph and graph:
                # Graph-enhanced
                entry_pts = find_entry_points(keyword_scored[:10], threshold=0.2)
                traversed = traverse_from(entry_pts, graph, max_depth=3, max_files=30)
                merged = {}
                for s in keyword_scored:
                    merged[s['path']] = {**s, 'kw': s['relevance'], 'gr': 0}
                for t in traversed:
                    p = t['path']
                    if p in merged:
                        merged[p]['gr'] = t['relevance']
                        merged[p]['relevance'] = min(1.0,
                            max(merged[p]['kw'], t['relevance']) +
                            min(merged[p]['kw'], t['relevance']) * 0.3)
                    else:
                        fe = next((f for f in index['files'] if f['path'] == p), None)
                        if fe:
                            merged[p] = {'path': p, 'relevance': t['relevance'], 'tokens': fe['tokens'],
                                         'tree': fe.get('tree'), 'knowledge_type': fe.get('knowledge_type', 'evidence'),
                                         'kw': 0, 'gr': t['relevance']}
                scored = sorted(merged.values(), key=lambda x: x['relevance'], reverse=True)[:30]
            else:
                scored = keyword_scored[:30]

            packed = pack_context(scored, budget)
            packed_map = {p['path'].lower(): p['depth'] for p in packed}

            gt_found = []; gt_missed = []; wsum = 0
            for g in gt:
                hit = False
                for pp, d in packed_map.items():
                    if g == pp or g in pp or pp in g or Path(g).name == Path(pp).name:
                        gt_found.append((g, d)); wsum += DEPTH_WEIGHTS.get(d, 0.1); hit = True; break
                if not hit: gt_missed.append(g)

            recall = len(gt_found) / len(gt) if gt else 0
            wrecall = wsum / len(gt) if gt else 0

            rel_packed = 0
            for pp in packed_map:
                for g in gt:
                    if g == pp or g in pp or pp in g or Path(g).name == Path(pp).name: rel_packed += 1; break
            precision = rel_packed / len(packed_map) if packed_map else 0

            crit_found = []; crit_missed = []
            for c in critical:
                hit = False
                for pp, d in packed_map.items():
                    if (c == pp or c in pp or pp in c or Path(c).name == Path(pp).name) and d <= 2:
                        crit_found.append((c, d)); hit = True; break
                if not hit: crit_missed.append(c)
            crit_rate = len(crit_found) / len(critical) if critical else 1.0

            tok_used = sum(p['tokens'] for p in packed)
            graph_discovered = 0
            if use_graph:
                for p in packed:
                    if any(p['path'].lower() == g or g in p['path'].lower() for g in gt):
                        if p.get('kw', 0) == 0 and p.get('gr', 0) > 0:
                            graph_discovered += 1

            results.append({
                'query': query, 'budget': budget, 'recall': round(recall, 3),
                'weighted_recall': round(wrecall, 3), 'precision': round(precision, 3),
                'critical_hit_rate': round(crit_rate, 3), 'files_packed': len(packed),
                'tokens_used': tok_used, 'graph_discovered': graph_discovered,
                'gt_found': gt_found, 'gt_missed': gt_missed,
            })
    return results

def print_comparison(kw_results, gr_results, budgets):
    print(f"\n{'='*85}")
    print(f"KEYWORD-ONLY vs GRAPH-ENHANCED COMPARISON")
    print(f"{'='*85}")
    print(f"\n{'Budget':>8} | {'--- Keyword ---':^25} | {'--- Graph ---':^25} | {'Delta':^10}")
    print(f"{'':>8} | {'Recall':>7} {'WRecal':>7} {'Prec':>6} {'CH':>4} | {'Recall':>7} {'WRecal':>7} {'Prec':>6} {'CH':>4} | {'WR':>5} {'P':>5}")
    print(f"{'-'*85}")

    for budget in budgets:
        kw = [r for r in kw_results if r['budget'] == budget]
        gr = [r for r in gr_results if r['budget'] == budget]
        n = len(kw)
        avg_kw = lambda k: sum(r[k] for r in kw) / n
        avg_gr = lambda k: sum(r[k] for r in gr) / n

        dwr = avg_gr('weighted_recall') - avg_kw('weighted_recall')
        dp = avg_gr('precision') - avg_kw('precision')
        print(f"{budget:>8} | {avg_kw('recall'):>7.3f} {avg_kw('weighted_recall'):>7.3f} "
              f"{avg_kw('precision'):>6.3f} {avg_kw('critical_hit_rate'):>4.2f} | "
              f"{avg_gr('recall'):>7.3f} {avg_gr('weighted_recall'):>7.3f} "
              f"{avg_gr('precision'):>6.3f} {avg_gr('critical_hit_rate'):>4.2f} | "
              f"{dwr:>+5.3f} {dp:>+5.3f}")

    # Per-query detail at 8K
    print(f"\n{'─'*85}")
    print("PER-QUERY @ 8K")
    print(f"{'─'*85}")
    for tc in TESTCASES:
        q = tc['query'][:45]
        kw_r = next(r for r in kw_results if r['query'] == tc['query'] and r['budget'] == 8000)
        gr_r = next(r for r in gr_results if r['query'] == tc['query'] and r['budget'] == 8000)

        print(f"\n  \"{q}\"")
        print(f"    KW: R={kw_r['recall']:.2f} WR={kw_r['weighted_recall']:.2f} P={kw_r['precision']:.2f} "
              f"CH={kw_r['critical_hit_rate']:.2f} ({kw_r['files_packed']}f)")
        print(f"    GR: R={gr_r['recall']:.2f} WR={gr_r['weighted_recall']:.2f} P={gr_r['precision']:.2f} "
              f"CH={gr_r['critical_hit_rate']:.2f} ({gr_r['files_packed']}f) "
              f"[+{gr_r['graph_discovered']} discovered]")

        kw_missed = set(kw_r['gt_missed'])
        gr_missed = set(gr_r['gt_missed'])
        newly_found = kw_missed - gr_missed
        if newly_found:
            print(f"    Graph found: {', '.join(Path(p).name for p in newly_found)}")

# ── Main ──
if __name__ == '__main__':
    print("Loading files...", file=sys.stderr)
    with open('session/modular-patchbay-files.json') as f:
        raw = json.load(f)

    print(f"Building index ({len(raw)} files)...", file=sys.stderr)
    index = build_index(raw)

    print("Building code graph...", file=sys.stderr)
    graph = build_graph(index['files'])
    print(f"Graph: {graph['stats']['total_nodes']} nodes, {graph['stats']['total_edges']} edges", file=sys.stderr)

    budgets = [4000, 8000, 16000]

    print("Running keyword-only eval...", file=sys.stderr)
    kw_results = eval_mode(index, TESTCASES, budgets, use_graph=False)

    print("Running graph-enhanced eval...", file=sys.stderr)
    gr_results = eval_mode(index, TESTCASES, budgets, use_graph=True, graph=graph)

    print_comparison(kw_results, gr_results, budgets)
