"""
Shared library for depth packing: scoring, packing, knowledge type classification,
topic/section filtering, and confidence scoring.

Used by: pack_context.py, mcp_server.py, eval scripts
Sources: agent-skills (core), chatbot (anti-hallucination), modular-patchbay (budget allocator)
"""

import re
from pathlib import Path

# ── Constants ──

DEPTH_NAMES = {0: 'Full', 1: 'Detail', 2: 'Summary', 3: 'Headlines', 4: 'Mention'}
DEPTH_COST_RATIO = {0: 1.0, 1: 0.40, 2: 0.20, 3: 0.08, 4: 0.03}

# ── Stop words (FR + EN) — from chatbot ──

STOP_WORDS = {
    # English
    "the", "is", "are", "what", "how", "does", "do", "a", "an", "in", "on", "of",
    "for", "to", "and", "or", "it", "its", "this", "that", "with", "from", "by",
    "at", "as", "be", "was", "were", "been", "has", "have", "had", "can", "will",
    # French
    "les", "le", "la", "un", "une", "des", "du", "de", "en", "et", "ou", "que",
    "qui", "comment", "est", "sont", "ce", "cette", "nous", "on", "dans", "pour",
    "par", "sur", "avec", "pas", "plus", "tout", "fait", "faire",
}


# ── Knowledge Types (from modular-studio) ──

KNOWLEDGE_TYPES = {
    'ground_truth': {
        'label': 'Ground Truth',
        'description': 'Canonical facts: source code, API docs, PRDs, schemas, configs',
        'depth_bonus': 0.1,
        'color': 'red',
    },
    'framework': {
        'label': 'Framework',
        'description': 'Mental models, guidelines, playbooks, architecture docs, conventions',
        'depth_bonus': 0.05,
        'color': 'green',
    },
    'evidence': {
        'label': 'Evidence',
        'description': 'Research, benchmarks, logs, audit reports, case studies',
        'depth_bonus': 0.0,
        'color': 'blue',
    },
    'signal': {
        'label': 'Signal',
        'description': 'Feedback, meeting notes, reviews, user interviews, discussions',
        'depth_bonus': -0.05,
        'color': 'yellow',
    },
    'hypothesis': {
        'label': 'Hypothesis',
        'description': 'Proposals, plans, RFCs, unvalidated ideas, roadmaps',
        'depth_bonus': -0.05,
        'color': 'purple',
    },
    'artifact': {
        'label': 'Artifact',
        'description': 'Generated outputs, reports, exports, changelogs, release notes',
        'depth_bonus': -0.1,
        'color': 'gray',
    },
}

# ── Knowledge Type Classifier ──

_KT_PATTERNS = {
    'ground_truth': {
        'path_patterns': [
            r'\.ts$', r'\.tsx$', r'\.js$', r'\.jsx$', r'\.py$', r'\.rs$', r'\.go$',
            r'\.vue$', r'\.svelte$',
            r'schema', r'types\.', r'interface', r'\.proto$',
            r'config\.(ts|js|json|yaml|yml)$', r'\.env',
            r'package\.json$', r'tsconfig', r'Dockerfile',
            r'PRD', r'SPEC', r'API[-_]', r'SCHEMA',
        ],
        'heading_patterns': [
            r'api\s*(reference|doc)', r'specification', r'schema',
            r'type\s*definition', r'interface', r'endpoint',
        ],
    },
    'framework': {
        'path_patterns': [
            r'GUIDE', r'GUIDELINE', r'CONVENTION', r'ARCHITECTURE',
            r'DESIGN[-_]', r'PATTERN', r'PLAYBOOK', r'PROCESS',
            r'METHODOLOGY', r'PRINCIPLES', r'RULES',
            r'\.md$.*rules', r'CONTRIBUTING',
        ],
        'heading_patterns': [
            r'guideline', r'convention', r'architecture', r'design\s*system',
            r'pattern', r'principle', r'methodology', r'workflow',
            r'best\s*practice', r'how\s*to', r'playbook',
        ],
    },
    'evidence': {
        'path_patterns': [
            r'AUDIT', r'BENCHMARK', r'RESEARCH', r'ANALYSIS',
            r'REPORT', r'STUDY', r'EVAL', r'METRIC',
            r'research', r'raw/', r'scrape', r'screenshot',
            r'battlecard', r'FEATURES\.md',
        ],
        'heading_patterns': [
            r'benchmark', r'result', r'finding', r'metric',
            r'comparison', r'competitive', r'analysis',
            r'scraped', r'source', r'evidence',
        ],
    },
    'signal': {
        'path_patterns': [
            r'FEEDBACK', r'REVIEW', r'RETRO', r'INTERVIEW',
            r'MEETING', r'NOTES', r'STANDUP', r'DISCUSSION',
            r'slack', r'message', r'transcript',
        ],
        'heading_patterns': [
            r'feedback', r'review', r'retrospective', r'interview',
            r'meeting\s*note', r'decision', r'action\s*item',
        ],
    },
    'hypothesis': {
        'path_patterns': [
            r'PLAN', r'PROPOSAL', r'RFC', r'ROADMAP',
            r'VISION', r'IDEA', r'DRAFT', r'TODO',
            r'SPRINT', r'BACKLOG', r'REMAINING',
        ],
        'heading_patterns': [
            r'proposal', r'rfc', r'roadmap', r'plan',
            r'next\s*step', r'future', r'goal', r'objective',
            r'sprint', r'milestone',
        ],
    },
    'artifact': {
        'path_patterns': [
            r'CHANGELOG', r'RELEASE', r'GENERATED',
            r'dist/', r'build/', r'output/',
            r'README', r'LICENSE', r'CONTRIBUTING',
            r'test[-_]result', r'\.(csv|json)$',
        ],
        'heading_patterns': [
            r'changelog', r'release\s*note', r'version',
            r'generated', r'output', r'export',
        ],
    },
}


