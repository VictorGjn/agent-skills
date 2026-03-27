"""Eval on pallets/flask — Python framework, RST docs, different language."""
import sys, json, re, hashlib
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from pack_context_lib import (
    classify_knowledge_type, estimate_tokens, stem, split_camel,
    tokenize_query, score_file, relevance_to_depth,
    pack_context, DEPTH_NAMES, KNOWLEDGE_TYPES
)

# ── Tree parsers ──

def parse_rst_tree(source, content):
    """RST uses underline-style headings. Convert to heading-tree."""
    lines = content.split('\n')
    counter = [0]
    def mknode(title, depth):
        counter[0] += 1
        return {'nodeId': f'n{depth}-{counter[0]}', 'title': title, 'depth': depth,
                'text': '', 'tokens': 0, 'totalTokens': 0, 'children': [],
                'firstSentence': '', 'firstParagraph': ''}

    # RST heading markers by convention: = - ~ ^ "
    heading_chars = '=-~^"'
    root = mknode(source, 0); stack = [root]; cur = []

    def flush():
        text = '\n'.join(cur).strip(); node = stack[-1]
        node['text'] = text; node['tokens'] = estimate_tokens(text)
        if text:
            m = re.match(r'^[^\n]*?[.!?](?:\s|$)', text)
            node['firstSentence'] = m.group(0).strip()[:200] if m else text.split('\n')[0][:200]
            node['firstParagraph'] = text.split('\n\n')[0][:500]
        cur.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        # Check if next line is an underline (RST heading)
        if i + 1 < len(lines) and len(line.strip()) > 0:
            next_line = lines[i + 1]
            if len(next_line) >= 3 and all(c == next_line[0] for c in next_line.strip()) and next_line[0] in heading_chars:
                flush()
                level = heading_chars.index(next_line[0]) + 1
                nd = mknode(line.strip(), min(level, 4))
                while len(stack) > 1 and stack[-1]['depth'] >= nd['depth']:
                    stack.pop()
                stack[-1]['children'].append(nd); stack.append(nd)
                i += 2; continue
        cur.append(line); i += 1

    flush(); _totals(root); return root

def parse_markdown_tree(source, content):
    lines = content.split('\n'); heading_re = re.compile(r'^(#{1,6})\s+(.+)$'); counter = [0]
    def mknode(title, depth):
        counter[0] += 1
        return {'nodeId': f'n{depth}-{counter[0]}', 'title': title, 'depth': depth,
                'text': '', 'tokens': 0, 'totalTokens': 0, 'children': [],
                'firstSentence': '', 'firstParagraph': ''}
    root = mknode(source, 0); stack = [root]; cur = []
    def flush():
        text = '\n'.join(cur).strip(); node = stack[-1]; node['text'] = text; node['tokens'] = estimate_tokens(text)
        if text:
            m = re.match(r'^[^\n]*?[.!?](?:\s|$)', text)
            node['firstSentence'] = m.group(0).strip()[:200] if m else text.split('\n')[0][:200]
            node['firstParagraph'] = text.split('\n\n')[0][:500]
        cur.clear()
    for line in lines:
        m = heading_re.match(line)
        if m:
            flush(); lv = len(m.group(1)); nd = mknode(m.group(2).strip(), lv)
            while len(stack) > 1 and stack[-1]['depth'] >= lv: stack.pop()
            stack[-1]['children'].append(nd); stack.append(nd)
        else: cur.append(line)
    flush(); _totals(root); return root

def parse_code_tree(source, content, lang):
    if lang == 'python':
        pat = re.compile(r'^(?:class|(?:async\s+)?def)\s+(\w+)', re.M)
    else:
        pat = re.compile(r'^(?:export\s+)?(?:function|class|const|let|var)\s+(\w+)', re.M)
    tokens = estimate_tokens(content)
    symbols = [m.group(1) for m in pat.finditer(content)] if pat else []
    symbols = list(dict.fromkeys(symbols))
    root = {'nodeId': 'c0', 'title': source, 'depth': 0, 'text': content, 'tokens': tokens,
            'totalTokens': tokens, 'children': [],
            'firstSentence': f'{len(symbols)} definitions',
            'firstParagraph': ', '.join(symbols[:15])}
    for i, sym in enumerate(symbols[:30]):
        root['children'].append({'nodeId': f'c{i+1}', 'title': sym, 'depth': 1, 'text': '',
                                  'tokens': 0, 'totalTokens': 0, 'children': [],
                                  'firstSentence': sym, 'firstParagraph': ''})
    _totals(root); return root

def _totals(n):
    t = n['tokens']
    for c in n['children']: t += _totals(c)
    n['totalTokens'] = t; return t

