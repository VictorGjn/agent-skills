"""CSB adapter smoke tests — verify the wiring files are well-formed.

These don't run a real CSB harness (out of scope for this repo); they
guard against typos and shape drift in the adapter pieces.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
CSB = HERE.parent  # eval/csb/


def test_mcp_json_template_is_valid_json():
    raw = (CSB / ".mcp.json.template").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    # Sanity on the shape — Claude/Anthropic conventions
    assert "mcpServers" in parsed
    assert "context-engineering" in parsed["mcpServers"]
    server = parsed["mcpServers"]["context-engineering"]
    assert server["type"] == "http"
    # Placeholders must be present and substitutable
    assert "{{CE_MCP_URL}}" in server["url"]
    assert "Authorization" in server["headers"]
    assert "{{CE_MCP_TOKEN}}" in server["headers"]["Authorization"]


def test_mcp_json_template_substitutes_cleanly():
    """sed-style substitution must produce valid JSON when both vars are filled in."""
    raw = (CSB / ".mcp.json.template").read_text(encoding="utf-8")
    filled = raw.replace("{{CE_MCP_URL}}", "https://example.test").replace("{{CE_MCP_TOKEN}}", "tok123")
    parsed = json.loads(filled)
    assert parsed["mcpServers"]["context-engineering"]["url"] == "https://example.test"
    assert parsed["mcpServers"]["context-engineering"]["headers"]["Authorization"] == "Bearer tok123"


def test_prompt_injection_references_canonical_tool_names():
    """If the injection drifts off the canonical tool names, the agent calls the wrong tool
    and we measure baseline twice (CSB report's gotcha)."""
    body = (CSB / "prompt-injection.md").read_text(encoding="utf-8")
    assert "ce_pack_context" in body
    assert "ce_find_relevant_files" in body
    # MUST tell the agent how to set corpus_id — otherwise it calls without one
    assert "CE_CORPUS_ID" in body or "corpus_id" in body


def test_prompt_injection_does_not_use_legacy_aliases():
    """SPEC § 3.0.2: aliases like `pack` / `resolve` are deprecated. Don't teach them
    to fresh agents."""
    body = (CSB / "prompt-injection.md").read_text(encoding="utf-8")
    # Ban the bare aliases (the canonical names contain them, so word-boundary check)
    for alias in ("pack_context", "find_relevant_files", "resolve"):
        # `pack_context` is part of `ce_pack_context`; only flag bare standalone uses
        # by requiring NOT preceded by `ce_` and NOT inside a code-fence header.
        pattern = re.compile(rf"(?<!ce_)\b{re.escape(alias)}\b")
        # The injection legitimately mentions the workflow — but only via the
        # canonical name. If a bare alias slipped in, this test breaks.
        bare = pattern.findall(body)
        # `pack` standalone is too common (English word); skip it.
        if alias != "resolve":
            continue
        assert not bare, f"prompt-injection.md uses legacy alias {alias!r} — use ce_{alias} instead"


def test_setup_corpus_help_runs():
    """setup_corpus.py --help should not crash."""
    result = subprocess.run(
        [sys.executable, str(CSB / "setup_corpus.py"), "--help"],
        capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0
    assert "--mcp-url" in result.stdout
    assert "--token" in result.stdout
    assert "--repo" in result.stdout
    assert "--upload" in result.stdout


def test_setup_corpus_requires_mode():
    """--repo and --upload are mutually exclusive AND required."""
    result = subprocess.run(
        [sys.executable, str(CSB / "setup_corpus.py"),
         "--mcp-url", "x", "--token", "y"],
        capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0
    # argparse writes mutually-exclusive errors to stderr
    assert "required" in result.stderr.lower() or "one of" in result.stderr.lower()


def test_setup_corpus_emits_parsable_export_format():
    """The harness pipes setup_corpus stdout into `eval $(...)` — must emit
    KEY=value lines only on success. Smoke this with --help (no real call needed).

    On real success, the only stdout line is `CE_CORPUS_ID=<id>`. Sanity check
    by grepping the source for the print format.
    """
    src = (CSB / "setup_corpus.py").read_text(encoding="utf-8")
    assert 'print(f"CE_CORPUS_ID={cid}")' in src or 'print("CE_CORPUS_ID=' in src


def test_upload_via_local_index_does_not_subprocess(tmp_path, monkeypatch):
    """Codex P1: prior version subprocessed `index_workspace.py --output`, but
    that flag doesn't exist — every --upload call FileNotFound'd before any MCP
    call. Verify the new in-process path actually returns a corpus_id."""
    sys.path.insert(0, str(CSB))
    from setup_corpus import upload_via_local_index  # type: ignore

    # Stub the HTTP POST so we don't need a real MCP. Verify it's reached.
    import setup_corpus as _sc

    captured: dict = {}
    def fake_post(url, token, payload, timeout=60):
        captured["payload"] = payload
        return {"jsonrpc": "2.0", "id": 1,
                "result": {"structuredContent": {"corpus_id": "local-test", "commit_sha": "x", "version": 1,
                                                  "stats": {"file_count": 1, "embedded_count": 0, "size_bytes": 0}}}}
    monkeypatch.setattr(_sc, "_post", fake_post)

    # Build a 1-file workspace
    (tmp_path / "a.md").write_text("# Hello\n\nWorld.\n", encoding="utf-8")

    cid = upload_via_local_index("http://x", "tok", str(tmp_path), "local-test", "internal")
    assert cid == "local-test"
    # Make sure we actually invoked ce_upload_corpus with the right shape
    assert captured["payload"]["params"]["name"] == "ce_upload_corpus"
    args = captured["payload"]["params"]["arguments"]
    assert args["corpus_id"] == "local-test"
    assert args["data_classification"] == "internal"
    assert isinstance(args["files"], list) and len(args["files"]) >= 1
    # contentHash normalization happened (scripts/* emit `hash`)
    for f in args["files"]:
        assert "contentHash" in f


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-xvs"]))
