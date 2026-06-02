# ContextVault — Obsidian companion plugin

This plugin pairs with the [ContextVault](../) CLI. It surfaces workspace context inside Obsidian:

- A status-bar item showing the detected workspace + uncaptured session count.
- Command palette: **Open current workspace hot cache**.
- Command palette: **Open Workspace Map canvas**.
- Command palette: **List known workspaces** — opens a modal with all workspaces, their session counts, and last-updated times.

The plugin reads vault files via Obsidian's own API. It talks to the local HTTP server (`contextvault serve --http`, default `http://127.0.0.1:7842`) only to fetch derived stats. Set the bearer token (from `~/.config/contextvault/token`) in the plugin's settings tab.

## Install

This plugin is not (yet) in the Obsidian community plugin browser. To sideload:

```bash
cd /path/to/your/vault/.obsidian/plugins
mkdir -p contextvault
cd contextvault
# Copy these files from this directory:
#   main.js  (built; see "Build" below)
#   manifest.json
#   styles.css
```

Then enable it from Obsidian's **Community plugins** settings (you may need to disable Restricted Mode first).

## Build

```bash
cd obsidian-plugin
npm install
npm run build      # produces main.js
```

For active development, `npm run dev` watches and rebuilds on save.

## Config

Open Obsidian → Settings → Community plugins → ContextVault:

| Field | Value |
|---|---|
| Server URL | `http://127.0.0.1:7842` (default) |
| Bearer token | contents of `~/.config/contextvault/token` |
| Status bar refresh | seconds (default 30) |

## Status

Alpha. Tested against Obsidian 1.6 on macOS. The plugin is intentionally read-only relative to the vault — writes to the vault flow through the Python CLI / HTTP server only.
