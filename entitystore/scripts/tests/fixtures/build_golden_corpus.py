#!/usr/bin/env python3
"""Generate the golden-query synthetic fixture corpus from golden_seed.json.

Reads the hand-authored seed graph (golden_seed.json — the ONE authored
artifact in this fixture, since it defines the graph shape by design) and
materializes schema-conformant entity JSON files under
golden_corpus/entities/<kind>/<slug>.json.

Deterministic and regenerable: re-running this script always produces byte-
identical output for a given seed (fixed created_at/updated_at/provenance),
so the generated golden_corpus/ tree can be regenerated on demand rather than
hand-maintained per-entity.

Usage:
    python build_golden_corpus.py [--check]

    --check   validate the seed + generated corpus against the vendored
              schema and report dead wiki_links, without needing pytest.

No network calls, no dependency on company-brain being checked out (the
schema is vendored in fixtures/schemas/entity.schema.json — schema only,
structural, not sensitive data).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent
SEED_PATH = FIXTURES_DIR / "golden_seed.json"
CORPUS_DIR = FIXTURES_DIR / "golden_corpus"
SCHEMA_PATH = FIXTURES_DIR / "schemas" / "entity.schema.json"

# Fixed timestamps so regeneration is byte-identical — this is a frozen
# fixture, not live data; "as_of" reality doesn't matter here.
_CREATED_AT = "2026-01-01T00:00:00Z"
_UPDATED_AT = "2026-01-01T00:00:00Z"
_PROVENANCE = {
    "extractor": "golden-fixture/v1",
    "extraction_method": "human",
    "extraction_confidence": 1.0,
}


def load_seed() -> list[dict]:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return data["entities"]


def materialize(entity_seed: dict) -> dict:
    """Fill required top-level fields the seed omits (created_at/updated_at/provenance)."""
    entity = dict(entity_seed)
    entity.pop("_comment", None)
    entity.setdefault("created_at", _CREATED_AT)
    entity.setdefault("updated_at", _UPDATED_AT)
    entity.setdefault("provenance", dict(_PROVENANCE))
    return entity


def prune_dead_links(entities: dict[str, dict]) -> int:
    """Drop wiki_links that don't resolve within the fixture. Returns count dropped."""
    ids = set(entities)
    dropped = 0
    for e in entities.values():
        links = e.get("wiki_links")
        if not links:
            continue
        kept = [l for l in links if l in ids]
        dropped += len(links) - len(kept)
        if kept:
            e["wiki_links"] = kept
        else:
            e.pop("wiki_links", None)
    return dropped


def build(write: bool = True) -> dict[str, dict]:
    seed_entities = [materialize(e) for e in load_seed()]
    entities = {e["id"]: e for e in seed_entities}
    if len(entities) != len(seed_entities):
        raise ValueError("duplicate entity id in golden_seed.json")

    dropped = prune_dead_links(entities)
    if dropped:
        print(f"build_golden_corpus: pruned {dropped} dead wiki_link(s)", file=sys.stderr)

    if write:
        for e in entities.values():
            out_dir = CORPUS_DIR / "entities" / e["kind"]
            out_dir.mkdir(parents=True, exist_ok=True)
            slug = e["id"].split(":", 1)[1]
            out_path = out_dir / f"{slug}.json"
            out_path.write_text(
                json.dumps(e, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    return entities


def validate(entities: dict[str, dict]) -> list[str]:
    """Validate against the vendored schema. Returns a list of error strings (empty = clean)."""
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    errors: list[str] = []
    ids = set(entities)
    for eid, e in entities.items():
        for err in validator.iter_errors(e):
            errors.append(f"{eid}: {err.message} (path={list(err.absolute_path)})")
        for link in e.get("wiki_links", []) or []:
            if link not in ids:
                errors.append(f"{eid}: dead wiki_link -> {link}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="validate only; still writes the corpus (regenerate + verify)")
    args = ap.parse_args()

    entities = build(write=True)
    print(f"build_golden_corpus: wrote {len(entities)} entities to {CORPUS_DIR}")

    if args.check:
        errors = validate(entities)
        if errors:
            print(f"build_golden_corpus: {len(errors)} schema/link error(s):", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
        print("build_golden_corpus: 0 schema errors, 0 dead links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
