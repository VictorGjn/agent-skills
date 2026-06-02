#!/usr/bin/env python3
"""entity-review — diff-scoped, vision-aware reviewer for EntityStore corpora.

Reviews the entities a branch / PR / file-set pushes against the LATEST
entity.schema.json plus the EntityStore vision guardrails. Complements (does not
replace) cb_engine.wiki_audit, which is corpus-wide. See SKILL.md for the check
taxonomy and rationale.

Exit 1 if any ERROR-severity finding, else 0 (CI-ready).
"""
from __future__ import annotations
import argparse, importlib.util, json, pathlib, re, subprocess, sys
from dataclasses import dataclass

# ---- severities -------------------------------------------------------------
ERROR, WARN, INFO = "ERROR", "WARN", "INFO"


@dataclass
class Finding:
    sev: str
    check: str
    file: str
    msg: str


# ---- helpers ----------------------------------------------------------------
def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


def load_cb_engine(engine_dir: pathlib.Path):
    """Import cb_engine.py from the entitystore skill so we reuse load_corpus/wiki_audit."""
    p = engine_dir / "cb_engine.py"
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location("cb_engine", p)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(engine_dir))
    try:
        spec.loader.exec_module(mod)  # type: ignore
        return mod
    except Exception:
        return None


def parse_prohibited(schema: dict) -> dict[str, list[str]]:
    """Source the anti-authority guardrails FROM the schema's own
    'PROHIBITED FIELDS:' / 'PROHIBITED:' annotations, per kind-overlay object."""
    out: dict[str, list[str]] = {}
    props = schema.get("properties", {})
    for kind, spec in props.items():
        desc = (spec.get("description") or "") if isinstance(spec, dict) else ""
        # capture ONLY the field-list: from the colon up to the first em/en-dash,
        # semicolon, or period (the prose that follows the list starts there).
        m = re.search(r"PROHIBITED(?:\s+FIELDS)?\s*:?\s*([^.;\n—–]+)", desc, re.I)
        if m:
            stop = {"any", "a", "an", "the", "is", "not", "or", "and", "no", "fields"}
            toks = [t for t in re.findall(r"[a-z][a-z0-9_-]*", m.group(1).lower())
                    if t not in stop]
            out[kind] = toks
    return out


def schema_version(schema: dict) -> str:
    desc = schema.get("description", "") + " " + schema.get("title", "")
    vs = re.findall(r"\bv(\d+)\b", desc)
    return f"v{max(int(v) for v in vs)}" if vs else "v?"


# reference fields per kind -> resolves to another entity id
REF_FIELDS = {
    "_top": [("wiki_links", True)],  # (field, is_list)
    "vessel": [("owner", False), ("operator", False), ("manager", False), ("navigations", True)],
    "navigation": [("vessel", False), ("captain_in_charge", False), ("charterer", False)],
    "person": [("affiliations", True)],
    "org": [("members", True)],
    "client": [("csm", False), ("account_owner", False), ("sponsor", False)],
    "product": [("vendor", False)],
    "post": [("author", False)],
}
NAV_BULK = ("track", "weather", "noon", "fuel", "waypoints", "positions", "route_points")
VOLATILE = ("live_status", "contract_status")
# C7 applies ONLY to anchor kinds: the "materialize lazily, never one node per
# voyage" rule (locked 2026-06-02) is navigation-specific. A scribe LEGITIMATELY
# bulk-creates orgs/concepts/persons from a system of record — that is not a
# role violation. Override with --anchor-kinds.
ANCHOR_KINDS = {"navigation"}


