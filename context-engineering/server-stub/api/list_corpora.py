"""Vercel Function — GET /api/list_corpora
Lists corpora available in this stub deployment. Stub reads from
`cache/<corpus_id>.index.json` files committed alongside the function.

Production v1 reads from syrocolab/company-brain via GitHub API. This stub
ships a single hard-coded corpus (agent-skills itself) for the YC demo.
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from auth import require_bearer  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not require_bearer(self):
            return
        corpora = []
        if CACHE_DIR.exists():
            for index_file in sorted(CACHE_DIR.glob("*.index.json")):
                corpus_id = index_file.stem.replace(".index", "")
                try:
                    with open(index_file, encoding="utf-8") as f:
                        meta = json.load(f).get("_meta", {})
                except (OSError, json.JSONDecodeError):
                    meta = {}
                corpora.append({
                    "corpus_id": corpus_id,
                    "source": meta.get("source", {"type": "github_repo", "uri": "unknown"}),
                    "lifecycle_state": "active",
                    "data_classification": meta.get("data_classification", "internal"),
                    "embedding": meta.get("embedding", {"provider": "none", "model": "n/a", "dims": 0}),
                    "stats": {
                        "file_count": meta.get("file_count", 0),
                        "embedded_count": 0,
                        "size_bytes": index_file.stat().st_size,
                    },
                    "version": meta.get("version", 1),
                    "last_refresh_completed_at": meta.get("last_refresh_completed_at"),
                    "archive_location": None,
                })

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "private, max-age=30")
        self.end_headers()
        self.wfile.write(json.dumps({"corpora": corpora}).encode("utf-8"))
