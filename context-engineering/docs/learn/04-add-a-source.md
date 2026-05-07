# Step 4 — Add a source

> **Neural systems: multi-corpus.** The engine doesn't care whether claims came from code, transcripts, emails, or CRM notes. They all flow through the same `Source` contract.

## What you'll do

Implement a tiny custom `Source` — say, a markdown notes directory — and feed events from it into the brain's append-only events log. This is the same contract every connector implements (Granola, Slack, Notion, HubSpot, Gmail in our scribes; `WorkspaceSource` and `GithubRepoSource` shipped in this skill).

## The contract

`scripts/wiki/source_adapter.py` defines `Source` (ABC). Four methods, two control flows:

| Method | Pull-shaped (indexer drives) | Push-shaped (skill drives) |
|--------|-------------------------------|------------------------------|
| `list_artifacts()` | enumerate paths/URLs/IDs | return `[]` |
| `fetch(ref)` | read raw bytes | raise `NotImplementedError` |
| `metadata(ref)` | `{mtime, hash, author, ...}` | raise `NotImplementedError` |
| `emit_events(...)` | parse content → list of events → append | append events directly |

The only method that *must* do real work is `emit_events`. Everything else is for pull-shaped sources where the engine drives traversal.

## The event shape

Every event written to the brain's log requires four keys:

| Required | Type | Meaning |
|----------|------|---------|
| `source_type` | `str` | The connector kind (e.g. `"notes"`, `"slack"`, `"granola"`) |
| `source_ref` | `str` | Stable reference to the artifact (path, URL, message ID) |
| `file_id` | `str` | A logical grouping ID — multiple events can share one |
| `claim` | `str` | The extracted statement |

Optional: `entity_hint` (slug used by `wiki_init` to group events into pages), `embedding_id`, `ts` (auto-stamped to wall-clock if absent), `symbol` (for code-backlink events).

This is not a freeform JSON dict — `EventStreamSource._validate` rejects events missing any required key, *atomically* across a batch (M3: either all events land or none do).

## A minimal pull-shaped Source

Save as `scripts/sources/notes_source.py`:

```python
from pathlib import Path
import hashlib
import re
from scripts.wiki.source_adapter import Source
from scripts.wiki.events import append_event


class NotesSource(Source):
    """Index a directory of markdown notes as events."""

    SOURCE_KIND = "notes"

    def __init__(self, root: Path, events_dir: Path):
        self.root = Path(root)
        self.events_dir = Path(events_dir)

    def list_artifacts(self) -> list[str]:
        return [str(p) for p in self.root.rglob("*.md")]

    def fetch(self, ref: str) -> bytes:
        return Path(ref).read_bytes()

    def metadata(self, ref: str) -> dict:
        p = Path(ref)
        b = p.read_bytes()
        return {
            "mtime": int(p.stat().st_mtime),
            "hash": hashlib.sha256(b).hexdigest(),
            "size": len(b),
        }

    def emit_events(self, events=None, *, ref=None, content=None) -> int:
        text = (content or self.fetch(ref)).decode("utf-8", "replace")
        meta = self.metadata(ref)
        slug = re.sub(r"[^a-z0-9]+", "-", Path(ref).stem.lower()).strip("-")
        n = 0
        for i, para in enumerate(p.strip() for p in text.split("\n\n") if p.strip()):
            append_event(
                self.events_dir,
                source_type=self.SOURCE_KIND,
                source_ref=f"{ref}#para-{i}",
                file_id=meta["hash"],
                claim=para[:500],
                entity_hint=slug,
                ts=meta["mtime"],
            )
            n += 1
        return n
```

## Wire it up

```python
from pathlib import Path
from scripts.sources.notes_source import NotesSource

src = NotesSource(root=Path("./notes"), events_dir=Path("./brain/events"))
for ref in src.list_artifacts():
    src.emit_events(ref=ref, content=src.fetch(ref))
```

The append-only events log under `brain/events/*.jsonl` is the brain's raw layer. Every later step in this ladder reads from there.

## Push-shaped sources

If you already have structured events from somewhere else (a webhook handler, a Slack bot, a Granola transcript scribe), use `EventStreamSource` directly:

```python
from pathlib import Path
from scripts.wiki.source_adapter import EventStreamSource

src = EventStreamSource(events_dir=Path("brain/events"))
n = src.emit_events([
    {
        "source_type": "slack",
        "source_ref": "slack://C123/p1714780800.000100",
        "file_id": "design-channel-2026-05",
        "claim": "We're going with OKLCH for the new palette.",
        "entity_hint": "oklch-palette",
    },
])
# n == 1; brain/events/<today>.jsonl now has the new line
```

No traversal, no fetching. Skills push events; the engine consolidates. `EventStreamSource` validates the whole batch up front and either appends all events or none — a malformed event at index N never leaves events 0..N-1 written to disk.

## What's deliberately not in this skill

The skill ships `WorkspaceSource` (step 2) and `GithubRepoSource` (step 2). Connectors for Granola, Slack, Notion, Gmail, HubSpot, JIRA, etc. live elsewhere — in the [`scribes`](https://github.com/victorgjn/agent-skills/tree/main/scribes) skill and in `syroco-product-ops`. Keeping connector implementations out of the engine is deliberate: the engine is the contract; connectors are the catalogue.

## Concept

Every Source feeds the same events log. The packer (steps 0-3) doesn't read events directly — it reads the *index* over a workspace. The next step is the bridge: how raw events become consolidated entity pages the packer can serve.

## Next

[Step 5 — EntityStore and wiki](05-entitystore-and-wiki.md). Compounding memory: the wiki layer that turns events into pages with full provenance.
