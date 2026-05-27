"""Append-only event log — the GAM 'event progression graph'.

One JSONL file per UTC day under `events/`. Each line is a single observation
or claim extracted from a source. We never rewrite an event. Synthesis
(Phase 2) reads the queue, decides what to consolidate into wiki/, and marks
the consumed events with a sidecar pointer — but never edits the events file.

Schema (v1.0):
    {
      schema_version: str,    # canonical SCHEMA_VERSION at write time
      ts: int,                # epoch seconds
      source_type: str,       # workspace | github | notion | …
      source_ref: str,        # path / repo@sha:path / notion page id / …
      file_id: str,           # stable id for the artifact
      claim: str,             # 1-3 sentence extracted statement
      entity_hint: str|null,  # synthesizer's best guess at owning entity slug
      embedding_id: str|null  # → events/<date>.embeddings.jsonl
    }

Per phase-1.md §1.2.1: events forward-migrate from day one (events are
primary truth; sources may not exist anymore at migration time, so we
can't rebuild). The schema_version field is what migrators key off.
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

# Canonical event-line schema. Bumping this requires a migrator under
# scripts/wiki/migrations/ (per phase-1.md §1.2.1). read_events tolerates
# legacy rows missing the field — treats them as v1.0.
#
# L1 fix: renamed from SCHEMA_VERSION to EVENT_SCHEMA_VERSION to
# disambiguate from validate_page.SCHEMA_VERSION (the wiki PAGE schema,
# currently 1.1, evolves on a different cadence). Same name for two
# different versioned schemas was a footgun for any future contributor
# importing "from .events import SCHEMA_VERSION" expecting the page
# schema. The legacy alias is kept as `SCHEMA_VERSION = EVENT_SCHEMA_VERSION`
# for one release cycle to avoid breaking external imports.
EVENT_SCHEMA_VERSION = "1.0"
SCHEMA_VERSION = EVENT_SCHEMA_VERSION  # deprecated alias; remove next bump

# Match canonical event-log filenames: <YYYY-MM-DD>.jsonl. Sidecar files like
# <YYYY-MM-DD>.embeddings.jsonl must NOT be picked up by read_events — those
# rows have a different schema and would corrupt downstream consolidation.
_EVENT_FILE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.jsonl$')


def _today_path(events_dir: Path) -> Path:
    events_dir.mkdir(parents=True, exist_ok=True)
    return events_dir / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"


def append_event(events_dir: Path, *, source_type: str, source_ref: str,
                 file_id: str, claim: str, entity_hint: str | None = None,
                 embedding_id: str | None = None, ts: int | None = None,
                 symbol: str | None = None) -> None:
    """Append one event line. Never blocks on a lock — append is atomic in POSIX
    for small writes; on Windows we accept the rare interleaving risk because
    a corrupted line is recoverable (parser skips bad lines).

    Phase 3 (CE x lat.md): optional ``symbol`` kwarg carries the AST-
    resolved symbol name for code-backlink events (`source_type=
    "code-backlink"`). When present, ``wiki_init`` emits the source
    citation as a lat.md-compatible ``[[src/path#symbol]]`` ref. Older
    callers omit ``symbol``; the field is written only when non-None so
    legacy event-line shape is preserved.
    """
    rec = {
        'schema_version': EVENT_SCHEMA_VERSION,
        'ts': int(ts or time.time()),
        'source_type': source_type,
        'source_ref': source_ref,
        'file_id': file_id,
        'claim': claim,
        'entity_hint': entity_hint,
        'embedding_id': embedding_id,
    }
    if symbol is not None:
        rec['symbol'] = symbol
    path = _today_path(events_dir)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def read_events(events_dir: Path, *, since_ts: int = 0,
                entity_hint: str | None = None) -> list[dict]:
    """Read events across all JSONL files in `events_dir`. Filters out lines
    older than `since_ts` and (optionally) lines whose entity_hint doesn't match.
    Skips malformed lines silently — events are append-only, corruption is rare."""
    if not events_dir.exists():
        return []
    out: list[dict] = []
    for jsonl in sorted(events_dir.glob('*.jsonl')):
        # Skip sidecar files (e.g. <date>.embeddings.jsonl) that share the
        # parent directory but have a different schema.
        if not _EVENT_FILE_RE.match(jsonl.name):
            continue
        try:
            with open(jsonl, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # JSON scalars / arrays / nulls also parse cleanly but
                    # would AttributeError on `.get(...)`. Skip non-objects —
                    # `append_event` only ever writes dicts.
                    if not isinstance(rec, dict):
                        continue
                    # `ts` may be a string or other non-numeric value if a
                    # different producer / hand-edit slipped one through.
                    # `<` would raise TypeError and abort the whole scan, so
                    # silently skip the row instead.
                    ts_val = rec.get('ts', 0)
                    if not isinstance(ts_val, (int, float)) or isinstance(ts_val, bool):
                        continue
                    if ts_val < since_ts:
                        continue
                    if entity_hint is not None and rec.get('entity_hint') != entity_hint:
                        continue
                    out.append(rec)
        except OSError:
            continue
    return out