def classify_knowledge_type(path: str, headings_text: str = '', content_preview: str = '') -> str:
    """Classify a file into a knowledge type based on path, headings, and content."""
    path_lower = path.lower()
    searchable = (headings_text + ' ' + content_preview).lower()

    scores = {}
    for kt, patterns in _KT_PATTERNS.items():
        score = 0
        for pp in patterns['path_patterns']:
            if re.search(pp, path_lower, re.IGNORECASE):
                score += 2
        for hp in patterns['heading_patterns']:
            if re.search(hp, searchable, re.IGNORECASE):
                score += 1
        scores[kt] = score

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        ext = Path(path).suffix.lower()
        if ext in ('.ts', '.tsx', '.js', '.jsx', '.py', '.rs', '.go', '.vue', '.svelte'):
            return 'ground_truth'
        if ext in ('.md', '.mdx'):
            return 'evidence'
        return 'artifact'

    return best


# ── Stemmer ──

def stem(word: str) -> str:
    """Minimal stemmer: strip common suffixes for fuzzy matching."""
    w = word.lower()
    for suffix in ['alization', 'ization', 'isation', 'ation', 'ising', 'izing',
                    'ment', 'ness', 'tion', 'sion', 'able', 'ible',
                    'ing', 'ous', 'ive', 'ful', 'less', 'ial', 'al',
                    'eur', 'ier', 'ère', 'ion', 'er', 'ed', 'ly', 'es', 's']:
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[:-len(suffix)]
    return w


def split_camel(name: str) -> list:
    """Split camelCase/PascalCase into lowercase parts."""
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', name)
    parts = re.split(r'[\s\-_]+', s.lower())
    return [p for p in parts if len(p) >= 2]


# ── Query Tokenizer ──

def tokenize_query(query: str) -> list:
    """Split query into normalized tokens for matching."""
    q = query.lower()
    q = re.sub(r'[^a-z0-9àâäéèêëïîôùûüÿçœæ\s\-_/]', ' ', q)
    words = [w for w in q.split() if len(w) >= 2]
    return words


def _collect_all_headings(tree: dict) -> str:
    """Recursively collect ALL heading titles from tree."""
    parts = []
    if tree.get('title'):
        parts.append(tree['title'].lower())
    for child in tree.get('children', []):
        parts.append(_collect_all_headings(child))
    return ' '.join(parts)


# ── Topic Filtering (from chatbot) ──

def extract_topic_terms(query: str) -> list:
    """Extract meaningful topic terms from a query, removing stop words."""
    words = re.sub(r"[^a-z0-9\u00e0-\u00ff\s\-]", " ", query.lower()).split()
    return [w for w in words if len(w) >= 3 and w not in STOP_WORDS]


