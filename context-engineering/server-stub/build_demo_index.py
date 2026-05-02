"""Build a small demo index for the YC stub by walking agent-skills/context-engineering/.

Run once locally; commits a few-MB JSON the Vercel function reads at request time.
Production v1 fetches per-corpus tarballs from syrocolab/company-brain instead.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # context-engineering/
OUT = Path(__file__).resolve().parent / "cache" / "gh-victorgjn-agent-skills-context-engineering.index.json"

INCLUDE_EXT = {".py", ".md", ".ts", ".tsx", ".json"}
SKIP_DIR_PARTS = {"cache", "__pycache__", "node_modules", ".git", "server-stub"}
MAX_FILES = 60


def main():
    files = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(ROOT).parts
        if any(part in SKIP_DIR_PARTS for part in rel_parts):
            continue
        if p.suffix not in INCLUDE_EXT:
            continue
        rel = "/".join(rel_parts)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not text.strip():
            continue
        text = text[:6000]
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        first_sentence = re.split(r"(?<=[.!?])\s", first_line, maxsplit=1)[0][:200] if first_line else ""
        first_paragraph = "\n".join(text.split("\n\n")[:1])[:1000]

        symbols = []
        for m in re.finditer(r"^(def|class)\s+([A-Za-z_][A-Za-z_0-9]*)", text, re.MULTILINE):
            kind = "function" if m.group(1) == "def" else "class"
            symbols.append({"name": m.group(2), "kind": kind, "isExported": True})
        for m in re.finditer(
            r"^export\s+(?:async\s+)?(?:function|const|class)\s+([A-Za-z_][A-Za-z_0-9]*)",
            text, re.MULTILINE,
        ):
            symbols.append({"name": m.group(1), "kind": "function", "isExported": True})

        files.append({
            "path": rel,
            # sha256 keeps contentHash stable across runs (Python's built-in hash()
            # is salted per-process via PYTHONHASHSEED, which would re-version the
            # demo index on every script invocation and break ETag idempotency).
            "contentHash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "tokens": max(1, len(text) // 4),
            "tree": {
                "title": rel,
                "firstSentence": first_sentence,
                "firstParagraph": first_paragraph,
                "text": text[:3000],
                "children": [],
            },
            "symbols": symbols[:20],
            "knowledge_type": "evidence" if rel.endswith(".py") else "guidelines",
        })
        if len(files) >= MAX_FILES:
            break

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "_meta": {
            "corpus_id": "gh-victorgjn-agent-skills-context-engineering",
            "source": {
                "type": "github_repo",
                "uri": "https://github.com/victorgjn/agent-skills",
                "branch": "main",
                "indexed_paths": ["context-engineering/"],
            },
            "data_classification": "public",
            "embedding": {"provider": "none", "model": "n/a", "dims": 0},
            "file_count": len(files),
            "version": 1,
            "last_refresh_completed_at": None,
            # Stable derived sha so a re-run with unchanged input produces the
            # same commit_sha — SPEC §3.1 ETag derivation depends on this.
            "commit_sha": "stub-" + hashlib.sha256(
                json.dumps(files, sort_keys=True).encode("utf-8")
            ).hexdigest()[:8],
        },
        "files": files,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"wrote {len(files)} files -> {OUT.name}, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
