"""Bearer auth middleware per SPEC-mcp.md § 6.1.

v1.0 reads `CE_MCP_BOOTSTRAP_TOKEN` from env, hashes on startup, compares constant-time.
v1.1 will read a hashed-token-map from KV (§ 6.1's `tokens:` namespace).

Roles per § 6.1:
  reader  — read tools, data_classification_max=internal
  writer  — reader + write tools, data_classification_max=confidential
  admin   — all + restricted

Bootstrap token is implicitly admin (so first deploy can self-administer).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TokenInfo:
    token_id: str
    role: str  # "reader" | "writer" | "admin"
    data_classification_max: str  # "public" | "internal" | "confidential" | "restricted"


_ROLE_CAPS = {
    "reader": "internal",
    "writer": "confidential",
    "admin": "restricted",
}


def _bootstrap_hash() -> Optional[str]:
    raw = os.environ.get("CE_MCP_BOOTSTRAP_TOKEN")
    if not raw:
        # Fail-closed warning at import. Vercel surfaces stderr to function logs.
        # If env is injected after import (e.g. via runtime config edit), the server
        # rejects all auth until the next cold start.
        import sys
        print(
            "[ce-mcp] WARN: CE_MCP_BOOTSTRAP_TOKEN unset at import — "
            "all Bearer auth will fail closed. Set the env and redeploy.",
            file=sys.stderr,
        )
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_BOOTSTRAP_SHA = _bootstrap_hash()


def authenticate(authorization_header: str | None) -> TokenInfo | None:
    """
    Validate an Authorization header. Returns TokenInfo on success, None on failure.

    Caller MUST treat None as UNAUTHENTICATED (§ 7.2 protocol error).
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return None
    token = authorization_header[len("Bearer "):].strip()
    if not token:
        return None

    if _BOOTSTRAP_SHA is None:
        # Server misconfigured — refuse all auth, do not allow open access.
        return None

    candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if hmac.compare_digest(candidate, _BOOTSTRAP_SHA):
        return TokenInfo(
            token_id="bootstrap",
            role="admin",
            data_classification_max=_ROLE_CAPS["admin"],
        )
    return None


def role_can_call(role: str, tool_name: str) -> bool:
    """
    Per § 6.1 role caps:
      reader  — all read tools
      writer  — reader + ce_upload_corpus, ce_index_github_repo
      admin   — all
    """
    if role == "admin":
        return True
    write_tools = {"ce_upload_corpus", "ce_index_github_repo"}
    if tool_name in write_tools:
        return role == "writer"
    return role in {"reader", "writer"}


def role_classification_max(role: str) -> str:
    return _ROLE_CAPS.get(role, "internal")
