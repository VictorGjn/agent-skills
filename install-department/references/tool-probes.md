# Tool probes

Per-tool probe contracts. The Phase 2 sub-agent for each connected tool
runs the probe described here and writes results to
`cache/probe/<tool>.jsonl` — one JSONL line per discovered entity.

## Common probe contract

Every probe MUST emit lines matching this shape:

```json
{
  "tool": "<tool slug>",
  "entity_kind": "<database | project | channel | list | board | label | other>",
  "entity_id": "<tool-native id>",
  "entity_name": "<human-readable name>",
  "item_count_estimate": <int | null>,
  "latest_activity_at": "<ISO-8601 timestamp | null>",
  "head_access": "<r | rw | none>",
  "linked_to": [
    { "tool": "<other tool slug>", "entity_id": "<other entity id>", "kind": "<reference | mention | foreign_key>" }
  ]
}
```

Any field unknown to the probe SHOULD be `null`, not omitted. The
synthesizer (Phase 4) treats `null` as "not probed" and `[]` as "probed
and confirmed empty" — these are different.

## Supported tools

| Tool | Slug | Connection method | Probe scope |
|---|---|---|---|
| Notion | `notion` | Pipedream MCP | Workspace databases the head has read access to |
| HubSpot | `hubspot` | Pipedream MCP | CRM objects, lists, workflows in the head's owner scope |
| Linear | `linear` | Pipedream MCP | Teams the head is a member of, plus owned projects |
| Slack | `slack` | Pipedream MCP | Channels the head is a member of (private + public), plus DMs metadata only (never message content) |
| Gmail | `gmail` | Pipedream MCP | Labels and filter rules; never message bodies |
| Granola | `granola` | Pipedream MCP | Folders + meeting count; never transcript content |
| GitHub | `github` | Pipedream MCP | Repos the head has push access to + their default branches' top-level structure |
| Figma | `figma` | Pipedream MCP | Teams + projects + file count |
| Mixpanel | `mixpanel` | Pipedream MCP | Project list + event count |

The skill probes only what the head has authorized. The probe MUST NOT
attempt to enumerate beyond the head's access scope; that surfaces as a
permission error and is ignored, not retried.

## Per-tool details

### Notion (`notion`)

```
For each database in the head's workspace where head has access:
  → emit one entity_kind=database line
  → item_count_estimate = page count via `notion-query-database` with limit metadata
  → latest_activity_at = max(last_edited_time) across first page of results
  → linked_to: scan first 50 pages for relation properties; emit foreign_key links
```

Excludes: Notion templates marked as such; private pages outside any
database.

### HubSpot (`hubspot`)

```
For each standard CRM object the head can read (Companies, Contacts, Deals, Tickets, Custom Objects):
  → emit one entity_kind=database line per object
  → item_count_estimate via the count endpoint
  → latest_activity_at = max(hs_lastmodifieddate) across recent batch
  → linked_to: emit known associations (deal→company, deal→contact, ticket→contact)
```

Also probes Workflows and Lists owned by or shared with the head.

### Linear (`linear`)

```
For each team the head is a member of:
  → emit entity_kind=board (the team)
  → for each project owned by head: emit entity_kind=project
  → for each cycle: emit entity_kind=other with name="cycle <number>"
  → item_count_estimate = open issue count
  → linked_to: project→team, cycle→team
```

### Slack (`slack`)

Most sensitive — the probe MUST NOT read message content.

```
For each channel the head is a member of:
  → emit entity_kind=channel
  → item_count_estimate = member count
  → latest_activity_at = via channel info API only
  → linked_to: []
```

DMs are summarized as a single aggregate entity, never enumerated by
counterparty.

### Gmail (`gmail`)

```
For each label in head's account:
  → emit entity_kind=label
  → item_count_estimate = message count via label metadata only
  → latest_activity_at = via label history API
For each filter:
  → emit entity_kind=other with name="filter: <human description>"
```

The probe MUST NOT call any message-read API. Subject lines are body content.

### Granola (`granola`)

```
For each folder the head has access to:
  → emit entity_kind=other with name="folder: <name>"
  → item_count_estimate = meeting count in folder
  → latest_activity_at = most-recent meeting start time
```

The probe MUST NOT fetch transcript content.

### GitHub (`github`)

```
For each repo where head has push access:
  → emit entity_kind=project
  → item_count_estimate = open issue + open PR count
  → latest_activity_at = most-recent commit on default branch
  → linked_to: cross-repo references via gh search if mentioned in heads' repos
```

Reads no source code. Reads no issue/PR bodies. Inventory only.

### Figma (`figma`)

```
For each team the head belongs to:
  → emit entity_kind=board (the team)
  → for each project: emit entity_kind=project
  → item_count_estimate = file count
```

Reads no design content.

### Mixpanel (`mixpanel`)

```
For each project the head has access to:
  → emit entity_kind=project
  → item_count_estimate = event count over last 30d via Mixpanel admin API
  → latest_activity_at = latest event timestamp
```

Reads no event-level data.

## Adding a new tool

1. Add the tool to the table above with slug + connection method.
2. Write its per-tool details section. Include the explicit no-content
   constraint if the tool stores user data.
3. Add the per-tool probe to `scripts/probe_tool.py` dispatch.
4. Test with a real connected account; add the probe output to a
   fixture in `cache/probe/.examples/`.

## Rules

| Rule | Why |
|---|---|
| Probes are read-only | The skill installs understanding, not changes |
| No message/content/transcript reading, ever | Privacy floor — content is not needed for inventory |
| Per-tool failures don't fail the run | One bad probe shouldn't block the install |
| Probe output is JSONL, never JSON | Append-only, streamable, one entity per line |
| Cache results so re-runs are cheap | The head may abandon mid-interview; we resume |
