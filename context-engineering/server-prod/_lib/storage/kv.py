"""Vercel KV (Upstash Redis) client.

stdlib-only HTTP — no `@vercel/kv` SDK or `redis-py` dep added to the
function bundle. Phase B.1 of v1.1 plan: foundation for async indexing
(jobs.py) and cross-instance corpus locks.

REST API contract (Upstash docs, https://upstash.com/docs/redis/features/restapi):
- Base URL: `KV_REST_API_URL` (auto-injected when you create a Vercel KV
  store on the project). Override via the env directly.
- Auth: `Authorization: Bearer <KV_REST_API_TOKEN>`
- Two request shapes:
    1. Path-style: POST `<base>/COMMAND/arg1/arg2/...` — concise but
       breaks on values with `/`, binary, large strings.
    2. JSON-body: POST `<base>/` with body `["COMMAND", "arg1", ...]`.
       Handles any value type. We use this everywhere.
- Response: `{"result": <value-or-null>}` on success;
  `{"error": "ERR ..."}` on failure.

Operations needed by the jobs API (B.1) + lock helper:
- get(key) → str | None
- set(key, value, *, ex_seconds, nx) → bool (True if set)
- delete(key) → int (count deleted)
- rpush(key, value) → int (new length)
- lpop(key) → str | None
- expire(key, seconds) → bool
- exists(key) → bool

Phase B.2+ may add: hash ops (hset/hget), pipeline batching.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


DEFAULT_TIMEOUT_S = 30


class KVError(Exception):
    """Raised when KV transport / API fails. `code` matches our SPEC § 7
    error codes where applicable."""

    def __init__(self, code: str, message: str, *, status: int | None = None,
                 body: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.body = body


def _api_url() -> str:
    url = os.environ.get("KV_REST_API_URL")
    if not url:
        raise KVError("PROVIDER_UNAVAILABLE",
                      "KV_REST_API_URL not set; can't reach Vercel KV")
    return url.rstrip("/")


def _token() -> str:
    tok = os.environ.get("KV_REST_API_TOKEN")
    if not tok:
        raise KVError("PROVIDER_UNAVAILABLE",
                      "KV_REST_API_TOKEN not set; can't reach Vercel KV")
    return tok


def _exec(command: list[str | int], *,
          timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """Execute a Redis command via Upstash REST. Returns the parsed JSON
    response body. Raises KVError on transport / 4xx / 5xx.

    Command is a list like ['SET', 'key', 'value', 'EX', '60', 'NX'].
    All args are coerced to str — Upstash accepts int via JSON natively
    but explicit string keeps the wire shape predictable.
    """
    body = json.dumps([str(part) for part in command]).encode("utf-8")
    req = urllib.request.Request(
        _api_url(), data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_token()}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")[:500]
        raise KVError(
            f"KV_{e.code}",
            f"Upstash returned {e.code}",
            status=e.code, body=body_str,
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise KVError("KV_TRANSPORT",
                      f"Upstash unreachable: {type(e).__name__}: {e}") from e

    if "error" in data:
        raise KVError("KV_COMMAND", f"Upstash error: {data['error']}")
    return data


# ── Operations ──

def get(key: str) -> str | None:
    """GET key → string value, or None if the key doesn't exist."""
    return _exec(["GET", key]).get("result")


def set(key: str, value: str, *,
        ex_seconds: int | None = None,
        nx: bool = False) -> bool:
    """SET key value [EX seconds] [NX]. Returns True iff the value was set.

    With NX=True, returns False when the key already existed (no overwrite).
    Otherwise returns True on the standard 'OK' response.
    """
    cmd: list[str | int] = ["SET", key, value]
    if ex_seconds is not None:
        cmd += ["EX", ex_seconds]
    if nx:
        cmd.append("NX")
    result = _exec(cmd).get("result")
    # Upstash returns "OK" on success, null when NX prevented the write
    return result == "OK"


def delete(key: str) -> int:
    """DEL key → count of keys removed (0 or 1)."""
    return int(_exec(["DEL", key]).get("result", 0) or 0)


def exists(key: str) -> bool:
    """EXISTS key → True if present."""
    return int(_exec(["EXISTS", key]).get("result", 0) or 0) == 1


def expire(key: str, seconds: int) -> bool:
    """EXPIRE key seconds → True if TTL was set, False if key missing."""
    return int(_exec(["EXPIRE", key, seconds]).get("result", 0) or 0) == 1


def rpush(key: str, value: str) -> int:
    """RPUSH key value → new list length."""
    return int(_exec(["RPUSH", key, value]).get("result", 0) or 0)


def lpop(key: str) -> str | None:
    """LPOP key → first list element, or None if list empty / missing.

    Atomic — safe for the worker pull pattern (no two workers can claim
    the same job_id from the queue).
    """
    return _exec(["LPOP", key]).get("result")


def llen(key: str) -> int:
    """LLEN key → list length, 0 if missing."""
    return int(_exec(["LLEN", key]).get("result", 0) or 0)


# ── Lock helper (cross-instance, time-bounded) ──

def acquire_lock(name: str, *, ttl_seconds: int = 90,
                 holder: str | None = None) -> str | None:
    """Atomically acquire a named lock with TTL. Returns the holder token
    if acquired, None if already held.

    Pattern: `SET lock:<name> <holder> EX <ttl> NX`. If the holder is None
    we generate a uuid so callers can release only their own lock.

    Use case: corpus_id-scoped lock during an index/upload to prevent
    cross-instance writers from trampling each other on Blob. Phase A's
    filesystem lock only protected intra-instance writers.
    """
    import uuid
    holder_token = holder or uuid.uuid4().hex
    if set(f"lock:{name}", holder_token, ex_seconds=ttl_seconds, nx=True):
        return holder_token
    return None


def release_lock(name: str, holder: str) -> bool:
    """Release the named lock IFF the caller is still the holder.

    Uses an EVAL script to make the check-and-delete atomic. Without this,
    a slow caller could DELETE a lock another worker just acquired after
    the original lock TTL expired.
    """
    # Lua: only delete if value matches; returns 1 on delete, 0 otherwise.
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    result = _exec(["EVAL", script, "1", f"lock:{name}", holder]).get("result", 0)
    return int(result or 0) == 1
