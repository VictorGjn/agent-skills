# CE MCP — production server (replaces YC-demo stub)

Conformant Model Context Protocol server implementing `SPEC-mcp.md` v1.0.0-rc4.

**Status (Phase 2)**: scaffold + `ce_get_health` only. Other 6 tools are registered in `tools/list` but return `NOT_IMPLEMENTED` until Phase 3 (read tools) and Phase 4 (write tools) land.

| | This server (production) | YC-demo stub at `server-stub/` |
|---|---|---|
| Tools (callable) | 1 working + 6 NOT_IMPLEMENTED | 3 working (pack_context, list_corpora, health) |
| Transport | MCP Streamable HTTP + JSON-RPC | Plain HTTP/JSON |
| Tool naming | `ce_*` canonical per § 3.0.2 | bare names (legacy) |
| Annotations | per § 3.0.3 | none |
| Auth | Bearer + (v1.1) OAuth 2.1 | Bearer |
| Error envelope | split tool/protocol per § 7 | flat HTTP |
| Multi-corpus | scaffolded for Phase 3 | none |

## Layout

```
api/
  mcp.py        — POST/GET /api/mcp — Streamable HTTP transport (JSON-RPC dispatcher)
  health.py     — GET /api/health — HTTP-direct liveness (parallel to MCP-mode ce_get_health)

_lib/
  auth.py       — Bearer token middleware + role lookup
  errors.py     — § 7.1 tool errors / § 7.2 protocol errors / HTTP-direct shape
  annotations.py — § 3.0.3 tool annotation table
  version.py    — package version + git sha
  tools/        — one file per ce_* tool
    health.py   — ce_get_health (Phase 2 only working tool)
    pack.py     — ce_pack_context (Phase 3)
    find.py     — ce_find_relevant_files (Phase 3)
    list_corpora.py  — ce_list_corpora (Phase 3)
    upload.py   — ce_upload_corpus (Phase 4)
    index_repo.py — ce_index_github_repo (Phase 4)
    job_status.py — ce_get_job_status (Phase 4)

pyproject.toml — context_engineering_mcp package metadata
requirements.txt — runtime deps
vercel.json    — Vercel Functions config
```

## Local dev

```bash
cd server-prod
pip install -r requirements.txt
vercel dev    # Vercel CLI; or `python3 -m server-prod.api.mcp` for stdio mode
```

Once running, smoke-test:

```bash
curl https://localhost:3000/api/health
# {"ok": true, "version": "1.0.0", ...}

# MCP initialize handshake
curl -X POST https://localhost:3000/api/mcp \
  -H "Authorization: Bearer $CE_MCP_BOOTSTRAP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke","version":"0"}}}'
```

## Auth

Set `CE_MCP_BOOTSTRAP_TOKEN` env. Server hashes it on startup, never stores plaintext. v1.1 adds the hashed-token-map KV per § 6.1.

## Embedding provider

Set exactly one of `MISTRAL_API_KEY` (default), `VOYAGE_API_KEY`, `OPENAI_API_KEY`. BGE-local is **disabled** in MCP server mode (sentence-transformers can't run in Vercel functions).

## Deployment

Phase 5 ships this to Vercel. Until then, this directory is buildable but not deployed.
