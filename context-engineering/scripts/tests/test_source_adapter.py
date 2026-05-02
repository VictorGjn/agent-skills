"""Unit tests for scripts/wiki/source_adapter.py.

Covers the Source ABC contract + EventStreamSource concrete (M1 in
plan/prd-closed-loop.md). EventStreamSource is the push-shaped Source:
skills supply pre-built event dicts, EventStreamSource appends them to
the events log via the existing events.append_event helper.

Acceptance criterion AC1 from prd-closed-loop.md:
  Given a brain with N existing entities, when a skill calls
  EventStreamSource.emit_events([{...}]), then events/<today>.jsonl
  contains the new event line within 100ms.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from wiki.source_adapter import EventStreamSource, Source  # noqa: E402
from wiki.events import read_events  # noqa: E402


class EventStreamSourceContractTests(unittest.TestCase):
    """The push-shaped Source contract."""

    def test_inherits_source_abc(self):
        """EventStreamSource is a Source subclass."""
        self.assertTrue(issubclass(EventStreamSource, Source))

    def test_list_artifacts_returns_empty(self):
        """Push-shaped sources have no artifacts to enumerate."""
        with tempfile.TemporaryDirectory() as td:
            src = EventStreamSource(events_dir=Path(td))
            self.assertEqual(src.list_artifacts(), [])

    def test_fetch_raises_not_implemented(self):
        """Push-shaped sources reject fetch — there's nothing to fetch."""
        with tempfile.TemporaryDirectory() as td:
            src = EventStreamSource(events_dir=Path(td))
            with self.assertRaises(NotImplementedError):
                src.fetch('any-ref')

    def test_metadata_raises_not_implemented(self):
        """Push-shaped sources reject metadata for the same reason."""
        with tempfile.TemporaryDirectory() as td:
            src = EventStreamSource(events_dir=Path(td))
            with self.assertRaises(NotImplementedError):
                src.metadata('any-ref')