def _headings(n, max_d=4):
    h = []
    if 0 < n.get('depth', 0) <= max_d and n.get('title'):
        h.append({'depth': n['depth'], 'title': n['title'], 'tokens': n['totalTokens']})
    for c in n.get('children', []): h.extend(_headings(c, max_d))
    return h

LANG_MAP = {'.py': 'python', '.pyi': 'python', '.md': 'markdown',
            '.rst': 'rst', '.txt': 'text',
            '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml'}

GENERIC_DIRS = {'src','lib','app','flask','tests','test','docs','doc','examples','scripts'}

def domain_stem(filepath):
    parts = Path(filepath).parts; fname = Path(filepath).stem
    s = re.sub(r'([a-z])([A-Z])', r'\1_\2', fname).lower().strip('_')
    s = re.sub(r'[-_]+', '_', s)
    if s in ('__init__', 'conftest', 'index', 'main', 'app', 'mod'):
        for part in reversed(parts[:-1]):
            if part.lower() not in GENERIC_DIRS: return part.lower()
    return s

def build_index(raw_files):
    files = []; total_tokens = 0
    for rf in raw_files:
        path = rf['path']; content = rf['content']; ext = Path(path).suffix.lower()
        lang = LANG_MAP.get(ext, 'unknown')
        if lang == 'rst': tree = parse_rst_tree(path, content)
        elif lang == 'markdown': tree = parse_markdown_tree(path, content)
        elif lang == 'python': tree = parse_code_tree(path, content, lang)
        else:
            tree = {'nodeId': 'n0', 'title': path, 'depth': 0, 'text': content[:500],
                    'tokens': estimate_tokens(content), 'totalTokens': estimate_tokens(content),
                    'children': [], 'firstSentence': content.split('\n')[0][:200],
                    'firstParagraph': content.split('\n\n')[0][:500]}
        headings = _headings(tree); headings_text = ' '.join(h['title'] for h in headings)
        kt = classify_knowledge_type(path, headings_text, tree.get('firstParagraph', ''))
        ds = domain_stem(path)
        fe = {'path': path, 'size': rf.get('size', len(content)), 'tokens': tree['totalTokens'],
              'hash': hashlib.md5(content.encode()).hexdigest()[:12], 'language': lang,
              'knowledge_type': kt, 'domain_stem': ds, 'headings': headings, 'tree': tree}
        files.append(fe); total_tokens += tree['totalTokens']
    kt_dist = defaultdict(int)
    for f in files: kt_dist[f['knowledge_type']] += 1
    clusters = defaultdict(list)
    for f in files: clusters[f['domain_stem']].append(f['path'])
    return {'root': 'pallets/flask', 'totalFiles': len(files), 'totalTokens': total_tokens,
            'knowledgeTypes': dict(kt_dist),
            'featureClusters': {k: len(v) for k, v in clusters.items() if len(v) > 1}, 'files': files}

# ── Test cases (verified against actual repo) ──

TESTCASES = [
    {
        "query": "application factory create app configuration",
        "ground_truth": [
            "src/flask/app.py", "src/flask/sansio/app.py",
            "docs/patterns/appfactories.rst", "docs/tutorial/factory.rst",
            "docs/config.rst",
        ],
        "critical": ["src/flask/app.py"],
    },
    {
        "query": "blueprint registration modular views",
        "ground_truth": [
            "src/flask/blueprints.py", "src/flask/sansio/blueprints.py",
            "docs/blueprints.rst", "docs/tutorial/views.rst",
        ],
        "critical": ["src/flask/blueprints.py", "docs/blueprints.rst"],
    },
    {
        "query": "request response context",
        "ground_truth": [
            "src/flask/ctx.py", "src/flask/globals.py",
            "src/flask/wrappers.py", "docs/reqcontext.rst",
            "docs/appcontext.rst",
        ],
        "critical": ["src/flask/ctx.py", "src/flask/wrappers.py"],
    },
    {
        "query": "session cookie handling",
        "ground_truth": [
            "src/flask/sessions.py", "docs/api.rst",
        ],
        "critical": ["src/flask/sessions.py"],
    },
    {
        "query": "template rendering jinja",
        "ground_truth": [
            "src/flask/templating.py", "docs/templating.rst",
            "docs/patterns/templateinheritance.rst",
        ],
        "critical": ["src/flask/templating.py"],
    },
    {
        "query": "testing client fixtures pytest",
        "ground_truth": [
            "src/flask/testing.py", "docs/testing.rst",
            "docs/tutorial/tests.rst",
        ],
        "critical": ["src/flask/testing.py", "docs/testing.rst"],
    },
    {
        "query": "error handling abort exception",
        "ground_truth": [
            "src/flask/sansio/app.py", "src/flask/app.py",
            "docs/errorhandling.rst",
        ],
        "critical": ["docs/errorhandling.rst"],
    },
    {
        "query": "URL routing rules endpoint",
        "ground_truth": [
            "src/flask/sansio/scaffold.py", "src/flask/app.py",
            "docs/quickstart.rst",
        ],
        "critical": ["src/flask/sansio/scaffold.py"],
    },
    {
        "query": "JSON API response serialization",
        "ground_truth": [
            "src/flask/json/__init__.py", "src/flask/json/provider.py",
            "docs/api.rst",
        ],
        "critical": ["src/flask/json/provider.py"],
    },
    {
        "query": "signal blinker event hook",
        "ground_truth": [
            "src/flask/signals.py", "docs/signals.rst",
            "docs/api.rst",
        ],
        "critical": ["src/flask/signals.py"],
    },
]

