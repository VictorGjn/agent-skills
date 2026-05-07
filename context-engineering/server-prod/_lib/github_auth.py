"""GitHub auth resolver — App-first, PAT fallback.

Returns a bearer string suitable for `Authorization: Bearer {token}` against
the GitHub REST/Contents APIs.

Resolution order:

1. **GitHub App** (preferred): when `GH_APP_ID`, `GH_APP_INSTALLATION_ID`,
   and `GH_APP_PRIVATE_KEY` are all set, mint a short-lived App JWT signed
   with the private key, exchange it for an installation access token, and
   cache the result in-process until ~5 min before expiry.

   Why preferred:
   - Installation tokens get a fresh **5000 req/hour** budget, decoupled
     from any single user account. The bench previously inherited the
     operator's exhausted PAT budget.
   - Tokens auto-refresh on a 1-hour cadence — no "expired PAT" surprises.
   - Per-repo install scope possible (we currently install on a personal
     account with `repository_selection=all`; tightening is a future fix).

2. **Personal access token** (legacy fallback): `GITHUB_TOKEN` or `GH_TOKEN`.
   Kept so a partial App misconfig (e.g. revoked install) doesn't take
   indexing down when a working PAT is also set.

Returns `None` when neither path is configured. The vendored indexer's
`github_get` then runs unauthenticated (60 req/hr — public repos only).

This module deliberately stays stdlib + PyJWT (no `requests`, no
`Github` SDK) to keep the Vercel cold-start small. PyJWT with `crypto`
extras pulls `cryptography` (~15 MB wheel); that's the bulk of the
new dependency cost.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional


# In-process cache. Vercel function instances live for tens of minutes
# between cold starts — caching here amortizes the JWT-mint round-trip
# across every indexer call within the same instance lifetime.
_INSTALL_TOKEN_CACHE: dict[str, float | str] | None = None

# Re-mint when the cached token has < this much life left, so a long-running
# indexer call doesn't get a stale token mid-fetch.
_REFRESH_SLACK_S = 5 * 60


def _now() -> float:
    return time.time()


def _normalize_pem(raw: str) -> str:
    """Vercel multi-line env vars sometimes round-trip with literal `\\n`
    escape sequences instead of real newlines. Both forms should work."""
    if "\\n" in raw and "\n" not in raw:
        return raw.replace("\\n", "\n")
    return raw


def _mint_app_jwt(app_id: str, pem: str) -> str:
    """RS256 JWT with a 9-minute exp window (under GitHub's 10-min cap).

    iat is set 30s in the past to absorb modest clock skew between the
    Vercel function clock and GitHub's clock — GitHub rejects JWTs with
    `iat` in the future.
    """
    import jwt  # PyJWT — pulled by `pyjwt[crypto]` in requirements.txt
    now = int(_now())
    return jwt.encode(
        {"iat": now - 30, "exp": now + 9 * 60, "iss": str(app_id)},
        _normalize_pem(pem),
        algorithm="RS256",
    )


def _exchange_for_install_token(app_jwt: str, install_id: str,
                                 timeout_s: int = 20) -> tuple[str, float]:
    """POST /app/installations/{id}/access_tokens — returns (token, unix_expires_at)."""
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{install_id}/access_tokens",
        method="POST",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        body = json.loads(r.read().decode("utf-8") or "{}")
    # GitHub returns ISO-8601 with a literal "Z"; fromisoformat needs "+00:00".
    expires_at = datetime.fromisoformat(
        body["expires_at"].replace("Z", "+00:00")
    ).timestamp()
    return body["token"], expires_at


def resolve_github_token() -> Optional[str]:
    """Return a token string, or None if no auth is configured.

    Reads env vars on every call (cheap), but caches the minted installation
    token in-process so concurrent indexer calls within the same Vercel
    function instance share one round-trip per hour.
    """
    global _INSTALL_TOKEN_CACHE

    app_id = os.environ.get("GH_APP_ID")
    install_id = os.environ.get("GH_APP_INSTALLATION_ID")
    pem = os.environ.get("GH_APP_PRIVATE_KEY")

    if app_id and install_id and pem:
        if (
            _INSTALL_TOKEN_CACHE is not None
            and isinstance(_INSTALL_TOKEN_CACHE.get("expires_at"), float)
            and _INSTALL_TOKEN_CACHE["expires_at"] - _now() > _REFRESH_SLACK_S
        ):
            return _INSTALL_TOKEN_CACHE["token"]  # type: ignore[return-value]
        try:
            jwt_token = _mint_app_jwt(app_id, pem)
            tok, exp = _exchange_for_install_token(jwt_token, install_id)
            _INSTALL_TOKEN_CACHE = {"token": tok, "expires_at": exp}
            return tok
        except Exception:
            # Refresh failed. If we have a cached token that's still
            # actually valid (under the 5-min slack window but not yet
            # past its real expiry), keep using it — better than falling
            # through to None / PAT and hitting SOURCE_FORBIDDEN on a
            # transient GH-side blip. Codex P1 round-5 on PR #60.
            if (
                _INSTALL_TOKEN_CACHE is not None
                and isinstance(_INSTALL_TOKEN_CACHE.get("expires_at"), float)
                and _INSTALL_TOKEN_CACHE["expires_at"] > _now()
            ):
                return _INSTALL_TOKEN_CACHE["token"]  # type: ignore[return-value]
            # Cache is empty or genuinely expired — fall through to PAT
            # so a partial App misconfig doesn't take indexing down when
            # GITHUB_TOKEN is also set.
            pass

    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _reset_cache_for_tests() -> None:
    """Test-only hook: clear the in-process install-token cache."""
    global _INSTALL_TOKEN_CACHE
    _INSTALL_TOKEN_CACHE = None
