# ContextVault

LLM-agnostic per-workspace memory backed by Obsidian.

ContextVault auto-captures your Claude Code sessions, files them into a knowledge graph in an Obsidian vault, and exposes that vault to any LLM client (Claude Code, Cursor, Claude Desktop, Continue.dev, Hermes Chat, raw HTTP) so the next session — in any tool — starts with the context already loaded.

## What it does

1. **Captures every Claude Code session** when it ends (`Stop` event, `/clear`, Ctrl+C, terminal close) into a structured Markdown note with: goal, summary, decisions, files touched, mutating commands, errors and resolutions, open TODOs, mentioned entities (auto-wikilinked).
2. **Scopes memory per workspace** — the workspace is your current working directory. Cross-workspace queries are opt-in.
3. **Builds a knowledge graph** via Obsidian wikilinks. Shared entities (people, libraries, APIs) and concepts (frameworks, ideas) live at the vault root and are visible to every workspace.
4. **Self-cleans** orphans, dead links, stale claims, frontmatter gaps via a built-in lint pass.
5. **Exposes the vault to any LLM** via a single local server that speaks both MCP (stdio, for Claude Code / Cursor / Claude Desktop / Continue.dev) and HTTP (loopback, for Hermes / curl / n8n / custom Python).
6. **Privacy-first**: extraction is deterministic and offline by default. Secret patterns (AWS keys, bearer tokens, JWT, `KEY=value`) are regex-redacted before they hit disk. LLM-quality summarization is opt-in behind `--allow-egress`.

## Status

`v0.1.0-alpha` — under active development. See `CHANGELOG.md`.

## Install

```bash
pipx install contextvault
contextvault init                       # scaffold vault + write config + generate token
contextvault adapter add claude-code    # install hooks into ~/.claude/settings.json
contextvault adapter add cursor         # print Cursor MCP snippet to paste
contextvault serve --both &             # MCP (stdio) + HTTP (127.0.0.1:7842)
```

See `docs/quickstart.md` for the full setup walkthrough.

> **Picking this up after a gap?** Start at [`docs/STATE.md`](docs/STATE.md) — current phase status, known bugs, v0.2 backlog, and how to rebuild context fast.

## Architecture

```
~/Documents/ContextVault/
├── hot.md                          ← global recent-context cache
├── entities/  concepts/            ← shared across workspaces
├── workspaces/
│   └── -Users-you-Desktop-foo/     ← workspace = encoded cwd
│       ├── hot.md  index.md  log.md
│       ├── sessions/               ← one Markdown note per captured session
│       ├── decisions/  todos/  errors/
│       └── Workspace Map.canvas    ← auto-regenerated Obsidian canvas
└── .vault-meta/                    ← state (BM25 index, embeddings, checkpoints)
```

See `docs/architecture.md` for the design rationale.

## CLI

```
contextvault init [--vault PATH]
contextvault serve [--mcp] [--http] [--both]
contextvault capture --cwd PATH [--mode incremental|final|sweep]
contextvault recall QUERY [--cwd PATH] [--scope workspace|global] [-k N]
contextvault lint [--cwd PATH] [--scope workspace|global]
contextvault hot [--workspace WS]
contextvault workspaces ls
contextvault adapter {add,remove} {claude-code,cursor,claude-desktop,continue-dev,hermes}
contextvault ingest FILE_OR_URL [--workspace WS]
contextvault save --title T --type TYPE          # stdin → note
```

## Attribution

ContextVault began as a fork of [`claude-obsidian`](https://github.com/AgriciDaniel/claude-obsidian) (MIT). The ingestion, retrieval (BM25 + cosine rerank + contextual prefix), and lint primitives are adapted from that project. See `NOTICE` for details.

## License

MIT. See `LICENSE`.
