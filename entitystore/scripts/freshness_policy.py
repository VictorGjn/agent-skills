#!/usr/bin/env python3
"""freshness_policy.py — computed-on-read freshness for entitystore (M11).

entitystore stores no `freshness_score` field anywhere (company-brain
prohibits stored scores/trust/tier/reputation fields — see
company-brain/CLAUDE.md "THE WRITER RULE"). Freshness is ALWAYS derived at
read time from the M4-written `last_verified_at` field. This module is the
single place that derivation happens; callers (wiki_init's frontmatter,
cb_engine's wiki_audit lint, wiki_ask stats) import it rather than
recomputing the decay curve independently.

Coverage reality (per BRAIN-DELIVERY-TRACK M4/M11): `last_verified_at` is
sparsely populated — enrich-pass only started stamping it recently, and
whole kinds (person, vessel) currently sit at 0% coverage. An entity with
no `last_verified_at` is NOT stale (0.0) and NOT an error — it predates the
freshness rule entirely. `status="pre-rule, never verified"` says exactly
that, with `score=None`, so a null-coverage kind doesn't read as "all
content expired."

Decay formula (linear over 2x half-life, clamped to [0, 1]), same shape as
context-engineering's per-source-type policy, but keyed by entity `kind`
instead of source type — this store's staleness signal is "how long since
enrich-pass re-derived this entity", not "how long since a particular
source was touched":

    score = max(0.0, 1.0 - elapsed_days / (2 * half_life_days))

At t=0: 1.0. At t=half_life: 0.5. At t=2*half_life: 0.0. Beyond: 0.0.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Per-kind half-life in days. Mirrors cb_engine.FRESHNESS_THRESHOLD_DAYS
# (the existing updated_at-based auditor threshold) so the two freshness
# signals — "last mutated" (updated_at, cb_engine's existing check) and
# "last re-derived from raw" (last_verified_at, this module) — read on the
# same clock even though they answer different questions. Unlisted kinds
# fall back to "default".
HALF_LIVES: dict[str, int] = {
    "post": 90,
    "concept": 365,
    "org": 180,
    "person": 180,
    "vessel": 180,
    "navigation": 90,
    "product": 365,
    "default": 180,
}

# Score below this is "stale" for the audit lint. Score is None (pre-rule)
# is a DIFFERENT bucket, never folded into "stale" — see module docstring.
STALE_FLOOR = 0.3


def half_life_days(kind: str | None) -> int:
    return HALF_LIVES.get(kind or "", HALF_LIVES["default"])


def _parse_iso8601(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(
            f"freshness_policy: cannot parse last_verified_at={s!r} as ISO 8601"
        ) from e


def compute_freshness(
    last_verified_at: str | None,
    kind: str | None,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Compute freshness for one entity. NEVER raises on a missing/malformed
    timestamp — that is the common, expected case (pre-rule coverage), not
    an error path.

    Returns:
        {
          "score": float | None,      # None = pre-rule, never computed
          "status": str,              # "pre-rule, never verified" | "fresh" | "aging" | "stale"
          "half_life_days": int,
          "elapsed_days": float | None,
        }
    """
    half_life = half_life_days(kind)

    if not last_verified_at:
        return {
            "score": None,
            "status": "pre-rule, never verified",
            "half_life_days": half_life,
            "elapsed_days": None,
        }

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    try:
        verified = _parse_iso8601(last_verified_at)
    except ValueError:
        # Malformed timestamp is a data-quality issue, not a "never verified"
        # one — surface it distinctly rather than silently treating it as
        # pre-rule (which would hide the bad write from the audit).
        return {
            "score": None,
            "status": "invalid last_verified_at",
            "half_life_days": half_life,
            "elapsed_days": None,
        }
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)

    elapsed_days = (now - verified).total_seconds() / 86400.0
    if elapsed_days < 0:
        # Clock skew: don't reward a future timestamp with score > 1.0.
        elapsed_days = 0.0

    score = max(0.0, min(1.0, 1.0 - elapsed_days / (2.0 * half_life)))
    if score < STALE_FLOOR:
        status = "stale"
    elif score < 0.7:
        status = "aging"
    else:
        status = "fresh"

    return {
        "score": round(score, 3),
        "status": status,
        "half_life_days": half_life,
        "elapsed_days": round(elapsed_days, 1),
    }
