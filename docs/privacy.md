# Privacy

ContextVault is designed so that the user's data never leaves their machine without explicit consent.

## What stays local by default

- The vault (`~/Documents/ContextVault/` or wherever you point it).
- All session transcripts, extracted notes, frontmatter, wikilinks.
- The BM25 retrieval index and any cosine embeddings.
- The HTTP server (bound only to `127.0.0.1`; non-loopback binds are refused at the socket layer).
- All logs (`.vault-meta/sweeper.log`, `.vault-meta/redacted.log`).

## What can optionally leave the machine

Only with explicit `--allow-egress` consent on the relevant subcommand:

- Anthropic API calls during contextual-prefix generation (Phase 1, optional rerank-quality tier).
- Anthropic API calls during LLM-quality session summarization (Phase 2, optional).

There is no telemetry, no usage tracking, no phone-home.

## Secret redaction

Before any extracted content is written to disk, a regex pass masks lines containing common secret patterns:

- AWS access keys (`AKIA[0-9A-Z]{16}`)
- Authorization headers (`Authorization:\s*Bearer\s+\S+`)
- `KEY=value` lines where KEY matches `SECRET|TOKEN|PASSWORD|KEY|API_?KEY`
- JWTs (three-part base64url-encoded tokens)

Matches are masked to `[REDACTED]` in the written note. The offset and pattern that matched are logged to `.vault-meta/redacted.log` (never the content itself).

This is **best-effort**, not a guarantee. Do not paste production secrets into chat sessions and assume redaction will save you.

## Network surface

- HTTP server: `127.0.0.1` only. Any attempt to bind to another interface exits with code 1 immediately.
- HTTP server: requires a bearer token (generated at `contextvault init`, stored at `~/.config/contextvault/token` with `0600` perms).
- MCP server: stdio only. Inherits the parent process trust.
- No outbound connections except when `--allow-egress` is passed to specific subcommands.

## Vault hygiene

Do not commit your vault to a shared git repo. The `.gitignore` shipped in this project explicitly excludes `vault/` and `*.token`. Consider an encrypted backup tool (Restic, Borg, Arq) instead.
