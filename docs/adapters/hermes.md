# Hermes Agent

Hermes has native MCP support, so it connects to ContextVault directly via the stdio MCP transport — no HTTP server needed.

## Setup (MCP — recommended)

### 1. Make sure `contextvault` is on your PATH

```bash
# If installed in a venv:
mkdir -p ~/.local/bin
ln -sf /path/to/contextvault/.venv/bin/contextvault ~/.local/bin/contextvault
export PATH="$HOME/.local/bin:$PATH"  # add to ~/.zshrc permanently
```

### 2. Register the MCP server

```bash
hermes mcp add contextvault --command contextvault --args serve
```

When prompted `Enable all 6 tools? [Y/n/select]:`, type **Y**.

This registers these tools:

| Tool | Description |
|------|-------------|
| `recall` | Search workspace memory, return top-K hits |
| `recent_sessions` | Return N most recent session notes |
| `save_note` | Write a Markdown note into the vault |
| `list_workspaces` | Enumerate workspaces with session counts |
| `graph_neighborhood` | Expand wikilink neighbors of a note |
| `lint` | Find orphan pages, dead links, stale claims |

### 3. Verify

```bash
hermes mcp list          # should show 'contextvault' with 6 tools
hermes mcp test contextvault   # should show ✓ Connected
```

### 4. Use it

```bash
hermes chat
```

Then ask things like:

- "What did I work on recently?"
- "List my workspaces"
- "Recall what I did with authentication"
- "Lint my vault"
- "Save a note titled 'TODO' with body 'fix the rate limiter'"

Hermes will call the MCP tools automatically and ground its answers in your session history.

## Removing

```bash
hermes mcp remove contextvault
```

## Alternative: HTTP endpoint

If for some reason MCP doesn't work, Hermes can also hit the HTTP loopback endpoint.

### Start the server

```bash
contextvault serve --http &
# →  contextvault: HTTP serving on http://127.0.0.1:7842
```

Or auto-start at login via launchd:

```bash
contextvault adapter add hermes
# creates ~/Library/LaunchAgents/contextvault.plist + prints system prompt
launchctl load ~/Library/LaunchAgents/contextvault.plist
```

### System-prompt template

Paste this into Hermes settings (`hermes config edit`, set the `system_prompt` field), or pass via `-z`:

```text
You have access to a persistent memory at http://127.0.0.1:7842.

Tools available (HTTP GET unless noted):

  /recall?query=<terms>&cwd=<absolute path>&scope=workspace
      → JSON array of {path, workspace, score, preview}
  /recent_sessions?cwd=<absolute path>&limit=5
      → JSON array of recent session frontmatters
  /list_workspaces
      → JSON array of {workspace, session_count, updated_at}
  /lint?scope=workspace&cwd=<absolute path>
      → JSON array of lint findings
  POST /save_note  body: {"title": "...", "body": "...", "workspace": "current", "cwd": "..."}
      → {"path": "..."}

Auth: every request needs `Authorization: Bearer <TOKEN>` where TOKEN is
in ~/.config/contextvault/token.

Before answering questions about past sessions, recent decisions, or
files-touched-yesterday, call /recall and ground your answer in the
preview text. Cite paths when you do.
```

### curl recipes

```bash
TOKEN=$(cat ~/.config/contextvault/token)

# Recall scoped to current workspace
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:7842/recall?query=auth&cwd=$PWD&scope=workspace"

# Global cross-workspace
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:7842/recall?query=stripe&scope=global"

# Save a quick note
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -X POST -d '{"title":"todo","body":"investigate the rate limiter","workspace":"current","cwd":"'$PWD'"}' \
  http://127.0.0.1:7842/save_note

# List workspaces
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7842/list_workspaces
```