# ---- file discovery ---------------------------------------------------------
def changed_files(repo, base, head, pr, files, corpus):
    """Return (added, modified, deleted) lists of entity-json paths (repo-relative)."""
    if files:
        return ([f for f in files], [], [])
    if pr is not None:
        out = subprocess.run(["gh", "pr", "diff", str(pr), "-R", _remote(repo),
                              "--name-only"], capture_output=True, text=True).stdout
        paths = [l for l in out.splitlines() if _is_entity(l, corpus)]
        # without status we treat all as "modified" for review purposes
        return ([], paths, [])
    rng = f"{base}..{head}"
    out = git(repo, "diff", "--name-status", rng, "--", f"{corpus}/entities/")
    a, m, d = [], [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0], parts[-1]
        if not _is_entity(path, corpus):
            continue
        (a if st.startswith("A") else d if st.startswith("D") else m).append(path)
    return a, m, d


def _is_entity(path: str, corpus: str) -> bool:
    return f"{corpus}/entities/" in path.replace("\\", "/") and path.endswith(".json")


def _remote(repo):
    url = git(repo, "remote", "get-url", "origin")
    m = re.search(r"[:/]([\w-]+/[\w-]+?)(?:\.git)?$", url)
    return m.group(1) if m else url


# ---- the checks -------------------------------------------------------------
def review(repo, corpus, schema_path, engine_dir, base, head, pr, files, anchor_kinds=ANCHOR_KINDS):
    repo = pathlib.Path(repo)
    corpus_dir = repo / corpus
    schema = json.loads(pathlib.Path(schema_path).read_text(encoding="utf-8"))
    prohibited = parse_prohibited(schema)
    cb = load_cb_engine(engine_dir)

    # full corpus id-set for referential / dup checks
    corpus_ids: set[str] = set()
    if cb and hasattr(cb, "load_corpus"):
        try:
            corpus_ids = set(cb.load_corpus(corpus_dir).keys())
        except Exception:
            pass
    if not corpus_ids:  # fallback: glob
        for p in corpus_dir.glob("entities/**/*.json"):
            try:
                corpus_ids.add(json.loads(p.read_text(encoding="utf-8")).get("id", ""))
            except Exception:
                pass

    added, modified, deleted = changed_files(repo, base, head, pr, files, corpus)
    review_paths = added + modified
    F: list[Finding] = []

    # load changed entities from working tree
    ents: dict[str, dict] = {}
    seen_ids: dict[str, str] = {}
    try:
        from jsonschema import Draft7Validator
        validator = Draft7Validator(schema)
    except Exception:
        validator = None

    for rel in review_paths:
        fp = repo / rel
        if not fp.exists():
            continue
        try:
            e = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as ex:
            F.append(Finding(ERROR, "C1 schema", rel, f"JSON parse failed: {ex}"))
            continue
        ents[rel] = e
        kind = e.get("kind", "")
        eid = e.get("id", "")

        # C1 schema validity
        if validator is not None:
            errs = sorted(validator.iter_errors(e), key=lambda x: list(x.path))
            for x in errs[:3]:
                F.append(Finding(ERROR, "C1 schema", rel, f"{list(x.path)}: {x.message[:120]}"))

        # C2 id integrity
        if not re.match(r"^[a-z][a-z0-9-]{0,63}:[a-z0-9][a-z0-9-]{0,127}$", eid):
            F.append(Finding(ERROR, "C2 id", rel, f"id {eid!r} violates URN <kind>:<slug>"))
        if eid in seen_ids:
            F.append(Finding(ERROR, "C2 id", rel, f"duplicate id {eid} (also {seen_ids[eid]})"))
        seen_ids[eid] = rel

        # C4 provenance honesty
        prov = e.get("provenance", {})
        if not prov.get("extractor") or not prov.get("extraction_method"):
            F.append(Finding(WARN, "C4 provenance", rel, "missing extractor / extraction_method"))
        if prov.get("extraction_method") == "llm" and not e.get("evidence"):
            F.append(Finding(WARN, "C4 provenance", rel, "extraction_method=llm but no evidence[] (claim without receipts)"))
        ec = prov.get("extraction_confidence")
        if isinstance(ec, (int, float)) and not (0 <= ec <= 1):
            F.append(Finding(WARN, "C4 provenance", rel, f"extraction_confidence {ec} out of [0,1]"))

        # C5 anti-authority PROHIBITED fields (sourced from schema)
        overlay = e.get(kind, {}) if isinstance(e.get(kind), dict) else {}
        for banned in prohibited.get(kind, []):
            if banned in overlay or banned in e:
                F.append(Finding(ERROR, "C5 anti-authority", rel,
                                 f"{kind} carries prohibited field '{banned}' — a {kind} is context/witness, not an authority"))

        # C6 navigation reference-anchor discipline
        if kind == "navigation":
            nav = e.get("navigation", {})
            blob = json.dumps(e).lower()
            for b in NAV_BULK:
                if f'"{b}' in blob:
                    F.append(Finding(ERROR, "C6 nav-anchor", rel, f"embeds bulk field '{b}' — navigation is a key, not a data mirror"))
            if isinstance(nav.get("captain_in_charge"), list):
                F.append(Finding(ERROR, "C6 nav-anchor", rel, "captain_in_charge is multi-valued (no change of command at sea)"))
            if not nav.get("backoffice_id"):
                F.append(Finding(WARN, "C6 nav-anchor", rel, "missing backoffice_id (system-of-record key)"))

        # C8 volatile-not-frozen
        for vf in VOLATILE:
            if vf in overlay and overlay.get(vf) is not None:
                claims = e.get("claims", []) or []
                if any(vf in (c.get("metric", "") or "") for c in claims):
                    F.append(Finding(WARN, "C8 volatile", rel, f"volatile field '{vf}' re-asserted as a claim — capture, don't freeze"))

        # C9 identity-as-revisable (v6)
        for ia in e.get("identity_assertions", []) or []:
            for req in ("assertion_id", "source_system", "source_id", "method", "as_of", "asserted_by", "status"):
                if req not in ia:
                    F.append(Finding(WARN, "C9 identity", rel, f"identity_assertion missing '{req}'"))
            if ia.get("status") == "retracted" and not ia.get("retraction_reason"):
                F.append(Finding(WARN, "C9 identity", rel, "retracted assertion lacks retraction_reason"))

        # C10 truth ⊥ relevance
        if kind == "concept":
            c = e.get("concept", {})
            if "falsifiability" not in c or "specificity" not in c:
                F.append(Finding(INFO, "C10 truth⊥relevance", rel, "concept missing falsifiability/specificity"))

        # C11 corroboration thinness
        if kind == "concept" and e.get("concept", {}).get("type") in ("claim", "opportunity"):
            srcs = {ev.get("source_id", "").split(":")[0] for ev in e.get("evidence", []) or []}
            if len(srcs) < 2:
                F.append(Finding(INFO, "C11 corroboration", rel, f"{e['concept']['type']} backed by <2 distinct sources"))

    # C3 referential integrity (delta-scoped) — refs must resolve in corpus ∪ added
    known = corpus_ids | set(seen_ids.keys())
    for rel, e in ents.items():
        kind = e.get("kind", "")
        for scope in ("_top", kind):
            for field, is_list in REF_FIELDS.get(scope, []):
                container = e if scope == "_top" else (e.get(kind, {}) if isinstance(e.get(kind), dict) else {})
                val = container.get(field)
                refs = (val if isinstance(val, list) else [val]) if val else []
                for r in refs:
                    if isinstance(r, str) and ":" in r and r not in known:
                        F.append(Finding(ERROR, "C3 ref-integrity", rel, f"{field} -> {r} does not resolve"))

    # C7 lazy-materialization / role boundary (push-level)
    added_ents = {r: ents[r] for r in added if r in ents}
    by_kind: dict[str, list[str]] = {}
    for r, e in added_ents.items():
        by_kind.setdefault(e.get("kind", ""), []).append(r)
    # only anchor kinds (default {navigation}) — scribes legitimately bulk-create the rest
    inbound = set()
    for e in list(ents.values()):
        for v in (e.get("wiki_links") or []):
            inbound.add(v)
        ov = e.get(e.get("kind", ""), {})
        if isinstance(ov, dict):
            for vv in ov.values():
                if isinstance(vv, list):
                    inbound.update(x for x in vv if isinstance(x, str))
    for kind, rels in by_kind.items():
        if kind not in anchor_kinds:
            continue
        orphans = [r for r in rels if added_ents[r].get("id") not in inbound]
        if orphans and len(orphans) == len(rels) and len(rels) > 1:
            F.append(Finding(WARN, "C7 lazy-materialization", "(push)",
                             f"{len(orphans)} added {kind} anchors are ALL orphan (no inbound ref) — anchors should materialize lazily, not one node per voyage (locked 2026-06-02)"))
        srcs = {added_ents[r].get("provenance", {}).get("extractor", "") for r in rels}
        if len(rels) >= 50 and len(srcs) == 1:
            F.append(Finding(INFO, "C7 role-boundary", "(push)",
                             f"{len(rels)} {kind} anchors from ONE extractor — confirm this is a system-of-record scribe, not an enricher mirroring another source (scribe≠enricher)"))

    # C12 un-merge via delete
    for rel in deleted:
        F.append(Finding(WARN, "C12 un-merge", rel, "entity deleted — un-merge should be retract/supersede with a reason, not rm"))

    return F, added, modified, deleted, schema_version(schema)


