#!/usr/bin/env python3
"""scribe-check (deterministic subset) — CI gate for scribe specs + emitted events.

This is the mechanical, no-LLM slice of the `scribe-check` skill (see ../SKILL.md
and ../CRITERIA.md). It runs the criteria that are reliably checkable without
judgment, exactly as `entity-review/scripts/review_entities.py` does for entities:

  • spec-lint   — parse changed scribe specs (a scribe-pass `## Module:` section
                  or a per-scribe SKILL.md) and flag the high-signal patterns.
  • output-validate — validate emitted `raw/*.jsonl` events (fully structural).

The judgment criteria (Group V vision-grade, the `llm` parts of O/S) are NOT run
here — they remain the agent-invoked skill. Like entity-review, this COMPLEMENTS
the full review; it does not replace it.

Exit 1 if any FAIL-severity finding, else 0 (CI-ready). Spec-lint heuristics are
deliberately conservative (mostly WARN/INFO) to avoid false-blocking on prose;
the hard gate lives in output-validate over real JSONL, plus a few unambiguous
structural FAILs on specs (mixed model, missing required field, resolved-slug
entity_hint on a Profile-B scribe).
"""
from __future__ import annotations
import argparse, glob, hashlib, json, pathlib, re, sys
from dataclasses import dataclass

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"
BANNED = ("score", "severity", "theme", "category", "sentiment",
          "priority", "trust", "tier", "reputation", "quality")
REQUIRED_B = ("schema_version", "scribe", "scribed_at", "source_type", "source_ref",
              "file_id", "external_id", "content_hash", "claim", "ts", "entity_hint", "payload")


@dataclass
class Finding:
    sev: str
    check: str
    where: str
    msg: str


# ----------------------------------------------------------------- spec-lint --
def split_modules(text: str, path: str) -> list[tuple[str, str]]:
    """Return [(unit_name, unit_text)]. A scribe-pass schedule.md splits on
    '## Module: <name>'; any other markdown is one unit named from frontmatter."""
    if "## Module:" in text:
        out, parts = [], re.split(r"^## Module:\s*", text, flags=re.M)[1:]
        for p in parts:
            name = re.match(r"([a-z0-9-]+)", p)
            out.append((name.group(1) if name else "?", p))
        return out
    m = re.search(r"^name:\s*([a-z0-9-]+)", text, re.M)
    return [((m.group(1) if m else pathlib.Path(path).parent.name), text)]


def _entitystore(unit: str) -> bool:
    """Profile C signal: writes EntityStore entity files directly + identity_assertions
    (system-of-record scribe, e.g. bo-scribe). No wiki.add, no raw/*.jsonl."""
    writes = bool(re.search(r"entities/[<>\w-]+/[<>\w.\-]+\.json", unit)) \
        or bool(re.search(r"\bwrites:\s*entitystore\b", unit, re.I)) \
        or bool(re.search(r"\bsource_of_record:\s*true\b", unit, re.I))
    ida = bool(re.search(r"identity_assertions?\b", unit))
    return writes and ida


def detect_profile(unit: str) -> str:
    push = bool(re.search(r"wiki\.add", unit))
    raw = bool(re.search(r"raw/[\w*<>-]+\.jsonl", unit))
    local_hint = bool(re.search(r'entity_hint`?\s*[:=].{0,40}<scribe>:|"\^<scribe>:', unit)) \
        or bool(re.search(r'"<scribe>:', unit)) or bool(re.search(r"`<scribe>:", unit))
    if raw or local_hint:
        return "B"
    if push:
        return "A"
    if _entitystore(unit):
        return "C"
    return "?"


