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


class GraphifyWikiSource(Source):
    """Pull-shaped Source: consumes graphify v0.1.7+ ``--wiki`` output.

    S1 from ``plan/prd-closed-loop.md`` (Wave 1). graphify
    (`safishamsi/graphify`) writes Wikipedia-style entity pages under
    ``graphify-out/wiki/`` — rich enough to seed CE, but lacking CE's
    frontmatter (no `id`, no `last_verified_at`, no `scope`). This Source
    reads those pages and re-emits each as one CE event keyed on the
    page slug, so ``wiki_init`` consolidates them through CE's normal
    pipeline.

    Hybrid model per phase-1.md §1.2.0: preserves user choice (run
    graphify upstream if you want; CE consumes it without duplicating
    community-detection or wikilink generation).

    AC9: ``list_artifacts()`` enumerates ``graphify-out/wiki/*.md``;
    ``emit_events()`` produces CE-schema-compliant events per page.
    """

    SOURCE_KIND = "graphify-wiki"

    def __init__(self, graphify_out_dir: Path, events_dir: Path):
        self.graphify_out_dir = Path(graphify_out_dir)
        self.events_dir = Path(events_dir)

    def list_artifacts(self) -> list[str]:
        wiki_dir = self.graphify_out_dir / "wiki"
        if not wiki_dir.exists():
            return []
        return sorted(
            str(p.relative_to(self.graphify_out_dir).as_posix())
            for p in wiki_dir.glob("*.md")
            if not p.name.startswith("_")
        )

    def fetch(self, ref: str) -> bytes:
        path = self.graphify_out_dir / ref
        return path.read_bytes()

    def metadata(self, ref: str) -> dict:
        path = self.graphify_out_dir / ref
        try:
            stat = path.stat()
        except OSError:
            return {"ref": ref, "exists": False}
        return {
            "ref": ref,
            "mtime": int(stat.st_mtime),
            "size": stat.st_size,
            "exists": True,
        }

    def emit_events(self, events: list[dict] | None = None,
                    *, ref: str | None = None,
                    content: bytes | None = None) -> int:
        """Parse graphify wiki page(s) and append CE events.

        Three call shapes:
            emit_events(ref="wiki/foo.md", content=<bytes>) → parse one artifact
            emit_events(ref="wiki/foo.md") → self-fetch via self.fetch(ref)
            emit_events() → walk every artifact in list_artifacts()
        """
        if events:
            return EventStreamSource(self.events_dir).emit_events(events)

        appended = 0
        if ref is not None:
            payload = content if content is not None else self.fetch(ref)
            appended += self._emit_from_artifact(ref, payload)
            return appended

        for art_ref in self.list_artifacts():
            try:
                payload = self.fetch(art_ref)
            except OSError:
                continue
            appended += self._emit_from_artifact(art_ref, payload)
        return appended

    def _emit_from_artifact(self, ref: str, payload: bytes) -> int:
        """Convert one graphify wiki page to a single CE event.

        V0.1: each page = one event whose claim is the page's first
        non-frontmatter paragraph (or the file's title heading if no
        paragraph). entity_hint = the file's slug.

        Future: split per-section / per-wikilink so a 50-claim graphify
        page produces 50 events for finer-grained consolidation.
        """
        try:
            text = payload.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, AttributeError):
            return 0

        slug = Path(ref).stem
        title = slug.replace("-", " ").replace("_", " ").title()
        claim = _first_meaningful_paragraph(text) or title

        append_event(
            self.events_dir,
            source_type=self.SOURCE_KIND,
            source_ref=ref,
            file_id=f"graphify-{slug}",
            claim=claim,
            entity_hint=slug,
        )
        return 1


def _first_meaningful_paragraph(text: str) -> str:
    """Skip frontmatter + heading lines; return first prose paragraph.

    Caps the result at 280 chars so events stay scannable in the log.
    """
    in_frontmatter = False
    started = False
    para: list[str] = []
    for line in text.splitlines():
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        stripped = line.strip()
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith("#"):
            continue
        para.append(stripped)
        started = True
    if not para:
        return ""
    text_out = " ".join(para)
    return text_out if len(text_out) <= 280 else text_out[:277] + "..."


# WorkspaceSource and GithubRepoSource land later (per phase-1.md §1.4 +
# Wave 2 scope). The Source ABC above is what they slot into when shipped.
