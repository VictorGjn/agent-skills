"""Filesystem storage backend (default).

Reads/writes under `cache_dir()` — what `corpus_store` did for v1. Kept as
the default for tests + local dev so existing behavior is unchanged.

Override the cache root via `CE_CORPUS_CACHE_DIR` env (existing pattern).
"""
from __future__ import annotations

import os
from pathlib import Path


_DEFAULT_CACHE = Path(__file__).resolve().parent.parent.parent / "cache"


def cache_dir() -> Path:
    override = os.environ.get("CE_CORPUS_CACHE_DIR")
    return Path(override).resolve() if override else _DEFAULT_CACHE


class LocalBackend:
    """Plain-file storage. Keys are filesystem paths relative to cache_dir."""

    def get_bytes(self, key: str) -> bytes | None:
        p = cache_dir() / key
        if not p.exists():
            return None
        try:
            return p.read_bytes()
        except OSError:
            return None

    def put_bytes(self, key: str, body: bytes) -> None:
        p = cache_dir() / key
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: temp + rename
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(p)

    def delete(self, key: str) -> None:
        p = cache_dir() / key
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    def list_keys(self, prefix: str = "") -> list[str]:
        cd = cache_dir()
        if not cd.exists():
            return []
        out: list[str] = []
        for p in sorted(cd.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(cd).as_posix()
            if not prefix or rel.startswith(prefix):
                out.append(rel)
        return out
