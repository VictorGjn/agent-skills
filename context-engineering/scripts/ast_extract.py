"""
AST Symbol Extractor — tree-sitter based, 14 languages.

Replaces regex extraction with proper AST parsing.
Extracts: functions, classes, interfaces, types, structs, traits, methods, imports.
Detects exports/visibility per language convention.

Supported: Python, JavaScript, TypeScript, TSX, Rust, Go, Ruby, Java,
           C, C++, C#, Kotlin, Scala, PHP.

Usage:
  from ast_extract import extract_symbols, SUPPORTED_LANGUAGES

  symbols = extract_symbols("typescript", source_code)
  # [{'name': 'resolveEntryPoints', 'kind': 'function', 'line': 10,
  #   'end_line': 16, 'exported': True, 'signature': 'resolveEntryPoints(query, graph)',
  #   'docstring': '...'}]

Install: pip install tree-sitter==0.21.3 tree-sitter-languages==1.10.2
"""

import re
from pathlib import Path

try:
    from tree_sitter_languages import get_parser
    HAS_TREESITTER = True
except ImportError:
    try:
        # tree_sitter_language_pack is the maintained successor (Python 3.12+)
        from tree_sitter_language_pack import get_parser
        HAS_TREESITTER = True
    except ImportError:
        HAS_TREESITTER = False

# ── Language mapping ──

EXT_TO_LANG = {
    '.py': 'python',
    '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'tsx',
    '.jsx': 'javascript',
    '.rs': 'rust',
    '.go': 'go',
    '.rb': 'ruby',
    '.java': 'java',
    '.c': 'c', '.h': 'c',
    '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp',
    '.cs': 'c_sharp',
    '.kt': 'kotlin', '.kts': 'kotlin',
    '.scala': 'scala', '.sc': 'scala',
    '.php': 'php',
}

SUPPORTED_LANGUAGES = list(set(EXT_TO_LANG.values()))


def lang_from_path(path: str) -> str:
    """Detect language from file extension."""
    ext = Path(path).suffix.lower()
    return EXT_TO_LANG.get(ext, '')


# ── Docstring extraction ──

def _get_docstring(node) -> str:
    """Extract docstring from the first child of a definition node."""
    body = node.child_by_field_name('body')
    if not body:
        return ''
    for child in body.children:
        if child.type == 'expression_statement':
            for sub in child.children:
                if sub.type == 'string':
                    text = sub.text.decode().strip("'\"")
                    if text.startswith('"""') or text.startswith("'''"):
                        text = text[3:-3] if len(text) > 6 else text[3:]
                    return text.strip()[:200]
        elif child.type == 'comment':
            text = child.text.decode().lstrip('/ *').strip()
            return text[:200]
        elif child.type not in ('comment', '{', '}', 'newline', '\n'):
            break
    return ''


def _get_comment_above(node) -> str:
    """Get JSDoc / block comment immediately above a node."""
    prev = node.prev_named_sibling
    if prev and prev.type == 'comment':
        text = prev.text.decode()
        text = re.sub(r'^/\*\*?\s*|\s*\*/$', '', text)
        text = re.sub(r'\n\s*\*\s?', ' ', text)
        return text.strip()[:200]
    return ''


# ── Core extraction ──

