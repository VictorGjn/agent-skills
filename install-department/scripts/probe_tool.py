"""
Per-tool probe runner.

probe(tool_slug) → list[dict] of probe entities matching the contract in
references/tool-probes.md.

list_connected_tools() → list[str] of tool slugs connected via Pipedream.

Per-tool implementations are dispatched from PROBE_REGISTRY. Adding a new
tool: write a function `_probe_<slug>(client) -> list[dict]` and register it.
"""

import os
from typing import Callable

PROBE_REGISTRY: dict[str, Callable] = {}


def register(slug: str):
    def decorator(fn: Callable) -> Callable:
        PROBE_REGISTRY[slug] = fn
        return fn
    return decorator


def list_connected_tools() -> list[str]:
    """Return the slugs of tools the head has authorized via Pipedream / Syroco Connect.

    Production: calls Pipedream's account-list endpoint via the Syroco Connect MCP.
    Stub: reads INSTALL_DEPARTMENT_TOOLS env var (comma-separated slugs).
    """
    env = os.environ.get("INSTALL_DEPARTMENT_TOOLS", "")
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    return []


def probe(tool_slug: str) -> list[dict]:
    """Run the probe for a tool. Raises NotImplementedError if no probe is registered."""
    fn = PROBE_REGISTRY.get(tool_slug)
    if fn is None:
        raise NotImplementedError(f"No probe registered for tool '{tool_slug}'.")
    client = _get_client(tool_slug)
    return fn(client)


def _get_client(tool_slug: str):
    """Return an MCP client for the tool. Production wires to Syroco Connect.

    Stub returns None — per-tool probes guard for None and return [] in dev.
    """
    return None


# ── Per-tool probes ──


@register("notion")
def _probe_notion(client) -> list[dict]:
    """Probe Notion: enumerate databases the head can read.

    Contract: emit one entity per database with its page count, latest edit,
    and any relation-property foreign-keys.
    """
    if client is None:
        return []

    out: list[dict] = []
    for db in client.list_databases():
        item_count = client.count_database_pages(db["id"])
        latest = client.latest_edit(db["id"])
        out.append({
            "tool": "notion",
            "entity_kind": "database",
            "entity_id": db["id"],
            "entity_name": db["title"],
            "item_count_estimate": item_count,
            "latest_activity_at": latest,
            "head_access": "rw" if db.get("can_edit") else "r",
            "linked_to": [
                {"tool": "notion", "entity_id": rel["target_db"], "kind": "foreign_key"}
                for rel in client.list_relations(db["id"])
            ],
        })
    return out


@register("hubspot")
def _probe_hubspot(client) -> list[dict]:
    """Probe HubSpot: enumerate CRM objects the head can read."""
    if client is None:
        return []

    out: list[dict] = []
    for obj_type in ("companies", "contacts", "deals", "tickets"):
        try:
            count = client.count_objects(obj_type)
            latest = client.latest_modified(obj_type)
        except PermissionError:
            continue
        out.append({
            "tool": "hubspot",
            "entity_kind": "database",
            "entity_id": obj_type,
            "entity_name": obj_type.capitalize(),
            "item_count_estimate": count,
            "latest_activity_at": latest,
            "head_access": "r",
            "linked_to": _hubspot_associations(obj_type),
        })
    return out


def _hubspot_associations(obj_type: str) -> list[dict]:
    if obj_type == "deals":
        return [
            {"tool": "hubspot", "entity_id": "companies", "kind": "foreign_key"},
            {"tool": "hubspot", "entity_id": "contacts", "kind": "foreign_key"},
        ]
    if obj_type == "tickets":
        return [{"tool": "hubspot", "entity_id": "contacts", "kind": "foreign_key"}]
    return []


@register("linear")
def _probe_linear(client) -> list[dict]:
    """Probe Linear: enumerate teams + projects."""
    if client is None:
        return []

    out: list[dict] = []
    for team in client.list_teams_for_user():
        out.append({
            "tool": "linear",
            "entity_kind": "board",
            "entity_id": team["id"],
            "entity_name": team["name"],
            "item_count_estimate": client.count_open_issues(team["id"]),
            "latest_activity_at": client.latest_issue_update(team["id"]),
            "head_access": "rw",
            "linked_to": [],
        })
        for project in client.list_projects(team["id"]):
            out.append({
                "tool": "linear",
                "entity_kind": "project",
                "entity_id": project["id"],
                "entity_name": project["name"],
                "item_count_estimate": project.get("issue_count"),
                "latest_activity_at": project.get("updated_at"),
                "head_access": "rw",
                "linked_to": [{"tool": "linear", "entity_id": team["id"], "kind": "reference"}],
            })
    return out