def lint_spec(path: str, text: str) -> list[Finding]:
    """Spec-lint is HEURISTIC over prose, so it never emits FAIL except the one
    unambiguous structural error (S0 mixed-model). Everything else is WARN/INFO —
    advisory in the step summary, but the blocking gate is output-validate over
    real JSONL. This is deliberate: a prose check that false-blocks a valid module
    is worse than no check (it trained the entity-review CI the same way)."""
    F: list[Finding] = []
    fname = pathlib.Path(path).name
    units = split_modules(text, path)
    file_profile = "B" if any(detect_profile(u) == "B" for _, u in units) else \
                   "A" if any(detect_profile(u) == "A" for _, u in units) else \
                   "C" if any(detect_profile(u) == "C" for _, u in units) else "?"

    # C1 — required envelope fields, checked at FILE level (the scribe-pass envelope
    # is declared once in a shared section; per-module sections only add deltas, so a
    # per-module check false-positives). Only for Profile B (A has a smaller schema).
    if file_profile == "B":
        missing = [f for f in REQUIRED_B if not re.search(rf'\b{re.escape(f)}\b', text)]
        if missing:
            F.append(Finding(WARN, "C1 envelope", fname,
                             f"required field(s) never named anywhere in the spec: {', '.join(missing)}"))

    for name, unit in split_modules(text, path):
        where = f"{fname}:{name}"
        push = bool(re.search(r"wiki\.add", unit))
        raw = bool(re.search(r"raw/[\w*<>-]+\.jsonl", unit))
        entitystore = _entitystore(unit)
        prof = detect_profile(unit)

        # S0 — mixed model: the ONLY spec-level FAIL (unambiguous, can't false-block).
        # Fires when a unit declares more than one of the three sinks.
        if sum([push, raw, entitystore]) > 1:
            F.append(Finding(FAIL, "S0 single-model", where,
                             "declares more than one model (wiki.add / raw JSONL / EntityStore "
                             "writes) — pick one profile (A, B or C)"))

        # S1 — content_hash fingerprints a label, not content (WARN)
        for m in re.finditer(r"content_hash[^\n]*sha256\((title|claim)\)", unit, re.I):
            window = unit[m.start(): m.start() + 200].lower()
            if not re.search(r"summary|transcript|content|action_items|updated_at|body", window):
                F.append(Finding(WARN, "S1 content-fingerprint", where,
                                 "content_hash hashes only the title/claim — finalized summaries/edits "
                                 "will never re-emit. Hash the captured content too."))

        # S3 — async content without a look-back re-scan (WARN)
        if re.search(r"summary|transcript", unit, re.I) and \
                not re.search(r"look-?back|min\([^)]*watermark|now\s*[-−]\s*\d+\s*d", unit, re.I):
            F.append(Finding(WARN, "S3 look-back", where,
                             "payload depends on async-generated content (summary/transcript) but the "
                             "Fetch has no trailing look-back window — late-finalized content is skipped."))

        # O1 — Profile B entity_hint should be source-local '<scribe>:<id>' (WARN).
        # Pass if the unit shows a colon-bearing hint token or says 'source-local'.
        if prof == "B" and "entity_hint" in unit:
            tail = unit[unit.find("entity_hint"): unit.find("entity_hint") + 160]
            ok = ("source-local" in tail) or ("<scribe>:" in tail) \
                or bool(re.search(r'[`"][a-z0-9-]+:', tail))
            if not ok:
                F.append(Finding(WARN, "O1 entity_hint", where,
                                 "Profile B entity_hint does not look source-local ('<scribe>:<id>') — "
                                 "resolution is the enricher's job, not the scribe's"))

        # O2/O3 — a banned interpretation key declared as an EMITTED JSON field (WARN)
        for b in BANNED:
            if re.search(rf'"{b}"\s*:', unit):
                F.append(Finding(WARN, "O2/O3 no-interpretation", where,
                                 f"event/payload appears to carry interpretation field '{b}' — "
                                 "scoring/trust is the enricher/consumer's job (concept-first trust)"))
    return F


