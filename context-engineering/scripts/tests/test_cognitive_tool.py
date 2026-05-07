"""Tests for the --cognitive-tool flag wiring in pack_context.py.

Covers:
- Loader correctness (placeholder substitution, prefix/suffix split)
- Path-traversal hardening (security — pack is MCP-exposed)
- Error paths (missing template, missing placeholder, multi-placeholder, oversize)
- HTML comment stripping (multiple leading blocks)
- Bundled fix.md / explain.md remain loadable

Run: python3 -m pytest scripts/tests/test_cognitive_tool.py -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import pack_context


class LoaderHappyPathTests(unittest.TestCase):
    def test_bundled_fix_loads(self):
        prefix, suffix = pack_context._load_cognitive_tool('fix', 'why is auth failing')
        self.assertIn('why is auth failing', prefix)
        self.assertIn('verification process', suffix.lower())
        self.assertNotIn('{query}', prefix)
        self.assertNotIn('{packed_context}', prefix)
        self.assertNotIn('{packed_context}', suffix)

    def test_bundled_explain_loads(self):
        prefix, suffix = pack_context._load_cognitive_tool('explain', 'how does the indexer work')
        self.assertIn('how does the indexer work', prefix)
        self.assertIn('analysis points', suffix.lower())

    def test_query_with_braces_does_not_break_loader(self):
        # The loader uses .replace(), not .format() — a query containing
        # `{packed_context}` or `{query}` should be left as literal text.
        weird = 'what does {packed_context} mean'
        prefix, suffix = pack_context._load_cognitive_tool('explain', weird)
        self.assertIn(weird, prefix)


class CustomTemplateTests(unittest.TestCase):
    """Drop a temporary template into cognitive_tools/, then patch the dir."""

    def setUp(self):
        self.tmp = Path(__file__).parent / '_tmp_cognitive_tools'
        self.tmp.mkdir(exist_ok=True)
        self._patch = mock.patch.object(pack_context, 'COGNITIVE_TOOLS_DIR', self.tmp)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        for p in self.tmp.glob('*'):
            p.unlink()
        self.tmp.rmdir()

    def _write(self, name: str, body: str) -> None:
        (self.tmp / f'{name}.md').write_text(body, encoding='utf-8')

    def test_strips_multiple_leading_html_comments(self):
        self._write('multi', '<!-- one -->\n<!-- two -->\nQ: {query}\n{packed_context}\nDone.\n')
        prefix, suffix = pack_context._load_cognitive_tool('multi', 'X')
        self.assertNotIn('<!--', prefix)
        self.assertIn('Q: X', prefix)
        self.assertIn('Done', suffix)

    def test_missing_packed_context_placeholder_raises(self):
        self._write('bad', 'Q: {query}\nNo placeholder here.\n')
        with self.assertRaises(ValueError) as ctx:
            pack_context._load_cognitive_tool('bad', 'X')
        self.assertIn('packed_context', str(ctx.exception))

    def test_multiple_packed_context_placeholders_raises(self):
        self._write('twin', '{packed_context}\n--\n{packed_context}\n')
        with self.assertRaises(ValueError) as ctx:
            pack_context._load_cognitive_tool('twin', 'X')
        self.assertIn('multiple', str(ctx.exception).lower())

    def test_oversize_template_raises(self):
        big = 'a' * (pack_context._COGNITIVE_TOOL_MAX_BYTES + 1)
        self._write('huge', f'{{packed_context}}\n{big}')
        with self.assertRaises(ValueError) as ctx:
            pack_context._load_cognitive_tool('huge', 'X')
        self.assertIn('exceeds', str(ctx.exception))


class SecurityTests(unittest.TestCase):
    """Path-traversal must not let --cognitive-tool reach files outside cognitive_tools/.

    Critical because pack is exposed via MCP — an LLM client steered into a
    malicious tool argument must not be able to read arbitrary files.
    """

    def test_dotdot_traversal_rejected_by_name_regex(self):
        with self.assertRaises(ValueError) as ctx:
            pack_context._load_cognitive_tool('../SKILL', 'X')
        self.assertIn('A-Za-z0-9_-', str(ctx.exception))

    def test_forward_slash_in_name_rejected(self):
        with self.assertRaises(ValueError):
            pack_context._load_cognitive_tool('subdir/file', 'X')

    def test_backslash_in_name_rejected(self):
        with self.assertRaises(ValueError):
            pack_context._load_cognitive_tool('subdir\\file', 'X')

    def test_absolute_path_rejected(self):
        with self.assertRaises(ValueError):
            pack_context._load_cognitive_tool('C:/Windows/System32/drivers/etc/hosts', 'X')

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            pack_context._load_cognitive_tool('', 'X')

    def test_leading_dot_rejected(self):
        with self.assertRaises(ValueError):
            pack_context._load_cognitive_tool('.hidden', 'X')


class MissingTemplateTests(unittest.TestCase):
    def test_unknown_name_raises_filenotfound_with_available_list(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            pack_context._load_cognitive_tool('does_not_exist', 'X')
        msg = str(ctx.exception)
        self.assertIn('Available', msg)
        # The two bundled templates should appear in the listing
        self.assertIn('fix', msg)
        self.assertIn('explain', msg)


if __name__ == '__main__':
    unittest.main()
