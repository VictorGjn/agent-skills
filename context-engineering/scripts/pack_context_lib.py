"""
Shared library for depth packing: scoring, packing, knowledge type classification.

Used by: pack-context.py, eval-repo.py, index-github-repo.py
"""

import re
from pathlib import Path

# ── Constants ──

DEPTH_NAMES = {0: 'Full', 1: 'Detail', 2: 'Summary', 3: 'Headlines', 4: 'Mention'}
DEPTH_COST_RATIO = {0: 1.0, 1: 0.40, 2: 0.20, 3: 0.08, 4: 0.03}

# ── Knowledge Types (from modular-studio) ──
# Each file gets classified into one of these types, which affects depth priority.

KNOWLEDGE_TYPES = {
    'ground_truth': {
        'label': 'Ground Truth',
        'description': 'Canonical facts: source code, API docs, PRDs, schemas, configs',
        'depth_bonus': 0.1,  # boost relevance for ground truth
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
        'depth_bonus': -0.05,  # slight penalty: volatile info
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

# Patterns for classifying files by type
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
    """
    Classify a file into a knowledge type based on path, headings, and content.
    Returns the knowledge type key (e.g., 'ground_truth', 'framework').
    """
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

    # Return highest scoring type, default to 'evidence'
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        # Fallback heuristics
        ext = Path(path).suffix.lower()
        if ext in ('.ts', '.tsx', '.js', '.jsx', '.py', '.rs', '.go', '.vue', '.svelte'):
            return 'ground_truth'
        if ext in ('.md', '.mdx'):
            return 'evidence'  # generic markdown defaults to evidence
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
    """Split camelCase/PascalCase into lowercase parts.
    treeIndexer -> ['tree', 'indexer']
    SaveAgentModal -> ['save', 'agent', 'modal']
    mcpStore -> ['mcp', 'store']
    """
    # Insert space before uppercase letters that follow lowercase
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', name)
    # Split on spaces, hyphens, underscores
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

    # Knowledge type bonus
    kt = file_entry.get('knowledge_type', 'evidence')
    kt_bonus = KNOWLEDGE_TYPES.get(kt, {}).get('depth_bonus', 0)
    score += kt_bonus

    return min(1.0, max(0.0, score))


def relevance_to_depth(relevance: float) -> int:
    if relevance >= 0.6: return 0
    if relevance >= 0.4: return 1
    if relevance >= 0.25: return 2
    if relevance >= 0.15: return 3
    return 4


def estimate_at_depth(file_tokens: int, depth: int) -> int:
    ratio = DEPTH_COST_RATIO.get(depth, 0.03)
    return max(5, int(file_tokens * ratio))


# ── Packing ──

# Knowledge type priority for sorting at equal relevance
_KT_PRIORITY = {
    'ground_truth': 0,  # canonical sources first
    'framework': 1,
    'evidence': 2,
    'signal': 3,
    'hypothesis': 4,
    'artifact': 5,
}


def pack_context(scored_files: list, token_budget: int) -> list:
    """3-phase packing: assign depth, demote if over, promote if under.

    Sort key: (1) relevance desc, (2) knowledge_type priority, (3) smaller files first.
    This ensures ground_truth files are promoted over evidence at equal relevance,
    and smaller files get higher depth (better budget use).
    """
    def sort_key(sf):
        kt_prio = _KT_PRIORITY.get(sf.get('knowledge_type', 'evidence'), 3)
        return (-sf['relevance'], kt_prio, sf['tokens'])

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

    # Phase 2: Demote from bottom (least relevant / lowest priority first)
    if total > token_budget:
        for i in range(len(items) - 1, -1, -1):
            if total <= token_budget:
                break
            item = items[i]
            while item['depth'] < 4 and total > token_budget:
                old_tokens = item['tokens']
                item['depth'] += 1
                item['tokens'] = estimate_at_depth(item['file_tokens'], item['depth'])
                total -= (old_tokens - item['tokens'])
        while items and total > token_budget:
            removed = items.pop()
            total -= removed['tokens']

    # Phase 3: Promote top files if budget has room
    if total < token_budget * 0.92:
        for i in range(len(items)):
            if total >= token_budget * 0.95:
                break
            item = items[i]
            if item['depth'] > 0:
                new_tokens = estimate_at_depth(item['file_tokens'], item['depth'] - 1)
                delta = new_tokens - item['tokens']
                if total + delta <= token_budget:
                    item['depth'] -= 1
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
