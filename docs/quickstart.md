# Quickstart

Get from zero to "my next session is captured into Obsidian" in five minutes.

## 1. Install

**Requires Python 3.11+** and [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended) or pip.

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone git@github.com:singhdevhub-lovepreet/contextvault.git
cd contextvault
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
```

`uv` downloads Python 3.12 automatically if it's not on your system.

<details>
<summary>Alternative: using pip directly</summary>

```bash
git clone git@github.com:singhdevhub-lovepreet/contextvault.git
cd contextvault
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
```

> If `python3.12` isn't found, check `python3 --version`. Any 3.11+ works.

</details>

Put `contextvault` on your PATH:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/.venv/bin/contextvault" ~/.local/bin/contextvault
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## 2. Initialise the vault

```bash
contextvault init
# ->  vault:  ~/Documents/ContextVault
#     config: ~/.config/contextvault/config.toml
#     token:  ~/.config/contextvault/token (chmod 600)
```

The vault is your Obsidian vault. You can pick a different location: `contextvault init --vault ~/path/to/some/vault`.

Verify your config points to the right place:

```bash
cat ~/.config/contextvault/config.toml
# [vault]
# path = "/Users/you/Documents/ContextVault"
```

## 3. Install an LLM client adapter

### Claude Code

```bash
contextvault adapter add claude-code
```

This merges hooks into `~/.claude/settings.json`:

- **Stop** -- captures incrementally on every turn end.
- **SessionStart** -- loads the workspace hot cache into context.

And registers an MCP server so Claude Code can call `recall` / `recent_sessions` / `save_note` directly.

Restart Claude Code for it to pick up the new settings.

### Hermes

Hermes has native MCP support -- no HTTP server needed:

```bash
hermes mcp add contextvault --command contextvault --args serve
# When prompted "Enable all 6 tools?", type Y
```

Verify:

```bash
hermes mcp list               # should show 'contextvault'
hermes mcp test contextvault   # should show "Connected"
```

Start a chat and test:

```bash
hermes chat
# Ask: "List my workspaces" or "What did I work on recently?"
```

For auto-capture hooks (capture every turn), see the [README](../README.md#hermes) or the [Hermes adapter guide](adapters/hermes.md).

### Cursor

```bash
contextvault adapter add cursor
# prints a snippet to paste into ~/.cursor/mcp.json
```

See [`docs/adapters/cursor.md`](adapters/cursor.md) for details.

## 4. Capture your first session

If you already have Claude Code sessions in `~/.claude/projects/`, capture them now:

```bash
contextvault capture --cwd "$(pwd)"
```

Or just start a Claude Code session with the hooks installed (step 3) -- it captures automatically when each turn ends.

## 5. Use it

In your next session -- in *any* LLM client -- ask it to recall:

```text
What did I work on in ~/some/project yesterday?
```

The MCP `recall` tool surfaces the relevant session notes; the LLM grounds its answer in them.

### CLI commands

```bash
contextvault recall "auth bug" --cwd "$(pwd)"     # search from terminal
contextvault workspaces ls                          # list all workspaces
contextvault hot                                    # show hot cache
contextvault lint --cwd "$(pwd)"                    # vault health check
contextvault export --workspace=-Users-you-project  # zip a workspace
```

## 6. Browse the vault in Obsidian

Open the vault in Obsidian (File > Open Vault > choose `~/Documents/ContextVault`):

```
~/Documents/ContextVault/
├── hot.md              <- global recent-context cache
├── index.md
├── entities/           <- shared across workspaces
├── concepts/
└── workspaces/
    └── -Users-you-some-project/
        ├── hot.md
        ├── log.md
        ├── Workspace Map.canvas    <- visual hub, auto-regenerated
        └── sessions/
            └── 2026-06-02-...md
```

Open the canvas for a visual view. Or use Obsidian's graph view (Cmd+G) -- wikilinks between sessions, entities, and concepts render as a knowledge graph.

## 7. Periodic hygiene

```bash
contextvault lint --scope workspace
# ->  orphan pages, dead links, missing frontmatter, empty sections,
#     duplicate titles, broken markdown links, huge notes, unused tags,
#     stale claims, semantic drift (if ollama installed)
```

## Troubleshooting

- **Hooks not firing?** Verify with `cat ~/.claude/settings.json | jq .hooks`. The Stop hook command should mention `contextvault capture`.
- **`contextvault` not on PATH?** If installed in a venv, prefix with `.venv/bin/`. Or symlink: `ln -s $(pwd)/.venv/bin/contextvault ~/.local/bin/contextvault`.
- **No session note appearing?** Check `~/.claude/projects/<encoded-cwd>/` for a `.jsonl` file. ContextVault reads from there. If the file exists but no capture happens, run `contextvault capture --cwd "$PWD"` manually to see the error.
- **HTTP 401?** Re-read the token from `~/.config/contextvault/token`. The file is 0600 so you may need to `cat` it as the right user.
- **Vault empty in Obsidian?** Make sure the vault path in `~/.config/contextvault/config.toml` matches the folder you opened in Obsidian. Both must point to the same directory.
- **Hermes MCP not connecting?** Run `hermes mcp test contextvault`. If it fails, check that `which contextvault` returns a path. Re-add with `hermes mcp remove contextvault && hermes mcp add contextvault --command contextvault --args serve`.