# ---- report -----------------------------------------------------------------
def report(F, added, modified, deleted, ver) -> int:
    n_err = sum(1 for f in F if f.sev == ERROR)
    n_warn = sum(1 for f in F if f.sev == WARN)
    n_info = sum(1 for f in F if f.sev == INFO)
    verdict = ("❌ CHANGES REQUESTED" if n_err else
               "🟡 APPROVE WITH COMMENTS" if n_warn else "✅ APPROVE")
    print(f"# entity-review — {verdict}\n")
    print(f"Schema: **{ver}** · reviewed **{len(added)+len(modified)}** entities "
          f"(+{len(added)} new, ~{len(modified)} modified, -{len(deleted)} deleted)")
    print(f"Findings: **{n_err} ERROR · {n_warn} WARN · {n_info} INFO**\n")
    for sev, icon in ((ERROR, "🔴"), (WARN, "🟡"), (INFO, "⚪")):
        items = [f for f in F if f.sev == sev]
        if not items:
            continue
        print(f"## {icon} {sev} ({len(items)})")
        for f in items[:200]:
            short = f.file.split("entities/")[-1] if "entities/" in f.file else f.file
            print(f"- `{short}` — **{f.check}**: {f.msg}")
        print()
    if not F:
        print("_No findings. Entities are schema-valid and on-track with the vision._")
    return 1 if n_err else 0


