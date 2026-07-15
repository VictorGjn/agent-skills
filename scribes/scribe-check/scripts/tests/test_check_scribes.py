"""Tests for scribe-check's output-validate C1 envelope rule, focused on the
`data_classification` field on Profile-B raw envelopes.

Synthetic scribe/source names only (`synthetic-scribe`, `synthetic:1`) — no
real internal identifiers, per this repo's public-visibility constraint.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from check_scribes import FAIL, validate_jsonl  # noqa: E402


def _base_line() -> dict:
    """A structurally-complete Profile-B envelope, missing only
    data_classification unless the caller adds it."""
    content_hash = hashlib.sha256(b"synthetic claim text").hexdigest()
    external_id = "synthetic:1"
    scribe = "synthetic-scribe"
    file_id = hashlib.sha256(f"{scribe}{external_id}{content_hash}".encode("utf-8")).hexdigest()
    return {
        "schema_version": "1.0",
        "scribe": scribe,
        "scribed_at": "2026-07-11T00:00:00Z",
        "source_type": "synthetic",
        "source_ref": "synthetic-ref",
        "file_id": file_id,
        "external_id": external_id,
        "content_hash": content_hash,
        "claim": "synthetic claim text",
        "ts": "2026-07-11T00:00:00Z",
        "entity_hint": f"{scribe}:1",
        "payload": {},
    }


def _fail_checks(findings) -> list[str]:
    return [f.check for f in findings if f.sev == FAIL]


def test_missing_data_classification_fails(tmp_path):
    line = _base_line()  # no data_classification key at all
    p = tmp_path / "sample.jsonl"
    p.write_text(json.dumps(line) + "\n", encoding="utf-8")

    findings = validate_jsonl(str(p))

    assert any(
        f.sev == FAIL and f.check == "C1 envelope" and "data_classification" in f.msg
        for f in findings
    ), _fail_checks(findings)


def test_bad_enum_value_fails(tmp_path):
    line = _base_line()
    line["data_classification"] = "top-secret"
    p = tmp_path / "sample.jsonl"
    p.write_text(json.dumps(line) + "\n", encoding="utf-8")

    findings = validate_jsonl(str(p))

    assert any(
        f.sev == FAIL and f.check == "C1 envelope" and "top-secret" in f.msg
        for f in findings
    ), _fail_checks(findings)


def test_valid_line_passes(tmp_path):
    line = _base_line()
    line["data_classification"] = "internal"
    p = tmp_path / "sample.jsonl"
    p.write_text(json.dumps(line) + "\n", encoding="utf-8")

    findings = validate_jsonl(str(p))

    assert _fail_checks(findings) == []
