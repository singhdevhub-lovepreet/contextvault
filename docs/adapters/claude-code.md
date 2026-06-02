# Claude Code adapter

## Install

```bash
contextvault adapter add claude-code
```

Merges three hooks + one MCP server entry into `~/.claude/settings.json`. Preserves any user-added hooks. Idempotent — re-running detects existing ContextVault entries and replaces them in place rather than duplicating.

## What gets installed

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume", "hooks": [
        { "type": "command",
          "command": "contextvault hot --workspace \"$(...)\" 2>/dev/null || true"
        }
      ]}
    ],
    "UserPromptSubmit": [
      { "matcher": "^/clear", "hooks": [
        { "type": "command",
          "command": "contextvault capture --cwd \"$PWD\" --mode final 2>/dev/null || true"
        }
      ]}
    ],
    "Stop": [
      { "matcher": "", "hooks": [
        { "type": "command",
          "command": "contextvault capture --cwd \"$PWD\" --mode incremental 2>/dev/null &"
        }
      ]}
    ]
  },
  "mcpServers": {
    "contextvault": { "command": "contextvault", "args": ["serve", "--mcp"] }
  }
}
```

The `&` on the Stop hook forks capture into the background — it should never block your prompt's return.

## Remove

```bash
contextvault adapter remove claude-code
```

Strips only the entries with `contextvault` in their command. Other user hooks pass through untouched.

## Troubleshooting

- **Hook didn't fire**: confirm Claude Code restarted after install. Check `~/.claude/settings.json` shows the merged config.
- **`contextvault: command not found` in hook output**: the hook runs in your login shell's PATH. If you installed via venv, either symlink `contextvault` into `/usr/local/bin/` or edit the hook command to use the full path.
- **Captures not appearing**: run `contextvault capture --cwd "$PWD"` manually to surface any error the hook is silently swallowing.
