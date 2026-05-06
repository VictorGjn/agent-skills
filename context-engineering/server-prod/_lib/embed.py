"""Mistral codestral-embed client.

Phase 5.5: enables real semantic mode in `ce_find_relevant_files` and
`ce_pack_context`. Until this lands, the engine falls back to keyword
scoring even when callers ask for `mode: semantic`.

stdlib-only HTTP — no `requests`/`httpx` dep added to the Vercel function
bundle. Lazy: nothing happens until a caller invokes `embed_query` or
`embed_batch`.

Errors:
- PROVIDER_UNAVAILABLE — MISTRAL_API_KEY missing
- EMBED_HTTP — non-2xx from Mistral
- EMBED_DIM_MISMATCH — vector length differs from MISTRAL_DIMS

Truncation: codestral-embed accepts ~8K input tokens. Chars > MAX_INPUT_CHARS
are head-truncated; embedding a head sample is preferable to OOM/timeout.
The engine's keyword pipeline already covers full-file matching — semantic
is meant to add fuzzy intent, not full-content retrieval.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

MISTRAL_ENDPOINT = "https://api.mistral.ai/v1/embeddings"
MISTRAL_MODEL = "codestral-embed"
MISTRAL_DIMS = 1536
# codestral-embed-v1 caps at 8192 input tokens. Code is denser than prose
# (~3 chars/token vs ~4); a 32K-char truncate produced 9349-token inputs and
# returned HTTP 400 from Mistral. 20K chars gives ~6500 tokens at code
# density, leaving headroom for outliers.
MAX_INPUT_CHARS = 20_000
DEFAULT_BATCH_SIZE = 32
DEFAULT_TIMEOUT_S = 30


class EmbedError(Exception):
    """Raised when embedding fails. `code` matches SPEC § 7 error codes."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _api_key() -> str:
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise EmbedError(
            "PROVIDER_UNAVAILABLE",
            "MISTRAL_API_KEY not set; semantic mode requires the codestral-embed provider",
        )
    return key


def _truncate(text: str) -> str:
    if len(text) <= MAX_INPUT_CHARS:
        return text
    return text[:MAX_INPUT_CHARS]


def _post(api_key: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        MISTRAL_ENDPOINT, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")[:500]
        raise EmbedError(
            "EMBED_HTTP",
            f"Mistral embeddings returned {e.code}",
            details={"status": e.code, "body": body_str},
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise EmbedError(
            "EMBED_HTTP",
            f"Mistral embeddings unreachable: {type(e).__name__}: {e}",
        ) from e


def embed_query(text: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> list[float]:
    """Embed a single query. Returns a 1536-dim vector."""
    if not text or not text.strip():
        raise EmbedError("INVALID_ARGUMENT", "embed_query: text is empty")
    api_key = _api_key()
    resp = _post(
        api_key,
        {"model": MISTRAL_MODEL, "input": [_truncate(text)]},
        timeout,
    )
    data = resp.get("data") or []
    if not data:
        raise EmbedError("EMBED_HTTP", "Mistral response missing data array", details={"resp": resp})
    vec = data[0].get("embedding") or []
    if len(vec) != MISTRAL_DIMS:
        raise EmbedError(
            "EMBED_DIM_MISMATCH",
            f"expected {MISTRAL_DIMS} dims, got {len(vec)}",
            details={"expected": MISTRAL_DIMS, "got": len(vec)},
        )
    return vec


def embed_batch(
    texts: list[str], *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: float = DEFAULT_TIMEOUT_S * 2,
) -> list[list[float]]:
    """Embed many texts. Returns one vector per input text, in order.

    Empty/whitespace-only texts are replaced with a single space ("·") to
    keep array alignment with input order — caller must filter zero-content
    files before calling if they want strict semantics.
    """
    api_key = _api_key()
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        payload_inputs = [_truncate(t) if t and t.strip() else "·" for t in chunk]
        resp = _post(
            api_key,
            {"model": MISTRAL_MODEL, "input": payload_inputs},
            timeout,
        )
        data = resp.get("data") or []
        if len(data) != len(chunk):
            raise EmbedError(
                "EMBED_HTTP",
                f"Mistral returned {len(data)} embeddings for {len(chunk)} inputs",
                details={"expected": len(chunk), "got": len(data)},
            )
        for i, item in enumerate(data):
            vec = item.get("embedding") or []
            if len(vec) != MISTRAL_DIMS:
                raise EmbedError(
                    "EMBED_DIM_MISMATCH",
                    f"batch row {start + i} has {len(vec)} dims, expected {MISTRAL_DIMS}",
                    details={"row": start + i, "expected": MISTRAL_DIMS, "got": len(vec)},
                )
            out.append(vec)
    return out
