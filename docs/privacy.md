# Privacy

ContextVault is designed so that your data never leaves your machine without explicit, per-command consent.

## What stays local by default

- The vault (`~/Documents/ContextVault/` or wherever you point it).
- All session transcripts, extracted notes, frontmatter, wikilinks.
- The BM25 retrieval index.
- The HTTP server (bound only to `127.0.0.1`; non-loopback binds are refused at the socket layer).
- All logs (`.vault-meta/sweeper.log`, `.vault-meta/redacted.log`).

## What can optionally leave the machine

Only with explicit `--allow-egress` consent on the relevant subcommand:

- Anthropic API calls during contextual-prefix generation (planned, v0.2 optional retrieval-quality tier).
- Anthropic API calls during LLM-quality session summarization (planned, v0.2 optional).

There is no telemetry, no usage tracking, no phone-home.

## Network surface

- HTTP server: `127.0.0.1` only. Any attempt to bind to another interface exits with code 1 immediately. There is no `--bind` flag and no environment override.
- HTTP server: requires a bearer token (generated at `contextvault init`, stored at `~/.config/contextvault/token` with `0600` perms). Token validation uses `secrets.compare_digest` (constant-time, no timing-leak).
- MCP server: stdio only. Inherits the parent process trust.
- No outbound connections by the default install. Egress requires opt-in.

## Secret redaction

Before any extracted content is written to disk, a regex pass masks lines containing common secret patterns:

- AWS access keys (`AKIA[0-9A-Z]{16}`, `ASIA…`)
- Authorization headers (`Authorization:\s*Bearer\s+\S+`)
- `KEY=value` lines where KEY matches `*SECRET`, `*PASSWORD`, `*TOKEN`, `*API_KEY`, `*PRIVATE_KEY` (covers `DATABASE_PASSWORD`, `GH_TOKEN`, etc.)
- JSON-shaped secrets: `"api_key": "value"`, `"token": "value"`, etc.
- JWTs (three-part base64url tokens)

Matches are masked to `[REDACTED]` in the written note. The audit log at `.vault-meta/redacted.log` records the offset and pattern name — never the content itself.

This is **best-effort**, not a guarantee. Do not paste production secrets into chat sessions and assume redaction will save you.

## Vault hygiene

The vault contains plain Markdown — readable by any text editor or backup tool. Two implications:

1. **Don't commit your vault to a shared git repo.** The `.gitignore` shipped in this project explicitly excludes `vault/` and `*.token`. Consider an encrypted backup tool (Restic, Borg, Arq) instead.
2. **Encrypt your disk.** ContextVault doesn't add a second layer of encryption — it relies on whatever your filesystem provides (FileVault on macOS, LUKS on Linux). If your disk isn't encrypted, your session history isn't either.

## Threat model

ContextVault is built for one user, one machine, on a developer workstation. We *do not* defend against:

- An attacker with local file access (they can read the vault directly).
- A compromised LLM client that's already authorized to call MCP / HTTP (it can recall anything you've captured).
- An attacker on the same machine trying to localhost-bind-jack the HTTP port (use a different port if you share the machine).

We *do* defend against:

- Accidental exposure to the local network (loopback bind).
- Token theft via timing attacks (`compare_digest`).
- Secret leakage into commit/sync-able files (regex redaction + audit log).
- Path-traversal attacks via crafted cwd / workspace-id arguments (every path goes through `Vault._safe_join`).
- Multi-writer corruption (fcntl advisory locks around mutations).

## Reporting concerns

Found a privacy-relevant bug? Email `security@contextvault.invalid` (placeholder) or open a private GitHub security advisory. Please don't file public issues for security concerns.
