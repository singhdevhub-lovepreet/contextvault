# Hermes Chat (and other non-MCP clients)

Anything that can issue an HTTP request can use ContextVault. Hermes Chat (and most local-LLM front-ends) don't speak MCP — they hit the HTTP loopback endpoint instead.

## Start the server

```bash
contextvault serve --http &
# →  contextvault: HTTP serving on http://127.0.0.1:7842
```

Or run both transports (HTTP + MCP):

```bash
contextvault serve --both
```

For long-running setup, drop a launchd plist (macOS) or systemd user unit (Linux) so the server starts at login. A minimal example for macOS:

```xml
<!-- ~/Library/LaunchAgents/contextvault.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
    <key>Label</key><string>contextvault</string>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/local/bin/contextvault</string>
      <string>serve</string>
      <string>--http</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
```

Then `launchctl load ~/Library/LaunchAgents/contextvault.plist`.

## System-prompt template

For Hermes / Ollama-served models / any local LLM, teach the model to call the endpoint:

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

## curl recipes

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

## n8n / workflow tools

The HTTP endpoints are stateless and return clean JSON — drop them into any HTTP node. Just remember to template the bearer token from your secrets store rather than hardcoding it.
