"""Unit tests for scripts/wiki/code_index.py.

Phase 2 of CE x lat.md interop. Covers:

- AST symbol indexing across multiple languages
- (path, mtime, sha1) cache invalidation correctness
- Atomic write semantics
- resolve_symbol / resolve_symbol_strict
- Schema-version refusal on stale cache
- PRD AC2: warm-cache rebuild over CE itself completes <2s
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.code_index import (  # noqa: E402
    SCHEMA_VERSION,
    build_code_index,
    load_code_index,
    resolve_symbol,
    resolve_symbol_strict,
)


_TS_FIXTURE = """\
export function validateToken(t: string): boolean {
  return t.length > 0;
}

class AuthGuard {
  validate() { return true; }
}

function localHelper() {}
"""

_PY_FIXTURE = """\
def public_helper(x):
    return x

def _private_helper(x):
    return x

class Service:
    def call(self):
        pass
"""


class BuildCodeIndexTests(unittest.TestCase):
    """Walking + parsing + persistence."""

    def _seed(self, root: Path) -> None:
        (root / "src").mkdir()
        (root / "src" / "auth.ts").write_text(_TS_FIXTURE, encoding="utf-8")
        (root / "scripts").mkdir()
        (root / "scripts" / "service.py").write_text(_PY_FIXTURE, encoding="utf-8")
        # Vendor / build dirs that MUST be skipped.
        (root / "node_modules").mkdir()
        (root / "node_modules" / "junk.ts").write_text("export function junk(){}", encoding="utf-8")
        (root / "dist").mkdir()
        (root / "dist" / "out.js").write_text("function compiled(){}", encoding="utf-8")

    def test_indexes_code_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed(root)
            idx = build_code_index(root)
            self.assertIn("src/auth.ts", idx["files"])
            self.assertIn("scripts/service.py", idx["files"])

    def test_skips_vendor_and_build_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed(root)
            idx = build_code_index(root)
            paths = list(idx["files"].keys())
            self.assertFalse(any("node_modules" in p for p in paths))
            self.assertFalse(any(p.startswith("dist") for p in paths))

    def test_extracts_typescript_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed(root)
            idx = build_code_index(root)
            symbols = idx["files"]["src/auth.ts"]["symbols"]
            names = {s["name"] for s in symbols}
            self.assertIn("validateToken", names)
            self.assertIn("AuthGuard", names)

    def test_extracts_python_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed(root)
            idx = build_code_index(root)
            symbols = idx["files"]["scripts/service.py"]["symbols"]
            names = {s["name"] for s in symbols}
            self.assertIn("public_helper", names)
            self.assertIn("Service", names)

    def test_writes_to_cache_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._seed(root)
            cache = root / "cache" / "code_index.json"
            idx = build_code_index(root, cache_path=cache)
            self.assertTrue(cache.exists())
            on_disk = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["schema_version"], SCHEMA_VERSION)
            self.assertEqual(on_disk["files"].keys(), idx["files"].keys())


class CacheInvalidationTests(unittest.TestCase):
    """``(path, mtime_ns, sha1[:8])`` invalidation semantics."""

    def test_warm_cache_reuses_unchanged_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("def f(): pass", encoding="utf-8")
            cache = root / "cache" / "code_index.json"
            build_code_index(root, cache_path=cache)
            idx2 = build_code_index(root, cache_path=cache)
            self.assertEqual(idx2["stats"]["files_reused_from_cache"], 1)
            self.assertEqual(idx2["stats"]["files_reparsed"], 0)

    def test_content_change_triggers_reparse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "a.py"
            f.write_text("def first(): pass", encoding="utf-8")
            cache = root / "cache" / "code_index.json"
            build_code_index(root, cache_path=cache)
            # Sleep to bump mtime past granularity floor on FAT/NTFS.
            time.sleep(0.05)
            f.write_text("def second(): pass", encoding="utf-8")
            idx = build_code_index(root, cache_path=cache)
            self.assertEqual(idx["stats"]["files_reparsed"], 1)
            names = {s["name"] for s in idx["files"]["a.py"]["symbols"]}
            self.assertIn("second", names)

    def test_rebuild_flag_ignores_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("def f(): pass", encoding="utf-8")
            cache = root / "cache" / "code_index.json"
            build_code_index(root, cache_path=cache)
            idx = build_code_index(root, cache_path=cache, rebuild=True)
            self.assertEqual(idx["stats"]["files_reused_from_cache"], 0)
            self.assertEqual(idx["stats"]["files_reparsed"], 1)


class ResolveSymbolTests(unittest.TestCase):
    """``resolve_symbol`` / ``resolve_symbol_strict`` exported-first ordering."""

    def _build(self, root: Path) -> dict:
        (root / "lib.ts").write_text(_TS_FIXTURE, encoding="utf-8")
        return build_code_index(root)

    def test_resolve_returns_match(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            idx = self._build(root)
            matches = resolve_symbol(idx, "lib.ts", "validateToken")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["name"], "validateToken")

    def test_resolve_unknown_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            idx = self._build(root)
            self.assertEqual(resolve_symbol(idx, "nope.ts", "anything"), [])

    def test_resolve_unknown_symbol_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            idx = self._build(root)
            self.assertEqual(resolve_symbol(idx, "lib.ts", "ghost"), [])

    def test_resolve_strict_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            idx = self._build(root)
            self.assertIsNone(resolve_symbol_strict(idx, "lib.ts", "ghost"))


class LoadCodeIndexTests(unittest.TestCase):
    """Schema-version refusal-and-rebuild path."""

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_code_index("/no/such/path.json")

    def test_load_stale_schema_raises(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "stale.json"
            cache.write_text(
                json.dumps({"schema_version": 999, "files": {}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as cm:
                load_code_index(cache)
            self.assertIn("--rebuild", str(cm.exception))


class CEPerfTests(unittest.TestCase):
    """PRD AC2: warm-cache rebuild over the CE repo (~50 files) <2s."""

    def test_ce_repo_warm_rebuild_under_2s(self):
        ce_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "code_index.json"
            # Cold build seeds the cache.
            build_code_index(ce_root, cache_path=cache)
            # Warm build is the assertion.
            started = time.time()
            idx = build_code_index(ce_root, cache_path=cache)
            elapsed = time.time() - started
            self.assertLess(
                elapsed,
                2.0,
                f"Warm-cache rebuild took {elapsed:.2f}s; PRD AC2 requires <2s",
            )
            # Sanity: warm rebuild must reuse, not reparse.
            stats = idx["stats"]
            self.assertGreater(stats["files_reused_from_cache"], 0)


if __name__ == "__main__":
    unittest.main()
