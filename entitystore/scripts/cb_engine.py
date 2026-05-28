#!/usr/bin/env python3
"""
companybrain engine — JSON-native EntityStore operations over company-brain entities.

Pure-Python functions (no MCP framework dependency). The MCP server (cb_mcp.py)
wraps each function as a tool; this file stays testable in isolation via
`python cb_engine.py --self-test`.

Six endpoints, per SURFACE.md (v1 + the three "wrong-defer" reversals):
  wiki_ask     — search (substring | semantic | hybrid) + wiki_link expansion
  wiki_audit   — charter-aware contradictions, dead_links, freshness, orphans, schema
  wiki_add     — validate + write + (optional) git commit-through
  wiki_pack    — depth-banded context pack within a token budget
  stats        — counts, breakdowns, freshness percentiles, embedding status
  resolve      — slug/alias/name → canonical entity id

Charter-normalization for the auditor is ported from
company-brain/scratch/promote_gate.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import jsonschema

# cb_embed is optional — if requests / API key not available, semantic falls back.
try:
    import cb_embed  # type: ignore
    _HAS_EMBED_MODULE = True
except Exception:  # pragma: no cover — defensive
    cb_embed = None  # type: ignore
    _HAS_EMBED_MODULE = False


# ──────────────────────────────────────────────────────────────────
# Path resolution — no hardcoded user paths, env-driven
# ──────────────────────────────────────────────────────────────────


class CorpusUnconfigured(Exception):
    pass


class SchemaUnconfigured(Exception):
    pass


def _resolve_corpus(corpus_dir: str | pathlib.Path | None) -> pathlib.Path:
    """Resolve corpus dir: arg > CB_CORPUS_DIR env. No silent fallback."""
    if corpus_dir:
        p = pathlib.Path(corpus_dir).resolve()
    else:
        env = os.environ.get("CB_CORPUS_DIR")
        if not env:
            raise CorpusUnconfigured(
                "CB_CORPUS_DIR env var not set and no --corpus arg given. "
                "Point it at a company-brain corpus directory."
            )
        p = pathlib.Path(env).resolve()
    if not (p / "entities").exists() and not (p / "manifest.json").exists():
        raise CorpusUnconfigured(
            f"{p} does not look like a corpus (no entities/ or manifest.json)"
        )
    return p


def _resolve_schema(corpus_dir: pathlib.Path) -> pathlib.Path:
    """Find entity.schema.json: env > <corpus>/../../schemas/entity.schema.json."""
    env = os.environ.get("CB_SCHEMA_PATH")
    if env:
        p = pathlib.Path(env).resolve()
        if not p.exists():
            raise SchemaUnconfigured(f"CB_SCHEMA_PATH set but not found: {p}")
        return p
    candidate = corpus_dir.parent.parent / "schemas" / "entity.schema.json"
    if candidate.exists():
        return candidate.resolve()
    raise SchemaUnconfigured(
        f"entity.schema.json not found. Tried CB_SCHEMA_PATH, then {candidate}"
    )


# Schema cache by (path, mtime) — schema is static within a process.
_SCHEMA_CACHE: dict[tuple[str, float], dict] = {}


def _load_schema(corpus_dir: pathlib.Path,
                 schema_path: str | pathlib.Path | None = None) -> dict:
    sp = pathlib.Path(schema_path) if schema_path else _resolve_schema(corpus_dir)
    key = (str(sp), sp.stat().st_mtime)
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached
    schema = json.loads(sp.read_text(encoding="utf-8"))
    _SCHEMA_CACHE[key] = schema
    return schema


# ──────────────────────────────────────────────────────────────────
# Corpus I/O — incl. path-traversal-safe entity_path
# ──────────────────────────────────────────────────────────────────


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def load_corpus(corpus_dir: pathlib.Path) -> dict[str, dict]:
    """Load every entity JSON into {entity_id: entity_dict}."""
    entities: dict[str, dict] = {}
    ent_root = corpus_dir / "entities"
    if not ent_root.exists():
        return entities
    for p in sorted(ent_root.rglob("*.json")):
        if p.name == "manifest.json":
            continue
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        eid = e.get("id")
        if eid:
            entities[eid] = e
    return entities


def _entity_path(corpus_dir: pathlib.Path, entity: dict) -> pathlib.Path:
    """Derive on-disk path. Rejects slugs that could escape the corpus."""
    eid = entity["id"]
    kind = entity["kind"]
    slug = eid.split(":", 1)[1] if ":" in eid else eid
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"unsafe slug {slug!r} (must match [a-z0-9][a-z0-9._-]*) — "
            "this would risk path traversal or write outside the corpus"
        )
    if not _SLUG_RE.match(kind):
        raise ValueError(f"unsafe kind {kind!r} (must match [a-z0-9][a-z0-9._-]*)")
    # Final defence in depth: resolve and ensure containment.
    base = (corpus_dir / "entities" / kind).resolve()
    target = (base / f"{slug}.json").resolve()
    if base not in target.parents:
        raise ValueError(f"path {target} escapes corpus root {base}")
    return target


# ──────────────────────────────────────────────────────────────────
# Charter normalization (ported from promote_gate.py)
# ──────────────────────────────────────────────────────────────────

NORM = {
    "role":   {"ship-owner": "owner", "shipowner": "owner", "owner-operator": "operator"},
    "cp":     {"tc": "time", "vc": "voyage", "bb": "bareboat", "n/a": "owned"},
    "tenor":  {"period": "long", "short-period": "short", "long-period": "long", "": "n/a"},
    "status": {"total": "all", "in-service": "operating", "active": "operating",
               "newbuild": "on-order", "on order": "on-order"},
}


def _n(field: str, v):
    return NORM[field].get(v, v) if v is not None else "n/a"


def _claim_key(eid, metric, role, cp_type, tenor, status, as_of):
    return (eid, metric, _n("role", role), _n("cp", cp_type),
            _n("tenor", tenor), _n("status", status), as_of)


def _flatten_claims(entities: dict[str, dict]) -> list[dict]:
    flat: list[dict] = []
    for eid, e in entities.items():
        for claim in e.get("claims", []) or []:
            metric = claim.get("metric", "")
            role = claim.get("role", "n/a")
            as_of = claim.get("as_of", "")
            for m in claim.get("measurements", []) or []:
                flat.append({
                    "entity": eid, "metric": metric, "role": role,
                    "cp_type": m.get("cp_type", "n/a"),
                    "tenor": m.get("tenor", "n/a"),
                    "status": m.get("status", "n/a"),
                    "as_of": as_of,
                    "value": m.get("value"),
                    "source": m.get("source", ""),
                })
    return flat


# ──────────────────────────────────────────────────────────────────
# Date parsing (timezone-normalized)
# ──────────────────────────────────────────────────────────────────


def _parse_iso(ts: str | None):
    """Parse ISO timestamp -> tz-aware UTC datetime (or None)."""
    if not ts:
        return None
    try:
        s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(s)
        # Promote naive -> aware UTC so arithmetic with aware now() never crashes.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────
# Depth-banded rendering (port of CE's depth concept, JSON-shaped)
# ──────────────────────────────────────────────────────────────────

DEPTH_NAMES = {0: "Full", 1: "Detail", 2: "Summary", 3: "Headlines", 4: "Mention"}


def render_at_depth(entity: dict, depth: int) -> dict:
    """Drop progressively more detail as depth rises (lower-resolution view)."""
    if depth <= 0:  # Full
        return entity
    if depth == 1:  # Detail — drop evidence + provenance (keep claims + concept)
        return {k: v for k, v in entity.items() if k not in {"evidence", "provenance"}}
    if depth == 2:  # Summary — id/kind/names/summary/concept.statement + claims briefs
        concept = entity.get("concept") or {}
        out = {
            "id": entity.get("id"),
            "kind": entity.get("kind"),
            "names": entity.get("names", []),
            "summary": entity.get("summary", ""),
            "topics": entity.get("topics", []),
        }
        if concept.get("statement"):
            out["concept_statement"] = concept["statement"]
        claims = entity.get("claims") or []
        if claims:
            out["claim_metrics"] = sorted({c.get("metric", "") for c in claims if c.get("metric")})
        return out
    if depth == 3:  # Headlines — id/kind + primary name
        names = entity.get("names") or [entity.get("id", "")]
        return {
            "id": entity.get("id"),
            "kind": entity.get("kind"),
            "name": names[0],
        }
    # depth >= 4 — Mention
    return {"id": entity.get("id"), "kind": entity.get("kind")}


def _est_tokens(obj) -> int:
    """Cheap token estimate: chars/4."""
    return max(1, len(json.dumps(obj, ensure_ascii=False)) // 4)


# ──────────────────────────────────────────────────────────────────
# Scoring (substring) — used as keyword baseline + hybrid keyword leg
# ──────────────────────────────────────────────────────────────────


def _substring_score(entity: dict, q_lower: str) -> float:
    """Cheap substring relevance in [0, 1]. 0 = no match."""
    if not q_lower:
        return 0.0
    score = 0.0
    # id + names get bigger weight (these are identity)
    if q_lower in entity.get("id", "").lower():
        score += 0.5
    for n in entity.get("names") or []:
        if q_lower in n.lower():
            score = max(score, 0.7)
            break
    summary = (entity.get("summary") or "").lower()
    if q_lower in summary:
        score = max(score, 0.4)
    stmt = ((entity.get("concept") or {}).get("statement") or "").lower()
    if q_lower in stmt:
        score = max(score, 0.4)
    for t in entity.get("topics") or []:
        if q_lower in t.lower():
            score = max(score, 0.3)
    return min(1.0, score)


# ──────────────────────────────────────────────────────────────────
# Endpoint 1: wiki_ask
# ──────────────────────────────────────────────────────────────────


def wiki_ask(
    query: str,
    corpus_dir: str | pathlib.Path | None = None,
    kind: str | None = None,
    topics: list[str] | None = None,
    depth: int = 1,
    budget: int = 8000,
    mode: str = "hybrid",
    top: int = 30,
) -> dict:
    """Search entities + expand wiki_link neighborhood.

    mode = "substring" | "semantic" | "hybrid" (default).
    Hybrid falls back to substring when no embedding provider is available.

    Truncation (over budget) drops the LOWEST-scored entities first, not LIFO.
    """
    cdir = _resolve_corpus(corpus_dir)
    entities = load_corpus(cdir)
    q = (query or "").strip()
    q_lower = q.lower()

    # Reject the "dump everything" foot-gun.
    if not q and not kind and not topics:
        return {
            "matched": [], "neighbors": [],
            "stats": {"matched": 0, "neighbors": 0, "truncated": False,
                      "corpus": cdir.name,
                      "error": "empty query AND no kind/topics filter — refusing to dump corpus"},
        }

    # 1. Filter by kind/topics first.
    candidates = entities
    if kind:
        candidates = {eid: e for eid, e in candidates.items() if e.get("kind") == kind}
    if topics:
        topic_set = set(topics)
        candidates = {eid: e for eid, e in candidates.items()
                      if topic_set.intersection(set(e.get("topics") or []))}

    # 2. Score.
    semantic_map: dict[str, float] = {}
    semantic_used = False
    if mode in ("semantic", "hybrid") and _HAS_EMBED_MODULE and cb_embed is not None:
        try:
            sem = cb_embed.semantic_rank(q, cdir, candidates, top_k=max(top * 2, 50))
            semantic_map = {r["id"]: r["score"] for r in sem}
            semantic_used = True
        except Exception as ex:  # noqa: BLE001 — broad on purpose; fall back cleanly
            if mode == "semantic":
                return {
                    "matched": [], "neighbors": [],
                    "stats": {"matched": 0, "neighbors": 0, "truncated": False,
                              "corpus": cdir.name,
                              "error": f"semantic mode requested but failed: {ex}"},
                }
            # hybrid → degrade silently to substring

    scored: list[tuple[float, dict]] = []
    for eid, e in candidates.items():
        sub = _substring_score(e, q_lower) if q else 0.0
        sem = semantic_map.get(eid, 0.0)
        if mode == "substring":
            s = sub
        elif mode == "semantic":
            s = sem
        else:  # hybrid: take max + small co-occurrence bonus
            s = max(sub, sem) + (0.1 if (sub > 0 and sem > 0) else 0.0)
            s = min(1.0, s)
        if s > 0 or not q:  # kind/topics-only filter passes everything
            scored.append((s, e))

    scored.sort(key=lambda x: -x[0])
    matched = [e for _, e in scored[:top]]

    # 3. Depth expansion: collect wiki_link neighbors (de-duped).
    seen = {e["id"] for e in matched}
    frontier = list(matched)
    neighbors: list[dict] = []
    for _ in range(max(0, depth)):
        next_frontier: list[dict] = []
        for e in frontier:
            for ref in e.get("wiki_links", []) or []:
                if ref in seen:
                    continue
                seen.add(ref)
                ne = entities.get(ref)
                if ne:
                    neighbors.append({
                        "id": ne["id"],
                        "kind": ne["kind"],
                        "names": ne.get("names", []),
                        "summary": ne.get("summary", ""),
                    })
                    next_frontier.append(ne)
        frontier = next_frontier
        if not frontier:
            break

    # 4. Budget-bounded truncation: drop lowest-scored matched + tail of neighbors.
    char_cap = budget * 4
    truncated = False

    def _ser():
        return json.dumps({"matched": matched, "neighbors": neighbors},
                          ensure_ascii=False)

    while len(_ser()) > char_cap and neighbors:
        neighbors.pop()  # FIFO tail of neighbors is fine; they're already lower-priority
        truncated = True

    # `matched` is sorted high→low; pop from the END drops the lowest-scored first.
    while len(_ser()) > char_cap and len(matched) > 1:
        matched.pop()
        truncated = True

    return {
        "matched": matched,
        "neighbors": neighbors,
        "stats": {
            "matched": len(matched),
            "neighbors": len(neighbors),
            "truncated": truncated,
            "corpus": cdir.name,
            "mode": mode,
            "semantic_used": semantic_used,
        },
    }


# ──────────────────────────────────────────────────────────────────
# Endpoint 2: wiki_audit
# ──────────────────────────────────────────────────────────────────

FRESHNESS_THRESHOLD_DAYS = {
    "post": 90, "concept": 365, "org": 180, "person": 180,
    "vessel": 180, "navigation": 90, "product": 365,
}


def wiki_audit(
    corpus_dir: str | pathlib.Path | None = None,
    schema_path: str | pathlib.Path | None = None,
    kinds: list[str] | None = None,
) -> dict:
    cdir = _resolve_corpus(corpus_dir)
    all_entities = load_corpus(cdir)
    total = len(all_entities)

    try:
        schema = _load_schema(cdir, schema_path)
    except (SchemaUnconfigured, OSError, json.JSONDecodeError):
        schema = None

    entities = (
        {eid: e for eid, e in all_entities.items() if e.get("kind") in kinds}
        if kinds else all_entities
    )

    # 1. contradictions (charter-normalized key)
    flat = _flatten_claims(entities)
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for c in flat:
        by_key[_claim_key(c["entity"], c["metric"], c["role"], c["cp_type"],
                          c["tenor"], c["status"], c["as_of"])].append(c)
    contradictions: list[dict] = []
    for k, group in by_key.items():
        values = {g["value"] for g in group}
        if len(values) > 1:
            contradictions.append({
                "key": {"entity": k[0], "metric": k[1], "role": k[2],
                        "cp_type": k[3], "tenor": k[4], "status": k[5],
                        "as_of": k[6]},
                "values": [{"value": g["value"], "source": g["source"]} for g in group],
            })

    # 2. dead_links (wiki_link target missing)
    existing_ids = set(all_entities.keys())  # use full set, not kind-filtered
    dead_links: list[dict] = []
    for eid, e in entities.items():
        for ref in e.get("wiki_links", []) or []:
            if ref not in existing_ids:
                dead_links.append({"from": eid, "to": ref})

    # 3. freshness expiries
    now = datetime.now(timezone.utc)
    freshness_expired: list[dict] = []
    for eid, e in entities.items():
        threshold = FRESHNESS_THRESHOLD_DAYS.get(e.get("kind", ""), 365)
        updated = _parse_iso(e.get("updated_at") or e.get("created_at"))
        if updated is None:
            continue
        delta = (now - updated).days
        if delta > threshold:
            freshness_expired.append({
                "id": eid, "kind": e.get("kind"),
                "updated_at": e.get("updated_at"),
                "days_stale": delta, "threshold_days": threshold,
            })

    # 4. orphans — no inbound + no claims + no evidence + no concept.statement
    inbound: dict[str, int] = defaultdict(int)
    for e in all_entities.values():
        for ref in e.get("wiki_links", []) or []:
            inbound[ref] += 1
    orphans: list[dict] = []
    for eid, e in entities.items():
        stmt = ((e.get("concept") or {}).get("statement") or "").strip()
        if (inbound[eid] == 0
                and not (e.get("claims") or [])
                and not (e.get("evidence") or [])
                and not stmt):
            orphans.append({"id": eid, "kind": e.get("kind")})

    # 5. schema invalid
    schema_invalid: list[dict] = []
    if schema is not None:
        for eid, e in entities.items():
            try:
                jsonschema.validate(e, schema)
            except jsonschema.ValidationError as ex:
                schema_invalid.append({
                    "id": eid, "error": ex.message,
                    "path": "/".join(str(x) for x in ex.absolute_path),
                })

    return {
        "corpus": cdir.name,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "entity_count_total": total,
        "entity_count_audited": len(entities),
        "kinds_filter": kinds,
        "contradictions": contradictions,
        "dead_links": dead_links,
        "freshness_expired": freshness_expired,
        "orphans": orphans,
        "schema_invalid": schema_invalid,
        "summary": {
            "contradictions": len(contradictions),
            "dead_links": len(dead_links),
            "freshness_expired": len(freshness_expired),
            "orphans": len(orphans),
            "schema_invalid": len(schema_invalid),
        },
    }


# ──────────────────────────────────────────────────────────────────
# Endpoint 3: wiki_add — with git commit-through
# ──────────────────────────────────────────────────────────────────


def _git_commit(file_path: pathlib.Path, entity_id: str,
                op: str) -> dict:
    """Commit a single file change. Returns status dict."""
    try:
        toplevel_proc = subprocess.run(
            ["git", "-C", str(file_path.parent), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if toplevel_proc.returncode != 0:
            return {"committed": False, "reason": "not_a_git_repo",
                    "stderr": toplevel_proc.stderr.strip()[:200]}
        repo = pathlib.Path(toplevel_proc.stdout.strip())
        rel = file_path.resolve().relative_to(repo)
    except (subprocess.SubprocessError, ValueError, OSError) as ex:
        return {"committed": False, "reason": "repo_resolution_failed",
                "error": str(ex)[:200]}

    try:
        add = subprocess.run(
            ["git", "-C", str(repo), "add", "--", str(rel)],
            capture_output=True, text=True, timeout=10,
        )
        if add.returncode != 0:
            return {"committed": False, "reason": "git_add_failed",
                    "stderr": add.stderr.strip()[:200]}

        # Empty commit guard: nothing staged means nothing to commit.
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--quiet"],
            capture_output=True, text=True, timeout=10,
        )
        if diff.returncode == 0:
            return {"committed": False, "reason": "no_changes_staged"}

        msg = f"feat(brain): {op} {entity_id}"
        commit = subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", msg, "--", str(rel)],
            capture_output=True, text=True, timeout=15,
        )
        if commit.returncode != 0:
            return {"committed": False, "reason": "git_commit_failed",
                    "stderr": commit.stderr.strip()[:200]}
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()[:12]
        return {"committed": True, "commit_sha": sha, "message": msg,
                "file": str(rel)}
    except subprocess.SubprocessError as ex:
        return {"committed": False, "reason": "subprocess_error",
                "error": str(ex)[:200]}


def wiki_add(
    entity: dict,
    corpus_dir: str | pathlib.Path | None = None,
    schema_path: str | pathlib.Path | None = None,
    commit: bool = True,
) -> dict:
    """Validate → write → optionally commit. Atomicity: validation runs before
    any disk write; write is single fs.write_text; commit is best-effort.

    Set commit=False for batch flows (caller commits N writes together).
    """
    try:
        cdir = _resolve_corpus(corpus_dir)
    except CorpusUnconfigured as e:
        return {"ok": False, "error_kind": "CorpusUnconfigured", "message": str(e)}

    try:
        schema = _load_schema(cdir, schema_path)
    except SchemaUnconfigured as e:
        return {"ok": False, "error_kind": "SchemaUnconfigured", "message": str(e)}
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error_kind": "SchemaUnconfigured", "message": str(e)}

    try:
        jsonschema.validate(entity, schema)
    except jsonschema.ValidationError as ex:
        return {
            "ok": False, "error_kind": "ValidationError", "message": ex.message,
            "details": {
                "path": "/".join(str(x) for x in ex.absolute_path),
                "schema_path": "/".join(str(x) for x in ex.absolute_schema_path),
            },
        }

    try:
        path = _entity_path(cdir, entity)
    except (KeyError, ValueError) as ex:
        return {"ok": False, "error_kind": "ValidationError", "message": str(ex)}

    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entity, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")

    result = {
        "ok": True,
        "id": entity["id"],
        "path": str(path),
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "op": "add" if is_new else "update",
    }
    if commit:
        result["git"] = _git_commit(path, entity["id"],
                                    result["op"])
    return result


# ──────────────────────────────────────────────────────────────────
# Endpoint 4: wiki_pack — depth-banded context within a budget
# ──────────────────────────────────────────────────────────────────


def wiki_pack(
    query: str,
    corpus_dir: str | pathlib.Path | None = None,
    kind: str | None = None,
    topics: list[str] | None = None,
    budget: int = 8000,
    mode: str = "hybrid",
    top: int = 50,
    include_neighbors: bool = True,
) -> dict:
    """Pack a depth-banded answer-bundle for `query` within a token budget.

    Algorithm:
      1. wiki_ask (no budget cap) to score candidates.
      2. Try Full depth for everyone; if over budget, demote the
         lowest-scored entities one band at a time (Full→Detail→Summary→
         Headlines→Mention) until it fits.
      3. If still over budget after all are at Mention, drop tail.
      4. If under budget, promote top entries up one band each pass.

    Returns one packed bundle: entities with per-item depth + total token usage.
    """
    # 1. Get scored candidates via wiki_ask but with a huge budget so we don't
    # lose anything to truncation yet — the packer makes the depth decisions.
    answer = wiki_ask(
        query=query, corpus_dir=corpus_dir, kind=kind, topics=topics,
        depth=1 if include_neighbors else 0,
        budget=10_000_000,  # effectively uncapped
        mode=mode,
        top=top,
    )
    matched = answer["matched"]
    neighbor_lookup = {n["id"]: n for n in answer.get("neighbors", [])}

    cdir = _resolve_corpus(corpus_dir)

    # 2. Assemble (entity, depth, tokens) items. Matched start at Full;
    # neighbors start at Summary (they got there via expansion, not query match).
    items: list[dict] = []
    for e in matched:
        d = 0
        items.append({
            "id": e["id"], "kind": e.get("kind"),
            "depth": d, "depth_name": DEPTH_NAMES[d],
            "tokens": _est_tokens(render_at_depth(e, d)),
            "source_entity": e,
            "via": "matched",
        })
    if include_neighbors:
        # Load full entity for neighbors so we have headroom to promote them.
        all_ents = load_corpus(cdir)
        for nid in neighbor_lookup:
            ne = all_ents.get(nid)
            if not ne:
                continue
            d = 2
            items.append({
                "id": ne["id"], "kind": ne.get("kind"),
                "depth": d, "depth_name": DEPTH_NAMES[d],
                "tokens": _est_tokens(render_at_depth(ne, d)),
                "source_entity": ne,
                "via": "neighbor",
            })

    if not items:
        return {
            "query": query, "budget": budget, "used_tokens": 0,
            "items": [], "stats": {"matched": 0, "neighbors": 0, "dropped": 0,
                                   "mode": mode}}

    def total() -> int:
        return sum(it["tokens"] for it in items)

    # 3. Demote from the bottom (lowest-priority) until we fit.
    dropped = 0
    max_depth = 4
    # Demote pass — each iteration drops one depth band from the lowest-priority item still at <4.
    while total() > budget:
        # Find the item to demote: highest depth_value first (least
        # informative still in the pack), tie-broken by reverse-index
        # (later items first = lower priority).
        idx = None
        for i in range(len(items) - 1, -1, -1):
            if items[i]["depth"] < max_depth:
                idx = i
                break
        if idx is None:
            break  # everyone at max depth; nothing else to do without dropping
        items[idx]["depth"] += 1
        items[idx]["depth_name"] = DEPTH_NAMES[items[idx]["depth"]]
        items[idx]["tokens"] = _est_tokens(
            render_at_depth(items[idx]["source_entity"], items[idx]["depth"]))

    # If still over budget (all at Mention), drop from the tail.
    while total() > budget and len(items) > 1:
        items.pop()
        dropped += 1

    # 4. Promote pass — opportunistically lift the top items if we have headroom.
    headroom = budget - total()
    if headroom > 0:
        for it in items:
            while it["depth"] > 0:
                trial_depth = it["depth"] - 1
                trial_tokens = _est_tokens(
                    render_at_depth(it["source_entity"], trial_depth))
                delta = trial_tokens - it["tokens"]
                if delta <= headroom:
                    headroom -= delta
                    it["depth"] = trial_depth
                    it["depth_name"] = DEPTH_NAMES[trial_depth]
                    it["tokens"] = trial_tokens
                else:
                    break

    # 5. Render the items at their final depth for the output payload.
    rendered = []
    for it in items:
        rendered.append({
            "id": it["id"], "kind": it["kind"], "via": it["via"],
            "depth": it["depth"], "depth_name": it["depth_name"],
            "tokens": it["tokens"],
            "payload": render_at_depth(it["source_entity"], it["depth"]),
        })

    depth_counts = Counter(it["depth_name"] for it in items)
    return {
        "query": query,
        "budget": budget,
        "used_tokens": total(),
        "items": rendered,
        "stats": {
            "items": len(items),
            "dropped": dropped,
            "depth_breakdown": dict(depth_counts),
            "mode": answer["stats"]["mode"],
            "semantic_used": answer["stats"].get("semantic_used", False),
            "corpus": cdir.name,
        },
    }


# ──────────────────────────────────────────────────────────────────
# Endpoint 5: stats
# ──────────────────────────────────────────────────────────────────


def stats(corpus_dir: str | pathlib.Path | None = None) -> dict:
    cdir = _resolve_corpus(corpus_dir)
    entities = load_corpus(cdir)

    by_kind = Counter(e.get("kind", "?") for e in entities.values())
    by_topic: Counter = Counter()
    for e in entities.values():
        for t in e.get("topics", []) or []:
            by_topic[t] += 1

    wiki_links_total = sum(len(e.get("wiki_links", []) or []) for e in entities.values())
    claims_total = sum(len(e.get("claims", []) or []) for e in entities.values())

    now = datetime.now(timezone.utc)
    ages: list[int] = []
    for e in entities.values():
        u = _parse_iso(e.get("updated_at") or e.get("created_at"))
        if u:
            ages.append((now - u).days)
    ages.sort()

    def pct(p: float):
        if not ages:
            return None
        return ages[min(int(p * len(ages)), len(ages) - 1)]

    manifest_path = cdir / "manifest.json"
    schema_version = None
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            schema_version = m.get("schema_version")
        except (OSError, json.JSONDecodeError):
            pass

    embed_status: dict
    if _HAS_EMBED_MODULE and cb_embed is not None:
        embed_status = cb_embed.provider_status()
        cache = cb_embed.load_cache(cdir)
        embed_status["cached_entities"] = sum(1 for v in cache.values()
                                              if v.get("embedding"))
        embed_status["entities_total"] = len(entities)
    else:
        embed_status = {"available": False, "reason": "cb_embed module not loaded"}

    return {
        "corpus": cdir.name,
        "entity_count": len(entities),
        "by_kind": dict(by_kind),
        "by_topic": dict(by_topic.most_common(20)),
        "wiki_links_total": wiki_links_total,
        "claims_total": claims_total,
        "freshness": {
            "p50_days": pct(0.50), "p90_days": pct(0.90),
            "p99_days": pct(0.99),
            "oldest_days": ages[-1] if ages else None,
        },
        "schema_version": schema_version,
        "embeddings": embed_status,
    }


# ──────────────────────────────────────────────────────────────────
# Endpoint 6: resolve
# ──────────────────────────────────────────────────────────────────


def resolve(
    query: str,
    corpus_dir: str | pathlib.Path | None = None,
    top_k: int = 10,
) -> dict:
    """Slug / alias / partial-name → canonical entity id, scored & ranked.

    Tiers (no overlap):
      1.00  exact id
      0.95  exact slug (kind-stripped)
      0.90  exact name (case-insensitive)
      0.50–0.89  substring on a name (longer overlap scores higher)
      0.30  substring in summary
    """
    cdir = _resolve_corpus(corpus_dir)
    entities = load_corpus(cdir)
    q = (query or "").strip().lower()
    if not q:
        return {"matches": []}

    scored: list[tuple[float, dict]] = []
    for eid, e in entities.items():
        score = 0.0
        eid_low = eid.lower()
        if eid_low == q:
            score = 1.0
        elif eid_low.split(":", 1)[-1] == q:
            score = 0.95
        else:
            names = [(n or "").lower() for n in (e.get("names") or [])]
            if q in names:
                score = 0.9
            else:
                for n in names:
                    if q in n and n:
                        ratio = len(q) / len(n)
                        # Cap at 0.89 so substring never beats exact-name (0.90).
                        score = max(score, min(0.89, 0.5 + 0.39 * ratio))
                        break
                if score == 0.0 and q in (e.get("summary") or "").lower():
                    score = 0.30
        if score > 0:
            scored.append((score, {
                "id": eid, "kind": e.get("kind"),
                "names": e.get("names", []), "score": round(score, 3),
            }))
    scored.sort(key=lambda x: -x[0])
    return {"matches": [m for _, m in scored[:top_k]]}


# ──────────────────────────────────────────────────────────────────
# Self-test — runs in a tempdir copy of the corpus + schema
# ──────────────────────────────────────────────────────────────────


def _self_test_in_tempdir() -> int:
    """Run the full test suite against a tempdir copy of the live corpus.

    Never mutates the source-of-truth corpus. Tests every endpoint including
    a NEGATIVE test for the contradiction detector (inject a known-bad claim
    and verify it surfaces).
    """
    try:
        live_corpus = _resolve_corpus(None)
    except CorpusUnconfigured as e:
        print(f"FAIL: cannot resolve corpus — {e}")
        return 1
    try:
        live_schema = _resolve_schema(live_corpus)
    except SchemaUnconfigured as e:
        print(f"FAIL: cannot resolve schema — {e}")
        return 1

    failures = 0
    with tempfile.TemporaryDirectory(prefix="cb_engine_selftest_") as td:
        td_path = pathlib.Path(td)
        test_corpus = td_path / live_corpus.name
        test_schemas = td_path / "schemas"

        shutil.copytree(live_corpus, test_corpus,
                        ignore=shutil.ignore_patterns(".cb_embed_cache.json"))
        test_schemas.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live_schema, test_schemas / "entity.schema.json")

        # Layout: <td>/<corpus_name>/  and  <td>/schemas/  — schema is at
        # <corpus>/../schemas/entity.schema.json, not ../../, because the tempdir
        # doesn't have the company-brain/corpora extra layer. Set CB_SCHEMA_PATH
        # explicitly to bypass the layout-walking heuristic.
        prev_corpus = os.environ.get("CB_CORPUS_DIR")
        prev_schema = os.environ.get("CB_SCHEMA_PATH")
        os.environ["CB_CORPUS_DIR"] = str(test_corpus)
        os.environ["CB_SCHEMA_PATH"] = str(test_schemas / "entity.schema.json")
        _SCHEMA_CACHE.clear()

        print(f"== Self-test in {test_corpus} ==\n")

        def check(name: str, cond: bool, detail: str = ""):
            nonlocal failures
            mark = "PASS" if cond else "FAIL"
            print(f"  [{mark}] {name}{(' -- ' + detail) if detail else ''}")
            if not cond:
                failures += 1

        try:
            # 1. stats
            s = stats()
            print(f"-- stats: {s['entity_count']} entities, "
                  f"by_kind={s['by_kind']}, schema_version={s['schema_version']}")
            check("stats: nonzero entity count", s["entity_count"] > 0)
            check("stats: schema_version detected",
                  s["schema_version"] is not None)
            check("stats: embeddings status reported",
                  isinstance(s.get("embeddings"), dict))

            # 2. resolve
            r = resolve("Klaveness")
            print(f"-- resolve('Klaveness'): top="
                  f"{r['matches'][0]['id'] if r['matches'] else None}")
            check("resolve: 'Klaveness' -> org:kcc",
                  any(m["id"] == "org:kcc" for m in r["matches"]))
            r2 = resolve("kcc")
            check("resolve: slug 'kcc' is top match (0.95)",
                  bool(r2["matches"]) and r2["matches"][0]["id"] == "org:kcc"
                  and r2["matches"][0]["score"] >= 0.95)

            # 3. wiki_ask substring
            a = wiki_ask("route optimization", depth=1, budget=4000, mode="substring")
            print(f"-- wiki_ask substring 'route optimization': "
                  f"matched={a['stats']['matched']}, "
                  f"neighbors={a['stats']['neighbors']}, "
                  f"truncated={a['stats']['truncated']}")
            check("wiki_ask substring: >=1 matched",
                  a["stats"]["matched"] >= 1)

            # 3b. wiki_ask empty + no filter is rejected (no corpus-dump)
            a_empty = wiki_ask("", depth=0, budget=4000)
            check("wiki_ask: refuses dump-all when query+kind+topics all empty",
                  a_empty["stats"].get("error") is not None
                  and a_empty["stats"]["matched"] == 0)

            # 4. wiki_pack — depth-banded
            pack = wiki_pack("route optimization", budget=4000)
            print(f"-- wiki_pack 'route optimization' budget=4000: "
                  f"items={pack['stats']['items']}, "
                  f"used_tokens={pack['used_tokens']}, "
                  f"depths={pack['stats']['depth_breakdown']}")
            check("wiki_pack: returns items", pack["stats"]["items"] >= 1)
            check("wiki_pack: fits within budget",
                  pack["used_tokens"] <= 4000)
            check("wiki_pack: has depth banding",
                  len(pack["stats"]["depth_breakdown"]) >= 1)

            # 5. wiki_audit — baseline (clean corpus copy)
            audit = wiki_audit()
            s_ = audit["summary"]
            print(f"-- wiki_audit baseline: contradictions={s_['contradictions']}, "
                  f"dead_links={s_['dead_links']}, "
                  f"freshness_expired={s_['freshness_expired']}, "
                  f"orphans={s_['orphans']}, schema_invalid={s_['schema_invalid']}")
            check("wiki_audit baseline: 0 schema_invalid",
                  s_["schema_invalid"] == 0)
            check("wiki_audit baseline: 0 dead_links",
                  s_["dead_links"] == 0)
            baseline_contradictions = s_["contradictions"]

            # 5b. NEGATIVE test: inject a known-bad claim and confirm the
            # detector surfaces it. Use a synthetic claim that CONTRADICTS
            # existing KCC owner/owned/operating=16 (key collision, different value).
            kcc_path = test_corpus / "entities" / "org" / "kcc.json"
            if kcc_path.exists():
                kcc = json.loads(kcc_path.read_text(encoding="utf-8"))
                # Append a new measurement with the SAME key but a different value.
                for claim in kcc.get("claims", []):
                    if claim.get("metric") == "fleet_count" and claim.get("role") == "owner":
                        claim.setdefault("measurements", []).append({
                            "cp_type": "owned", "tenor": "n/a", "status": "operating",
                            "value": 12,
                            "source": "test:synthetic-contradiction",
                        })
                        break
                kcc_path.write_text(json.dumps(kcc, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
                audit2 = wiki_audit()
                contradictions_kcc = [c for c in audit2["contradictions"]
                                      if c["key"]["entity"] == "org:kcc"]
                print(f"-- wiki_audit AFTER injecting contradiction: "
                      f"contradictions={audit2['summary']['contradictions']} "
                      f"(KCC={len(contradictions_kcc)})")
                check("wiki_audit: detects injected KCC contradiction",
                      len(contradictions_kcc) >= 1)
                check("wiki_audit: charter-normalization holds (other KCC "
                      "operating-vs-on-order still NOT flagged)",
                      audit2["summary"]["contradictions"] ==
                      baseline_contradictions + 1)

            # 6. wiki_add — synthetic + commit=False (no real git for tempdir)
            synthetic = {
                "id": "concept:cb-engine-tempdir-self-test",
                "kind": "concept",
                "names": ["cb_engine tempdir self-test marker"],
                "summary": "Synthetic entity for cb_engine self-test. Lives in tempdir only.",
                "wiki_links": [],
                "topics": ["test"],
                "evidence": [{
                    "source_id": "test:cb-engine-tempdir-self-test",
                    "stance": "asserts",
                    "quote": "self-test marker",
                }],
                "concept": {
                    "statement": "self-test write-roundtrip + git-commit pathway exercised",
                    "type": "observation",
                    "specificity": "high",
                    "falsifiability": "tested",
                    "maturity": "tested",
                },
                "provenance": {
                    "extracted_from": ["test:cb-engine-tempdir-self-test"],
                    "extractor": "cb_engine/self-test",
                    "extraction_method": "system",
                    "extraction_confidence": 1.0,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            add_result = wiki_add(synthetic, commit=False)
            print(f"-- wiki_add(synthetic, commit=False): ok={add_result.get('ok')}")
            check("wiki_add: synthetic validates + writes (no commit)",
                  add_result.get("ok") is True)

            # 6b. Path-traversal guard
            bad = dict(synthetic)
            bad["id"] = "concept:../../../etc/passwd"
            bad_result = wiki_add(bad, commit=False)
            print(f"-- wiki_add(traversal): ok={bad_result.get('ok')}, "
                  f"err_kind={bad_result.get('error_kind')}")
            check("wiki_add: rejects traversal slug",
                  bad_result.get("ok") is False
                  and bad_result.get("error_kind") == "ValidationError")

        finally:
            # Restore env so caller's state is intact.
            if prev_corpus is None:
                os.environ.pop("CB_CORPUS_DIR", None)
            else:
                os.environ["CB_CORPUS_DIR"] = prev_corpus
            if prev_schema is None:
                os.environ.pop("CB_SCHEMA_PATH", None)
            else:
                os.environ["CB_SCHEMA_PATH"] = prev_schema
            _SCHEMA_CACHE.clear()

    print(f"\n{'=' * 50}")
    print(f"Self-test: {'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    return 0 if failures == 0 else 1


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--corpus", default=None,
                    help="override CB_CORPUS_DIR for this invocation")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("stats")

    aa = sub.add_parser("wiki-ask")
    aa.add_argument("query")
    aa.add_argument("--kind")
    aa.add_argument("--depth", type=int, default=1)
    aa.add_argument("--budget", type=int, default=8000)
    aa.add_argument("--mode", default="hybrid",
                    choices=["substring", "semantic", "hybrid"])

    ap_pack = sub.add_parser("wiki-pack")
    ap_pack.add_argument("query")
    ap_pack.add_argument("--budget", type=int, default=8000)
    ap_pack.add_argument("--mode", default="hybrid",
                         choices=["substring", "semantic", "hybrid"])
    ap_pack.add_argument("--kind")

    au = sub.add_parser("wiki-audit")
    au.add_argument("--kinds", nargs="*")

    rs = sub.add_parser("resolve")
    rs.add_argument("query")

    sub.add_parser("build-embeddings",
                   help="(re)build semantic embedding cache for the corpus")

    args = ap.parse_args()
    if args.corpus:
        os.environ["CB_CORPUS_DIR"] = args.corpus

    if args.self_test:
        sys.exit(_self_test_in_tempdir())

    if args.cmd == "stats":
        print(json.dumps(stats(), indent=2))
    elif args.cmd == "wiki-ask":
        print(json.dumps(
            wiki_ask(args.query, kind=args.kind, depth=args.depth,
                     budget=args.budget, mode=args.mode),
            indent=2, default=str))
    elif args.cmd == "wiki-pack":
        print(json.dumps(
            wiki_pack(args.query, kind=args.kind, budget=args.budget,
                      mode=args.mode),
            indent=2, default=str))
    elif args.cmd == "wiki-audit":
        print(json.dumps(wiki_audit(kinds=args.kinds), indent=2))
    elif args.cmd == "resolve":
        print(json.dumps(resolve(args.query), indent=2))
    elif args.cmd == "build-embeddings":
        if cb_embed is None:
            print("cb_embed module not loaded", file=sys.stderr)
            sys.exit(1)
        cdir = _resolve_corpus(None)
        cache = cb_embed.build_embeddings(cdir, load_corpus(cdir))
        embedded = sum(1 for v in cache.values() if v.get("embedding"))
        print(f"OK — cache has {embedded}/{len(cache)} embedded entities at "
              f"{cdir / '.cb_embed_cache.json'}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
