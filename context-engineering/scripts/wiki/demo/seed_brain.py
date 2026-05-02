"""Generate a synthetic ~50-entity brain across three Anabasis use cases.

Three scopes:
    default              ~15 entities — code-context, slow-decay (90d half-life)
    competitive-intel    ~20 entities — web-source, fast-decay (30d half-life),
                         includes a superseded-decision chain for AC3
    lead-qual            ~15 entities — mixed sources, decision pages

Total events emitted: ~120 (multiple events per entity to exercise
consolidation). Some entities are deliberately seeded with old
last_verified_at so the freshness rule fires on cue.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable


# Fixed seed so demo runs are deterministic.
_NOW_TS = 1714608000  # 2024-05-02T00:00:00Z baseline
_DAY = 86400


def _events_for_default(events_dir: Path, *, append_event) -> int:
    """~15 default-scope entities, code-source, fresh."""
    fresh_ts = _NOW_TS  # all just verified
    entities = [
        ("auth-middleware", "JWT verifier middleware mounted at /api/*."),
        ("token-store", "Refresh tokens persisted in Redis with 7d TTL."),
        ("session-policy", "Sessions invalidate on logout + password change."),
        ("rate-limiter", "Sliding-window 100 req/min per token."),
        ("cors-policy", "Allowed origins read from CORS_ALLOW env."),
        ("error-middleware", "Maps thrown errors to JSON-RPC error codes."),
        ("logger-service", "Structured logging via pino; correlation-id propagation."),
        ("config-loader", "Reads .env then env then defaults."),
        ("health-route", "GET /health; returns 200 + version."),
        ("metrics-route", "GET /metrics; Prometheus scrape format."),
        ("user-model", "Sequelize model for users; email is unique."),
        ("payment-service", "Stripe wrapper; idempotency keys on every charge."),
        ("notification-bus", "Redis pub-sub fan-out for app events."),
        ("queue-worker", "BullMQ worker pool, autoscale by queue depth."),
        ("schema-registry", "Centralized Joi schemas for all API request bodies."),
    ]
    n = 0
    for hint, claim in entities:
        append_event(
            events_dir,
            source_type="code",
            source_ref=f"src/{hint.replace('-', '/')}.ts:12",
            file_id=f"sha-{hint[:8]}",
            claim=claim,
            entity_hint=hint,
            ts=fresh_ts,
        )
        n += 1
    return n


def _events_for_competitive_intel(events_dir: Path, *, append_event) -> int:
    """~20 entities, web-source. Includes:
       - 3 stale entities (last_verified_at 60+ days ago) for AC4
       - A superseded decision chain for AC3
    """
    n = 0
    fresh_ts = _NOW_TS
    stale_ts = _NOW_TS - 60 * _DAY  # 60 days ago — past 30d half-life

    # Fresh competitive-intel entities
    fresh = [
        ("acme-pricing-q2", "Acme tier 2 raised from $5k to $7k.", fresh_ts),
        ("competitor-b-launch", "Competitor B launched feature X.", fresh_ts),
        ("competitor-c-acquired", "Competitor C acquired by megacorp.", fresh_ts),
        ("acme-team-reorg", "Acme reorganized eng into 3 squads.", fresh_ts),
        ("market-segment-shift", "Mid-market segment growing 15% qoq.", fresh_ts),
        ("competitor-d-pricing", "Competitor D added freemium tier.", fresh_ts),
        ("acme-customer-list", "Acme published top-10 customer logos.", fresh_ts),
        ("acme-roadmap-leak", "Acme roadmap surfaced via job posting.", fresh_ts),
    ]
    for hint, claim, ts in fresh:
        append_event(
            events_dir,
            source_type="web",
            source_ref=f"https://acme.example/{hint}",
            file_id=f"web-{hint[:8]}",
            claim=claim,
            entity_hint=hint,
            ts=ts,
        )
        n += 1

    # Stale entities (will trigger AC4 freshness-expired)
    stale = [
        ("acme-old-pricing", "Acme tier 2 was $5k.", stale_ts),
        ("competitor-z-feature", "Competitor Z launched feature Y back then.", stale_ts),
        ("market-segment-old", "Mid-market segment was 8% qoq.", stale_ts),
    ]
    for hint, claim, ts in stale:
        append_event(
            events_dir,
            source_type="web",
            source_ref=f"https://acme.example/{hint}",
            file_id=f"web-{hint[:8]}",
            claim=claim,
            entity_hint=hint,
            ts=ts,
        )
        n += 1

    # Decision-chain setup for AC3 (stale supersession).
    # decision-acme-pricing-v1 is superseded; entity acme-rate-card still
    # references the v1 in its claim/body.
    append_event(
        events_dir,
        source_type="rfc",
        source_ref="docs/decisions/acme-pricing-v1.md",
        file_id="dec-acme-v1",
        claim="Acme pricing decision v1: tier-2 at $5k.",
        entity_hint="decision-acme-pricing-v1",
        ts=fresh_ts - 90 * _DAY,
    )
    append_event(
        events_dir,
        source_type="rfc",
        source_ref="docs/decisions/acme-pricing-v2.md",
        file_id="dec-acme-v2",
        claim="Acme pricing decision v2: tier-2 raised to $7k.",
        entity_hint="decision-acme-pricing-v2",
        ts=fresh_ts,
    )
    append_event(
        events_dir,
        source_type="web",
        source_ref="https://acme.example/rate-card",
        file_id="acme-rate-card",
        claim="Per [[decision-acme-pricing-v1]], tier-2 was $5k. See rate card.",
        entity_hint="acme-rate-card",
        ts=fresh_ts,
    )
    n += 3

    return n


def _events_for_lead_qual(events_dir: Path, *, append_event) -> int:
    """~15 lead-qualification entities, mixed sources."""
    n = 0
    fresh_ts = _NOW_TS
    leads = [
        ("lead-acme-corp", "Acme Corp interested in tier-3.", "email"),
        ("lead-beta-co", "Beta Co requested demo.", "email"),
        ("lead-gamma-inc", "Gamma Inc evaluating against Competitor B.", "transcript"),
        ("lead-delta-ltd", "Delta Ltd negotiating discount.", "email"),
        ("lead-epsilon-gmbh", "Epsilon GmbH POC in progress.", "transcript"),
        ("lead-zeta-llc", "Zeta LLC referred by Acme.", "email"),
        ("lead-eta-sa", "Eta SA scheduled discovery call.", "email"),
        ("lead-theta-bv", "Theta BV interested in enterprise tier.", "transcript"),
        ("decision-acme-tier", "Decision: offer Acme tier-3 at 20% discount."),
        ("decision-beta-poc", "Decision: 30-day POC with Beta Co."),
        ("contact-acme-cto", "Acme CTO is the key technical evaluator."),
        ("contact-beta-vp", "Beta Co VP eng is the budget owner."),
        ("opportunity-acme", "Acme deal value $84k ARR estimated."),
        ("opportunity-beta", "Beta Co deal value $30k ARR estimated."),
        ("playbook-discovery", "Discovery call: 5 qualifying questions."),
    ]
    for entry in leads:
        if len(entry) == 3:
            hint, claim, source_type = entry
        else:
            hint, claim = entry
            source_type = "notion"
        append_event(
            events_dir,
            source_type=source_type,
            source_ref=f"crm/lead-qual/{hint}",
            file_id=f"crm-{hint[:8]}",
            claim=claim,
            entity_hint=hint,
            ts=fresh_ts,
        )
        n += 1
    return n


def seed(brain_dir: Path) -> dict[str, int]:
    """Seed the brain with synthetic events. Returns counts per scope."""
    # Late import so the module loads cleanly even when wiki.* siblings
    # haven't been imported yet (avoids cycles when running as a script).
    from wiki.events import append_event

    events_dir = brain_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    counts = {
        "default": _events_for_default(events_dir, append_event=append_event),
        "competitive-intel": _events_for_competitive_intel(events_dir, append_event=append_event),
        "lead-qual": _events_for_lead_qual(events_dir, append_event=append_event),
    }
    return counts


if __name__ == "__main__":
    import sys
    import argparse
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    parser = argparse.ArgumentParser(description="Seed a 50-entity demo brain")
    parser.add_argument("--brain", required=True, type=Path)
    args = parser.parse_args()

    counts = seed(args.brain)
    total = sum(counts.values())
    print(f"Seeded {total} events: {counts}")
