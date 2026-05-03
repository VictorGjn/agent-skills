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

import os
import re
import sys
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

        M3 fix: validates the WHOLE batch before appending any event, so
        a malformed event at index N never leaves events 0..N-1 written
        to disk. The prior validate-inside-the-loop semantics required
        the caller to inspect ``e.appended_before_error`` to resume; now
        the batch is atomic — either all events land or none do.
        """
        if events is None:
            return 0
        # Batch-validate up front. Any failure raises with the bad index
        # AND appended_before_error=0 attached to the exception (consumers
        # may still look for it; emitted as a structured attribute rather
        # than embedded in the message).
        for i, ev in enumerate(events):
            try:
                self._validate(ev)
            except ValueError as e:
                e.appended_before_error = 0  # type: ignore[attr-defined]
                e.failed_index = i  # type: ignore[attr-defined]
                raise
        appended = 0
        for ev in events:
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
        # Codex P2 fix: distinguish "explicit events list (possibly empty)"
        # from "no events arg, walk artifacts." `if events:` treats `[]` as
        # falsy and falls through to walk mode — that's semantically wrong
        # and risks appending duplicate events when the caller meant a no-op.
        if events is not None:
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


# `// @lat: [[entity-slug]]` (TS/JS/Java/Go/Rust/C/C++/C#/Kotlin/Scala/PHP/Swift)
# `# @lat: [[entity-slug]]`  (Python/Ruby)
# Anchored at line start; whitespace tolerant on either side of the colon.
_AT_LAT_RE = re.compile(
    r"^[ \t]*(?://|\#)[ \t]*@lat:[ \t]*\[\[\s*(?P<ref>[^\]]+?)\s*\]\][ \t]*$",
    re.MULTILINE,
)


class SourceCommentBacklinkSource(Source):
    """Pull-shaped Source: walks a codebase and emits one CE event per
    ``// @lat:`` (or ``# @lat:``) source-code comment.

    Phase 3 of CE x lat.md interop. Lets developers annotate implementation
    code with explicit backlinks to wiki entities; the auditor + retrieval
    surface then know which symbols implement which concept WITHOUT relying
    on heuristic AST extraction alone.

    Each comment becomes a code-backlink event::

        {
            'source_type':   'code-backlink',
            'source_ref':    'src/foo.ts:142',         # path:line
            'file_id':       'sha256[:12] of file content',
            'claim':         '<surrounding 5 lines>',
            'entity_hint':   'auth-middleware',         # parsed from [[ref]]
            'symbol':        'validateToken',           # AST-resolved
        }

    The events flow through the existing ``events.append_event`` path; the
    consolidator already dedupes by ``(source_type, source_ref)``, so each
    comment lands as ONE source on the target entity's wiki page with zero
    ``wiki_init`` change. ``symbol`` causes wiki_init to render the source
    as ``[[src/path#symbol]]`` (Phase 1 forward-looking branch).
    """

    SOURCE_KIND = "code-backlink"

    # Mirror code_index's vendor-skip + extension config so this Source sees
    # the same file population the auditor will validate against.
    SKIP_DIRS: frozenset[str] = frozenset({
        ".git", "node_modules", "__pycache__", ".cache", "assets",
        "screenshots", ".next", ".turbo", "dist", "build", "out", "coverage",
        ".vscode", ".idea", "vendor", "target",
    })
    MAX_FILE_SIZE = 200_000
    SURROUND_LINES = 2  # emit `claim` as 2 lines before + the comment line + 2 after

    def __init__(self, repo_root: Path, events_dir: Path):
        self.repo_root = Path(repo_root).resolve()
        self.events_dir = Path(events_dir)

    def list_artifacts(self) -> list[str]:
        """Enumerate every code file with at least one ``@lat:`` comment.

        Pre-grep so callers (and tests) see only files that produce events.
        """
        # Lazy import: ast_extract pulls tree-sitter; only load if walked.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from ast_extract import EXT_TO_LANG  # noqa: E402
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = sorted(d for d in dirnames if d not in self.SKIP_DIRS)
            for name in filenames:
                ext = Path(name).suffix.lower()
                if ext not in EXT_TO_LANG:
                    continue
                p = Path(dirpath) / name
                try:
                    if p.stat().st_size > self.MAX_FILE_SIZE:
                        continue
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "@lat:" not in text:
                    continue
                out.append(str(p.relative_to(self.repo_root)).replace(os.sep, "/"))
        out.sort()
        return out

    def fetch(self, ref: str) -> bytes:
        path = self.repo_root / ref
        return path.read_bytes()

    def metadata(self, ref: str) -> dict:
        path = self.repo_root / ref
        try:
            stat = path.stat()
        except OSError:
            return {"ref": ref, "exists": False}
        return {"ref": ref, "mtime": int(stat.st_mtime), "size": stat.st_size, "exists": True}

    def emit_events(self, events: list[dict] | None = None,
                    *, ref: str | None = None,
                    content: bytes | None = None) -> int:
        """Three call shapes (mirrors GraphifyWikiSource):

        - ``emit_events(events=[...])`` -- caller-supplied events; passes
          through to ``EventStreamSource``.
        - ``emit_events(ref="src/foo.ts")`` -- parse ONE artifact (auto-fetch
          unless ``content`` supplied).
        - ``emit_events()`` -- walk the repo and emit for every comment.
        """
        if events is not None:
            return EventStreamSource(self.events_dir).emit_events(events)

        if ref is not None:
            payload = content if content is not None else self.fetch(ref)
            return self._emit_from_artifact(ref, payload)

        appended = 0
        for art_ref in self.list_artifacts():
            try:
                payload = self.fetch(art_ref)
            except OSError:
                continue
            appended += self._emit_from_artifact(art_ref, payload)
        return appended

    def _emit_from_artifact(self, ref: str, payload: bytes) -> int:
        """Extract every ``@lat:`` comment in one source file. Returns the
        count of events appended.
        """
        try:
            text = payload.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, AttributeError):
            return 0
        # Normalize line endings to LF so the `$` anchor in _AT_LAT_RE works
        # on Windows-authored files (CRLF leaves a `\r` before `\n` which
        # the regex's `[ \t]*$` rejects). UTF-16 BOMs are decoded by
        # decode() above.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if "@lat:" not in text:
            return 0

        # Lazy AST extraction so files without comments don't pay the cost.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from ast_extract import extract_symbols, lang_from_path  # noqa: E402
        from .wikiref import parse_wikiref  # noqa: E402

        lang = lang_from_path(ref)
        symbols = extract_symbols(lang, text) if lang else []

        # Stable file_id: sha256[:12] of content. Same shape used elsewhere
        # in CE caches; collision-safe up to ~16M files.
        import hashlib
        file_id = "sha256:" + hashlib.sha256(payload).hexdigest()[:12]

        lines = text.splitlines()
        appended = 0
        for m in _AT_LAT_RE.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1  # 1-based
            ref_inside = m.group("ref")
            wikiref = parse_wikiref(ref_inside)
            if wikiref is None:
                continue  # Malformed [[...]]; skip silently.

            # Use parse_wikiref's slug property: for slug/section refs,
            # this is the target verbatim; for code refs, it's the
            # basename without extension. The latter is unusual inside an
            # @lat: annotation but harmless.
            entity_hint = wikiref.slug or wikiref.target

            symbol = self._symbol_at_line(symbols, line_no)
            claim = self._surrounding_lines(lines, line_no)

            append_event(
                self.events_dir,
                source_type=self.SOURCE_KIND,
                source_ref=f"{ref}:{line_no}",
                file_id=file_id,
                claim=claim,
                entity_hint=entity_hint,
                symbol=symbol,
            )
            appended += 1
        return appended

    def _symbol_at_line(self, symbols: list[dict], line_no: int) -> str | None:
        """Return the name of the innermost symbol whose [line, end_line]
        range contains ``line_no``. Innermost wins -- a method inside a
        class wins over the class. Returns None when no symbol contains
        the line (e.g., the comment is at module top-level).
        """
        best: dict | None = None
        best_size = float("inf")
        for s in symbols:
            start = s.get("line", 0)
            end = s.get("end_line", 0)
            if start <= line_no <= end:
                size = end - start
                if size < best_size:
                    best = s
                    best_size = size
        return best["name"] if best else None

    def _surrounding_lines(self, lines: list[str], line_no: int) -> str:
        lo = max(0, line_no - 1 - self.SURROUND_LINES)
        hi = min(len(lines), line_no + self.SURROUND_LINES)
        snippet = "\n".join(lines[lo:hi])
        # Cap claim at 280 chars so the events log stays scannable.
        if len(snippet) > 280:
            snippet = snippet[:277] + "..."
        return snippet