class EventStreamSourceEmitTests(unittest.TestCase):
    """The actual emit + round-trip behavior. Maps to AC1."""

    def _required_event(self, **overrides) -> dict:
        base = {
            'source_type': 'manual',
            'source_ref': 'test/competitive-intel-routine',
            'file_id': 'acme-pricing-2026q2',
            'claim': 'Acme raised pricing tier from $5k to $7k.',
            'entity_hint': 'acme-pricing',
        }
        base.update(overrides)
        return base

    def test_appended_event_has_schema_version(self):
        """Pre-ultrareview cleanup C2: per phase-1.md §1.2.1 every event
        line MUST include schema_version (events forward-migrate from
        day one; sources may not exist at migration time, so we can't
        rebuild without a v-marker on the row itself)."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)
            src.emit_events([self._required_event()])

            today = time.strftime("%Y-%m-%d", time.gmtime())
            written = (events_dir / f"{today}.jsonl").read_text(encoding="utf-8")
            rec = json.loads(written.strip().splitlines()[0])
            self.assertEqual(rec.get("schema_version"), "1.0",
                             "every event row must include schema_version")

    def test_read_events_tolerates_legacy_rows_without_schema_version(self):
        """C2 backwards-compat: legacy event-log lines (pre-cleanup) lack
        schema_version. read_events MUST still load them — events are
        append-only, so we can't rewrite history; treat absent
        schema_version as the implicit pre-versioning baseline."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            today = time.strftime("%Y-%m-%d", time.gmtime())
            events_dir.mkdir(exist_ok=True)
            with open(events_dir / f"{today}.jsonl", "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": 1700000000,
                    "source_type": "manual",
                    "source_ref": "legacy/x",
                    "file_id": "legacy-x",
                    "claim": "Legacy row, no schema_version",
                    "entity_hint": "legacy",
                }) + "\n")
                f.write(json.dumps({
                    "schema_version": "1.0",
                    "ts": 1700001000,
                    "source_type": "manual",
                    "source_ref": "new/y",
                    "file_id": "new-y",
                    "claim": "New row, with schema_version",
                    "entity_hint": "new",
                }) + "\n")

            events = read_events(events_dir)
            self.assertEqual(len(events), 2,
                             "read_events must accept legacy rows without schema_version")

    def test_emit_single_event_appends_to_today_log(self):
        """AC1: skill calls emit_events; today's events JSONL has the line."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            t0 = time.time()
            n = src.emit_events([self._required_event()])
            dt_ms = (time.time() - t0) * 1000

            self.assertEqual(n, 1)
            self.assertLess(dt_ms, 100, f"emit took {dt_ms:.1f}ms, AC1 caps at 100ms")

            # Round-trip via read_events: the event we emitted is readable
            events = read_events(events_dir)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['source_type'], 'manual')
            self.assertEqual(events[0]['claim'],
                             'Acme raised pricing tier from $5k to $7k.')
            self.assertEqual(events[0]['entity_hint'], 'acme-pricing')

    def test_emit_multiple_events_in_one_call(self):
        """Batched emits all land; order preserved on disk."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            evs = [
                self._required_event(claim=f'fact {i}', file_id=f'f{i}')
                for i in range(5)
            ]
            n = src.emit_events(evs)
            self.assertEqual(n, 5)

            events = read_events(events_dir)
            self.assertEqual(len(events), 5)
            for i, e in enumerate(events):
                self.assertEqual(e['claim'], f'fact {i}')

    def test_emit_with_explicit_ts(self):
        """ts in the event dict is honored, not auto-stamped."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            fixed_ts = 1735603200  # 2024-12-31 00:00:00 UTC
            src.emit_events([self._required_event(ts=fixed_ts)])

            # Read directly from today's file (the ts inside the JSON should
            # be the fixed one even though the file is named for today's
            # date — auto-stamping is per-event, not per-file).
            today = time.strftime('%Y-%m-%d', time.gmtime())
            written = (events_dir / f'{today}.jsonl').read_text(encoding='utf-8')
            rec = json.loads(written.strip().splitlines()[0])
            self.assertEqual(rec['ts'], fixed_ts)

    def test_emit_auto_stamps_ts_when_absent(self):
        """When ts is omitted, EventStreamSource auto-stamps wall-clock."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            t_before = int(time.time())
            src.emit_events([self._required_event()])  # no ts
            t_after = int(time.time())

            events = read_events(events_dir)
            self.assertEqual(len(events), 1)
            ts = events[0]['ts']
            self.assertGreaterEqual(ts, t_before)
            self.assertLessEqual(ts, t_after)

    def test_emit_empty_list_is_noop(self):
        """Calling with [] returns 0 and writes nothing."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            self.assertEqual(src.emit_events([]), 0)
            # No JSONL files created
            self.assertEqual(list(events_dir.glob('*.jsonl')), [])

    def test_emit_none_is_noop(self):
        """Calling with no events arg returns 0; matches the ABC default
        signature (push-shape uses keyword args only)."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            self.assertEqual(src.emit_events(), 0)
            self.assertEqual(list(events_dir.glob('*.jsonl')), [])

    def test_emit_validates_required_keys(self):
        """Missing source_type / source_ref / file_id / claim raises."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            for missing_key in ('source_type', 'source_ref', 'file_id', 'claim'):
                bad = self._required_event()
                del bad[missing_key]
                with self.assertRaises(ValueError) as cm:
                    src.emit_events([bad])
                self.assertIn(missing_key, str(cm.exception))

    def test_emit_validates_no_partial_writes_on_invalid(self):
        """If event N in the batch is invalid, prior events still appended,
        but the invalid one raises (caller must handle resume semantics)."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            valid = self._required_event(claim='ok')
            invalid = self._required_event(claim='bad')
            del invalid['source_type']

            with self.assertRaises(ValueError):
                src.emit_events([valid, invalid])

            # The valid one was already appended before the invalid one
            # raised. This is documented behavior — emit_events doesn't
            # transactionally batch.
            events = read_events(events_dir)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['claim'], 'ok')

    def test_round_trip_with_entity_hint_filter(self):
        """Events emitted with entity_hint are filterable via read_events."""
        with tempfile.TemporaryDirectory() as td:
            events_dir = Path(td)
            src = EventStreamSource(events_dir=events_dir)

            src.emit_events([
                self._required_event(entity_hint='entity_a', claim='a1'),
                self._required_event(entity_hint='entity_b', claim='b1'),
                self._required_event(entity_hint='entity_a', claim='a2'),
            ])

            a_events = read_events(events_dir, entity_hint='entity_a')
            self.assertEqual(len(a_events), 2)
            self.assertSetEqual(
                {e['claim'] for e in a_events}, {'a1', 'a2'},
            )


if __name__ == '__main__':
    unittest.main()