@register("slack")
def _probe_slack(client) -> list[dict]:
    """Probe Slack: enumerate channels the head is in. Never reads message content."""
    if client is None:
        return []

    out: list[dict] = []
    for ch in client.list_channels_for_user():
        out.append({
            "tool": "slack",
            "entity_kind": "channel",
            "entity_id": ch["id"],
            "entity_name": ch["name"],
            "item_count_estimate": ch.get("num_members"),
            "latest_activity_at": ch.get("last_message_at"),
            "head_access": "rw",
            "linked_to": [],
        })
    return out


@register("gmail")
def _probe_gmail(client) -> list[dict]:
    """Probe Gmail: enumerate labels and filters. Never reads message bodies."""
    if client is None:
        return []

    out: list[dict] = []
    for label in client.list_labels():
        out.append({
            "tool": "gmail",
            "entity_kind": "label",
            "entity_id": label["id"],
            "entity_name": label["name"],
            "item_count_estimate": label.get("messages_total"),
            "latest_activity_at": None,
            "head_access": "rw",
            "linked_to": [],
        })
    for f in client.list_filters():
        out.append({
            "tool": "gmail",
            "entity_kind": "other",
            "entity_id": f["id"],
            "entity_name": f"filter: {f['description']}",
            "item_count_estimate": None,
            "latest_activity_at": None,
            "head_access": "rw",
            "linked_to": [],
        })
    return out


@register("granola")
def _probe_granola(client) -> list[dict]:
    """Probe Granola: enumerate folders + meeting count. Never reads transcripts."""
    if client is None:
        return []

    out: list[dict] = []
    for folder in client.list_folders():
        out.append({
            "tool": "granola",
            "entity_kind": "other",
            "entity_id": folder["id"],
            "entity_name": f"folder: {folder['name']}",
            "item_count_estimate": folder.get("meeting_count"),
            "latest_activity_at": folder.get("latest_meeting_at"),
            "head_access": "rw",
            "linked_to": [],
        })
    return out


@register("github")
def _probe_github(client) -> list[dict]:
    """Probe GitHub: enumerate repos with push access. Reads inventory only."""
    if client is None:
        return []

    out: list[dict] = []
    for repo in client.list_repos_with_push():
        out.append({
            "tool": "github",
            "entity_kind": "project",
            "entity_id": repo["full_name"],
            "entity_name": repo["full_name"],
            "item_count_estimate": (repo.get("open_issues_count") or 0) + (repo.get("open_prs") or 0),
            "latest_activity_at": repo.get("pushed_at"),
            "head_access": "rw",
            "linked_to": [],
        })
    return out


@register("figma")
def _probe_figma(client) -> list[dict]:
    """Probe Figma: enumerate teams + projects. Reads no design content."""
    if client is None:
        return []

    out: list[dict] = []
    for team in client.list_teams():
        out.append({
            "tool": "figma",
            "entity_kind": "board",
            "entity_id": team["id"],
            "entity_name": team["name"],
            "item_count_estimate": None,
            "latest_activity_at": None,
            "head_access": "rw",
            "linked_to": [],
        })
        for project in client.list_projects(team["id"]):
            out.append({
                "tool": "figma",
                "entity_kind": "project",
                "entity_id": project["id"],
                "entity_name": project["name"],
                "item_count_estimate": project.get("file_count"),
                "latest_activity_at": project.get("updated_at"),
                "head_access": "rw",
                "linked_to": [{"tool": "figma", "entity_id": team["id"], "kind": "reference"}],
            })
    return out


@register("mixpanel")
def _probe_mixpanel(client) -> list[dict]:
    """Probe Mixpanel: enumerate projects with admin access. Reads no event data."""
    if client is None:
        return []

    out: list[dict] = []
    for project in client.list_projects():
        out.append({
            "tool": "mixpanel",
            "entity_kind": "project",
            "entity_id": project["id"],
            "entity_name": project["name"],
            "item_count_estimate": project.get("event_count_30d"),
            "latest_activity_at": project.get("latest_event_at"),
            "head_access": "rw",
            "linked_to": [],
        })
    return out
