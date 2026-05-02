"""Source ABC + concrete subclasses for the closed-loop wiki layer.

Defines the contract every connector implements to feed events into the
brain's append-only events log. Two control flows coexist behind the same
ABC:

- **Pull-shaped** (WorkspaceSource, GithubRepoSource, GraphifyWikiSource):
  the indexer drives. `list_artifacts()` enumerates inputs; `fetch()` reads
  one; `metadata()` returns mtime/hash/etc.; `emit_events()` converts the
  artifact's contents to a list of event dicts and appends each via
  `events.append_event`.

- **Push-shaped** (EventStreamSource): a skill drives. `list_artifacts()`
  returns the empty list; `fetch()` and `metadata()` raise NotImplementedError.
  Skills call `emit_events(events)` directly with pre-built event dicts that
  get appended to the log. The Source ABC accommodates both because
  `emit_events` is the only method that matters for the events log.

Per ``plan/phases/phase-1.md`` §1.4 + ``plan/prd-closed-loop.md`` M1.

Phase 1 ships with EventStreamSource only (the closed-loop write path).
WorkspaceSource / GithubRepoSource / GraphifyWikiSource are documented in
the spec and slot into this same ABC when shipped (Wave 1).
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from .events import append_event


class Source(ABC):
    """Anabasis SignalSource ABC (CE reference impl).

    Every connector implements these four methods. Push-shaped sources
    (skills emitting events directly) override the pull methods to be
    no-ops or raise — see EventStreamSource below.
    """

    @abstractmethod
    def list_artifacts(self) -> list[str]:
        """Return artifact references (paths, URLs, IDs).

        Push-shaped sources return an empty list — there are no artifacts to
        enumerate; events arrive via `emit_events` calls from skills.
        """

    @abstractmethod
    def fetch(self, ref: str) -> bytes:
        """Fetch raw bytes for an artifact. Push-shaped sources raise
        NotImplementedError — there's nothing to fetch."""

    @abstractmethod
    def metadata(self, ref: str) -> dict:
        """Return source-type metadata (mtime, hash, author, etc.).
        Push-shaped sources raise NotImplementedError."""

    @abstractmethod
    def emit_events(self, events: list[dict] | None = None,
                    *, ref: str | None = None,
                    content: bytes | None = None) -> int:
        """Append events to the brain's log. Returns count appended.

        Pull shape: caller supplies (ref, content); implementer parses
        content into events and appends.
        Push shape: caller supplies events directly; implementer appends
        each to the events log without parsing.
        """


class EventStreamSource(Source):
    """Push-shaped Source: a skill emits pre-built event dicts.

    Closes the loop in the Anabasis architecture — any skill that has
    derived a claim from upstream sources can push it back into the brain
    via this Source, the same events log every other connector writes to.

    Usage from a skill::

        from scripts.wiki.source_adapter import EventStreamSource

        src = EventStreamSource(events_dir=Path('brain/events'))
        n = src.emit_events([
            {
                'source_type': 'manual',
                'source_ref': 'competitive-intel-routine/2026-05-02',
                'file_id': 'acme-pricing-2026q2',
                'claim': 'Acme raised pricing tier from $5k to $7k.',
                'entity_hint': 'acme-pricing',
            },
        ])
        # n == 1; brain/events/2026-05-02.jsonl now has the new line

    `ts` is auto-stamped to wall-clock if not supplied. Other fields per
    `events.append_event`.
    """

    SOURCE_KIND = "event-stream"

    def __init__(self, events_dir: Path):
        self.events_dir = Path(events_dir)

    # Pull-shape methods are no-ops / raise — events come from skills, not
    # from a corpus we can enumerate.

    def list_artifacts(self) -> list[str]:
        return []

    def fetch(self, ref: str) -> bytes:
        raise NotImplementedError(
            f"{type(self).__name__} is push-shaped: skills call emit_events() "
            "directly; there is nothing to fetch."
        )

    def metadata(self, ref: str) -> dict:
        raise NotImplementedError(
            f"{type(self).__name__} is push-shaped: skills call emit_events() "
            "directly; metadata is per-event, not per-ref."
        )

    def emit_events(self, events: list[dict] | None = None,
                    *, ref: str | None = None,
                    content: bytes | None = None) -> int:
        """Append each event dict to today's events log. Returns count.

        Required keys per event: `source_type`, `source_ref`, `file_id`,
        `claim`. Optional: `entity_hint`, `embedding_id`, `ts` (auto-stamped
        if absent).

        `ref` and `content` are ignored on this Source — they're for the
        pull-shape contract.
        """
        if events is None:
            return 0
        appended = 0
        for ev in events:
            self._validate(ev)
            append_event(
                self.events_dir,
                source_type=ev['source_type'],
                source_ref=ev['source_ref'],
                file_id=ev['file_id'],
                claim=ev['claim'],
                entity_hint=ev.get('entity_hint'),
                embedding_id=ev.get('embedding_id'),
                ts=ev.get('ts'),
            )
            appended += 1
        return appended

    @staticmethod
    def _validate(ev: dict) -> None:
        required = ('source_type', 'source_ref', 'file_id', 'claim')
        missing = [k for k in required if k not in ev or ev[k] is None]
        if missing:
            raise ValueError(
                f"EventStreamSource event missing required keys: {missing!r}. "
                f"Required: {list(required)}"
            )


# WorkspaceSource, GithubRepoSource, GraphifyWikiSource land in Wave 1
# (per plan/prd-closed-loop.md S1). The ABC above is what they slot into.
