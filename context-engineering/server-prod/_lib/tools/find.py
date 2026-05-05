"""ce_find_relevant_files — § 3.2.

Like ce_pack_context but returns ranked paths only — no content.
Multi-corpus rules identical to pack: all-or-nothing, prefix collision
detection, EMBEDDING_PROVIDER_MISMATCH on `mode: semantic`.
"""
from __future__ import annotations

import time
from typing import Any

from .. import corpus_access, corpus_store, embed, engine, errors  # noqa: F401 — corpus_store kept for type clarity
from ..auth import TokenInfo
from . import pack as _pack  # validators only — keeps mode/task enums in sync


VALID_MODES = _pack.VALID_MODES
VALID_TASKS = _pack.VALID_TASKS
VALID_RERANK = {None, "mmr"}

DEFAULT_TOP_K = 20
MIN_TOP_K = 1
MAX_TOP_K = 200
MMR_LAMBDA = 0.7  # PR #35 default — relevance-leaning


def _validate_args(args: dict) -> dict | None:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return errors.tool_error("INVALID_ARGUMENT", "query is required and must be a non-empty string")
    if len(query) > _pack.MAX_QUERY_CHARS:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            f"query exceeds {_pack.MAX_QUERY_CHARS} chars",
            details={"length": len(query), "max": _pack.MAX_QUERY_CHARS},
        )

    cid = args.get("corpus_id")
    cids = args.get("corpus_ids")
    if cid and cids:
        return errors.tool_error("INVALID_ARGUMENT", "exactly one of corpus_id / corpus_ids must be set, not both")
    if not cid and not cids:
        return errors.tool_error("INVALID_ARGUMENT", "exactly one of corpus_id / corpus_ids must be set")
    if cid is not None and not isinstance(cid, str):
        return errors.tool_error("INVALID_ARGUMENT", "corpus_id must be a string")
    if cids is not None:
        if not isinstance(cids, list) or not all(isinstance(x, str) for x in cids):
            return errors.tool_error("INVALID_ARGUMENT", "corpus_ids must be a list of strings")
        if len(cids) < 1 or len(cids) > _pack.MAX_CORPUS_IDS:
            return errors.tool_error(
                "INVALID_ARGUMENT",
                f"corpus_ids length must be in [1, {_pack.MAX_CORPUS_IDS}]",
                details={"length": len(cids), "max": _pack.MAX_CORPUS_IDS},
            )
        if len(set(cids)) != len(cids):
            return errors.tool_error("INVALID_ARGUMENT", "corpus_ids must not contain duplicates")

    mode = args.get("mode", "auto")
    if mode not in VALID_MODES:
        return errors.tool_error("INVALID_ARGUMENT", f"unknown mode: {mode!r}",
                                 details={"valid_modes": sorted(VALID_MODES)})
    task = args.get("task")
    if task is not None and task not in VALID_TASKS:
        return errors.tool_error("INVALID_ARGUMENT", f"unknown task: {task!r}",
                                 details={"valid_tasks": sorted(VALID_TASKS)})

    top_k = args.get("top_k", DEFAULT_TOP_K)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            f"top_k must be int in [{MIN_TOP_K}, {MAX_TOP_K}]",
            details={"got": top_k},
        )

    rerank = args.get("rerank")
    if rerank not in VALID_RERANK:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            f"unknown rerank: {rerank!r}",
            details={"valid_rerank": sorted(x for x in VALID_RERANK if x is not None)},
        )
    if rerank == "mmr" and mode != "semantic":
        return errors.tool_error(
            "INVALID_ARGUMENT",
            "rerank='mmr' requires mode='semantic' (MMR diversifies cosine results)",
            details={"got_mode": mode, "got_rerank": rerank},
        )

    return None


