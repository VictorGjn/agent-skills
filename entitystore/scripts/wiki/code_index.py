"""code_index.py — symbol index for [[src/file#symbol]] resolution.

Phase 2 of CE x lat.md interop. Wraps ``ast_extract.extract_symbols`` and
walks a repository to produce a path -> {symbol -> {line, end_line, kind,
exported}} index, persisted to ``cache/code_index.json`` with
``(path, mtime, sha1[:8])`` cache invalidation. Used by:

- ``audit.py`` rule ``find_broken_refs`` (Phase 2) — validates that every
  ``[[src/file.ts#symbol]]`` ref in the wiki resolves to a real symbol.
- ``mcp_server.py`` Phase 4 ``lat.locate`` / ``lat.section`` / ``lat.search``
  tools — symbol-level retrieval over the indexed codebase.

Performance target: rebuild over ~50 files <2s warm-cache (PRD AC2). The
cache only re-parses files whose ``(mtime, sha1[:8])`` changed.

CLI::

    python scripts/wiki/code_index.py /path/to/repo
    python scripts/wiki/code_index.py /path/to/repo --cache cache/code_index.json
    python scripts/wiki/code_index.py /path/to/repo --rebuild

Per ``plan/PRD-latmd-integration.md`` Phase 2 deliverables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Reuse the exact same skip-dirs / extension config as index_workspace, so
# code_index sees the same files. Cleaner than redefining.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ast_extract import extract_symbols, lang_from_path, EXT_TO_LANG  # noqa: E402

SCHEMA_VERSION = 1
MAX_FILE_SIZE = 200_000  # match index_workspace.py
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".cache", "assets", "screenshots",
    ".next", ".turbo", "dist", "build", "out", "coverage", ".vscode", ".idea",
    "vendor", "target",
})
CODE_EXTENSIONS: frozenset[str] = frozenset(EXT_TO_LANG.keys())


def _file_cache_key(path: Path) -> tuple[int, str]:
    """Deterministic cache key for a file: ``(mtime_ns, sha1_prefix)``.

    ``mtime_ns`` is an int (no float drift) and the sha1 prefix gives
    content-level invalidation that survives mtime quirks (Windows ctime,
    fast-edit/save cycles within the same mtime granularity, etc.).
    """
    st = path.stat()
    mtime_ns = st.st_mtime_ns
    h = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
    return mtime_ns, h


def _index_one_file(path: Path, rel: str) -> Optional[dict]:
    """Parse one source file. Returns ``{symbols: [...], mtime_ns, sha1_prefix}``
    or ``None`` if the file is too large / unreadable / has no detectable lang.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0 or size > MAX_FILE_SIZE:
        return None

    lang = lang_from_path(rel)
    if not lang:
        return None

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    mtime_ns, sha1_prefix = _file_cache_key(path)
    raw_symbols = extract_symbols(lang, content)
    # Trim to the fields the auditor + lat tools actually need; keeps the
    # cache file small for repos with thousands of symbols.
    symbols = [
        {
            "name": s["name"],
            "kind": s.get("kind", "function"),
            "line": s.get("line", 0),
            "end_line": s.get("end_line", 0),
            "exported": bool(s.get("exported", False)),
        }
        for s in raw_symbols
    ]
    return {
        "mtime_ns": mtime_ns,
        "sha1_prefix": sha1_prefix,
        "symbols": symbols,
    }