def topic_overlap(text: str, terms: list) -> float:
    """Fraction of topic terms found in text. 0.0 to 1.0."""
    if not terms:
        return 1.0
    lower = (text or "").lower()
    return sum(1 for t in terms if t in lower) / len(terms)


def filter_by_topic(scored_files: list, query: str,
                    min_topic_overlap: float = 0.25,
                    min_score_bypass: float = 0.5) -> list:
    """Remove results whose content doesn't overlap with query terms.

    From chatbot anti-hallucination: prevents off-topic context from reaching the LLM.

    A file passes if:
    - topic_overlap >= min_topic_overlap, OR
    - relevance score >= min_score_bypass (high confidence overrides), OR
    - knowledge_type is ground_truth and score >= 0.35
    """
    terms = extract_topic_terms(query)
    if not terms:
        return scored_files

    filtered = []
    for f in scored_files:
        search_text = ' '.join(filter(None, [
            _collect_all_headings(f.get('tree', {})) if f.get('tree') else '',
            f.get('path', ''),
        ]))
        t_score = topic_overlap(search_text, terms)
        f['topic_score'] = t_score

        if (t_score >= min_topic_overlap
                or f.get('relevance', 0) >= min_score_bypass
                or (f.get('knowledge_type') == 'ground_truth' and f.get('relevance', 0) >= 0.35)):
            filtered.append(f)

    # Re-sort by combined score: 60% relevance + 40% topic overlap
    filtered.sort(key=lambda x: -(x.get('relevance', 0) * 0.6 + x.get('topic_score', 0) * 0.4))
    return filtered


# ── Confidence Scoring (from chatbot) ──

def confidence_check(scored_files: list, low_threshold: float = 0.25) -> dict:
    """Assess confidence of search results.

    Returns a dict with:
    - is_low: True if average relevance is below threshold
    - avg_score: average relevance across results
    - signal: string to inject into LLM prompt when confidence is low

    From chatbot anti-hallucination: when results are weak, tell the LLM to say "I don't know".
    """
    if not scored_files:
        return {'is_low': True, 'avg_score': 0, 'signal': 'No matching files found. Say you don\'t know.'}

    avg = sum(f.get('relevance', 0) for f in scored_files) / len(scored_files)
    is_low = avg < low_threshold

    signal = ''
    if is_low:
        signal = (
            f'Low confidence (avg={avg:.2f}). The retrieved context may not fully answer the query. '
            'If you cannot find a clear answer in the provided context, say so honestly '
            'and suggest related topics the user could explore.'
        )

    return {'is_low': is_low, 'avg_score': avg, 'signal': signal}


# ── Scoring ──

def score_file(file_entry: dict, query_tokens: list, query_lower: str) -> float:
    """Score a file's relevance to the query. 0.0 to 1.0."""
    score = 0.0
    path_lower = file_entry['path'].lower()
    matched_tokens = set()

    query_stems = {qt: stem(qt) for qt in query_tokens}

    # Path matching (split camelCase in path segments)
    raw_parts = re.split(r'[/\-_.]', path_lower)
    path_parts = []
    for rp in raw_parts:
        path_parts.extend(split_camel(rp) if any(c.isupper() for c in rp) else [rp])
    path_parts = [p for p in path_parts if len(p) >= 2]
    path_stems = [stem(p) for p in path_parts if len(p) >= 3]
    for qt in query_tokens:
        qs = query_stems[qt]
        if qt in path_parts:
            score += 0.3
            matched_tokens.add(qt)
        elif qs in path_stems:
            score += 0.25
            matched_tokens.add(qt)
        elif qt in path_lower:
            score += 0.15
            matched_tokens.add(qt)

    # Heading matching
    tree = file_entry.get('tree', {})
    all_headings = _collect_all_headings(tree) if tree else ''
    root_summary = ''
    if tree:
        root_summary = (tree.get('firstSentence', '') + ' ' + tree.get('firstParagraph', '')).lower()
    searchable = all_headings + ' ' + root_summary
    searchable_stems = ' '.join(stem(w) for w in searchable.split() if len(w) >= 3)

    for qt in query_tokens:
        qs = query_stems[qt]
        if qt in searchable:
            score += 0.2
            matched_tokens.add(qt)
        elif qs in searchable_stems:
            score += 0.15
            matched_tokens.add(qt)

    # Filename match (with camelCase splitting)
    filename = Path(file_entry['path']).stem
    filename_parts = split_camel(filename)
    filename_stems = [stem(p) for p in filename_parts if len(p) >= 3]
    for qt in query_tokens:
        qs = query_stems[qt]
        if qt in filename_parts:
            score += 0.2
            matched_tokens.add(qt)
        elif qs in filename_stems:
            score += 0.15
            matched_tokens.add(qt)
        elif qt in filename:
            score += 0.1
            matched_tokens.add(qt)

    # Directory proximity
    dir_path = str(Path(file_entry['path']).parent).lower()
    for qt in query_tokens:
        if qt in dir_path:
            score += 0.1
            matched_tokens.add(qt)

    # Co-occurrence bonus
    if len(query_tokens) > 1 and len(matched_tokens) >= 2:
        coverage = len(matched_tokens) / len(query_tokens)
        score += coverage * 0.3

    return min(1.0, max(0.0, score))


