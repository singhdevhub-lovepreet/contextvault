# Changelog

All notable changes to ContextVault. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [0.1.0] - 2026-06-02

First alpha release. End-to-end LLM-agnostic per-workspace memory.

### Added

- **Core vault primitives** (`vault.py`, `workspace.py`): atomic writes,
  fcntl per-file advisory locks with stale auto-clear, path-traversal
  hardening, cwd→workspace-id encoder matching Claude Code's
  `~/.claude/projects/` convention.
- **Retrieval** (`retrieve/`): Okapi BM25 with k1=1.5, b=0.75, plus
  first-class workspace-scope filtering as a document metadata field
  (not bolted on). Vault-walk + index + scope-filtered query orchestration
  via `retrieve.query.run_recall`.
- **Session capture** (`capture/`): JSONL transcript reader for Claude
  Code's `~/.claude/projects/*.jsonl` files, deterministic extractive
  summarizer (goal / decisions / files / commands / errors / TODOs /
  entities), secret-pattern redactor (AWS keys, bearer tokens,
  KEY=value pairs with qualified prefixes, JSON secret fields, JWTs),
  incremental checkpoint via `.vault-meta/captured.json`, per-session
  fcntl lock around writes.
- **Server** (`server/`): MCP (stdio JSON-RPC 2.0) + HTTP (loopback-only,
  127.0.0.1) dual-transport over the same six backend tools (`recall`,
  `recent_sessions`, `save_note`, `list_workspaces`,
  `graph_neighborhood`, `lint`). Bearer-token auth on HTTP using
  `secrets.compare_digest`.
- **Lint** (`lint/`): eight automated checks — orphan pages, dead
  wikilinks, missing frontmatter, empty sections, duplicate titles,
  broken markdown links, huge notes, unused tags.
- **Graph** (`graph/`): wikilink BFS neighborhood expansion +
  Obsidian-canvas auto-generator for per-workspace visual maps.
- **Adapters** (`adapters/`): Claude Code (hooks + MCP installer with
  idempotent merge into `~/.claude/settings.json`), Cursor (config
  snippet generator).
- **CLI** (`cli.py`): 10 subcommands — `init`, `serve`, `capture`,
  `recall`, `lint`, `hot`, `workspaces ls`, `adapter add/remove`,
  `ingest`, `save`.
- **Obsidian companion plugin** (`obsidian-plugin/`): TypeScript plugin
  with workspace-aware status bar, "Open current workspace hot cache",
  "Open Workspace Map canvas", and "List known workspaces" commands.
  Plus a settings tab for the HTTP server URL + bearer token.
- **Docs**: `quickstart.md`, `architecture.md`, `api.md`,
  `session-capture.md`, `privacy.md`, plus per-adapter setup guides
  for Claude Code, Cursor, and Hermes Chat.

### Test coverage

249+ pytest cases (unit + integration). Hermetic by default — no Anthropic
API, no ollama, no HTTP egress. ruff clean, mypy strict clean across the
source tree.

### Forked-from

ContextVault began as a fork of
[claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian)
(MIT). We kept the proven primitives — BM25 + lint checks + capture
philosophy + fcntl locks — and discarded the Claude-Code-only
coupling, the flat-vault assumption, the dozen-scripts surface, and
the implicit `CLAUDE.md`-as-config convention. See [`NOTICE`](NOTICE)
for attribution.

## [0.0.0] - 2026-06-02

Project bootstrap.
