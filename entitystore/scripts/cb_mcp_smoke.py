#!/usr/bin/env python3
"""
companybrain MCP — live stdio smoke test.

Spawns cb_mcp.py as a subprocess, drives the JSON-RPC protocol manually
(initialize -> tools/list -> tools/call x N), and verifies each tool returns
a sensible payload. Proves the MCP wire boundary works end-to-end against the
live corpus.

Improvements over v1:
  - Per-call timeout on stdout reads (no hangs if server stalls).
  - Tests the new wiki_pack tool.
  - Verifies wiki_ask rejects dump-all (empty query + no filter).
  - Verifies stats reports embedding-provider availability.
  - wiki_add uses commit=False (smoke shouldn't dirty git).

Usage:
    python -X utf8 cb_mcp_smoke.py
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
CB_MCP = HERE / "cb_mcp.py"

READ_TIMEOUT_S = 60.0  # wiki_audit over a few-k-entity corpus is ~10s of work


def main() -> int:
    if not os.environ.get("CB_CORPUS_DIR") or not os.environ.get("CB_SCHEMA_PATH"):
        print("FAIL: CB_CORPUS_DIR and CB_SCHEMA_PATH must be set in the "
              "environment before running cb_mcp_smoke.py. Example:\n"
              "  CB_CORPUS_DIR=... CB_SCHEMA_PATH=... python cb_mcp_smoke.py",
              file=sys.stderr)
        return 2
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        [sys.executable, str(CB_MCP)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=False, bufsize=0,
    )

    # Thread-based line reader — selectors.select doesn't work with Windows
    # pipe handles (WSAStartup required). A reader thread + queue gives us
    # cross-platform per-call timeouts without OS-specific code.
    line_q: queue.Queue = queue.Queue()

    def _reader():
        try:
            for line in iter(proc.stdout.readline, b""):
                line_q.put(("data", line))
        except Exception as e:
            line_q.put(("error", str(e)))
        finally:
            line_q.put(("eof", None))

    threading.Thread(target=_reader, daemon=True).start()

    def send(req: dict) -> None:
        proc.stdin.write(json.dumps(req).encode("utf-8") + b"\n")
        proc.stdin.flush()

    def recv(timeout: float = READ_TIMEOUT_S) -> dict:
        try:
            kind, payload = line_q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(
                f"MCP server did not respond within {timeout}s") from None
        if kind == "eof":
            err = proc.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"MCP server EOF — exit={proc.poll()}, stderr={err[:500]}")
        if kind == "error":
            raise RuntimeError(f"reader thread error: {payload}")
        return json.loads(payload.decode("utf-8"))

    failures = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}{(' -- ' + detail) if detail else ''}")
        if not cond:
            failures += 1

    try:
        # 1. initialize
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "cb_mcp_smoke", "version": "0.2"}}})
        init = recv()
        srv = init.get("result", {}).get("serverInfo", {})
        print(f"-- initialize: server={srv.get('name')!r}")
        check("initialize: server name == 'companybrain'",
              srv.get("name") == "companybrain")

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # 2. tools/list
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_resp = recv()
        tools = tools_resp.get("result", {}).get("tools", [])
        names = sorted(t["name"] for t in tools)
        print(f"-- tools/list: {len(tools)} tools -- {names}")
        expected = {"wiki_ask", "wiki_pack", "wiki_audit", "wiki_add", "stats", "resolve"}
        check("tools/list: 6 tools registered (added wiki_pack)", len(tools) == 6)
        check("tools/list: all 6 expected tools present",
              expected.issubset(set(names)))

        # 3. stats — also reports embedding-provider availability
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "stats", "arguments": {}}})
        sresp = recv()
        sjson = json.loads(sresp["result"]["content"][0]["text"])
        emb = sjson.get("embeddings", {})
        print(f"-- stats: entity_count={sjson.get('entity_count')}, "
              f"schema_version={sjson.get('schema_version')}, "
              f"embeddings={emb}")
        # Range-based: corpus is fluid (scribe-passes land entities). Lower
        # bound is loose enough to ride out short-term growth/shrink; the upper
        # bound catches a runaway double-write or a load_corpus regression.
        ec = sjson.get("entity_count", 0)
        check("stats: entity_count in plausible range (500 <= ec <= 50000)",
              500 <= ec <= 50_000, f"got {ec}")
        check("stats: schema_version=5", sjson.get("schema_version") == 5)
        check("stats: wiki_links_total > 400",
              sjson.get("wiki_links_total", 0) > 400)
        check("stats: embeddings status reported",
              isinstance(emb.get("available"), bool))

        # 4. resolve('Klaveness')
        send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "resolve", "arguments": {"query": "Klaveness"}}})
        rjson = json.loads(recv()["result"]["content"][0]["text"])
        top = rjson["matches"][0]["id"] if rjson["matches"] else None
        print(f"-- resolve('Klaveness'): top={top}, matches={len(rjson['matches'])}")
        check("resolve: 'Klaveness' -> org:kcc top", top == "org:kcc")

        # 5. wiki_ask: empty + no filter -> rejected (no corpus dump)
        send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "wiki_ask", "arguments": {"query": ""}}})
        ej = json.loads(recv()["result"]["content"][0]["text"])
        print(f"-- wiki_ask(''): error={ej['stats'].get('error', '')[:60]}")
        check("wiki_ask: refuses dump-all on empty query",
              ej["stats"].get("error") is not None
              and ej["stats"]["matched"] == 0)

        # 6. wiki_ask('route optimization', mode=hybrid)
        send({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "wiki_ask",
                         "arguments": {"query": "route optimization",
                                       "depth": 1, "budget": 4000,
                                       "mode": "hybrid"}}})
        aj = json.loads(recv()["result"]["content"][0]["text"])
        print(f"-- wiki_ask hybrid 'route optimization': "
              f"matched={aj['stats']['matched']}, "
              f"neighbors={aj['stats']['neighbors']}, "
              f"semantic_used={aj['stats']['semantic_used']}, "
              f"truncated={aj['stats']['truncated']}")
        check("wiki_ask hybrid: >=1 matched for 'route optimization'",
              aj["stats"]["matched"] >= 1)
        sample = aj["matched"][0]["id"] if aj["matched"] else None
        check("wiki_ask: matched payload has concept id",
              isinstance(sample, str) and sample.startswith("concept:"),
              f"sample={sample}")

        # 7. wiki_pack — THIS is the v1.1->v1 win: budget-bounded answer bundle
        send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
              "params": {"name": "wiki_pack",
                         "arguments": {"query": "route optimization",
                                       "budget": 4000, "mode": "hybrid"}}})
        pj = json.loads(recv()["result"]["content"][0]["text"])
        print(f"-- wiki_pack 'route optimization' budget=4000: "
              f"items={pj['stats']['items']}, "
              f"used_tokens={pj['used_tokens']}, "
              f"depths={pj['stats']['depth_breakdown']}, "
              f"dropped={pj['stats']['dropped']}")
        check("wiki_pack: returns items", pj["stats"]["items"] >= 1)
        check("wiki_pack: stays within budget",
              pj["used_tokens"] <= 4000)
        check("wiki_pack: has depth banding (more than one depth tier used)",
              len(pj["stats"]["depth_breakdown"]) >= 2,
              f"depths={pj['stats']['depth_breakdown']}")
        # Top-ranked item should be at the richest depth.
        if pj["items"]:
            top_item = pj["items"][0]
            check("wiki_pack: top-ranked item is at Full depth",
                  top_item["depth"] == 0,
                  f"top={top_item['id']} depth_name={top_item['depth_name']}")

        # 8. wiki_audit (baseline — clean corpus has 0 contradictions)
        send({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
              "params": {"name": "wiki_audit", "arguments": {}}})
        adj = json.loads(recv()["result"]["content"][0]["text"])
        s_ = adj["summary"]
        print(f"-- wiki_audit: contradictions={s_['contradictions']}, "
              f"dead_links={s_['dead_links']}, "
              f"freshness_expired={s_['freshness_expired']}, "
              f"orphans={s_['orphans']}, "
              f"schema_invalid={s_['schema_invalid']}")
        # schema_loaded must be True — otherwise schema_invalid is None and
        # "== 0" hides the missing-schema failure mode.
        check("wiki_audit: schema actually loaded",
              adj.get("schema_loaded") is True)
        check("wiki_audit: 0 schema_invalid (schema loaded)",
              s_["schema_invalid"] == 0)
        check("wiki_audit: 0 contradictions on clean corpus",
              s_["contradictions"] == 0)
        check("wiki_audit: 0 dead_links", s_["dead_links"] == 0)
        check("wiki_audit: reports both totals",
              "entity_count_total" in adj and "entity_count_audited" in adj)

        # 9. wiki_add — commit=False so we don't dirty git on smoke runs.
        synthetic = {
            "id": "concept:cb-mcp-smoke-marker",
            "kind": "concept",
            "names": ["cb_mcp smoke marker"],
            "summary": "Synthetic entity for the MCP wire smoke test (commit=False).",
            "wiki_links": [],
            "topics": ["test"],
            "evidence": [{"source_id": "test:cb-mcp-smoke", "stance": "asserts",
                           "quote": "smoke marker"}],
            "concept": {
                "statement": "smoke test write-roundtrip without git commit",
                "type": "observation", "specificity": "high",
                "falsifiability": "tested", "maturity": "tested",
            },
            "provenance": {
                "extracted_from": ["test:cb-mcp-smoke"],
                "extractor": "cb_mcp/smoke",
                "extraction_method": "system",
                "extraction_confidence": 1.0,
            },
            "created_at": "2026-05-28T00:00:00Z",
            "updated_at": "2026-05-28T00:00:00Z",
        }
        send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "wiki_add",
                         "arguments": {"entity": synthetic, "commit": False}}})
        add = json.loads(recv()["result"]["content"][0]["text"])
        print(f"-- wiki_add(synthetic, commit=False): ok={add.get('ok')}, "
              f"path={add.get('path', add.get('message'))}")
        check("wiki_add: synthetic validates + writes",
              add.get("ok") is True)
        # Clean up (filesystem only — no commit was made).
        if add.get("ok"):
            p = Path(add["path"])
            if p.exists():
                p.unlink()
                print(f"   (cleaned up {p.name})")

        # 10. wiki_add path-traversal block
        bad = dict(synthetic)
        bad["id"] = "concept:../../../etc/passwd-malicious-marker"
        send({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
              "params": {"name": "wiki_add",
                         "arguments": {"entity": bad, "commit": False}}})
        badr = json.loads(recv()["result"]["content"][0]["text"])
        print(f"-- wiki_add(traversal): ok={badr.get('ok')}, "
              f"err={badr.get('error_kind')}")
        check("wiki_add: rejects path-traversal slug",
              badr.get("ok") is False
              and badr.get("error_kind") == "ValidationError")

    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()

    print(f"\n{'=' * 50}")
    print(f"MCP smoke test: {'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
