"""Per-source-type half-life policy for computed-on-read freshness.

CE deliberately stores no `freshness_score` field on entity pages — only
`last_verified_at`. Callers compute freshness at query time using the
policy in this module.

Spec: ``plan/phases/phase-1.md`` §1.2.2.

Decay formula (linear over 2× half-life, clamped to [0, 1]):

    freshness_score = max(0.0, 1.0 - elapsed_days / (2 × half_life_days))

At t=0: 1.0. At t=half_life: 0.5. At t=2×half_life: 0.0. Beyond: 0.0.

Multi-source entities: callers should use the **shortest** half-life
across the entity's `sources[]` — the entity is only as fresh as its
fastest-decaying source. ``shortest_half_life()`` below is the helper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

# Per-source-type half-life in days. The dict is THE spec — adding a row
# in phase-1.md §1.2.2 means adding a key here.
HALF_LIVES: dict[str, int] = {
    "code": 90,
    "web": 30,
    "transcript": 60,
    "email": 21,
    "notion": 60,
    "rfc": 180,
    "department-spec": 180,
    "default": 60,
}


def half_life_days(source_type: str) -> int:
    """Return the half-life in days for a source type, falling back to default."""
    return HALF_LIVES.get(source_type, HALF_LIVES["default"])


def shortest_half_life(source_types: Iterable[str]) -> int:
    """Return the shortest half-life across an entity's source types.

    The entity is only as fresh as its fastest-decaying source — a fresh
    web page paired with an ancient code reference should be flagged as
    stale, not as fresh-via-the-young-source.
    """
    materialized = list(source_types)
    if not materialized:
        return HALF_LIVES["default"]
    return min(half_life_days(st) for st in materialized)


def compute_freshness(
    last_verified_at: str | datetime,
    source_type: str,
    *,
    now: Optional[datetime] = None,
) -> float:
    """Compute freshness in [0.0, 1.0] from last_verified_at + source-type half-life.

    Args:
        last_verified_at: ISO 8601 string (e.g. "2026-05-01T10:23:45Z") or
            a timezone-aware datetime. Naive datetimes are interpreted as
            UTC.
        source_type: key into HALF_LIVES; falls back to "default".
        now: override for the current time (testability).

    Returns:
        freshness_score in [0.0, 1.0]. 1.0 means fresh; 0.0 means decayed
        beyond 2 × half_life and should be flagged by the Auditor's
        "freshness expired" rule (combined with the elapsed > half_life
        guard — see §1.2.2).

    Raises:
        ValueError: if last_verified_at can't be parsed as ISO 8601.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if isinstance(last_verified_at, datetime):
        verified = last_verified_at
    else:
        verified = _parse_iso8601(last_verified_at)

    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)

    elapsed_days = (now - verified).total_seconds() / 86400.0
    if elapsed_days < 0:
        # last_verified_at is in the future — clamp to fresh rather than
        # over 1.0 (a clock-skew situation shouldn't bias freshness up).
        return 1.0

    half_life = half_life_days(source_type)
    score = 1.0 - elapsed_days / (2.0 * half_life)
    return max(0.0, min(1.0, score))


def compute_freshness_multi_source(
    last_verified_at: str | datetime,
    source_types: Iterable[str],
    *,
    now: Optional[datetime] = None,
) -> float:
    """Multi-source variant: uses the SHORTEST half-life across sources.

    This is the canonical entry point for entities with heterogeneous
    `sources[]`. See module docstring for rationale.
    """
    types = list(source_types)
    if not types:
        return compute_freshness(last_verified_at, "default", now=now)
    # Pick the source type with the shortest half-life and compute against it.
    governing = min(types, key=half_life_days)
    return compute_freshness(last_verified_at, governing, now=now)


def _parse_iso8601(s: str) -> datetime:
    """Parse an ISO 8601 timestamp string, accepting common variants.

    Handles:
      - "2026-05-01T10:23:45Z" (Z suffix)
      - "2026-05-01T10:23:45+00:00" (offset)
      - "2026-05-01T10:23:45" (naive — interpreted as UTC by caller)
      - "2026-05-01" (date only — midnight UTC)
    """
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(
            f"freshness_policy: cannot parse last_verified_at={s!r} as ISO 8601"
        ) from e