def extract_symbols(lang: str, source: bytes | str, path: str = '') -> list:
    """
    Extract symbols from source code using tree-sitter AST.

    Args:
        lang: Language name (e.g., 'typescript', 'python', 'rust')
        source: Source code as bytes or str
        path: File path (for context, optional)

    Returns:
        List of symbol dicts: {name, kind, line, end_line, exported, signature, docstring}
    """
    if not HAS_TREESITTER:
        return _fallback_regex(lang, source, path)

    if isinstance(source, str):
        source = source.encode('utf-8')

    try:
        parser = get_parser(lang)
    except Exception:
        return _fallback_regex(lang, source.decode('utf-8', errors='replace'), path)

    tree = parser.parse(source)
    symbols = []

    def _text(node):
        return node.text.decode('utf-8', errors='replace') if node else ''

    def _is_exported_ts(node):
        """Check if a TS/JS node is exported (parent is export_statement)."""
        p = node.parent
        return p is not None and p.type == 'export_statement'

    def visit(node):
        t = node.type

        # ── TypeScript / JavaScript ──
        if lang in ('typescript', 'javascript', 'tsx'):
            if t == 'function_declaration':
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                doc = _get_comment_above(node.parent if _is_exported_ts(node) else node)
                symbols.append({
                    'name': name, 'kind': 'function',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': _is_exported_ts(node), 'signature': f'{name}{params}',
                    'docstring': doc,
                })
            elif t == 'class_declaration':
                name = _text(node.child_by_field_name('name'))
                doc = _get_comment_above(node.parent if _is_exported_ts(node) else node)
                symbols.append({
                    'name': name, 'kind': 'class',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': _is_exported_ts(node), 'signature': f'class {name}',
                    'docstring': doc,
                })
            elif t == 'interface_declaration':
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'interface',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': _is_exported_ts(node), 'signature': f'interface {name}',
                    'docstring': _get_comment_above(node.parent if _is_exported_ts(node) else node),
                })
            elif t == 'type_alias_declaration':
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'type',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': _is_exported_ts(node), 'signature': f'type {name}',
                    'docstring': '',
                })
            elif t == 'method_definition':
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                symbols.append({
                    'name': name, 'kind': 'method',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'{name}{params}',
                    'docstring': _get_comment_above(node),
                })
            elif t in ('lexical_declaration', 'variable_declaration'):
                if _is_exported_ts(node):
                    for decl in node.children:
                        if decl.type == 'variable_declarator':
                            name = _text(decl.child_by_field_name('name'))
                            symbols.append({
                                'name': name, 'kind': 'const',
                                'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                                'exported': True, 'signature': f'const {name}',
                                'docstring': '',
                            })
            elif t == 'import_statement':
                symbols.append({
                    'name': _text(node)[:100], 'kind': 'import',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': False, 'signature': '', 'docstring': '',
                })

        # ── Python ──
        elif lang == 'python':
            if t == 'function_definition':
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                is_method = node.parent and node.parent.type == 'block' and \
                            node.parent.parent and node.parent.parent.type == 'class_definition'
                symbols.append({
                    'name': name, 'kind': 'method' if is_method else 'function',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': not name.startswith('_'),
                    'signature': f'def {name}{params}',
                    'docstring': _get_docstring(node),
                })
            elif t == 'class_definition':
                name = _text(node.child_by_field_name('name'))
                superclasses = node.child_by_field_name('superclasses')
                sig = f'class {name}({_text(superclasses)})' if superclasses else f'class {name}'
                symbols.append({
                    'name': name, 'kind': 'class',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': not name.startswith('_'), 'signature': sig,
                    'docstring': _get_docstring(node),
                })
            elif t in ('import_statement', 'import_from_statement'):
                symbols.append({
                    'name': _text(node)[:100], 'kind': 'import',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': False, 'signature': '', 'docstring': '',
                })

        # ── Rust ──
        elif lang == 'rust':
            if t == 'function_item':
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                vis = any(c.type == 'visibility_modifier' for c in node.children)
                symbols.append({
                    'name': name, 'kind': 'function',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': vis, 'signature': f'fn {name}{params}',
                    'docstring': _get_comment_above(node),
                })
            elif t == 'struct_item':
                name = _text(node.child_by_field_name('name'))
                vis = any(c.type == 'visibility_modifier' for c in node.children)
                symbols.append({
                    'name': name, 'kind': 'struct',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': vis, 'signature': f'struct {name}',
                    'docstring': _get_comment_above(node),
                })
            elif t == 'enum_item':
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'enum',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'enum {name}',
                    'docstring': '',
                })
            elif t == 'trait_item':
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'trait',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'trait {name}',
                    'docstring': _get_comment_above(node),
                })
            elif t == 'impl_item':
                tp = node.child_by_field_name('type')
                if tp:
                    symbols.append({
                        'name': _text(tp), 'kind': 'impl',
                        'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                        'exported': True, 'signature': f'impl {_text(tp)}',
                        'docstring': '',
                    })
            elif t == 'use_declaration':
                symbols.append({
                    'name': _text(node)[:100], 'kind': 'import',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': False, 'signature': '', 'docstring': '',
                })

        # ── Go ──
        elif lang == 'go':
            if t == 'function_declaration':
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                symbols.append({
                    'name': name, 'kind': 'function',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': name[0].isupper() if name else False,
                    'signature': f'func {name}{params}',
                    'docstring': _get_comment_above(node),
                })
            elif t == 'method_declaration':
                name = _text(node.child_by_field_name('name'))
                recv = _text(node.child_by_field_name('receiver'))
                params = _text(node.child_by_field_name('parameters'))
                symbols.append({
                    'name': name, 'kind': 'method',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': name[0].isupper() if name else False,
                    'signature': f'func {recv} {name}{params}',
                    'docstring': _get_comment_above(node),
                })
            elif t == 'type_declaration':
                for c in node.children:
                    if c.type == 'type_spec':
                        name = _text(c.child_by_field_name('name'))
                        type_node = c.child_by_field_name('type')
                        kind = 'interface' if type_node and type_node.type == 'interface_type' else 'type'
                        symbols.append({
                            'name': name, 'kind': kind,
                            'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                            'exported': name[0].isupper() if name else False,
                            'signature': f'type {name}',
                            'docstring': _get_comment_above(node),
                        })

        # ── Java / Kotlin / C# / Scala ──
        elif lang in ('java', 'kotlin', 'c_sharp', 'scala'):
            if t in ('method_declaration', 'function_declaration', 'function_definition'):
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                vis = _has_modifier(node, ('public', 'internal', 'open'))
                symbols.append({
                    'name': name, 'kind': 'function',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': vis, 'signature': f'{name}{params}',
                    'docstring': _get_comment_above(node),
                })
            elif t in ('class_declaration', 'object_declaration'):
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'class',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'class {name}',
                    'docstring': _get_comment_above(node),
                })
            elif t == 'interface_declaration':
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'interface',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'interface {name}',
                    'docstring': '',
                })

        # ── C / C++ ──
        elif lang in ('c', 'cpp'):
            if t == 'function_definition':
                decl = node.child_by_field_name('declarator')
                name = ''
                if decl:
                    fn_decl = decl if decl.type == 'function_declarator' else None
                    if fn_decl:
                        name_node = fn_decl.child_by_field_name('declarator')
                        name = _text(name_node)
                        params = _text(fn_decl.child_by_field_name('parameters'))
                    else:
                        name = _text(decl)
                        params = ''
                if name:
                    symbols.append({
                        'name': name, 'kind': 'function',
                        'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                        'exported': not name.startswith('_'),
                        'signature': f'{name}{params}' if params else name,
                        'docstring': _get_comment_above(node),
                    })
            elif t == 'struct_specifier' and node.child_by_field_name('name'):
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'struct',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'struct {name}',
                    'docstring': '',
                })
            if lang == 'cpp' and t == 'class_specifier':
                name = _text(node.child_by_field_name('name'))
                if name:
                    symbols.append({
                        'name': name, 'kind': 'class',
                        'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                        'exported': True, 'signature': f'class {name}',
                        'docstring': '',
                    })

        # ── Ruby ──
        elif lang == 'ruby':
            if t == 'method':
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                symbols.append({
                    'name': name, 'kind': 'function',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': not name.startswith('_'),
                    'signature': f'def {name}{params}',
                    'docstring': '',
                })
            elif t == 'class':
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'class',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'class {name}',
                    'docstring': '',
                })
            elif t == 'module':
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'module',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'module {name}',
                    'docstring': '',
                })

        # ── PHP ──
        elif lang == 'php':
            if t == 'function_definition':
                name = _text(node.child_by_field_name('name'))
                params = _text(node.child_by_field_name('parameters'))
                symbols.append({
                    'name': name, 'kind': 'function',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'function {name}{params}',
                    'docstring': _get_comment_above(node),
                })
            elif t in ('class_declaration',):
                name = _text(node.child_by_field_name('name'))
                symbols.append({
                    'name': name, 'kind': 'class',
                    'line': node.start_point[0] + 1, 'end_line': node.end_point[0] + 1,
                    'exported': True, 'signature': f'class {name}',
                    'docstring': '',
                })

        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return symbols


