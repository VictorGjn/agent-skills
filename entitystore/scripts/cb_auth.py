#!/usr/bin/env python3
"""
cb_auth — Bearer-token verification for company-brain MCP serving (M12).

Engine-only, zero HTTP/transport dependency: this module knows nothing about
FastMCP, sockets, or headers. It reads a token-map JSON file and exposes
verify_token() for server middleware to call once a Bearer token has been
extracted from a request; the middleware is responsible for then holding
`with cb_engine.request_cap(cap):` for the lifetime of that request — this
module never imports or touches cb_engine, and never sets a cap anywhere
itself.

Token-map file format (path from CB_AUTH_TOKENS_PATH env var):

    {
      "sha256:<hex of sha256(presented-token)>": {"role": "<role-name>"},
      "sha256:<hex of sha256(another-token)>":   {"role": "<role-name>"},
      "roles": {"<role-name>": "<cap-level>", ...}   // optional, see below
    }

"roles" is an OPTIONAL sibling key inside the same file, mapping each role
name to a company-brain classification cap level (public/internal/
confidential/restricted). When a file has no "roles" section, DEFAULT_ROLE_CAPS
is used: reader -> public, internal -> internal, analyst -> confidential,
admin -> restricted.

Security notes:
- Plaintext tokens are NEVER stored in the token-map file (only their SHA-256
  hash, as the map's key) and NEVER logged by this module.
- verify_token() hashes the presented token and compares it against each
  candidate key with hmac.compare_digest (constant-time), never `==`.
- Stdlib only: hashlib, hmac, json, os, pathlib. No new dependency.
- Fails closed: any missing/unreadable/malformed input (no env var, missing
  file, bad JSON, unknown role) returns None rather than raising, so a
  caller can't accidentally treat an auth error as "let it through".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib

# role -> classification cap, used only when the token-map file has no
# "roles" section of its own.
DEFAULT_ROLE_CAPS: dict[str, str] = {
    "reader": "public",
    "internal": "internal",
    "analyst": "confidential",
    "admin": "restricted",
}

_TOKEN_KEY_PREFIX = "sha256:"


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of a presented token, in the token-map file's key
    format ('sha256:<hex>'). Never logged, never returned to a caller."""
    return _TOKEN_KEY_PREFIX + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_token_map(path: str | os.PathLike) -> dict | None:
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_role_caps(token_map: dict) -> dict[str, str]:
    """Role -> cap mapping: the token-map file's own "roles" section if
    present and non-empty, else DEFAULT_ROLE_CAPS."""
    roles = token_map.get("roles")
    if isinstance(roles, dict) and roles:
        return roles
    return dict(DEFAULT_ROLE_CAPS)


def verify_token(
    presented: str | None,
    tokens_path: str | os.PathLike | None = None,
) -> tuple[str, str] | None:
    """Verify a presented Bearer token against the CB_AUTH_TOKENS_PATH token
    map. Returns (role, cap) on success, else None — never raises on a bad
    or missing token/file, so callers can treat None uniformly as
    "unauthenticated, fall back to the most restrictive behavior".

    `tokens_path` overrides CB_AUTH_TOKENS_PATH for tests; production
    callers should leave it unset and rely on the env var.
    """
    if not presented:
        return None

    path = tokens_path if tokens_path is not None else os.environ.get("CB_AUTH_TOKENS_PATH")
    if not path:
        return None

    token_map = _load_token_map(path)
    if token_map is None:
        return None

    presented_hash = _hash_token(presented)
    role_caps = load_role_caps(token_map)

    matched_role = None
    for key, entry in token_map.items():
        if key == "roles" or not isinstance(key, str) or not key.startswith(_TOKEN_KEY_PREFIX):
            continue
        # Constant-time compare against every candidate key — never `==`,
        # which leaks timing information proportional to the matching
        # prefix length.
        if hmac.compare_digest(key, presented_hash) and isinstance(entry, dict):
            matched_role = entry.get("role")
            break

    if not isinstance(matched_role, str) or not matched_role:
        return None

    cap = role_caps.get(matched_role)
    if cap is None:
        return None
    return (matched_role, cap)
