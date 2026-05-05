"""Phase A tests — pluggable storage backend + Vercel Blob client.

Covers:
- `storage.get_backend()` resolution: explicit override, BLOB_READ_WRITE_TOKEN
  presence, default fallback to local.
- `storage.local.LocalBackend`: round-trip put/get/delete/list_keys.
- `storage.blob.BlobBackend`: get/put/head/delete/list against a mock HTTP
  layer (no real Vercel hits). Verifies URL templates, auth header, and
  the put/head/get-blob 2-step retrieval.
- `corpus_store` integration: load_corpus/write_corpus uses the active
  backend; Blob path populates and reads from a `/tmp` warm cache
  (cold-start safety).
- Cold-start simulation: write corpus → reset warm cache → load_corpus
  still works via Blob.

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs server-prod/tests/test_phase_a_blob.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

from _lib import corpus_store, storage  # noqa: E402
from _lib.storage import blob as blob_mod, local as local_mod  # noqa: E402


# ── Reset the corpus_store singleton between tests ──

@pytest.fixture(autouse=True)
def reset_backend(monkeypatch):
    """Tests monkeypatch env vars to switch backends; clear the cached
    backend before AND after so state doesn't leak."""
    corpus_store.set_backend(None)
    yield
    corpus_store.set_backend(None)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cd = tmp_path / "cache"
    cd.mkdir()
    monkeypatch.setenv("CE_CORPUS_CACHE_DIR", str(cd))
    return cd


# ── Backend resolution ──

def test_get_backend_default_is_local(monkeypatch):
    monkeypatch.delenv("CE_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    backend = storage.get_backend()
    assert isinstance(backend, local_mod.LocalBackend)


def test_get_backend_blob_when_token_present(monkeypatch):
    monkeypatch.delenv("CE_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_xxx_yyy")
    backend = storage.get_backend()
    assert isinstance(backend, blob_mod.BlobBackend)


def test_get_backend_explicit_override_local(monkeypatch):
    """CE_STORAGE_BACKEND=local wins over BLOB_READ_WRITE_TOKEN."""
    monkeypatch.setenv("CE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_xxx_yyy")
    backend = storage.get_backend()
    assert isinstance(backend, local_mod.LocalBackend)


def test_get_backend_explicit_override_blob_without_token_still_returns_blob(monkeypatch):
    """CE_STORAGE_BACKEND=blob returns the backend; missing token surfaces
    on the next call (PROVIDER_UNAVAILABLE), not at construction."""
    monkeypatch.setenv("CE_STORAGE_BACKEND", "blob")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    backend = storage.get_backend()
    assert isinstance(backend, blob_mod.BlobBackend)


# ── LocalBackend round-trips ──

def test_local_backend_put_get_round_trip(cache_dir):
    b = local_mod.LocalBackend()
    b.put_bytes("foo.index.json", b'{"hello": "world"}')
    out = b.get_bytes("foo.index.json")
    assert out == b'{"hello": "world"}'


def test_local_backend_get_missing_returns_none(cache_dir):
    b = local_mod.LocalBackend()
    assert b.get_bytes("nope.index.json") is None


def test_local_backend_delete_idempotent(cache_dir):
    b = local_mod.LocalBackend()
    b.put_bytes("x.json", b"x")
    b.delete("x.json")
    assert b.get_bytes("x.json") is None
    b.delete("x.json")  # second delete is OK


def test_local_backend_list_keys_filters_by_prefix(cache_dir):
    b = local_mod.LocalBackend()
    b.put_bytes("alpha.index.json", b"a")
    b.put_bytes("beta.index.json", b"b")
    b.put_bytes("other.txt", b"o")
    keys = sorted(b.list_keys(prefix=""))
    assert "alpha.index.json" in keys
    assert "beta.index.json" in keys
    assert "other.txt" in keys
    keys_a = b.list_keys(prefix="al")
    assert keys_a == ["alpha.index.json"]


# ── BlobBackend with mock HTTP ──

class _FakeHttp:
    """Records all _request() calls; returns scripted responses by URL match."""

    def __init__(self):
        self.calls: list[dict] = []
        self.routes: dict[tuple[str, str], tuple[int, bytes, dict]] = {}
        # Storage-side blob bodies, keyed by storage URL
        self.blob_bodies: dict[str, bytes] = {}

    def add_route(self, method: str, url_substring: str, *,
                  status: int = 200, body: bytes = b"{}",
                  headers: dict[str, str] | None = None):
        self.routes[(method, url_substring)] = (status, body, headers or {})

    def __call__(self, method, url, *, body=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers})
        # First check storage URLs (download path)
        if url in self.blob_bodies:
            return 200, self.blob_bodies[url], {}
        # Then route by substring match
        for (m, sub), resp in self.routes.items():
            if m == method and sub in url:
                return resp
        return 404, b'{"error": "no route"}', {}