def _has_modifier(node, modifiers):
    """Check if a Java/Kotlin/C# node has a visibility modifier."""
    for child in node.children:
        if child.type == 'modifiers':
            text = child.text.decode()
            return any(m in text for m in modifiers)
    return True  # default to exported if no modifier specified


# ── Fallback regex (when tree-sitter unavailable) ──

def _fallback_regex(lang: str, source: str, path: str) -> list:
    """Basic regex fallback for environments without tree-sitter."""
    symbols = []
    lines = source.split('\n')

    if lang in ('typescript', 'javascript', 'tsx'):
        export_re = re.compile(
            r'^export\s+(?:default\s+)?(?:async\s+)?(?:declare\s+)?'
            r'(?:(?:abstract\s+)?class|interface|type|enum|function|(?:const|let|var))\s+(\w+)', re.M)
        for m in export_re.finditer(source):
            line = source[:m.start()].count('\n') + 1
            symbols.append({'name': m.group(1), 'kind': 'export', 'line': line,
                            'end_line': line, 'exported': True, 'signature': m.group(1), 'docstring': ''})
    elif lang == 'python':
        py_re = re.compile(r'^(?:class|(?:async\s+)?def)\s+(\w+)', re.M)
        for m in py_re.finditer(source):
            line = source[:m.start()].count('\n') + 1
            kind = 'class' if m.group(0).startswith('class') else 'function'
            symbols.append({'name': m.group(1), 'kind': kind, 'line': line,
                            'end_line': line, 'exported': not m.group(1).startswith('_'),
                            'signature': m.group(1), 'docstring': ''})
    elif lang == 'rust':
        rust_re = re.compile(r'^(?:pub\s+)?(?:fn|struct|enum|trait|impl)\s+(\w+)', re.M)
        for m in rust_re.finditer(source):
            line = source[:m.start()].count('\n') + 1
            symbols.append({'name': m.group(1), 'kind': 'export', 'line': line,
                            'end_line': line, 'exported': 'pub' in m.group(0),
                            'signature': m.group(1), 'docstring': ''})

    return symbols
