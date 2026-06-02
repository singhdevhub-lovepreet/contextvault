# Architecture

ContextVault is a four-layer pipeline:

```
┌──────────────────────────────────────────────────────┐
│  LLM clients (Claude Code / Cursor / Hermes / curl)  │
└─────────────────────────┬────────────────────────────┘
                          │  MCP (stdio) or HTTP (loopback)
                          ▼
┌──────────────────────────────────────────────────────┐
│  Server (server/{mcp,http,tools,auth}.py)            │
│    six tools: recall, recent_sessions, save_note,    │
│    list_workspaces, graph_neighborhood, lint         │
└─────────────────────────┬────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│  Domain modules                                       │
│    capture/  → transcript → summary + redaction      │
│    retrieve/ → BM25 + workspace scope filter         │
│    lint/     → orphans + dead links + 6 more checks  │
│    graph/    → wikilink BFS + canvas generator       │
└─────────────────────────┬────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│  Vault primitives (vault.py, workspace.py)           │
│    atomic write, fcntl advisory locks, cwd encoder   │
└─────────────────────────┬────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│  Filesystem (~/Documents/ContextVault by default)    │
│    workspaces/<encoded-cwd>/sessions/…md             │
│    .vault-meta/{captured.json, redacted.log, bm25/…} │
└──────────────────────────────────────────────────────┘
```

## Why the layers split where they do

- **Vault primitives** know nothing about session notes, BM25, or wikilinks. They expose `read / write / lock / list_files` with path-safety and crash-atomicity. Everything above can crash and the on-disk vault is still consistent.
- **Domain modules** are pure-Python data transforms. They never reach the network, never call the model. Capture is *deterministic and extractive* — that's why your secrets never leave the machine without explicit `--allow-egress`.
- **Server** is the thin translation layer that adapts the domain modules to MCP or HTTP. It owns serialization, bearer-token auth, and the loopback bind. It never duplicates domain logic.
- **Clients** are anything that speaks one of the transports.

## Workspace encoding

The workspace id for a cwd is its absolute path with slashes replaced by hyphens, prefixed with a leading hyphen:

```
/Users/you/Desktop/foo  →  -Users-you-Desktop-foo
```

This matches the convention Claude Code itself uses for `~/.claude/projects/<encoded-cwd>/`, so the vault subtree at `workspaces/<id>/` maps 1-to-1 with Claude Code's per-project state.

Encoding is one-way and lossy (a path with literal hyphens can't be uniquely reversed). That's by design: encoding happens at write time, never decoded back. Path-traversal is rejected at the source (`workspace.encode` normalises `..` and refuses null bytes / relative paths).

## Capture flow

1. **Stop hook fires** (Claude Code emits an event on every model-turn boundary, plus `/clear` via UserPromptSubmit).
2. **CLI dispatches** `contextvault capture --cwd "$PWD"`.
3. **Runner resolves** the workspace, locates the transcript at `~/.claude/projects/<encoded>/`, reads the checkpoint `.vault-meta/captured.json`.
4. **Reader streams** only entries newer than the last-captured uuid (incremental).
5. **Summarizer extracts** goal / decisions / files / commands / errors / TODOs / entities. Pure Python, no LLM.
6. **Redactor masks** secret patterns (AWS keys, bearer tokens, KEY=value pairs, JWTs) before any text touches disk. Audit log records *offsets only*, never content.
7. **Vault writes** the session note + workspace hot.md + workspace log.md, all under an fcntl lock so concurrent writers can't corrupt.
8. **Canvas regenerates** asynchronously (best-effort — failure doesn't abort capture).
9. **Checkpoint advances** to the last seen uuid.

If Claude Code is killed mid-step, the next invocation picks up where the checkpoint left off. The atomic write + lock means a kill mid-write either leaves the old file or the new file — never half-written.

## Retrieval flow

1. `recall(query, cwd, scope)` walks every `.md` in the vault that matches scope.
2. Each note's body becomes a BM25 document; `extract_workspace_from_path` derives metadata.
3. The query tokenizer (`tokenize`) drops stopwords, normalizes hyphens/apostrophes/Unicode.
4. Okapi BM25 scoring with k1=1.5, b=0.75 (industry defaults).
5. Scope filter prunes during posting-list traversal — workspace queries see workspace pages + shared (vault-root) pages.
6. Top-k results returned as `{path, workspace, score, preview}`.

Phase 1 rebuilds the index on every call (fine for vaults under ~10k notes). v0.2 will persist `.vault-meta/bm25/index.json` and update incrementally as the capture pipeline writes.

## Privacy model

The privacy guarantees are layered:

1. **Default offline.** Capture's summarizer is regex-based, deterministic, never calls an LLM or HTTP. The retrieval pipeline is local BM25 (no embeddings = no Anthropic API call). The vault stays on your disk.
2. **Bound to loopback.** The HTTP server refuses any non-`127.0.0.1` bind at startup. There's no `--bind` flag, no environment override, no way to expose it remotely without modifying the source.
3. **Bearer auth.** Even on loopback, every HTTP request needs a token. The token is generated at `init` with 0600 perms and validated via `secrets.compare_digest`.
4. **Redaction at the edge.** Secrets are masked *before* they're written to disk. The audit log captures pattern + line offset, never the redacted content itself.
5. **Egress is explicit.** Any feature that would call an external API (LLM-quality summarization, contextual-prefix embeddings) requires `--allow-egress` on the specific subcommand. Defaults are no-egress.

## What we keep, what we discard

ContextVault began as a fork of [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) (MIT). We kept the proven primitives:

- BM25 + contextual prefix + cosine rerank (retrieval math)
- The lint check inventory (orphans, dead links, etc.)
- The capture/redact philosophy (extractive, offline-default, secret regex)
- fcntl advisory locks for multi-writer safety

We discarded:

- The Claude-Code-as-skill abstraction (we have a CLI and a server)
- The flat-vault assumption (workspace scoping is first-class)
- The "wiki" naming (it's just a vault)
- The dozen separate scripts (one `contextvault` binary, one Python package)
- The Anthropic-API-required tiers (everything is optional, gated, default-off)
