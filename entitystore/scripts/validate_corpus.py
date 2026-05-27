#!/usr/bin/env python3
"""
entitystore — schema-injection validator (the schema-agnostic seam).

Validates a corpus of JSON entities against an EXTERNALLY supplied schema. This
is the proof that the engine carries no schema of its own: the schema lives with
the data (company-brain), and the engine reads it by path. Adding a new kind to
that schema requires NO change here.

    validate_corpus.py --schema <entity.schema.json> --corpus <dir-with entities/>
"""
import argparse
import json
import pathlib
import sys

import jsonschema


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True, help="path to entity.schema.json")
    ap.add_argument("--corpus", required=True, help="dir containing entity JSON files")
    args = ap.parse_args()

    schema = json.loads(pathlib.Path(args.schema).read_text(encoding="utf-8"))
    root = pathlib.Path(args.corpus)
    files = [f for f in sorted(root.rglob("*.json")) if f.name != "manifest.json"]

    ok, by_kind, failures = 0, {}, []
    for f in files:
        e = json.loads(f.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(e, schema)
            ok += 1
            by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
        except jsonschema.ValidationError as ex:
            failures.append((f.name, ex.message))

    print(f"schema : {args.schema}")
    print(f"corpus : {root}")
    print(f"result : {ok} valid, {len(failures)} invalid")
    print(f"by kind: {by_kind}")
    for name, msg in failures:
        print(f"  FAIL {name}: {msg}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