@pytest.fixture
def fake_blob(monkeypatch):
    """Wire a fake HTTP layer into _lib.storage.blob and return the recorder."""
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_test_test")
    fake = _FakeHttp()
    monkeypatch.setattr(blob_mod, "_request", fake)
    return fake


def test_blob_put_sends_pathname_query_and_bearer(fake_blob):
    fake_blob.add_route("PUT", "?pathname=", body=b'{"url":"https://x.private.blob.vercel-storage.com/foo.json"}')
    b = blob_mod.BlobBackend()
    b.put_bytes("foo.json", b'{"hello": "world"}')
    assert len(fake_blob.calls) == 1
    call = fake_blob.calls[0]
    assert call["method"] == "PUT"
    assert "pathname=foo.json" in call["url"]
    assert call["headers"]["x-allow-overwrite"] == "1"
    assert call["headers"]["x-add-random-suffix"] == "0"


def test_blob_get_uses_head_then_download_url(fake_blob):
    download_url = "https://x.private.blob.vercel-storage.com/bar.json"
    fake_blob.add_route("GET", "?url=bar.json",
                        body=json.dumps({"downloadUrl": download_url, "url": download_url}).encode())
    fake_blob.blob_bodies[download_url] = b'{"hello": "bar"}'

    b = blob_mod.BlobBackend()
    out = b.get_bytes("bar.json")
    assert out == b'{"hello": "bar"}'
    # 2 calls: head (GET /?url=...) then storage GET
    methods = [c["method"] for c in fake_blob.calls]
    assert methods == ["GET", "GET"]


def test_blob_get_missing_returns_none(fake_blob):
    # No routes registered → 404 from default
    b = blob_mod.BlobBackend()
    assert b.get_bytes("nope.json") is None


def test_blob_head_returns_metadata_dict(fake_blob):
    fake_blob.add_route("GET", "?url=meta.json",
                        body=json.dumps({"url": "https://x/meta.json", "size": 42}).encode())
    b = blob_mod.BlobBackend()
    meta = b.head("meta.json")
    assert meta == {"url": "https://x/meta.json", "size": 42}


def test_blob_delete_resolves_url_then_posts_delete(fake_blob):
    storage_url = "https://x.private.blob.vercel-storage.com/del.json"
    fake_blob.add_route("GET", "?url=del.json",
                        body=json.dumps({"url": storage_url, "downloadUrl": storage_url}).encode())
    fake_blob.add_route("POST", "/delete", body=b"{}")
    b = blob_mod.BlobBackend()
    b.delete("del.json")
    methods_urls = [(c["method"], c["url"]) for c in fake_blob.calls]
    assert methods_urls[0][0] == "GET"  # HEAD
    assert methods_urls[1][0] == "POST"  # DELETE
    assert "/delete" in methods_urls[1][1]


def test_blob_delete_missing_is_no_op(fake_blob):
    """Missing blob → head returns None → delete short-circuits without
    POST. Matches LocalBackend behavior."""
    b = blob_mod.BlobBackend()
    b.delete("nope.json")
    # Only the head call fired
    assert len(fake_blob.calls) == 1
    assert fake_blob.calls[0]["method"] == "GET"


def test_blob_list_keys_pages_until_no_cursor(fake_blob):
    page1 = json.dumps({
        "blobs": [{"pathname": "a.json"}, {"pathname": "b.json"}],
        "hasMore": True, "cursor": "tok",
    }).encode()
    page2 = json.dumps({
        "blobs": [{"pathname": "c.json"}],
        "hasMore": False,
    }).encode()
    # Both pages match the LIST endpoint substring; we sequence by call count.
    page_iter = iter([page1, page2])

    def fake_request(method, url, *, body=None, headers=None, timeout=None):
        fake_blob.calls.append({"method": method, "url": url, "body": body})
        return 200, next(page_iter), {}

    import _lib.storage.blob as _b
    _b._request = fake_request  # type: ignore[assignment]

    b = blob_mod.BlobBackend()
    keys = b.list_keys()
    assert keys == ["a.json", "b.json", "c.json"]
    # Second page request had cursor=tok
    assert "cursor=tok" in fake_blob.calls[-1]["url"]


# ── corpus_store integration with Blob backend ──