def _rank_one(loaded: corpus_store.LoadedCorpus, query: str, top_k: int,
              mode: str, prefix: str | None,
              query_embedding: list[float] | None = None,
              rerank: str | None = None) -> list[dict]:
    """Rank a single corpus's files for `query`.

    Phase 5.5 dispatch:
    - mode == "semantic" + query_embedding present + corpus has embeddings:
      cosine-rank against stored vectors. `rerank == "mmr"` applies MMR
      diversity rerank (lambda=0.7).
    - All other paths fall through to keyword scoring (engine.score_corpus).
      A corpus that requested semantic but has no embeddings logs a "fellback
      to keyword" note in the per-file reason.
    """
    use_semantic = (
        mode == "semantic"
        and query_embedding is not None
        and bool(loaded.embeddings)
    )

    if use_semantic:
        scored = engine.score_corpus_semantic(
            query_embedding, loaded.files, loaded.embeddings, top=top_k * 4
        )
        if rerank == "mmr" and scored:
            scored = engine.mmr_rerank(
                scored, query_embedding, loaded.embeddings,
                lambda_=MMR_LAMBDA, k=top_k,
            )
        else:
            scored = scored[:top_k]
    else:
        scored = engine.score_corpus(query, loaded.files, top=top_k)

    out = []
    for s in scored:
        path = s["path"]
        if prefix:
            path = f"{prefix}:{path}"
        rel = round(s["relevance"], 4)
        if use_semantic:
            sem = rel
            kw = 0.0
            tag = f"mmr (lambda={MMR_LAMBDA})" if rerank == "mmr" else "cosine"
            reason = f"matched {sem:.3f} via {tag} (mode={mode!r})"
        else:
            sem = 0.0
            kw = rel
            note = "" if mode in {"auto", "keyword"} else f" — fellback from {mode!r}"
            reason = f"matched {kw:.3f} via keyword{note}"
        out.append({
            "path": path,
            "relevance": rel,
            "keyword_score": kw,
            "semantic_score": sem,
            "graph_score": 0.0,
            "reason": reason,
            "corpus_id": loaded.meta.corpus_id,
        })
    return out


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    start = time.time()
    err = _validate_args(args)
    if err:
        return err

    query: str = args["query"].strip()
    mode: str = args.get("mode", "auto")
    top_k: int = args.get("top_k", DEFAULT_TOP_K)
    rerank: str | None = args.get("rerank")

    cid = args.get("corpus_id")
    cids = args.get("corpus_ids")
    multi = bool(cids)

    canonical = {k: v for k, v in args.items() if v is not None}

    query_embedding: list[float] | None = None
    if mode == "semantic":
        try:
            query_embedding = embed.embed_query(query)
        except embed.EmbedError as e:
            # PROVIDER_UNAVAILABLE / EMBED_HTTP / EMBED_DIM_MISMATCH — let the
            # rank path fall back to keyword. Caller can inspect `reason` to
            # see what happened. We don't fail the whole request on this.
            query_embedding = None
            _embed_error = e  # noqa: F841 — kept for future telemetry hook

    # ── Single-corpus ──
    if cid:
        loaded, err = corpus_access.load_or_error(cid, token.data_classification_max)
        if err:
            return err
        if mode == "semantic":
            err = corpus_access.check_embeddings_loaded([loaded])
            if err:
                return err
        ranked = _rank_one(loaded, query, top_k, mode, prefix=None,
                           query_embedding=query_embedding, rerank=rerank)
        out = _wire(ranked, multi=False,
                    single_sha=loaded.meta.commit_sha or None, multi_shas=None,
                    took_ms=int((time.time() - start) * 1000), keep_corpus_id=False)
        out["_x_etag"] = _pack._compute_etag(canonical, corpus_store.commit_key(loaded))
        out["_x_cache_control"] = _pack._cache_control_for([loaded.meta.data_classification])
        return out

    # ── Multi-corpus ──
    loaded_list, err = corpus_access.aggregate_load(cids, token.data_classification_max)
    if err:
        return err

    if mode == "semantic":
        err = corpus_access.check_embedding_parity(loaded_list)
        if err:
            return err
        err = corpus_access.check_embeddings_loaded(loaded_list)
        if err:
            return err

    err = corpus_access.detect_prefix_collisions(loaded_list)
    if err:
        return err

    per_corpus_topk = max(1, top_k // max(len(loaded_list), 1) + 1)
    flat: list[dict] = []
    for c in loaded_list:
        flat.extend(_rank_one(c, query, per_corpus_topk, mode, prefix=c.meta.corpus_id,
                              query_embedding=query_embedding, rerank=rerank))
    flat.sort(key=lambda x: -x["relevance"])
    flat = flat[:top_k]

    multi_shas = {c.meta.corpus_id: (c.meta.commit_sha or "") for c in loaded_list}
    multi_shas = dict(sorted(multi_shas.items()))

    out = _wire(flat, multi=True, single_sha=None, multi_shas=multi_shas,
                took_ms=int((time.time() - start) * 1000), keep_corpus_id=True)
    by_id = {c.meta.corpus_id: c for c in loaded_list}
    commit_key = "|".join(
        f"{cid}:{corpus_store.commit_key(by_id[cid])}" for cid in multi_shas
    )
    out["_x_etag"] = _pack._compute_etag(canonical, commit_key)
    classifications = [c.meta.data_classification for c in loaded_list]
    out["_x_cache_control"] = _pack._cache_control_for(classifications)
    return out


def _wire(ranked: list[dict], multi: bool, single_sha: str | None,
          multi_shas: dict | None, took_ms: int, keep_corpus_id: bool) -> dict[str, Any]:
    files_out = []
    for r in ranked:
        entry = {
            "path": r["path"],
            "relevance": r["relevance"],
            "keyword_score": r["keyword_score"],
            "semantic_score": r["semantic_score"],
            "graph_score": r["graph_score"],
            "reason": r["reason"],
        }
        if keep_corpus_id:
            entry["corpus_id"] = r["corpus_id"]
        files_out.append(entry)
    return {
        "files": files_out,
        "corpus_commit_sha": single_sha,
        "corpus_commit_shas": multi_shas,
        "took_ms": took_ms,
    }
