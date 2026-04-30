# CE MCP — server stub (YC-demo deployment)

Vercel-deployable HTTP wrapper around the existing context-engineering CLI.
Two tools (`pack_context`, `list_corpora`) plus `health`. Bearer auth.

**This is the stub for the YC video demo, not the production v1.** The
production spec lives at [`../SPEC-mcp.md`](../SPEC-mcp.md). Differences:

| | Stub | Production v1 |
|---|---|---|
| Tool count | 2 + health | 6 + health |
| State | committed `cache/<corpus>.index.json` | live `syrocolab/company-brain` GitHub repo |
| Indexing | `build_demo_index.py` (one-shot, regex-based) | server-side via GitHub App + tarball API |
| Scoring | keyword only | RRF hybrid: keyword + semantic + graph |
| Depth bands | 3 (full / summary / mention) | 5 (full / detail / summary / structure / mention) |
| Embeddings | none | 4-provider abstraction via PR #12 |
| Auth | single bearer (`CE_MCP_TOKEN`) | hashed token map + role-based caps + `data_classification` gates |
| Transport | plain HTTP/JSON | MCP HTTP+SSE per `modelcontextprotocol.io/spec` |

The stub answers "is the wire shape demonstrably correct?", not "does this
ship to production?". Spec compliance for `pack_context` request/response
shapes is preserved (a v1 client can swap stub URL → prod URL transparently).

## Endpoints

- `GET /api/health` — liveness, version, providers configured
- `GET /api/list_corpora` — corpora available in this deployment
- `POST /api/pack_context` — depth-packed markdown for a query

See `public/index.html` for curl examples.

## Local dev

```bash
cd server-stub
python3 build_demo_index.py        # produces cache/<corpus>.index.json
python3 -m http.server 3000        # not Vercel; just to check public/index.html

# Run the actual functions (they're plain BaseHTTPRequestHandler classes;
# Vercel CLI is the easiest way to spin them up locally):
npm i -g vercel
vercel dev
```

`CE_MCP_TOKEN` env unset → open mode (no auth). Set it for parity with
deployed behavior.

## Deploy

```bash
# from the repo root (NOT this subdir):
vercel link --project ce-mcp-stub
vercel env add CE_MCP_TOKEN production    # set a long random string
vercel --prod
```

The `vercel.json` here pins the build to this directory. The deployed URL
will look like `https://ce-mcp-stub.vercel.app/api/pack_context`.

## What this stub costs

Vercel Hobby tier free quota covers expected YC-demo traffic. Functions
hit Vercel's 10s timeout cap; `pack_context` against the 32-file demo
corpus runs in ~50ms warm. Production v1 needs Pro tier (60s) for sync
GitHub indexing.

## Updating the demo corpus

Re-run `build_demo_index.py` whenever a meaningfully relevant file
changes in `../scripts/` or `../*.md`. Commit the regenerated JSON.

The build script's `MAX_FILES = 60` cap keeps the index <500KB so the
function bundle stays small. Lift the cap when the live brain ships.
