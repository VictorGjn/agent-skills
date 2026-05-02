"""Wiki page schema validator (M3 in plan/prd-closed-loop.md).

Refusal-and-rebuild model per ``plan/phases/phase-1.md`` §1.2.1: any
``wiki/<slug>.md`` whose ``schema_version`` doesn't match the current CE
release MUST cause a hard error with a remediation message pointing at
``wiki_init.py --rebuild``. This is the v0.1 strategy below 10k entities;
once we cross that threshold we add forward-migration scripts.

Used by:
- ``wiki_init.py`` (verify each page before consuming/updating)
- ``audit.py`` (skip un-validatable pages, surface as proposals)
- ``wiki.ask`` MCP serve path (reject reads of stale-schema pages)

CLI:
    python3 scripts/wiki/validate_page.py wiki/auth-middleware.md
    python3 scripts/wiki/validate_page.py wiki/*.md   # batch

Exits non-zero on first validation failure with a clear stderr message.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

# Current schema version. Bumping this requires either:
#   (a) corpus < 10k entities -> rerun wiki_init.py --rebuild from events
#   (b) corpus >= 10k entities -> ship a migrate_v1_to_v2.py and friends
# Both per phase-1.md §1.2.1.
#
# 1.0 -> 1.1: make_id widened from sha256[:8] to sha256[:12] to push 1%
# birthday-paradox collision past 16M entities. The hash *output* changes
# even though the schema fields are identical, so any pre-1.1 brain has
# stale `id:` values that no longer round-trip through wiki_init's
# scope-by-id / supersedes-chain joins. Refusal-and-rebuild is the
# documented remedy: validate_page rejects 1.0 pages, operator runs
# `wiki_init.py --rebuild`, ids regenerate from the (unchanged) events.
# events.SCHEMA_VERSION stays at 1.0 — events don't carry entity ids.
SCHEMA_VERSION = "1.1"

# Required frontmatter keys per phase-1.md §1.2 (after PR #20 schema additions).
# `kind: decision` adds three more (supersedes/superseded_by/valid_until); those
# are validated only when kind == "decision".
_REQUIRED_KEYS_ALL = {
    "id", "kind", "title", "slug", "scope", "sources", "confidence",
    "updated", "last_verified_at", "schema_version",
}
_REQUIRED_KEYS_DECISION = {"supersedes", "superseded_by", "valid_until"}

_VALID_KINDS = {
    "concept", "component", "decision", "actor", "process", "metric",
}

# Frontmatter delimiter pattern: lines of three or more dashes. We don't
# need a real YAML parser; the validator only inspects top-level keys and
# specific string values, all of which are safe to extract via regex
# against the frontmatter block.
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL,
)


class ValidationError(Exception):
    """Raised when a wiki page fails schema validation. The message is
    user-facing and includes a remediation pointer."""


def validate_page(path: Path) -> dict[str, Any]:
    """Read, parse, and validate a wiki/<slug>.md file.

    Returns the parsed frontmatter as a dict on success. Raises
    ValidationError on any failure (with a remediation message in the
    exception text).
    """
    if not path.is_file():
        raise ValidationError(f"{path}: not a file")

    text = path.read_text(encoding="utf-8")
    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match is None:
        raise ValidationError(
            f"{path}: missing YAML frontmatter (expected `---` block at top). "
            f"Run `python3 scripts/wiki/wiki_init.py --rebuild` to regenerate."
        )

    frontmatter = _parse_simple_frontmatter(fm_match.group(1))

    # 1. schema_version MUST match the current CE release.
    page_version = frontmatter.get("schema_version")
    if page_version != SCHEMA_VERSION:
        raise ValidationError(
            f"{path}: schema_version={page_version!r} does not match current "
            f"CE release {SCHEMA_VERSION!r}. "
            f"Run `python3 scripts/wiki/wiki_init.py --rebuild` to regenerate "
            f"from events log."
        )

    # 2. All-kinds required keys present.
    missing = _REQUIRED_KEYS_ALL - frontmatter.keys()
    if missing:
        raise ValidationError(
            f"{path}: missing required frontmatter keys {sorted(missing)!r}. "
            f"Run `python3 scripts/wiki/wiki_init.py --rebuild`."
        )

    # 3. kind must be one of the documented values.
    kind = frontmatter.get("kind")
    if kind not in _VALID_KINDS:
        raise ValidationError(
            f"{path}: kind={kind!r} not in {sorted(_VALID_KINDS)!r}."
        )

    # 4. kind == "decision" requires the continuity fields (tri-state:
    #    present-with-value, present-as-null, or omitted — but for the
    #    validator a "missing" key is failure; "null" or a value is OK).
    if kind == "decision":
        missing_decision = _REQUIRED_KEYS_DECISION - frontmatter.keys()
        if missing_decision:
            raise ValidationError(
                f"{path}: kind=decision requires keys "
                f"{sorted(missing_decision)!r}. Use null when the field is "
                f"genuinely empty (e.g. supersedes: null for a fresh decision)."
            )

    return frontmatter


def _parse_simple_frontmatter(block: str) -> dict[str, Any]:
    """Tiny YAML-subset parser: top-level scalar keys only.

    We deliberately avoid a YAML dep — the wiki frontmatter is generated
    by CE itself (wiki_init.py), so we control the shape. This parser
    handles:
      - `key: value` (string or scalar; quoted or unquoted)
      - `key: null` -> None
      - `key: 0.85` -> kept as string (validator doesn't type-check numeric)
      - `key: [a, b]` -> kept as raw string (validator doesn't deep-check)
      - skip nested blocks (lines starting with whitespace)
      - skip `# comment` lines
    """
    result: dict[str, Any] = {}
    for line in block.splitlines():
        # Skip indented lines (nested block content) and empty/comment lines.
        if not line or line[0] in (" ", "\t", "#"):
            continue
        if ":" not in line:
            continue
        key, _, raw_val = line.partition(":")
        key = key.strip()
        val = raw_val.strip()
        # Strip inline comments (best-effort; doesn't handle quoted #).
        comment_pos = val.find("#")
        if comment_pos > 0 and val[comment_pos - 1] in (" ", "\t"):
            val = val[:comment_pos].rstrip()
        # Map literal "null" / empty -> None.
        if val in ("null", "~", ""):
            result[key] = None
        else:
            # Strip surrounding quotes if matched.
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            result[key] = val
    return result


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            "usage: validate_page.py <wiki/page.md> [<wiki/page.md> ...]",
            file=sys.stderr,
        )
        return 2

    failures = 0
    for arg in args:
        path = Path(arg)
        try:
            validate_page(path)
        except ValidationError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            failures += 1
        else:
            print(f"OK:   {path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
