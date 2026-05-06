"""Vercel Blob storage backend.

stdlib-only HTTP client for the Vercel Blob REST API — no `@vercel/blob`
SDK or `requests` dep added to the function bundle. Selected automatically
when `BLOB_READ_WRITE_TOKEN` is in env (Phase A of v1.1 plan).

REST API contract (extracted from vercel/storage @vercel/blob source):
- Base URL: `https://vercel.com/api/blob`
  (override via `VERCEL_BLOB_API_URL` for local stubs / dogfood)
- Auth: `Authorization: Bearer <BLOB_READ_WRITE_TOKEN>`
- PUT `/?pathname=<encoded>` body=bytes → returns {url, pathname, etag, downloadUrl}
- POST `/delete` body=`{"urls": [<url>]}` → 200
- GET `/?url=<urlOrPathname>` → blob metadata (head; 404 = not found)
- GET `/?prefix=<prefix>&limit=<n>&cursor=<c>` → paged list

For private blobs, the actual blob content lives at the returned `url` on
`<storeId>.private.blob.vercel-storage.com`. We fetch from there with the
same Bearer token.

Cold-start performance: each call is a single HTTP roundtrip (~200-500ms
to Vercel infra from cdg1 functions). Callers should cache aggressively
(corpus_store keeps a per-instance /tmp warm cache; see corpus_store.py).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_API_URL = "https://vercel.com/api/blob"
DEFAULT_TIMEOUT_S = 30
LIST_PAGE_SIZE = 1000  # max per Vercel API page

# Pinned to vercel/storage's BLOB_API_VERSION constant (api.ts).
# Override via VERCEL_BLOB_API_VERSION_OVERRIDE env (mirrors the SDK).
DEFAULT_API_VERSION = "12"

# Vercel Blob's `access` is REQUIRED on writes. Our corpora are not
# user-shared assets — keep them private (token-gated). Public blobs are
# served from a different host with no auth, which we don't want.
# Override with BLOB_ACCESS=public for bench/eval setups that use a
# public-mode store (created on Hobby plan or for shared eval corpora).
DEFAULT_ACCESS = os.environ.get("BLOB_ACCESS", "private")


class BlobError(Exception):
    """Raised on transport / API failure. `code` matches our SPEC § 7 codes
    where applicable; otherwise `BLOB_<status>`."""

    def __init__(self, code: str, message: str, *, status: int | None = None,
                 body: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.body = body


def _api_base() -> str:
    return os.environ.get("VERCEL_BLOB_API_URL") or DEFAULT_API_URL


def _api_version() -> str:
    return os.environ.get("VERCEL_BLOB_API_VERSION_OVERRIDE") or DEFAULT_API_VERSION


def _common_headers() -> dict[str, str]:
    """Headers the SDK sends on EVERY request: api-version + per-request id."""
    import uuid
    return {
        "x-api-version": _api_version(),
        "x-api-blob-request-id": uuid.uuid4().hex,
        "x-api-blob-request-attempt": "0",
    }


def _token() -> str:
    tok = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not tok:
        raise BlobError("PROVIDER_UNAVAILABLE",
                        "BLOB_READ_WRITE_TOKEN not set; can't reach Vercel Blob")
    return tok


def _request(method: str, url: str, *, body: bytes | None = None,
             headers: dict[str, str] | None = None,
             timeout: float = DEFAULT_TIMEOUT_S) -> tuple[int, bytes, dict[str, str]]:
    """Single HTTP roundtrip. Returns (status, body, headers). On non-2xx,
    consumers raise BlobError with the response body."""
    h = {"Authorization": f"Bearer {_token()}"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BlobError("BLOB_TRANSPORT",
                        f"Vercel Blob unreachable: {type(e).__name__}: {e}") from e


class BlobBackend:
    """Vercel Blob client conforming to StorageBackend protocol."""

    def get_bytes(self, key: str) -> bytes | None:
        """Fetch the blob's bytes by pathname.

        We HEAD by pathname to get the storage URL, then GET that URL.
        Two roundtrips per read — acceptable on cold starts, mitigated by
        the per-instance /tmp warm cache in corpus_store.
        """
        meta = self.head(key)
        if meta is None:
            return None
        download_url = meta.get("downloadUrl") or meta.get("url")
        if not download_url:
            return None
        # The blob URL is on .blob.vercel-storage.com; same Bearer token works
        # for private stores. Public stores would 200 without auth, but ours
        # are private — keep auth on for both.
        status, body, _ = _request("GET", download_url)
        if status == 404:
            return None
        if status >= 400:
            raise BlobError(
                f"BLOB_{status}",
                f"GET {download_url} returned {status}",
                status=status, body=body[:500].decode("utf-8", errors="replace"),
            )
        return body

    def put_bytes(self, key: str, body: bytes) -> None:
        """Upload bytes under pathname `key`, overwriting any existing blob.

        Header set verified against vercel/storage @vercel/blob put-helpers.ts
        and api.ts:
        - x-api-version + x-api-blob-request-id + x-api-blob-request-attempt
          are required on every API call (api.ts).
        - x-vercel-blob-access is required on writes ("private" | "public").
          Our corpora are token-gated → private always.
        - x-add-random-suffix=0 + x-allow-overwrite=1 → deterministic pathname
          + idempotent re-write (so re-indexing the same content doesn't 409).
        - x-content-length is sent only when the SDK can compute it.
        """
        params = urllib.parse.urlencode({"pathname": key})
        url = f"{_api_base()}/?{params}"
        headers = _common_headers()
        headers.update({
            "x-vercel-blob-access": DEFAULT_ACCESS,
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-content-length": str(len(body)),
            "x-content-type": "application/json",
        })
        status, resp_body, _ = _request("PUT", url, body=body, headers=headers)
        if status >= 400:
            raise BlobError(
                f"BLOB_{status}",
                f"PUT {key} returned {status}",
                status=status,
                body=resp_body[:500].decode("utf-8", errors="replace"),
            )

    def head(self, key: str) -> dict | None:
        """Return blob metadata or None if missing.

        SDK uses GET /?url=<pathname> for head — same endpoint serves both
        full URL and bare pathname lookups.
        """
        params = urllib.parse.urlencode({"url": key})
        url = f"{_api_base()}/?{params}"
        headers = _common_headers()
        status, resp_body, _ = _request("GET", url, headers=headers)
        if status == 404:
            return None
        if status >= 400:
            raise BlobError(
                f"BLOB_{status}",
                f"HEAD {key} returned {status}",
                status=status,
                body=resp_body[:500].decode("utf-8", errors="replace"),
            )
        try:
            return json.loads(resp_body)
        except json.JSONDecodeError as e:
            raise BlobError(
                "BLOB_PARSE",
                f"head {key} response not JSON: {resp_body[:200]!r}",
            ) from e

    def delete(self, key: str) -> None:
        """Delete by pathname. No-op on missing (matches LocalBackend)."""
        # SDK del takes URL or pathname; we resolve to URL via head first to
        # match the SDK's behavior on the wire.
        meta = self.head(key)
        if meta is None:
            return
        target_url = meta.get("url")
        if not target_url:
            return
        url = f"{_api_base()}/delete"
        body = json.dumps({"urls": [target_url]}).encode("utf-8")
        headers = _common_headers()
        headers["Content-Type"] = "application/json"
        status, resp_body, _ = _request("POST", url, body=body, headers=headers)
        if status >= 400 and status != 404:
            raise BlobError(
                f"BLOB_{status}",
                f"DELETE {key} returned {status}",
                status=status,
                body=resp_body[:500].decode("utf-8", errors="replace"),
            )

    def list_keys(self, prefix: str = "") -> list[str]:
        """List blob pathnames under `prefix` (paged, returns all)."""
        out: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, str] = {"limit": str(LIST_PAGE_SIZE), "mode": "expanded"}
            if prefix:
                params["prefix"] = prefix
            if cursor:
                params["cursor"] = cursor
            url = f"{_api_base()}/?{urllib.parse.urlencode(params)}"
            headers = _common_headers()
            status, resp_body, _ = _request("GET", url, headers=headers)
            if status >= 400:
                raise BlobError(
                    f"BLOB_{status}",
                    f"LIST {prefix!r} returned {status}",
                    status=status,
                    body=resp_body[:500].decode("utf-8", errors="replace"),
                )
            try:
                data = json.loads(resp_body)
            except json.JSONDecodeError as e:
                raise BlobError(
                    "BLOB_PARSE",
                    f"list {prefix!r} response not JSON",
                ) from e
            for blob in data.get("blobs", []):
                pn = blob.get("pathname")
                if pn:
                    out.append(pn)
            cursor = data.get("cursor")
            if not cursor or not data.get("hasMore"):
                break
        return out