def relevance_to_depth(relevance: float) -> int:
    """Binary depth: Full (0) when relevant enough to include in body,
    else pointer (4 = Mention, a one-line reference). The intermediate
    Detail/Summary/Headlines bands were removed — they never moved a
    downstream answer vs. full-or-pointer (see references/eval-results.md)."""
    return 0 if relevance >= 0.4 else 4


def estimate_at_depth(file_tokens: int, depth: int) -> int:
    ratio = DEPTH_COST_RATIO.get(depth, 0.03)
    return max(5, int(file_tokens * ratio))


# ── Packing ──


def pack_context(scored_files: list, token_budget: int) -> list:
    """3-phase packing: assign depth, demote if over, promote if under.

    Sort key: (1) relevance desc, (2) smaller files first. Knowledge-type
    priority was removed — relevance + size alone decide ordering.
    """
    def sort_key(sf):
        return (-sf['relevance'], sf['tokens'])

    items = []
    for sf in sorted(scored_files, key=sort_key):
        depth = relevance_to_depth(sf['relevance'])
        tokens = estimate_at_depth(sf['tokens'], depth)
        items.append({
            'path': sf['path'],
            'relevance': sf['relevance'],
            'depth': depth,
            'tokens': tokens,
            'file_tokens': sf['tokens'],
            'tree': sf.get('tree'),
            'knowledge_type': sf.get('knowledge_type', 'evidence'),
        })

    total = sum(it['tokens'] for it in items)

    # Phase 2: Demote from bottom — Full (0) → pointer (4) in one step, then drop
    if total > token_budget:
        for i in range(len(items) - 1, -1, -1):
            if total <= token_budget:
                break
            item = items[i]
            if item['depth'] == 0:
                old_tokens = item['tokens']
                item['depth'] = 4
                item['tokens'] = estimate_at_depth(item['file_tokens'], 4)
                total -= (old_tokens - item['tokens'])
        while items and total > token_budget:
            removed = items.pop()
            total -= removed['tokens']

    # Phase 3: Promote top pointers (4) back to Full (0) if budget has room
    if total < token_budget * 0.92:
        for i in range(len(items)):
            if total >= token_budget * 0.95:
                break
            item = items[i]
            if item['depth'] == 4:
                new_tokens = estimate_at_depth(item['file_tokens'], 0)
                delta = new_tokens - item['tokens']
                if total + delta <= token_budget:
                    item['depth'] = 0
                    item['tokens'] = new_tokens
                    total += delta

    return items


# ── Token estimation ──

def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    code_blocks = re.findall(r'```[\s\S]*?```', text)
    code_chars = sum(len(b) for b in code_blocks)
    prose_chars = len(text) - code_chars
    return max(1, int(prose_chars / 4 + code_chars / 2.5))


# ── Rendering (single source of truth — CLI, MCP, and prod engine all import this) ──

def _walk(node):
    yield node
    for c in node.get('children', []):
        yield from _walk(c)


def render_at_depth(tree, depth: int, file_path: str) -> str:
    """Render a packed file. Two levels: pointer (depth 4 / no tree) or full body."""
    if not tree:
        return f"- `{file_path}`"
    if depth == 4:
        return f"- `{file_path}` ({tree.get('totalTokens', 0)} tok)"
    lines = [f"### {file_path}"]
    for node in _walk(tree):
        if node.get('depth', 0) > 0 and node.get('title'):
            lines.append(f"{'#' * min(node['depth'] + 2, 6)} {node['title']}")
        if node.get('text'):
            lines.append(node['text'])
            lines.append('')
    return '\n'.join(lines)