DEPTH_WEIGHTS = {0: 1.0, 1: 0.8, 2: 0.5, 3: 0.3, 4: 0.15}

def run_eval(index, testcases, budgets):
    results = []
    for tc in testcases:
        query = tc['query']; gt = [g.lower() for g in tc['ground_truth']]
        critical = [c.lower() for c in tc.get('critical', [])]
        qt = tokenize_query(query); ql = query.lower()
        for budget in budgets:
            scored = []
            for f in index['files']:
                rel = score_file(f, qt, ql)
                if rel > 0:
                    scored.append({'path': f['path'], 'relevance': rel, 'tokens': f['tokens'],
                                   'tree': f.get('tree'), 'knowledge_type': f.get('knowledge_type', 'evidence')})
            scored.sort(key=lambda x: x['relevance'], reverse=True); scored = scored[:50]
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
            results.append({'query': query, 'budget': budget, 'recall': round(recall, 3),
                'weighted_recall': round(wrecall, 3), 'precision': round(precision, 3),
                'critical_hit_rate': round(crit_rate, 3), 'files_packed': len(packed),
                'tokens_used': tok_used, 'gt_found': gt_found, 'gt_missed': gt_missed,
                'crit_found': crit_found, 'crit_missed': crit_missed})
    return results

# ── Main ──
if __name__ == '__main__':
    with open('session/flask-files.json') as f: raw = json.load(f)
    index = build_index(raw)
    budgets = [2000, 4000, 8000, 16000, 32000]
    results = run_eval(index, TESTCASES, budgets)

    print(f"\n{'='*80}")
    print(f"DEPTH PACKING EVAL — pallets/flask (Python, RST docs)")
    print(f"{'='*80}")
    print(f"Files: {index['totalFiles']} | Tokens: {index['totalTokens']:,}")
    print(f"KT: {json.dumps(index['knowledgeTypes'])}")
    print()

    print(f"{'Budget':>8} | {'Recall':>7} | {'W.Recal':>7} | {'Precis.':>7} | {'CritHit':>7} | {'Files':>5} | {'TokUsed':>7}")
    print(f"{'-'*8}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*5}-+-{'-'*7}")
    for budget in budgets:
        br = [r for r in results if r['budget'] == budget]; n = len(br)
        avg = lambda key: sum(r[key] for r in br) / n
        print(f"{budget:>8} | {avg('recall'):>7.3f} | {avg('weighted_recall'):>7.3f} | "
              f"{avg('precision'):>7.3f} | {avg('critical_hit_rate'):>7.3f} | "
              f"{avg('files_packed'):>5.0f} | {avg('tokens_used'):>7.0f}")

    print(f"\n{'─'*80}")
    for tc in TESTCASES:
        query = tc['query']
        for budget in [4000, 16000]:
            r = next((r for r in results if r['query'] == query and r['budget'] == budget), None)
            if not r: continue
            status = '✓' if r['critical_hit_rate'] >= 0.8 and r['recall'] >= 0.5 else '△' if r['recall'] >= 0.3 else '✗'
            missed = ', '.join(Path(p).name for p in r['gt_missed'][:3])
            found_str = ', '.join(f"{Path(p).name}@{DEPTH_NAMES[d]}" for p, d in r['gt_found'][:5])
            print(f"  {status} @{budget:>5} \"{query[:45]}\" R={r['recall']:.2f} CH={r['critical_hit_rate']:.2f}")
            if missed: print(f"          Missed: {missed}")

    print(f"\n{'─'*80}")
    print("KNOWLEDGE TYPE DISTRIBUTION")
    for kt, count in sorted(index['knowledgeTypes'].items(), key=lambda x: -x[1]):
        label = KNOWLEDGE_TYPES.get(kt, {}).get('label', kt)
        print(f"  {label:>15}: {count:>3} files")
