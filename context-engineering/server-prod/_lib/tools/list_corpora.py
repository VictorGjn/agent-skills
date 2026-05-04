"""ce_list_corpora — § 3.5.

Discoverability. Returns corpora visible to the caller, with metadata.
Filters: lifecycle_state, data_classification_max, source_type.
Pagination: limit + offset, plus next_offset hint and brain_head_sha echo.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .. import corpus_store, errors
from ..auth import TokenInfo


VALID_LIFECYCLE = {"active", "idle", "archived", "frozen"}
VALID_CLASSIFICATIONS = ["public", "internal", "confidential", "restricted"]
DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 200


_CLASS_RANK = {c: i for i, c in enumerate(VALID_CLASSIFICATIONS)}


def _validate_args(args: dict) -> dict | None:
    lifecycle = args.get("lifecycle_state", ["active", "idle"])
    if not isinstance(lifecycle, list) or not all(isinstance(x, str) for x in lifecycle):
        return errors.tool_error(
            "INVALID_ARGUMENT",
            "lifecycle_state must be a list of strings",
        )
    bad = [x for x in lifecycle if x not in VALID_LIFECYCLE]
    if bad:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            f"unknown lifecycle_state values: {bad}",
            details={"valid": sorted(VALID_LIFECYCLE)},
        )

    cmax = args.get("data_classification_max", "internal")
    if cmax not in VALID_CLASSIFICATIONS:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            f"unknown data_classification_max: {cmax!r}",
            details={"valid": VALID_CLASSIFICATIONS},
        )

    source_type = args.get("source_type")
    if source_type is not None and not isinstance(source_type, str):
        return errors.tool_error("INVALID_ARGUMENT", "source_type must be a string when set")

    limit = args.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < MIN_LIMIT or limit > MAX_LIMIT:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            f"limit must be int in [{MIN_LIMIT}, {MAX_LIMIT}]",
            details={"got": limit},
        )

    offset = args.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            "offset must be a non-negative integer",
            details={"got": offset},
        )

    return None


def _brain_head_sha(metas: list[corpus_store.CorpusMeta]) -> str:
    """Echo a content-addressed sha of the visible corpora set.

    v1 has no brain repo (handoff: "defer brain repo to v1.1"). We compute a
    stable sha over (corpus_id, commit_sha) pairs so paginating clients can
    detect set drift between calls per § 3.5: "Clients SHOULD compare
    brain_head_sha across pages and re-page from offset 0 if it changes."
    """
    payload = sorted([(m.corpus_id, m.commit_sha or "") for m in metas])
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    start = time.time()
    err = _validate_args(args)
    if err:
        return err

    lifecycle = args.get("lifecycle_state", ["active", "idle"])
    cmax = args.get("data_classification_max", "internal")
    source_type = args.get("source_type")
    limit = args.get("limit", DEFAULT_LIMIT)
    offset = args.get("offset", 0)

    # Caller's role-derived classification cap floors the requested cmax.
    # § 6.1: data_classification_max can never exceed role cap.
    role_cap = token.data_classification_max
    if _CLASS_RANK[cmax] > _CLASS_RANK[role_cap]:
        cmax = role_cap

    all_metas = corpus_store.list_metas()

    # Visibility scope: corpora the caller's role + classification cap permits to see.
    # Independent of the user-supplied filters (lifecycle/source_type) because those
    # are query-level slices, not auth gates. Codex P2 fix: scoping the head_sha
    # to this set prevents leaking hidden-corpus churn through hash changes.
    visible_metas = [
        m for m in all_metas
        if _CLASS_RANK.get(m.data_classification, 99) <= _CLASS_RANK[cmax]
    ]

    # Filter (visible ∩ user filters)
    filtered = [
        m for m in visible_metas
        if m.lifecycle_state in lifecycle
        and (source_type is None or m.source.get("type") == source_type)
    ]
    # Stable sort for pagination determinism (corpus_id ascending).
    filtered.sort(key=lambda m: m.corpus_id)

    total = len(filtered)
    page = filtered[offset:offset + limit]
    has_more = offset + len(page) < total
    next_offset = offset + len(page) if has_more else None

    # Compute brain_head_sha over the visible set (no user filters) so pagination
    # comparisons across calls are stable when filters change but data hasn't,
    # AND so hidden-corpus churn doesn't leak through hash drift.
    head_sha = _brain_head_sha(visible_metas)

    return {
        "corpora": [m.to_list_entry() for m in page],
        "total_count": total,
        "has_more": has_more,
        "next_offset": next_offset,
        "brain_head_sha": head_sha,
        "took_ms": int((time.time() - start) * 1000),
    }
