"""Vercel Function — POST /api/pack_context
Headline tool. Given a query and a corpus, returns a depth-packed markdown
bundle of relevant files within a token budget.

This stub implements a *minimal* version of the depth packer — keyword
scoring + 3 depth bands (Full / Summary / Mention). The production v1
implementation in scripts/pack_context_lib.py has 5 depth bands, RRF
hybrid scoring, semantic mode, graph traversal, and knowledge_type
classification. The stub is for YC-demo use only.

Spec: SPEC-mcp.md §3.1 (CE-1.0-draft).
"""
from http.server import BaseHTTPRequestHandler
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from auth import require_bearer  # noqa: E402
from packer import pack  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"

DEFAULT_BUDGET = 32000
MIN_BUDGET = 1000
MAX_BUDGET = 200000

VALID_MODES = {"auto", "keyword", "semantic", "graph", "deep", "wide"}
VALID_TASKS = {"fix", "review", "explain", "build", "document", "research"}


def _err(self, code, message, status=400, retryable=False, details=None):
    body = {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
    }
    if details:
        body["error"]["details"] = details
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(json.dumps(body).encode("utf-8"))


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not require_bearer(self):
            return

        start = time.time()

        # ── Parse body ──
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError) as e:
            return _err(self, "INVALID_ARGUMENT", f"malformed JSON body: {e}")

        # ── Validate inputs ──
        query = (req.get("query") or "").strip()
        if not query:
            return _err(self, "INVALID_ARGUMENT", "query is required")
        if len(query) > 4096:
            return _err(self, "INVALID_ARGUMENT", "query exceeds 4096 chars")

        corpus_id = (req.get("corpus_id") or "").strip()
        if not corpus_id:
            return _err(self, "INVALID_ARGUMENT", "corpus_id is required")
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,127}$", corpus_id):
            return _err(self, "INVALID_ARGUMENT", f"invalid corpus_id format: {corpus_id!r}")

        budget = req.get("budget", DEFAULT_BUDGET)
        if not isinstance(budget, int) or budget < MIN_BUDGET or budget > MAX_BUDGET:
            return _err(self, "INVALID_ARGUMENT",
                        f"budget must be int in [{MIN_BUDGET}, {MAX_BUDGET}]")

        mode = req.get("mode", "auto")
        if mode not in VALID_MODES:
            return _err(self, "INVALID_ARGUMENT", f"unknown mode: {mode}")

        task = req.get("task")
        if task is not None and task not in VALID_TASKS:
            return _err(self, "INVALID_ARGUMENT", f"unknown task: {task}")

        why = bool(req.get("why", False))

        # ── Locate corpus ──
        index_path = CACHE_DIR / f"{corpus_id}.index.json"
        if not index_path.exists():
            return _err(self, "CORPUS_NOT_FOUND",
                        f"no index for corpus {corpus_id!r}",
                        status=404)

        try:
            with open(index_path, encoding="utf-8") as f:
                index = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return _err(self, "INTERNAL", f"index corrupt: {e}", status=500, retryable=True)

        # ── Pack ──
        try:
            result = pack(query=query, index=index, budget=budget, why=why)
        except Exception as e:
            return _err(self, "INTERNAL", str(e), status=500, retryable=True)

        result["corpus_commit_sha"] = index.get("_meta", {}).get("commit_sha", "stub")
        result["took_ms"] = int((time.time() - start) * 1000)

        # ── Send (private cache only — confidential corpora MUST NOT be
        #    cached by shared/CDN intermediaries; per audit fix #1) ──
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "private, max-age=60")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode("utf-8"))
