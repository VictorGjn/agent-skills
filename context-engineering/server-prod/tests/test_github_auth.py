"""Tests for _lib/github_auth.py.

The GitHub round-trip is mocked; we never touch the real API in these tests.
PyJWT + cryptography ARE imported (real RS256 sign/verify) so the JWT shape
matches what GitHub will actually accept.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from _lib import github_auth  # noqa: E402


def _generate_test_pem() -> str:
    """Generate a real 2048-bit RSA private key in PEM (PKCS#8, unencrypted)
    so PyJWT's RS256 sign + cryptography's load_pem_private_key both accept it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


# Module-scoped: generated once per test session (key gen is ~50ms).
_TEST_PEM = _generate_test_pem()


@pytest.fixture(autouse=True)
def _clean_env_and_cache(monkeypatch):
    """Each test starts with no env vars + no cached install token."""
    for var in ("GH_APP_ID", "GH_APP_INSTALLATION_ID", "GH_APP_PRIVATE_KEY",
                "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    github_auth._reset_cache_for_tests()
    yield
    github_auth._reset_cache_for_tests()


# ── Resolution order ──────────────────────────────────────────────────────────

def test_returns_none_when_nothing_configured():
    assert github_auth.resolve_github_token() is None


def test_returns_pat_when_only_pat_set(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pat_value")
    assert github_auth.resolve_github_token() == "ghp_pat_value"


def test_gh_token_alias_works(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_alias_value")
    assert github_auth.resolve_github_token() == "ghp_alias_value"


def test_app_path_wins_over_pat(monkeypatch):
    """App fully configured + PAT set → App token returned, PAT untouched."""
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GH_APP_PRIVATE_KEY", _TEST_PEM)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_not_be_used")

    with mock.patch.object(
        github_auth, "_exchange_for_install_token",
        return_value=("ghs_install_token_xyz", time.time() + 3600),
    ) as exch:
        out = github_auth.resolve_github_token()
    assert out == "ghs_install_token_xyz"
    assert exch.call_count == 1


# ── Caching ───────────────────────────────────────────────────────────────────

def test_cache_reuse_within_validity(monkeypatch):
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GH_APP_PRIVATE_KEY", _TEST_PEM)

    with mock.patch.object(
        github_auth, "_exchange_for_install_token",
        return_value=("ghs_first_token", time.time() + 3600),
    ) as exch:
        a = github_auth.resolve_github_token()
        b = github_auth.resolve_github_token()
        c = github_auth.resolve_github_token()
    assert a == b == c == "ghs_first_token"
    assert exch.call_count == 1, "cached install token should be reused"


def test_cache_refreshes_when_within_slack_window(monkeypatch):
    """When the cached token has < _REFRESH_SLACK_S life left, re-mint."""
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GH_APP_PRIVATE_KEY", _TEST_PEM)

    fresh_expiry = time.time() + 3600
    near_expiry = time.time() + 60  # under the 5-min slack — must refresh
    next_expiry = time.time() + 3600

    call_log: list[float] = []

    def _fake_exchange(jwt_str, install_id, timeout_s=20):
        call_log.append(time.time())
        n = len(call_log)
        if n == 1:
            return ("first", near_expiry)
        return ("second", next_expiry)

    with mock.patch.object(github_auth, "_exchange_for_install_token",
                           side_effect=_fake_exchange):
        a = github_auth.resolve_github_token()  # mints "first"
        b = github_auth.resolve_github_token()  # forced refresh -> "second"
    assert a == "first"
    assert b == "second"
    assert len(call_log) == 2


# ── Failure handling ──────────────────────────────────────────────────────────

def test_app_mint_failure_falls_back_to_pat(monkeypatch):
    """Mint exception → PAT path takes over; no None return when PAT is set."""
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GH_APP_PRIVATE_KEY", _TEST_PEM)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_safety_net")

    with mock.patch.object(
        github_auth, "_exchange_for_install_token",
        side_effect=RuntimeError("github 503"),
    ):
        out = github_auth.resolve_github_token()
    assert out == "ghp_safety_net"


def test_app_mint_failure_does_not_poison_cache(monkeypatch):
    """A failed mint must NOT cache None — the next attempt should retry."""
    monkeypatch.setenv("GH_APP_ID", "12345")
    monkeypatch.setenv("GH_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GH_APP_PRIVATE_KEY", _TEST_PEM)

    calls = {"n": 0}

    def _fake(jwt_str, install_id, timeout_s=20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return ("recovered", time.time() + 3600)

    with mock.patch.object(github_auth, "_exchange_for_install_token",
                           side_effect=_fake):
        a = github_auth.resolve_github_token()  # fail, returns None (no PAT)
        b = github_auth.resolve_github_token()  # retry, succeeds
    assert a is None
    assert b == "recovered"
    assert calls["n"] == 2


# ── PEM normalization ─────────────────────────────────────────────────────────

def test_pem_with_literal_backslash_n_is_normalized(monkeypatch):
    """Vercel UI sometimes round-trips PEMs with literal `\\n` instead of
    real newlines. The normalizer should turn it back into a usable PEM."""
    escaped_pem = _TEST_PEM.replace("\n", "\\n")
    assert "\n" not in escaped_pem
    out = github_auth._normalize_pem(escaped_pem)
    assert out == _TEST_PEM


def test_pem_with_real_newlines_is_unchanged():
    out = github_auth._normalize_pem(_TEST_PEM)
    assert out == _TEST_PEM


# ── JWT signing smoke ─────────────────────────────────────────────────────────

def test_mint_app_jwt_produces_decodable_rs256_token():
    """Sanity: the JWT we mint round-trips through PyJWT verify with the
    same key. Catches PEM parse / claim shape regressions."""
    import jwt
    from cryptography.hazmat.primitives import serialization

    token = github_auth._mint_app_jwt("12345", _TEST_PEM)

    # Public key from the same private key for verify
    private = serialization.load_pem_private_key(
        _TEST_PEM.encode("utf-8"), password=None,
    )
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert decoded["iss"] == "12345"
    assert "iat" in decoded and "exp" in decoded
    assert decoded["exp"] > decoded["iat"]
