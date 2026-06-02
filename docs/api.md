# API reference

ContextVault exposes the same six tools over two transports. Use whichever fits your client.

## MCP (stdio)

JSON-RPC 2.0. Launch with `contextvault serve --mcp`. MCP-aware clients (Claude Code, Cursor, Claude Desktop, Continue.dev) connect via stdio.

Methods:

- `initialize` → returns protocolVersion `2024-11-05`, serverInfo, capabilities.
- `tools/list` → returns the six tool definitions with JSON schemas.
- `tools/call` with `{"name": "<tool>", "arguments": {...}}` → invokes a tool; result is wrapped as `{"content": [{"type": "text", "text": "<json>"}], "isError": false}`.
- `ping` → `{}`.

## HTTP (loopback)

Bound to `127.0.0.1:<port>` (default 7842). Every request needs `Authorization: Bearer <token>`. Token lives at `~/.config/contextvault/token`.

### Endpoints

| Method | Path | Args | Returns |
|---|---|---|---|
| `GET` | `/recall` | `query` (required), `cwd`, `scope` (`workspace` \| `global`), `k` (1-100) | array of hits `{path, workspace, score, preview}` |
| `GET` | `/recent_sessions` | `cwd`, `limit` (1-100) | array of session frontmatter |
| `GET` | `/list_workspaces` | — | array of `{workspace, session_count, updated_at}` |
| `GET` | `/graph_neighborhood` | `path`, `depth` (1-4) | `{root, nodes[], edges[][]}` |
| `GET` | `/lint` | `cwd`, `scope` | array of `{category, severity, path, message}` |
| `POST` | `/save_note` | JSON body | `{path, workspace, bytes}` |

### `POST /save_note` body schema

```json
{
  "title":     "My note title",     // required
  "body":      "Markdown body",     // required
  "type":      "note",              // optional, default "note"
  "tags":      ["tag1", "tag2"],    // optional
  "cwd":       "/abs/path",         // required when workspace=current
  "workspace": "current"            // "current" (default) | "global" | "<workspace-id>"
}
```

### HTTP status codes

| Code | Meaning |
|---|---|
| 200 | OK |
| 400 | Invalid arguments (missing required field, out-of-range, bad JSON body) |
| 401 | Missing or invalid bearer token |
| 404 | Endpoint not found, or note path not found (graph_neighborhood) |
| 500 | Vault I/O error |

## Tool reference (both transports)

### recall
Search the vault by BM25 score, workspace-scope-filtered.

- `query` — natural-language search string
- `cwd` — absolute working directory (required for `scope=workspace`)
- `scope` — `"workspace"` (default) or `"global"`
- `top_k` / `k` — max results (default 10, range 1-100)

### recent_sessions
Return the N most recent session notes by mtime, optionally workspace-scoped.

- `cwd` — optional. When provided, restrict to that workspace's sessions.
- `limit` — default 5, range 1-100.

### save_note
Write a Markdown note with frontmatter. Workspace is derived from `cwd` (with `workspace=current`), explicit (`workspace=<id>`), or shared (`workspace=global` → `notes/` at vault root).

### list_workspaces
Enumerate every `workspaces/<id>/` with its session count and last-modified timestamp.

### graph_neighborhood
BFS-expand the wikilink neighborhood of a note up to `depth` hops. Drops edges to nonexistent notes.

- `path` — vault-relative note path
- `depth` — default 1, range 1-4

### lint
Run the eight lint checks (orphans, dead links, missing frontmatter, empty sections, duplicate titles, broken markdown links, huge notes, unused tags). Optionally workspace-scoped.

## Client recipes

### curl

```bash
TOKEN=$(cat ~/.config/contextvault/token)
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:7842/recall?query=auth%20refactor&cwd=$PWD&scope=workspace"
```

### Python

```python
import os, urllib.request, urllib.parse, json

token = open(os.path.expanduser("~/.config/contextvault/token")).read().strip()
qs = urllib.parse.urlencode({"query": "auth", "scope": "global"})
req = urllib.request.Request(
    f"http://127.0.0.1:7842/recall?{qs}",
    headers={"Authorization": f"Bearer {token}"},
)
print(json.load(urllib.request.urlopen(req)))
```

### Hermes Chat / system prompt template

For local LLMs that don't speak MCP, teach the model to call the HTTP endpoint:

```text
You have access to a persistent memory at http://127.0.0.1:7842.
Before answering questions about past sessions or workspaces, issue:

  GET /recall?query=<terms>&scope=workspace&cwd=<absolute path>

with the header `Authorization: Bearer <TOKEN>`. The response is a JSON
array of hits with `path`, `workspace`, `score`, and `preview`. Use the
`preview` fields to ground your answer; cite paths when you do.
```