def main():
    try:  # markdown report uses emoji/em-dash; force utf-8 on cp1252 consoles (Windows)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Diff-scoped, vision-aware EntityStore reviewer.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--corpus", default="corpora/syroco-commercial")
    ap.add_argument("--schema", default=None, help="default: <corpus>/../../schemas/entity.schema.json")
    ap.add_argument("--engine", default=None, help="entitystore scripts/ dir (auto-detected)")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--pr", type=int, default=None)
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--anchor-kinds", default="navigation",
                    help="comma-separated kinds C7 (lazy-materialization/role-boundary) applies to")
    a = ap.parse_args()
    anchor_kinds = {k.strip() for k in a.anchor_kinds.split(",") if k.strip()}

    repo = pathlib.Path(a.repo)
    schema = a.schema or str(repo / a.corpus / ".." / ".." / "schemas" / "entity.schema.json")
    schema = str(pathlib.Path(schema).resolve())
    engine = pathlib.Path(a.engine) if a.engine else \
        pathlib.Path(__file__).resolve().parents[2] / "entitystore" / "scripts"

    F, added, modified, deleted, ver = review(
        a.repo, a.corpus, schema, engine, a.base, a.head, a.pr, a.files, anchor_kinds)
    sys.exit(report(F, added, modified, deleted, ver))


if __name__ == "__main__":
    main()
