"""ce_get_health — § 3.6.

Liveness + version + provider availability + auth methods.

P4.2 (v1.2): `deep` arg (default False) opt-in probes each backend with a
real round-trip:
- KV: SET ex_seconds=60 + GET + DEL on a unique key.
- Blob: PUT + GET + DELETE on a unique key under `health/`.
- Mistral: 1-token embed_query roundtrip (cost ~$0; budget-safe even on
  per-second polling).

Each probe reports `{ok, took_ms, ...}` with the actual error message on
failure. Without `deep=True`, response stays backwards-compatible with the
v1.0 shape.

Default-off: deep probes touch real prod systems and rotate small amounts
of state. Callers (monitors, smoke tests) opt in explicitly. The shallow
liveness probe stays cheap so it's safe to call from tight loops.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

from ..auth import TokenInfo
from ..version import GIT_SHA, SERVER_VERSION


def embedding_providers() -> list[str]:
    """List configured embedding providers via env. Public — also used by api/health.py."""
    out = []
    for env, name in [
        ("OPENAI_API_KEY", "openai"),
        ("MISTRAL_API_KEY", "mistral"),
        ("VOYAGE_API_KEY", "voyage"),
    ]:
        if os.environ.get(env):
            out.append(name)
    return out


def auth_methods() -> list[str]:
    """List supported auth methods. Public — also used by api/health.py."""
    methods = ["bearer"]
    # OAuth 2.1 promoted to v1.0 optional per § 6.1; flip on once metadata path is wired.
    if os.environ.get("CE_MCP_OAUTH_ENABLED") == "1":
        methods.append("oauth2.1")
    return methods


def _probe_kv() -> dict[str, Any]:
    """SET (TTL=60s) + GET + DEL on a unique key. Returns ok/took_ms or
    `error` with the underlying message. Probe key is 60s-TTL'd so a crash
    mid-probe doesn't leak permanent state.

    Uses time.monotonic() for elapsed-time math (NTP-correction safe).
    """
    from ..storage import kv  # late import — keeps health import-time cheap
    start = time.monotonic()
    if not (os.environ.get("KV_REST_API_URL") and os.environ.get("KV_REST_API_TOKEN")):
        return {"ok": False, "took_ms": 0, "error": "KV_REST_API_* env not set"}
    key = f"health:probe:{uuid.uuid4().hex}"
    val = f"ts={time.time():.6f}"  # wall-clock value is fine — only the elapsed math uses monotonic
    try:
        # kv.set defaults to nx=False, so it returns True on every successful
        # write — no NX collision branch to worry about. We still check the
        # boolean so a future change to default=nx surfaces here, but the
        # normal path always sees True.
        if not kv.set(key, val, ex_seconds=60):
            return {"ok": False, "took_ms": int((time.monotonic() - start) * 1000),
                    "error": "kv.set returned False unexpectedly"}
        got = kv.get(key)
        if got != val:
            # Always try to clean up before returning, even on mismatch — the
            # probe key is 60s-TTL'd so this is belt-and-suspenders, not strictly
            # required, but matches blob's hygiene.
            try:
                kv.delete(key)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
            return {"ok": False, "took_ms": int((time.monotonic() - start) * 1000),
                    "error": f"read-back mismatch: wrote {val!r}, got {got!r}"}
        kv.delete(key)
    except Exception as e:  # noqa: BLE001 — surface the real failure
        return {"ok": False, "took_ms": int((time.monotonic() - start) * 1000),
                "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "took_ms": int((time.monotonic() - start) * 1000)}


def _probe_blob() -> dict[str, Any]:
    """PUT + GET + DELETE on a unique health/ key. Returns ok/took_ms or
    `error`. Probe blob is small (~30 bytes) so even repeated polling is
    free against the Pro quota (100 GB).

    Cleanup invariant: the blob is always deleted on success. On read-back
    mismatch we attempt cleanup (best-effort) so quota doesn't bleed across
    repeated mismatches; on PUT/GET exception, no orphan exists yet so
    nothing to clean.
    """
    from ..storage import blob  # late import
    start = time.monotonic()
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        return {"ok": False, "took_ms": 0, "error": "BLOB_READ_WRITE_TOKEN not set"}
    key = f"health/probe-{uuid.uuid4().hex}.txt"
    body = f"ts={time.time():.6f}".encode("utf-8")
    backend = blob.BlobBackend()
    try:
        backend.put_bytes(key, body)
        got = backend.get_bytes(key)
        if got != body:
            try:
                backend.delete(key)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
            return {"ok": False, "took_ms": int((time.monotonic() - start) * 1000),
                    "error": f"read-back mismatch on {key}"}
        backend.delete(key)
    except Exception as e:  # noqa: BLE001 — surface the real failure
        details: dict[str, Any] = {
            "ok": False,
            "took_ms": int((time.monotonic() - start) * 1000),
            "error": f"{type(e).__name__}: {e}",
        }
        for attr in ("code", "status"):
            v = getattr(e, attr, None)
            if v is not None:
                details[f"exception_{attr}"] = v
        return details
    return {"ok": True, "took_ms": int((time.monotonic() - start) * 1000)}


def _probe_mistral() -> dict[str, Any]:
    """1-token embed_query round-trip. Budget-safe: cost ~$0 per probe.

    Late-imports embed module to keep health.py's import graph lean —
    embed pulls Mistral client + retry helpers that aren't needed for
    shallow liveness probes.
    """
    from .. import embed as embed_lib  # late import
    start = time.monotonic()
    if not os.environ.get("MISTRAL_API_KEY"):
        return {"ok": False, "took_ms": 0, "error": "MISTRAL_API_KEY not set"}
    try:
        v = embed_lib.embed_query("ok")
    except embed_lib.EmbedError as e:
        out: dict[str, Any] = {
            "ok": False,
            "took_ms": int((time.monotonic() - start) * 1000),
            "error": f"{e.code}: {e.message}",
        }
        # Surface details (e.g. retry_after_seconds, response body snippet)
        # so callers diagnosing 401/429 don't have to re-call.
        if e.details:
            out["details"] = e.details
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "took_ms": int((time.monotonic() - start) * 1000),
                "error": f"{type(e).__name__}: {e}"}
    return {
        "ok": True,
        "took_ms": int((time.monotonic() - start) * 1000),
        "model": embed_lib.MISTRAL_MODEL,
        "dims": len(v),
    }


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    start = time.monotonic()
    response: dict[str, Any] = {
        "ok": True,
        "version": SERVER_VERSION,
        "commit_sha": GIT_SHA,
        "brain_head_sha": None,  # Phase 4 wires real brain repo state
        "providers_available": embedding_providers(),
        "auth_methods_supported": auth_methods(),
    }
    if args.get("deep") is True:
        # Run probes serially (cheap; no need for parallelism overhead). Each
        # probe is self-contained and surfaces its own error; the top-level
        # `ok` flips False if ANY probe failed so callers can short-circuit.
        probes = {
            "kv": _probe_kv(),
            "blob": _probe_blob(),
            "mistral": _probe_mistral(),
        }
        response["probes"] = probes
        if not all(p["ok"] for p in probes.values()):
            response["ok"] = False
    response["took_ms"] = int((time.monotonic() - start) * 1000)
    return response