def test_corpus_store_write_then_load_via_blob_backend(cache_dir, fake_blob, monkeypatch):
    """Write a corpus through corpus_store.write_corpus, then load_corpus
    must read it back via the Blob backend.
    """
    # Set up a Blob-backed flow: write, then HEAD/GET on read.
    download_url = "https://x.private.blob.vercel-storage.com/test-corpus.index.json"
    body_obj = {
        "_meta": {
            "corpus_id": "test-corpus",
            "source": {"type": "github_repo", "uri": "https://github.com/x/y"},
            "data_classification": "public",
            "embedding": {"provider": "none", "model": "n/a", "dims": 0},
            "file_count": 1, "embedded_count": 0, "version": 1,
            "last_refresh_completed_at": "2026-05-06T00:00:00Z",
            "commit_sha": "deadbeef0000",
            "lifecycle_state": "active",
        },
        "files": [{"path": "x.py", "contentHash": "h1", "tokens": 10,
                   "tree": {"text": "x", "children": []}}],
    }
    body = json.dumps(body_obj).encode("utf-8")

    fake_blob.add_route("PUT", "?pathname=test-corpus.index.json",
                        body=json.dumps({"url": download_url, "pathname": "test-corpus.index.json"}).encode())
    fake_blob.add_route("GET", "?url=test-corpus.index.json",
                        body=json.dumps({"url": download_url, "downloadUrl": download_url}).encode())
    fake_blob.blob_bodies[download_url] = body

    # Force backend resolution to Blob.
    corpus_store.set_backend(blob_mod.BlobBackend())

    # First, evict any local warm cache from an earlier test.
    warm = cache_dir / "test-corpus.index.json"
    warm.unlink(missing_ok=True)

    # Write through corpus_store API
    size = corpus_store.write_corpus("test-corpus", body)
    assert size == len(body)

    # Warm cache should now be populated
    assert warm.exists()
    assert warm.read_bytes() == body

    # Read should hit warm cache (no extra Blob roundtrip)
    pre_call_count = len(fake_blob.calls)
    loaded = corpus_store.load_corpus("test-corpus")
    assert loaded is not None
    assert loaded.meta.corpus_id == "test-corpus"
    assert loaded.meta.commit_sha == "deadbeef0000"
    # Warm cache hit means no new Blob calls
    assert len(fake_blob.calls) == pre_call_count


def test_corpus_store_cold_start_falls_back_to_blob(cache_dir, fake_blob, monkeypatch):
    """Simulate a cold start: warm cache is empty, but Blob has the corpus.
    load_corpus must round-trip through Blob and re-populate the warm cache.
    """
    download_url = "https://x.private.blob.vercel-storage.com/cold-corpus.index.json"
    body_obj = {
        "_meta": {
            "corpus_id": "cold-corpus",
            "source": {"type": "github_repo", "uri": "https://github.com/x/y"},
            "data_classification": "public",
            "embedding": {"provider": "none", "model": "n/a", "dims": 0},
            "file_count": 1, "embedded_count": 0, "version": 1,
            "last_refresh_completed_at": "2026-05-06T00:00:00Z",
            "commit_sha": "cafe00000000",
            "lifecycle_state": "active",
        },
        "files": [],
    }
    body = json.dumps(body_obj).encode("utf-8")

    fake_blob.add_route("GET", "?url=cold-corpus.index.json",
                        body=json.dumps({"url": download_url, "downloadUrl": download_url}).encode())
    fake_blob.blob_bodies[download_url] = body

    corpus_store.set_backend(blob_mod.BlobBackend())

    # Confirm warm cache is empty (cold-start condition)
    warm = cache_dir / "cold-corpus.index.json"
    assert not warm.exists()

    loaded = corpus_store.load_corpus("cold-corpus")
    assert loaded is not None
    assert loaded.meta.corpus_id == "cold-corpus"

    # Warm cache must now be populated for subsequent reads
    assert warm.exists()


def test_corpus_store_load_missing_returns_none_on_blob(cache_dir, fake_blob):
    """load_corpus returns None when neither warm cache nor Blob have the
    corpus — must NOT raise, must NOT return a malformed object."""
    corpus_store.set_backend(blob_mod.BlobBackend())
    out = corpus_store.load_corpus("not-here-corpus")
    assert out is None


# ── Token / config error paths ──

def test_blob_request_without_token_raises_provider_unavailable(monkeypatch):
    """Calling _token() without env raises BlobError(PROVIDER_UNAVAILABLE)."""
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    with pytest.raises(blob_mod.BlobError) as exc:
        blob_mod._token()
    assert exc.value.code == "PROVIDER_UNAVAILABLE"
