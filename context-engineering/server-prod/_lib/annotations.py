"""Tool annotation table per SPEC-mcp.md § 3.0.3.

Per MCP 2025-06-18 spec, annotations are hints clients use for parallelization,
safety prompts, and read-only-mode filtering. NOT security guarantees.

destructiveHint is `false` for write tools because § 3.3/§ 3.4 idempotency
contracts make a re-call with identical inputs a no-op — the v2 delete_corpus
will be the first tool to set this true.
"""
from __future__ import annotations

# Per § 3.0.3: { tool_name: {readOnlyHint, destructiveHint, idempotentHint, openWorldHint} }
ANNOTATIONS: dict[str, dict[str, bool]] = {
    "ce_pack_context": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "ce_find_relevant_files": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "ce_upload_corpus": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    "ce_index_github_repo": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    "ce_list_corpora": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "ce_get_health": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "ce_get_job_status": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


# v1.0 alias map per § 3.0.2 — bare names accepted with X-CE-Deprecated warning header.
ALIASES: dict[str, str] = {
    "pack_context": "ce_pack_context",
    "pack": "ce_pack_context",
    "find_relevant_files": "ce_find_relevant_files",
    "resolve": "ce_find_relevant_files",
    "upload_indexed_corpus": "ce_upload_corpus",
    "register_corpus": "ce_upload_corpus",
    "index_github_repo": "ce_index_github_repo",
    "index_workspace": "ce_index_github_repo",
    "list_corpora": "ce_list_corpora",
    "get_health": "ce_get_health",
    "health": "ce_get_health",
    "get_job_status": "ce_get_job_status",
}


def canonical(tool_name: str) -> str:
    """Resolve any v1.0 alias / legacy name to the canonical ce_* name."""
    return ALIASES.get(tool_name, tool_name)


def is_alias(tool_name: str) -> bool:
    return tool_name in ALIASES
