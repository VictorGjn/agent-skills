"""Tool handlers per SPEC-mcp.md § 3.

Each tool exports a `handle(args: dict, token: TokenInfo) -> dict` function.
On success: returns the tool's structured result dict.
On tool error: returns `errors.tool_error(...)` envelope with `_http_status`.

Register all handlers via _lib.transport.register_tool() at import time.
"""
from .. import transport
from . import find, get_job_status, health, index_github_repo, list_corpora, pack, upload_corpus

transport.register_tool("ce_get_health", health.handle)
transport.register_tool("ce_pack_context", pack.handle)
transport.register_tool("ce_find_relevant_files", find.handle)
transport.register_tool("ce_list_corpora", list_corpora.handle)
transport.register_tool("ce_upload_corpus", upload_corpus.handle)
transport.register_tool("ce_index_github_repo", index_github_repo.handle)
transport.register_tool("ce_get_job_status", get_job_status.handle)
