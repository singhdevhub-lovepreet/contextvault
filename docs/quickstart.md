# Quickstart

Get from zero to "my next Claude Code session is captured into Obsidian" in five minutes.

## 1. Install

```bash
git clone https://github.com/contextvault/contextvault.git
cd contextvault
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

(Once the package is on PyPI, this becomes `pipx install contextvault`.)

## 2. Initialise the vault

```bash
contextvault init
# →  vault:  /Users/you/Documents/ContextVault
#    config: /Users/you/.config/contextvault/config.toml
#    token:  /Users/you/.config/contextvault/token (chmod 600)
```

The vault is your Obsidian vault. Open it in Obsidian (File → Open Vault → choose `~/Documents/ContextVault`).

You can pick a different location: `contextvault init --vault ~/path/to/some/vault`.

## 3. Install the Claude Code adapter

```bash
contextvault adapter add claude-code
```

This merges three hooks into `~/.claude/settings.json`:

- **SessionStart** — loads the workspace hot cache into context.
- **UserPromptSubmit** (matcher `^/clear`) — captures before `/clear` wipes the session.
- **Stop** — captures incrementally on every turn end.

And registers an MCP server so Claude Code can call `recall` / `recent_sessions` / `save_note` directly.

Restart Claude Code for it to pick up the new settings.

## 4. (Optional) Add other clients

**Cursor:** `contextvault adapter add cursor` prints a snippet to paste into `~/.cursor/mcp.json`.

**Hermes / curl / any HTTP client:** start the loopback HTTP server and use the bearer token:

```bash
contextvault serve --http &
TOKEN=$(cat ~/.config/contextvault/token)
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:7842/recall?query=auth&scope=global"
```

## 5. Use it

Start a Claude Code session anywhere:

```bash
cd ~/some/project
claude
# (chat normally, edit files, run tests…)
```

When the session ends (you press Ctrl+C, type `/clear`, or just close the terminal), ContextVault writes a session note to:

```
~/Documents/ContextVault/workspaces/-Users-you-some-project/sessions/2026-06-02-<short-id>.md
```

With frontmatter, goal, summary, decisions, files touched, commands run, errors + resolutions, open TODOs, and entities (auto-wikilinked).

In your next session — in *any* LLM client — ask it to recall:

```text
What did I work on in ~/some/project yesterday?
```

The MCP `recall` tool surfaces the relevant session notes; the LLM grounds its answer in them.

## 6. Browse the vault in Obsidian

Open the vault in Obsidian. You'll see:

```
~/Documents/ContextVault/
├── hot.md              ← global recent-context cache
├── index.md
├── entities/           ← shared across workspaces
├── concepts/
└── workspaces/
    └── -Users-you-some-project/
        ├── hot.md
        ├── log.md
        ├── Workspace Map.canvas    ← visual hub, auto-regenerated
        └── sessions/
            └── 2026-06-02-...md
```

Open the canvas for a visual view. Or use Obsidian's graph view — wikilinks between sessions, entities, and concepts render as a knowledge graph.

## 7. Periodic hygiene

```bash
contextvault lint --scope workspace
# →  orphan pages, dead links, missing frontmatter, empty sections,
#    duplicate titles, broken markdown links, huge notes, unused tags
```

## Troubleshooting

- **Hooks not firing?** Verify with `cat ~/.claude/settings.json | jq .hooks`. The Stop hook command should mention `contextvault capture`.
- **`contextvault` not on PATH?** If installed in a venv, prefix with `.venv/bin/`. Or symlink: `ln -s $(pwd)/.venv/bin/contextvault /usr/local/bin/contextvault`.
- **No session note appearing?** Check `~/.claude/projects/<encoded-cwd>/` for a `.jsonl` file. ContextVault reads from there. If the file exists but no capture happens, run `contextvault capture --cwd "$PWD"` manually to see the error.
- **HTTP 401?** Re-read the token from `~/.config/contextvault/token`. The file is 0600 so you may need to `cat` it as the right user.