def _walk_code_files(root: Path) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, relative_path)`` for every code file under root,
    skipping vendor / build / cache directories. Mirrors
    ``index_workspace.scan_directory`` semantics so the two indexes see the
    same file population.
    """
    out: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext in CODE_EXTENSIONS:
                p = Path(dirpath) / name
                out.append((p, str(p.relative_to(root)).replace(os.sep, "/")))
    out.sort(key=lambda t: t[1])
    return out


def build_code_index(
    root: str | Path,
    *,
    cache_path: Optional[Path] = None,
    rebuild: bool = False,
) -> dict:
    """Build (or refresh) the symbol index for ``root``.

    Args:
        root: repository root.
        cache_path: optional ``cache/code_index.json``-shaped path. When set,
            a previous index is loaded for incremental refresh; the result
            is also written back atomically.
        rebuild: if ``True``, ignore the prior cache and re-parse every file.

    Returns the in-memory index dict.
    """
    root = Path(root).resolve()
    prior: dict = {}
    if cache_path and cache_path.exists() and not rebuild:
        try:
            prior = json.loads(cache_path.read_text(encoding="utf-8"))
            if prior.get("schema_version") != SCHEMA_VERSION:
                # Schema bump invalidates the cache, refusal-and-rebuild.
                prior = {}
        except (OSError, json.JSONDecodeError):
            prior = {}
    prior_files: dict = prior.get("files", {}) if prior else {}

    files: dict[str, dict] = {}
    n_reused = 0
    n_parsed = 0
    for absolute, rel in _walk_code_files(root):
        # Cheap mtime check first; sha1 only on candidate cache hit.
        try:
            current_mtime = absolute.stat().st_mtime_ns
        except OSError:
            continue
        prior_entry = prior_files.get(rel)
        if prior_entry and prior_entry.get("mtime_ns") == current_mtime:
            # mtime matches; verify sha1 to defeat fast-edit-within-tick.
            try:
                current_sha = hashlib.sha1(absolute.read_bytes()).hexdigest()[:8]
            except OSError:
                continue
            if current_sha == prior_entry.get("sha1_prefix"):
                files[rel] = prior_entry
                n_reused += 1
                continue
        entry = _index_one_file(absolute, rel)
        if entry is None:
            continue
        files[rel] = entry
        n_parsed += 1

    index = {
        "schema_version": SCHEMA_VERSION,
        "root": str(root).replace(os.sep, "/"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": {
            "files_indexed": len(files),
            "files_reused_from_cache": n_reused,
            "files_reparsed": n_parsed,
        },
        "files": files,
    }

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file + rename so a crash mid-write doesn't
        # leave a half-formed JSON the next run will reject.
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache_path)

    return index


def load_code_index(cache_path: str | Path) -> dict:
    """Load a previously-written index from disk. Raises FileNotFoundError if
    missing, ValueError on schema mismatch (refusal-and-rebuild remediation
    pointer in the message).
    """
    p = Path(cache_path)
    if not p.is_file():
        raise FileNotFoundError(
            f"code_index: no index at {p}. Run "
            f"`python scripts/wiki/code_index.py <root> --cache {p}` first."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"code_index: schema_version={data.get('schema_version')!r} does "
            f"not match current {SCHEMA_VERSION}. Re-run with --rebuild."
        )
    return data


def resolve_symbol(index: dict, path: str, symbol: str) -> list[dict]:
    """Return every symbol entry in ``path`` whose ``name`` equals ``symbol``.

    Multiple matches are possible (overloaded methods, nested classes whose
    inner symbol shares a name with an outer). Callers that want one
    deterministic answer should use ``resolve_symbol_strict``.
    """
    file_entry = index.get("files", {}).get(path)
    if not file_entry:
        return []
    return [s for s in file_entry["symbols"] if s["name"] == symbol]


def resolve_symbol_strict(index: dict, path: str, symbol: str) -> Optional[dict]:
    """Pick the best single match for ``[[path#symbol]]``: prefer exported
    over private, then earliest line. Returns ``None`` if no match.
    """
    matches = resolve_symbol(index, path, symbol)
    if not matches:
        return None
    matches.sort(key=lambda s: (not s.get("exported", False), s.get("line", 0)))
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("root", help="Repository root to index")
    p.add_argument("--cache", default=None, help="Path to write/read code_index.json")
    p.add_argument("--rebuild", action="store_true", help="Ignore cache; re-parse every file")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    cache = Path(args.cache) if args.cache else None
    started = time.time()
    index = build_code_index(args.root, cache_path=cache, rebuild=args.rebuild)
    elapsed = time.time() - started
    stats = index["stats"]
    if not args.quiet:
        print(
            f"code_index: {stats['files_indexed']} files "
            f"({stats['files_reparsed']} reparsed, {stats['files_reused_from_cache']} "
            f"cached) in {elapsed*1000:.0f}ms"
            + (f" -> {cache}" if cache else "")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
