"""ce_get_health — § 3.6.

Liveness + version + provider availability + auth methods.
Never errors when reachable.
"""
from __future__ import annotations

import os
import time
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


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    start = time.time()
    return {
        "ok": True,
        "version": SERVER_VERSION,
        "commit_sha": GIT_SHA,
        "brain_head_sha": None,  # Phase 4 wires real brain repo state
        "providers_available": embedding_providers(),
        "auth_methods_supported": auth_methods(),
        "took_ms": int((time.time() - start) * 1000),
    }
