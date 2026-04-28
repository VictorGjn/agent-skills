"""Append-only event log — the GAM 'event progression graph'.

One JSONL file per UTC day under `events/`. Each line is a single observation
or claim extracted from a source. We never rewrite an event. Synthesis
(Phase 2) reads the queue, decides what to consolidate into wiki/, and marks
the consumed events with a sidecar pointer — but never edits the events file.

Schema:
    {
      ts: int,                # epoch seconds
      source_type: str,       # workspace | github | notion | …
      source_ref: str,        # path / repo@sha:path / notion page id / …
      file_id: str,           # stable id for the artifact
      claim: str,             # 1-3 sentence extracted statement
      entity_hint: str|null,  # synthesizer's best guess at owning entity slug
      embedding_id: str|null  # → events/<date>.embeddings.jsonl
    }
"""
from __future__ import annotations
import json
import time
from pathlib import Path


def _today_path(events_dir: Path) -> Path:
    events_dir.mkdir(parents=True, exist_ok=True)
    return events_dir / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"


def append_event(events_dir: Path, *, source_type: str, source_ref: str,
                 file_id: str, claim: str, entity_hint: str | None = None,
                 embedding_id: str | None = None, ts: int | None = None) -> None:
    """Append one event line. Never blocks on a lock — append is atomic in POSIX
    for small writes; on Windows we accept the rare interleaving risk because
    a corrupted line is recoverable (parser skips bad lines)."""
    rec = {
        'ts': int(ts or time.time()),
        'source_type': source_type,
        'source_ref': source_ref,
        'file_id': file_id,
        'claim': claim,
        'entity_hint': entity_hint,
        'embedding_id': embedding_id,
    }
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
                    if rec.get('ts', 0) < since_ts:
                        continue
                    if entity_hint is not None and rec.get('entity_hint') != entity_hint:
                        continue
                    out.append(rec)
        except OSError:
            continue
    return out
