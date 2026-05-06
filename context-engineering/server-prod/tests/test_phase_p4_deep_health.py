"""Phase P4.2 tests — deeper ce_get_health probes.

Covers:
- Backwards compat: shallow `ce_get_health()` unchanged shape; the response
  doesn't include `probes` unless the caller passes `deep=True`.
- `deep=True` runs all three probes (kv, blob, mistral) and reports per-
  backend ok / took_ms.
- Each probe surfaces real exception messages on failure (no swallowing).
- Top-level `ok` flips False when any probe fails.
- Probes degrade gracefully when env vars are missing — no exception thrown
  through the catch-all.

Run: CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -xvs tests/test_phase_p4_deep_health.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))
os.environ.setdefault("CE_MCP_BOOTSTRAP_TOKEN", "test-token")

from _lib.tools import health  # noqa: E402
from _lib.auth import TokenInfo  # noqa: E402


_TOKEN = TokenInfo(token_id="test", role="writer", data_classification_max="internal")


# ── Backwards compat ─────────────────────────────────────────────────────────

def test_shallow_health_unchanged_shape():
    """No `deep` arg → no `probes` field. v1.0 shape preserved."""
    out = health.handle({}, _TOKEN)
    assert out["ok"] is True
    assert "probes" not in out
    assert {"version", "commit_sha", "providers_available", "auth_methods_supported", "took_ms"} <= set(out)


def test_shallow_health_when_deep_false_explicit():
    """deep=False also skips probes."""
    out = health.handle({"deep": False}, _TOKEN)
    assert "probes" not in out


# ── deep=True triggers probes ────────────────────────────────────────────────

def test_deep_health_runs_all_three_probes(monkeypatch):
    """deep=True calls kv/blob/mistral probes and includes per-backend status."""
    monkeypatch.setattr(health, "_probe_kv", lambda: {"ok": True, "took_ms": 10})
    monkeypatch.setattr(health, "_probe_blob", lambda: {"ok": True, "took_ms": 20})
    monkeypatch.setattr(health, "_probe_mistral", lambda: {"ok": True, "took_ms": 30, "model": "codestral-embed", "dims": 1536})
    out = health.handle({"deep": True}, _TOKEN)
    assert out["ok"] is True
    assert set(out["probes"]) == {"kv", "blob", "mistral"}
    assert out["probes"]["kv"]["took_ms"] == 10
    assert out["probes"]["mistral"]["dims"] == 1536


def test_deep_health_overall_ok_false_when_any_probe_fails(monkeypatch):
    """A single probe failure flips top-level ok → False so callers can short-circuit."""
    monkeypatch.setattr(health, "_probe_kv", lambda: {"ok": True, "took_ms": 10})
    monkeypatch.setattr(health, "_probe_blob", lambda: {"ok": False, "took_ms": 50, "error": "BLOB_400: ..."})
    monkeypatch.setattr(health, "_probe_mistral", lambda: {"ok": True, "took_ms": 30, "model": "x", "dims": 1536})
    out = health.handle({"deep": True}, _TOKEN)
    assert out["ok"] is False
    assert out["probes"]["blob"]["ok"] is False
    assert "BLOB_400" in out["probes"]["blob"]["error"]


# ── Probe internals: missing env handled gracefully ──────────────────────────

def test_probe_kv_missing_env_returns_error_not_raise(monkeypatch):
    """No env vars → ok:false with explanation; doesn't raise."""
    monkeypatch.delenv("KV_REST_API_URL", raising=False)
    monkeypatch.delenv("KV_REST_API_TOKEN", raising=False)
    out = health._probe_kv()
    assert out["ok"] is False
    assert "KV_REST_API" in out["error"]


def test_probe_blob_missing_env_returns_error_not_raise(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    out = health._probe_blob()
    assert out["ok"] is False
    assert "BLOB_READ_WRITE_TOKEN" in out["error"]


def test_probe_mistral_missing_env_returns_error_not_raise(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    out = health._probe_mistral()
    assert out["ok"] is False
    assert "MISTRAL_API_KEY" in out["error"]


# ── Probe internals: surface real exception details ──────────────────────────

def test_probe_kv_surfaces_exception_message(monkeypatch):
    """When kv.set raises, the probe returns the type + message, not just type."""
    monkeypatch.setenv("KV_REST_API_URL", "http://stub")
    monkeypatch.setenv("KV_REST_API_TOKEN", "stub")
    from _lib.storage import kv as kv_module
    def _raise(*a, **kw):
        raise RuntimeError("upstash unreachable")
    monkeypatch.setattr(kv_module, "set", _raise)
    out = health._probe_kv()
    assert out["ok"] is False
    assert "RuntimeError" in out["error"]
    assert "upstash unreachable" in out["error"]


def test_probe_blob_surfaces_exception_with_status(monkeypatch):
    """BlobError carries .code and .status — probe should expose them."""
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "stub")
    from _lib.storage import blob as blob_module

    class _StubBackend:
        def put_bytes(self, key, body):
            err = blob_module.BlobError("BLOB_400", "Cannot use private access on a public store", status=400)
            raise err
        def get_bytes(self, key): raise AssertionError("should not get past put_bytes")
        def delete(self, key): pass

    monkeypatch.setattr(blob_module, "BlobBackend", _StubBackend)
    out = health._probe_blob()
    assert out["ok"] is False
    # BlobError's str() is the message, not the code; the code surfaces via
    # the exception_code/exception_status attrs the probe lifts off the exception.
    assert "Cannot use private access" in out["error"]
    assert out.get("exception_code") == "BLOB_400"
    assert out.get("exception_status") == 400


def test_probe_mistral_surfaces_embed_error(monkeypatch):
    """EmbedError.code + .message both surface in the error string."""
    monkeypatch.setenv("MISTRAL_API_KEY", "stub")
    from _lib import embed as embed_module
    def _raise(*a, **kw):
        raise embed_module.EmbedError("EMBED_HTTP", "401 Unauthorized: bad key")
    monkeypatch.setattr(embed_module, "embed_query", _raise)
    out = health._probe_mistral()
    assert out["ok"] is False
    assert "EMBED_HTTP" in out["error"]
    assert "Unauthorized" in out["error"]


# ── Probes write transient state, no leak on success ────────────────────────

def test_probe_kv_cleans_up_after_success(monkeypatch):
    """kv probe deletes its key after read-back succeeds."""
    monkeypatch.setenv("KV_REST_API_URL", "http://stub")
    monkeypatch.setenv("KV_REST_API_TOKEN", "stub")
    from _lib.storage import kv as kv_module

    state: dict[str, str] = {}
    deleted: list[str] = []

    def _set(k, v, *, ex_seconds=None, nx=False):
        state[k] = v
        return True

    def _get(k):
        return state.get(k)

    def _delete(k):
        deleted.append(k)
        state.pop(k, None)
        return 1

    monkeypatch.setattr(kv_module, "set", _set)
    monkeypatch.setattr(kv_module, "get", _get)
    monkeypatch.setattr(kv_module, "delete", _delete)

    out = health._probe_kv()
    assert out["ok"] is True
    assert len(deleted) == 1
    assert deleted[0].startswith("health:probe:")
    assert state == {}  # nothing left behind
