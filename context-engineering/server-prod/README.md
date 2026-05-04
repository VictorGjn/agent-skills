# CE MCP — production server

Conformant Model Context Protocol server implementing `SPEC-mcp.md` v1.0.0-rc4.

**Status (Phase 5)**: all 7 spec tools wired. Read tools (pack/find/list_corpora) and write tools (upload/index/job_status) ship in v1; `ce_index_github_repo` with `async=true` returns `NOT_IMPLEMENTED` until v1.1 lands the Cron + queue worker.

| | This server (production) | YC-demo stub at `server-stub/` |
|---|---|---|
| Tools | 7 working (1 has `async=true` deferred) | 3 working (pack/list/health) |
| Transport | MCP Streamable HTTP + JSON-RPC | Plain HTTP/JSON |
| Tool naming | `ce_*` canonical per § 3.0.2 | bare names (legacy) |
| Annotations | per § 3.0.3 | none |
| Auth | Bearer + (v1.1) OAuth 2.1 | Bearer |
| Error envelope | split tool/protocol per § 7 | flat HTTP |
| Multi-corpus | yes (`corpus_ids[]`, parity, prefix collision) | no |
| ETag + Cache-Control | yes (304 conditional, classification-aware no-store) | no |

## Layout

```
api/
  mcp.py        — POST/GET /api/mcp — Streamable HTTP transport
  health.py     — GET /api/health — HTTP-direct liveness

_lib/
  auth.py       — Bearer token middleware + role lookup
  errors.py     — § 7.1 / § 7.2 / HTTP-direct shapes
  annotations.py — § 3.0.3 tool annotation table
  corpus_store.py — filesystem corpus index reader
  corpus_access.py — shared load/parity/collision helpers
  engine.py     — bridge to vendored pack_context_lib
  job_store.py  — in-memory job registry (v1.1: Vercel KV)
  vendor/
    pack_context_lib.py — VENDORED COPY of scripts/pack_context_lib.py
                          (Vercel can't reach parent dirs; sync via test)
  tools/
    health.py
    pack.py        — ce_pack_context (§ 3.1)
    find.py        — ce_find_relevant_files (§ 3.2)
    list_corpora.py — ce_list_corpora (§ 3.5)
    upload_corpus.py — ce_upload_corpus (§ 3.3)
    index_github_repo.py — ce_index_github_repo (§ 3.4)
    get_job_status.py — ce_get_job_status (§ 3.7)
  transport.py  — JSON-RPC dispatcher

vercel.json     — runtime config (cdg1, 60s, 1024MB)
requirements.txt — runtime deps
pyproject.toml  — package metadata
tests/          — Phase 2/3/4/5 test suites (94 tests, 100% green)
```

## Local dev

```bash
cd context-engineering/server-prod
pip install -r requirements.txt
export CE_MCP_BOOTSTRAP_TOKEN=$(openssl rand -hex 32)
vercel dev
```

Smoke:

```bash
curl http://localhost:3000/api/health
# {"ok": true, "version": "1.0.0", ...}

curl -X POST http://localhost:3000/api/mcp \
  -H "Authorization: Bearer $CE_MCP_BOOTSTRAP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"smoke","version":"0"}}}'

curl -X POST http://localhost:3000/api/mcp \
  -H "Authorization: Bearer $CE_MCP_BOOTSTRAP_TOKEN" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

## Auth

`CE_MCP_BOOTSTRAP_TOKEN` env. Server hashes it on startup, never stores plaintext. The bootstrap token has `admin` role + `restricted` data classification cap. v1.1 adds the hashed-token-map KV per § 6.1.

## Embedding provider (read tools)

The v1 keyword pipeline doesn't need embeddings. For semantic mode (Phase 6+), set exactly one of:
- `MISTRAL_API_KEY` (default; codestral-embed, 1536d)
- `VOYAGE_API_KEY`
- `OPENAI_API_KEY`

BGE-local is **disabled** in MCP server mode (sentence-transformers can't run in Vercel functions).

## Deployment to Vercel

```bash
cd context-engineering/server-prod
vercel link                    # one-time: creates .vercel/project.json
vercel env add CE_MCP_BOOTSTRAP_TOKEN production
vercel env add MISTRAL_API_KEY production   # optional, semantic mode only
vercel --prod                  # deploy
```

Region: `cdg1` (Paris) for proximity to Syroco infra.

After deploy:

```bash
DEPLOY_URL=$(vercel ls --prod --json | jq -r '.[0].url')
curl https://$DEPLOY_URL/api/health
# Expect: {"ok": true, "version": "1.0.0", ...}
```

## Caching headers

Per SPEC § 3.1:
- `ce_pack_context` / `ce_find_relevant_files` responses include `ETag` + `Cache-Control`
- Classification-aware: `private, max-age=60` for public/internal; `no-store` for confidential/restricted (or any multi-corpus mix containing one)
- `If-None-Match` returns `304 Not Modified` with the cached `ETag` echoed

ETag is a sha256(commit_key || RFC8785-canonical-inputs)[:24]. Multi-corpus uses lex-sorted `<corpus_id>:<sha>` joined with `|` as the commit_key.

## Vendor sync

`_lib/vendor/pack_context_lib.py` is a byte-identical copy of `../scripts/pack_context_lib.py`. Vercel function bundles can't reach parent directories. The sync test (`tests/test_phase5.py::test_vendor_pack_context_lib_in_sync_with_canonical`) sha-checks both files and fails on drift.

Refresh on canonical changes:

```bash
cp ../scripts/pack_context_lib.py _lib/vendor/pack_context_lib.py
python -m pytest tests/test_phase5.py::test_vendor_pack_context_lib_in_sync_with_canonical
```

## Test suite

```bash
CE_MCP_BOOTSTRAP_TOKEN=test-token python -m pytest -v tests/
```

- `test_phase2.py` — server foundation (initialize, tools/list, auth, alias, errors)
- `test_phase3.py` — read tools (pack/find/list_corpora, multi-corpus, classification, lifecycle)
- `test_phase4.py` — write tools (upload/index/job_status, idempotency, locks, role gating)
- `test_phase5.py` — deploy hardening (vendor sync, ETag, Cache-Control, 304)

94 tests. All green.
