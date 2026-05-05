"""Shared corpus-access helpers for tool handlers.

Both `tools/pack.py` and `tools/find.py` (and Phase 4 write tools) need:
  - load-or-error with classification + lifecycle gates
  - embedding-provider parity check (multi-corpus + semantic mode)
  - prefix-collision detection (multi-corpus)
  - classification ranking

Keeping these here avoids tools importing each other's privates.
"""
from __future__ import annotations

from typing import Any

from . import corpus_store, errors


_CLASSIFICATION_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


def classification_visible(corpus_classification: str, caller_max: str) -> bool:
    return _CLASSIFICATION_RANK.get(corpus_classification, 99) <= _CLASSIFICATION_RANK.get(caller_max, 0)


def load_or_error(corpus_id: str, classification_max: str
                  ) -> tuple[corpus_store.LoadedCorpus | None, dict | None]:
    """Load a corpus or return a tool_error envelope. Enforces § 6.3 classification + lifecycle."""
    if not corpus_store.is_valid_corpus_id(corpus_id):
        return None, errors.tool_error(
            "INVALID_ARGUMENT",
            f"invalid corpus_id format: {corpus_id!r}",
        )
    loaded = corpus_store.load_corpus(corpus_id)
    if loaded is None:
        return None, errors.tool_error(
            "CORPUS_NOT_FOUND",
            f"no index for corpus {corpus_id!r}",
            details={"corpus_id": corpus_id},
        )
    if not classification_visible(loaded.meta.data_classification, classification_max):
        return None, errors.tool_error(
            "INVALID_ARGUMENT",
            f"corpus {corpus_id!r} classification {loaded.meta.data_classification!r} exceeds caller's max {classification_max!r}",
            details={
                "corpus_id": corpus_id,
                "exceeded_classification": loaded.meta.data_classification,
                "caller_max": classification_max,
            },
        )
    if loaded.meta.lifecycle_state in ("archived", "frozen"):
        return None, errors.tool_error(
            "CORPUS_ARCHIVED",
            f"corpus {corpus_id!r} is {loaded.meta.lifecycle_state}",
            details={"corpus_id": corpus_id, "lifecycle_state": loaded.meta.lifecycle_state},
        )
    return loaded, None


def check_embedding_parity(corpora: list[corpus_store.LoadedCorpus]) -> dict | None:
    """§ 3.1: in multi-corpus + semantic mode, all corpora must share (provider, model, dims).

    Returns EMBEDDING_PROVIDER_MISMATCH envelope if not, else None.
    """
    seen = set()
    providers = []
    for c in corpora:
        emb = c.meta.embedding
        triple = (
            emb.get("provider", "none"),
            emb.get("model", "n/a"),
            int(emb.get("dims", 0) or 0),
        )
        seen.add(triple)
        providers.append({"corpus_id": c.meta.corpus_id, **emb})
    if len(seen) > 1:
        return errors.tool_error(
            "EMBEDDING_PROVIDER_MISMATCH",
            "multi-corpus semantic mode requires identical (provider, model, dims) across corpora",
            details={"providers": providers},
        )
    return None


def check_embeddings_loaded(corpora: list[corpus_store.LoadedCorpus]) -> dict | None:
    """When the caller asks for `mode: semantic` and a corpus declares
    `embedding.dims > 0` but its `embeddings` payload map is empty, the
    server has no vectors to cosine against — the cause is a hand-built or
    partially-migrated index, not "this corpus is keyword-only by design"
    (which is `dims == 0`).

    Without this check, `_rank_one`/`_pack_single` would silently keyword-
    fallback for a corpus that LOOKS semantic-eligible per metadata, and
    the response would mix cosine scores from semantic-eligible corpora
    with keyword scores from broken ones — meaningless ordering. Stricter
    than `check_embedding_parity`, which only inspects the metadata triple.

    Caller controls the gate: only call when `mode == "semantic"`. For
    `mode == "auto"` etc. the keyword fallback is intentional and silent.

    Returns EMBEDDING_PROVIDER_MISMATCH envelope (with `empty_corpora` in
    details) if any corpus is in this broken state, else None.
    """
    broken = []
    for c in corpora:
        declared_dims = int(c.meta.embedding.get("dims", 0) or 0)
        if declared_dims > 0 and not c.embeddings:
            broken.append({
                "corpus_id": c.meta.corpus_id,
                "declared_dims": declared_dims,
                "embeddings_payload": "empty",
            })
    if broken:
        return errors.tool_error(
            "EMBEDDING_PROVIDER_MISMATCH",
            "corpus declares embedding dims>0 but has no embeddings payload; "
            "re-upload with vectors or query with mode != 'semantic'",
            details={"empty_corpora": broken},
        )
    return None


def detect_prefix_collisions(corpora: list[corpus_store.LoadedCorpus]) -> dict | None:
    """§ 3.1: paths under `<corpus_id>:<path>` must not produce ambiguous addressing.

    We check that no corpus_id is a strict prefix of another (with `-` separator),
    which would let downstream parsers split on the wrong boundary.

    Returns CORPUS_PREFIX_COLLISION envelope if collision, else None.
    """
    ids = [c.meta.corpus_id for c in corpora]
    colliders: list[tuple[str, str]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a == b or a.startswith(b + "-") or b.startswith(a + "-"):
                colliders.append((a, b))
    if colliders:
        return errors.tool_error(
            "CORPUS_PREFIX_COLLISION",
            "two or more corpus_ids would produce ambiguous prefixed paths",
            details={"colliding_corpora": colliders},
        )
    return None


def aggregate_load(corpus_ids: list[str], classification_max: str
                   ) -> tuple[list[corpus_store.LoadedCorpus] | None, dict | None]:
    """Load N corpora, fail-fast on any non-NOT_FOUND error, aggregate NOT_FOUND.

    Returns (loaded_list, None) on success, or (None, tool_error_envelope) on failure.
    """
    loaded_list: list[corpus_store.LoadedCorpus] = []
    missing: list[str] = []
    for cid in corpus_ids:
        loaded, err = load_or_error(cid, classification_max)
        if err is None:
            loaded_list.append(loaded)
            continue
        code = err["structuredContent"]["code"]
        if code == "CORPUS_NOT_FOUND":
            missing.append(cid)
        else:
            return None, err
    if missing:
        return None, errors.tool_error(
            "CORPUS_NOT_FOUND",
            f"{len(missing)} of {len(corpus_ids)} corpora not found",
            details={"missing_corpora": missing},
        )
    return loaded_list, None
