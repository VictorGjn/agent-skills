# Step 2 — Index a workspace

> **Cell: persistent memory.** The index is the brain's short-term memory. It's structured, regenerable, and cheap.

## What you'll do

Index a workspace explicitly (instead of relying on the auto-index from step 0), inspect what was produced, and re-pack against it.

## Command

```bash
python3 scripts/index_workspace.py /path/to/my-app/
```

Stderr prints a one-line summary; the workspace index lands in `cache/workspace-index.json` with a `cache/workspace-index-light.json` companion.

## What just happened

The indexer walks the directory and emits two kinds of structure per file:

| File type | Extracted | Engine used |
|-----------|-----------|-------------|
| Markdown / prose | Heading tree | Native parser |
| Code (14 languages) | AST symbols — every function, class, interface becomes a heading | tree-sitter |
| Other text | Whole-file token count | Plain |

Common build artefacts are skipped automatically (`.next`, `dist`, `node_modules`, `target`, etc.).

The two output files differ in size:

| File | Size for ~500-file repo | Contents |
|------|--------------------------|----------|
| `workspace-index.json` | ~3-5 MB | Full heading trees with content, first sentences, first paragraphs |
| `workspace-index-light.json` | ~500 KB | Headings + metadata only — quick lookups |

A file entry in the light index looks like:

```json
{
  "path": "src/hooks/use-tab-history.ts",
  "tokens": 406,
  "nodeCount": 3,
  "headings": [
    { "depth": 0, "title": "src/hooks/use-tab-history.ts", "tokens": 406 },
    { "depth": 1, "title": "const popDirectionHints", "tokens": 5 },
    { "depth": 1, "title": "useTabHistory", "tokens": 36 }
  ]
}
```

## Concept

An index is the persistent intermediate the packer talks to. Without one, every pack would re-walk the filesystem. With one, packing is a scoring + rendering problem against a fixed structure.

There is currently no incremental mode — re-run `index_workspace.py` whenever the corpus changes. For most workspaces this takes seconds; for monorepos a minute or two.

## GitHub repos

If the corpus you want isn't local, point at GitHub directly:

```bash
python3 scripts/index_github_repo.py owner/repo --branch main
```

This uses the GitHub API (no clone), respects the same skip-list, and writes to a separately-named cache so workspace indexes don't collide.

## Re-pack against the explicit index

```bash
python3 scripts/pack_context.py "authentication middleware" --budget 8000 --no-auto-index
```

`--no-auto-index` tells the packer to error out if no index exists — useful in CI / scripts where you want indexing to be an explicit step.

## Concepts to keep

- **Index = corpus structure.** Heading trees + AST symbols, schema-versioned.
- **Index ≠ embeddings.** Embeddings are a separate cache (built on demand by `--semantic`).
- **Index is regenerable.** Treat it like a build artefact, not a database.

## Next

[Step 3 — Graph and multi-hop](03-graph-and-multi-hop.md). The organ layer: linked reasoning across imports, calls, and types.
