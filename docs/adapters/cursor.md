# Cursor adapter

## Install

```bash
contextvault adapter add cursor
```

This prints the snippet to paste into Cursor's MCP config. Cursor's MCP config is interactive only — we don't write into `~/.cursor/mcp.json` automatically.

Open or create `~/.cursor/mcp.json` and merge:

```json
{
  "mcpServers": {
    "contextvault": {
      "command": "contextvault",
      "args": ["serve", "--mcp"]
    }
  }
}
```

Restart Cursor. The "contextvault" MCP server appears under **Settings → MCP Tools**. Toggle it on.

## Use it

In a Cursor chat, ask:

> What did I work on in `/Users/you/some/project` yesterday?

Cursor calls `recall` via MCP, surfacing the session notes ContextVault has captured.

## Tools available

- `recall(query, cwd?, scope, top_k)`
- `recent_sessions(cwd?, limit)`
- `save_note(title, body, type, tags, cwd?, workspace)`
- `list_workspaces()`
- `graph_neighborhood(path, depth)`
- `lint(cwd?, scope)`

See [`api.md`](../api.md) for full schemas.