# ----------------------------------------------------------- output-validate --
def validate_jsonl(path: str) -> list[Finding]:
    F: list[Finding] = []
    seen_file_id: dict[str, int] = {}
    by_external: dict[str, int] = {}
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except Exception as ex:
        return [Finding(FAIL, "io", path, f"cannot read sample: {ex}")]
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception as ex:
            F.append(Finding(FAIL, "C1 envelope", f"{pathlib.Path(path).name}:{i}",
                             f"line is not valid JSON: {ex}"))
            continue
        scribe = e.get("scribe") or e.get("source_type") or "?"
        # C1 required fields (Profile B envelope; A events lack external_id and are skipped)
        if "external_id" in e:
            for f in REQUIRED_B:
                if f not in e:
                    F.append(Finding(FAIL, "C1 envelope", f"{pathlib.Path(path).name}:{i}",
                                     f"missing required field '{f}'"))
        # O7 entity_hint present + non-empty
        hint = e.get("entity_hint")
        if not hint:
            F.append(Finding(FAIL, "O7 entity_hint", f"{pathlib.Path(path).name}:{i}",
                             "entity_hint missing/empty — event drops at consolidation"))
        elif "external_id" in e and not str(hint).startswith(f"{scribe}:"):
            # O1 Profile B: must be source-local
            F.append(Finding(FAIL, "O1 entity_hint", f"{pathlib.Path(path).name}:{i}",
                             f"entity_hint {hint!r} is not source-local '{scribe}:<id>' (resolved slug?)"))
        # O2/O3 banned keys anywhere (recursive)
        for b in _banned_in(e):
            F.append(Finding(FAIL, "O2/O3 no-interpretation", f"{pathlib.Path(path).name}:{i}",
                             f"event carries interpretation field '{b}'"))
        # S1 content_hash must fingerprint CONTENT, not a label. Failing sha256(claim)
        # ONLY makes sense when richer content lives in the payload (claim is a label,
        # e.g. a meeting title). When claim IS the full captured content (a message/email
        # body), content_hash == sha256(claim) is CORRECT — do not flag it.
        ch, claim = e.get("content_hash"), e.get("claim")
        pl = e.get("payload", {}) if isinstance(e.get("payload"), dict) else {}
        RICHER = ("summary", "transcript", "body", "markdown", "action_items")
        claim_is_label = any(any(r in k.lower() for r in RICHER) for k in pl)
        if ch and isinstance(claim, str) and claim_is_label \
                and ch == hashlib.sha256(claim.encode("utf-8")).hexdigest():
            F.append(Finding(FAIL, "S1 content-fingerprint", f"{pathlib.Path(path).name}:{i}",
                             "content_hash == sha256(claim) but richer content (summary/transcript/…) "
                             "lives in payload — finalized/edited content can never supersede"))
        # S5 duplicate file_id
        fid = e.get("file_id")
        if fid:
            if fid in seen_file_id:
                F.append(Finding(FAIL, "S5 idempotency", f"{pathlib.Path(path).name}:{i}",
                                 f"duplicate file_id (first at line {seen_file_id[fid]}) — dedup broken"))
            seen_file_id[fid] = i
            # C2 file_id must follow the documented Profile-B formula (deterministic, not
            # random/time-based). WARN not FAIL: the concat has no pinned separator, so a
            # mismatch may be a convention difference rather than a real defect.
            if "external_id" in e and ch is not None:
                want = hashlib.sha256(f"{scribe}{e.get('external_id', '')}{ch}".encode("utf-8")).hexdigest()
                if fid != want:
                    F.append(Finding(WARN, "C2 file_id", f"{pathlib.Path(path).name}:{i}",
                                     "file_id != sha256(scribe+external_id+content_hash) — a non-deterministic "
                                     "id, or a different concatenation convention than SPEC documents"))
        # S6 inlined large blob despite a re-fetch id
        pl = e.get("payload", {})
        if isinstance(pl, dict) and any(k.endswith("_id") for k in pl):
            big = [k for k, v in pl.items() if isinstance(v, str) and len(v) > 5000]
            for k in big:
                F.append(Finding(WARN, "S6 by-reference", f"{pathlib.Path(path).name}:{i}",
                                 f"payload.{k} is >5KB but an id field exists — store by-reference, not inlined"))
        ext = e.get("external_id")
        if ext:
            by_external[ext] = by_external.get(ext, 0) + 1
    revs = sum(1 for n in by_external.values() if n > 1)
    if revs:
        F.append(Finding(INFO, "S2 supersession", pathlib.Path(path).name,
                         f"{revs} external_id(s) have multiple events (content revisions — expected if intentional)"))
    return F


def _banned_in(obj, _depth=0):
    found = set()
    if _depth > 6:
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in BANNED:
                found.add(k.lower())
            found |= _banned_in(v, _depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            found |= _banned_in(v, _depth + 1)
    return found


# -------------------------------------------------------------------- report --
def report(F: list[Finding]) -> int:
    n_fail = sum(1 for f in F if f.sev == FAIL)
    n_warn = sum(1 for f in F if f.sev == WARN)
    n_info = sum(1 for f in F if f.sev == INFO)
    verdict = "❌ BLOCK" if n_fail else "🟡 PASS WITH WARNINGS" if n_warn else "✅ PASS"
    print(f"# scribe-check (CI subset) — {verdict}\n")
    print(f"Findings: **{n_fail} FAIL · {n_warn} WARN · {n_info} INFO**  "
          f"· _vision-grade (LLM) criteria not run in CI — invoke the scribe-check skill for those_\n")
    for sev, icon in ((FAIL, "🔴"), (WARN, "🟡"), (INFO, "⚪")):
        items = [f for f in F if f.sev == sev]
        if not items:
            continue
        print(f"## {icon} {sev} ({len(items)})")
        for f in items[:200]:
            print(f"- `{f.where}` — **{f.check}**: {f.msg}")
        print()
    if not F:
        print("_No findings. Scribe specs/events conform to the deterministic criteria._")
    return 1 if n_fail else 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Deterministic CI subset of scribe-check.")
    ap.add_argument("--specs", nargs="*", default=[],
                    help="scribe spec files to lint (scribe-pass schedule.md and/or per-scribe SKILL.md)")
    ap.add_argument("--sample", nargs="*", default=[],
                    help="emitted raw/*.jsonl globs to output-validate")
    a = ap.parse_args()

    F: list[Finding] = []
    for spec in a.specs:
        p = pathlib.Path(spec)
        if not p.exists():
            continue
        F += lint_spec(spec, p.read_text(encoding="utf-8"))
    for pattern in a.sample:
        for path in glob.glob(pattern, recursive=True):
            F += validate_jsonl(path)

    if not a.specs and not a.sample:
        print("# scribe-check (CI subset) — ✅ PASS\n\n_No scribe specs or samples in scope._")
        sys.exit(0)
    sys.exit(report(F))


if __name__ == "__main__":
    main()
