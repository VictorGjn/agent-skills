"""ce_pack_context — § 3.1.

Headline tool. Given (query, corpus_id | corpus_ids, budget, mode, ...),
returns a depth-packed markdown bundle (or structured JSON) sized to budget.

Multi-corpus contract per § 3.1 invariants:
- Exactly one of `corpus_id` / `corpus_ids` MUST be set.
- All-or-nothing semantics: any per-corpus failure fails the whole call.
- `mode: semantic` requires identical (provider, model, dims) across corpora;
  mismatch → EMBEDDING_PROVIDER_MISMATCH.
- Output paths in multi-corpus mode are prefixed `<corpus_id>:<path>`.
- Path-prefix collisions (two corpora sharing a root basename) →
  CORPUS_PREFIX_COLLISION.
- `corpus_commit_shas` is the authoritative reproducibility key in multi-corpus
  mode; `corpus_commit_sha` is null. Lex-sorted on the wire.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .. import corpus_access, corpus_store, engine, errors
from ..auth import TokenInfo


VALID_MODES = {"auto", "keyword", "semantic", "graph", "deep", "wide"}
VALID_TASKS = {"fix", "review", "explain", "build", "document", "research"}
VALID_RESPONSE_FORMATS = {"markdown", "structured", "both"}

DEFAULT_BUDGET = 32000
MIN_BUDGET = 1000
MAX_BUDGET = 200000
MIN_FILE_OVERHEAD = 500  # § 3.1 BUDGET_TOO_SMALL threshold

MAX_QUERY_CHARS = 4096
MAX_CORPUS_IDS = 10  # § 3.1 multi-corpus cap

# Modes the v1 keyword engine actually executes. Modes outside this set
# (semantic, graph, deep, wide) are accepted by the schema but the engine
# falls back to keyword scoring with a trace note. Phase 4+ will wire the
# real semantic + graph paths.
_KEYWORD_MODES = {"auto", "keyword", "deep", "wide"}


def _compute_etag(canonical_inputs: dict, commit_key: str) -> str:
    """SPEC § 3.1: ETag = sha256(commit_key || canonical(inputs)).

    canonical_inputs are JSON-canonicalized (RFC 8785) — sort_keys + no whitespace.
    commit_key is corpus_commit_sha (single) or sorted '<corpus_id>:<sha>' join (multi).
    """
    body = commit_key + "|" + json.dumps(canonical_inputs, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def _cache_control_for(classifications: list[str]) -> str:
    """§ 3.1: confidential/restricted → no-store; lower → private, max-age=60."""
    if any(c in ("confidential", "restricted") for c in classifications):
        return "no-store"
    return "private, max-age=60"


def _scale_budget(budget: int | None, model_context: int | None) -> int:
    """§ 3.1: when model_context is set without explicit budget, scale to ~12% of context, clamped [4000, 64000]."""
    if budget is not None:
        return budget
    if model_context is None:
        return DEFAULT_BUDGET
    scaled = int(model_context * 0.12)
    return max(4000, min(scaled, 64000))


def _validate_args(args: dict) -> dict | None:
    """Return a tool_error envelope if invalid, else None."""
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return errors.tool_error("INVALID_ARGUMENT", "query is required and must be a non-empty string")
    if len(query) > MAX_QUERY_CHARS:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            f"query exceeds {MAX_QUERY_CHARS} chars",
            details={"length": len(query), "max": MAX_QUERY_CHARS},
        )

    cid = args.get("corpus_id")
    cids = args.get("corpus_ids")
    if cid and cids:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            "exactly one of corpus_id / corpus_ids must be set, not both",
        )
    if not cid and not cids:
        return errors.tool_error(
            "INVALID_ARGUMENT",
            "exactly one of corpus_id / corpus_ids must be set",
        )
    if cid is not None and not isinstance(cid, str):
        return errors.tool_error("INVALID_ARGUMENT", "corpus_id must be a string")
    if cids is not None:
        if not isinstance(cids, list) or not all(isinstance(x, str) for x in cids):
            return errors.tool_error("INVALID_ARGUMENT", "corpus_ids must be a list of strings")
        if len(cids) < 1 or len(cids) > MAX_CORPUS_IDS:
            return errors.tool_error(
                "INVALID_ARGUMENT",
                f"corpus_ids length must be in [1, {MAX_CORPUS_IDS}]",
                details={"length": len(cids), "max": MAX_CORPUS_IDS},
            )
        if len(set(cids)) != len(cids):
            return errors.tool_error(
                "INVALID_ARGUMENT",
                "corpus_ids must not contain duplicates",
            )

    mode = args.get("mode", "auto")
    if mode not in VALID_MODES:
        return errors.tool_error("INVALID_ARGUMENT", f"unknown mode: {mode!r}",
                                 details={"valid_modes": sorted(VALID_MODES)})
    task = args.get("task")
    if task is not None and task not in VALID_TASKS:
        return errors.tool_error("INVALID_ARGUMENT", f"unknown task: {task!r}",
                                 details={"valid_tasks": sorted(VALID_TASKS)})

    rf = args.get("response_format", "markdown")
    if rf not in VALID_RESPONSE_FORMATS:
        return errors.tool_error("INVALID_ARGUMENT", f"unknown response_format: {rf!r}",
                                 details={"valid": sorted(VALID_RESPONSE_FORMATS)})

    budget = args.get("budget")
    if budget is not None and (not isinstance(budget, int) or isinstance(budget, bool)):
        return errors.tool_error("INVALID_ARGUMENT", "budget must be an integer")
    if isinstance(budget, int) and not isinstance(budget, bool):
        if budget < MIN_BUDGET or budget > MAX_BUDGET:
            return errors.tool_error(
                "INVALID_ARGUMENT",
                f"budget must be in [{MIN_BUDGET}, {MAX_BUDGET}]",
                details={"got": budget},
            )

    model_ctx = args.get("model_context")
    if model_ctx is not None and (not isinstance(model_ctx, int) or isinstance(model_ctx, bool) or model_ctx <= 0):
        return errors.tool_error("INVALID_ARGUMENT", "model_context must be a positive integer")

    return None


def _allocate_quota(budget: int, n_corpora: int) -> int:
    """Per-corpus budget for round-robin top-K allocation. Floor at 1000."""
    return max(1000, budget // max(n_corpora, 1))


def _check_budget(budget: int) -> dict | None:
    if budget < MIN_FILE_OVERHEAD:
        return errors.tool_error(
            "BUDGET_TOO_SMALL",
            f"budget {budget} below minimum file overhead ({MIN_FILE_OVERHEAD})",
            details={"min_overhead": MIN_FILE_OVERHEAD, "got": budget},
        )
    return None


def _pack_single(loaded: corpus_store.LoadedCorpus, query: str, budget: int,
                 prefix: str | None) -> list[dict]:
    """Score → pack one corpus. Returns packed items annotated with corpus_id + prefixed path."""
    scored = engine.score_corpus(query, loaded.files, top=100)
    packed = engine.pack(scored, budget) if scored else []
    out = []
    for item in packed:
        path = item["path"]
        if prefix:
            path = f"{prefix}:{path}"
        out.append({
            "path": path,
            "depth": engine.depth_name(item["depth"]),
            "tokens": item["tokens"],
            "relevance": round(item["relevance"], 4),
            "_engine": item,  # internal — kept for markdown rendering, stripped before return
            "corpus_id": loaded.meta.corpus_id,
        })
    return out


def _merge_with_quota(per_corpus: list[list[dict]], total_budget: int) -> list[dict]:
    """Round-robin per-corpus picks until budget exhausted, then global rerank by relevance.

    On each round, take from each queue the first item that fits the remaining
    budget. An oversized head must NOT block smaller trailing items in the
    same queue (Codex P2 fix): we scan past the head and pop the first fitter,
    leaving any oversized items in place for the rare case the budget grows
    later (it doesn't here, but the ordering is preserved for relevance).
    """
    queues = [list(items) for items in per_corpus]
    merged: list[dict] = []
    used = 0
    # Round-robin
    while any(queues) and used < total_budget:
        progress = False
        for q in queues:
            if not q:
                continue
            # Find the first item in this queue that fits the remaining budget.
            fit_idx = None
            for idx, item in enumerate(q):
                if used + item["tokens"] <= total_budget:
                    fit_idx = idx
                    break
            if fit_idx is None:
                continue  # nothing in this queue fits the remaining budget
            item = q.pop(fit_idx)
            merged.append(item)
            used += item["tokens"]
            progress = True
        if not progress:
            break
    # Rerank by relevance for output stability
    merged.sort(key=lambda x: -x["relevance"])
    return merged


def _build_output(packed: list[dict], budget: int, response_format: str,
                   query: str, mode: str, multi: bool,
                   single_sha: str | None, multi_shas: dict | None,
                   trace: str | None, took_ms: int) -> dict[str, Any]:
    files_out: list[dict] = []
    total_tokens = 0
    for item in packed:
        engine_item = item.get("_engine") or {}
        rendered = engine.render_at_depth(engine_item.get("tree"), engine_item.get("depth", 4), item["path"])
        entry = {
            "path": item["path"],
            "depth": item["depth"],
            "tokens": item["tokens"],
            "relevance": item["relevance"],
        }
        if multi:
            entry["corpus_id"] = item["corpus_id"]
        if response_format in ("structured", "both"):
            entry["content"] = rendered
        files_out.append(entry)
        total_tokens += item["tokens"]

    out: dict[str, Any] = {
        "tokens_used": total_tokens,
        "tokens_budget": budget,
        "files": files_out,
        "trace": trace,
        "corpus_commit_sha": single_sha,
        "corpus_commit_shas": multi_shas,
        "took_ms": took_ms,
    }

    if response_format in ("markdown", "both"):
        # Build markdown from the packed items via the engine helper.
        engine_packed = []
        for item in packed:
            ei = item.get("_engine") or {}
            engine_packed.append({
                "depth": ei.get("depth", 4),
                "path": item["path"],
                "tree": ei.get("tree"),
            })
        out["markdown"] = engine.assemble_markdown(query, mode, engine_packed, total_tokens)
    return out


def handle(args: dict, token: TokenInfo) -> dict[str, Any]:
    start = time.time()

    err = _validate_args(args)
    if err:
        return err

    query: str = args["query"].strip()
    mode: str = args.get("mode", "auto")
    task = args.get("task")
    why = bool(args.get("why", False))
    response_format = args.get("response_format", "markdown")
    budget = _scale_budget(args.get("budget"), args.get("model_context"))

    err = _check_budget(budget)
    if err:
        return err

    cid = args.get("corpus_id")
    cids = args.get("corpus_ids")
    multi = bool(cids)

    # Canonical inputs for ETag (excluding `why` since it's purely a debug toggle).
    canonical = {k: v for k, v in args.items() if k != "why" and v is not None}

    # ── Single-corpus path ──
    if cid:
        loaded, err = corpus_access.load_or_error(cid, token.data_classification_max)
        if err:
            return err
        packed = _pack_single(loaded, query, budget, prefix=None)
        trace = _build_trace(why, mode, task, query, [loaded], budget) if why else None
        out = _build_output(
            packed, budget, response_format, query, mode, multi=False,
            single_sha=loaded.meta.commit_sha or None, multi_shas=None,
            trace=trace, took_ms=int((time.time() - start) * 1000),
        )
        out["_x_etag"] = _compute_etag(canonical, loaded.meta.commit_sha or "nosha")
        out["_x_cache_control"] = _cache_control_for([loaded.meta.data_classification])
        return out

    # ── Multi-corpus path ──
    loaded_list, err = corpus_access.aggregate_load(cids, token.data_classification_max)
    if err:
        return err

    # Embedding parity check (semantic mode only). Other modes don't use vectors.
    if mode == "semantic":
        err = corpus_access.check_embedding_parity(loaded_list)
        if err:
            return err

    err = corpus_access.detect_prefix_collisions(loaded_list)
    if err:
        return err

    # Per-corpus pack with quota allocation, then global merge.
    use_quota = args.get("corpus_quota", True)
    per_corpus_budget = _allocate_quota(budget, len(loaded_list)) if use_quota else budget
    per_corpus_packed = [
        _pack_single(c, query, per_corpus_budget, prefix=c.meta.corpus_id)
        for c in loaded_list
    ]
    if use_quota:
        packed = _merge_with_quota(per_corpus_packed, budget)
    else:
        # Flat merge — fat-corpus dominates by design.
        flat: list[dict] = []
        for items in per_corpus_packed:
            flat.extend(items)
        flat.sort(key=lambda x: -x["relevance"])
        # Truncate to budget greedily
        used = 0
        packed = []
        for item in flat:
            if used + item["tokens"] > budget:
                continue
            packed.append(item)
            used += item["tokens"]

    multi_shas = {c.meta.corpus_id: (c.meta.commit_sha or "") for c in loaded_list}
    # Lex-sorted on the wire per § 3.1
    multi_shas = dict(sorted(multi_shas.items()))

    trace = _build_trace(why, mode, task, query, loaded_list, budget) if why else None
    out = _build_output(
        packed, budget, response_format, query, mode, multi=True,
        single_sha=None, multi_shas=multi_shas,
        trace=trace, took_ms=int((time.time() - start) * 1000),
    )
    # § 3.1: multi-corpus ETag uses the lex-sorted '<corpus_id>:<sha>' concatenation.
    commit_key = "|".join(f"{cid}:{sha}" for cid, sha in multi_shas.items())
    out["_x_etag"] = _compute_etag(canonical, commit_key)
    classifications = [c.meta.data_classification for c in loaded_list]
    out["_x_cache_control"] = _cache_control_for(classifications)
    return out


def _build_trace(why: bool, mode: str, task: str | None, query: str,
                  corpora: list[corpus_store.LoadedCorpus], budget: int) -> str:
    if not why:
        return ""
    lines = [
        f"mode={mode!r}, task={task!r}, query={query!r}",
        f"budget={budget}",
        f"corpora=[{', '.join(c.meta.corpus_id for c in corpora)}]",
    ]
    if mode not in _KEYWORD_MODES:
        lines.append(
            f"NOTE: mode {mode!r} requested but v1 production engine runs keyword scoring; "
            "semantic/graph modes land in Phase 4+."
        )
    return "\n".join(lines)
