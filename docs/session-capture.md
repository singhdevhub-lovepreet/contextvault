# Session capture

The marquee feature. When a Claude Code session ends — Stop event, `/clear`, Ctrl+C, terminal close — ContextVault reads the transcript and writes a structured session note into the vault.

## What gets captured

| Field | Source |
|---|---|
| `goal` | First user message of the session |
| `summary` | Extractive: the goal + an indicative assistant text |
| `decisions` | Regex over "we'll / let's / going with / chose" in any message |
| `files_touched` | `file_path` arg of every `Edit` / `Write` / `Read` / `NotebookEdit` tool call (dedup, source order) |
| `commands` | `Bash` tool calls, filtered to mutating ones (drops `ls`, `cat`, `pwd`, `git status`, `git diff`, etc.) |
| `errors` | Assistant text matching error patterns, paired with the next non-error assistant message as resolution |
| `open_todos` | `TODO:` / `FIXME:` / `XXX:` / `HACK:` regex in user messages |
| `entities` | Capitalized nouns (≥ 4 chars), minus a 35-word conversational-verb stopword list. Auto-wikilinked. |

All extraction is *deterministic and offline*. No LLM call, no network. Reproducing the same transcript twice produces byte-identical output.

## What does NOT get captured

- Assistant thinking blocks (visible to ContextVault, intentionally discarded — they're noisy and often contain partial reasoning we don't want surfaced as decisions).
- Tool output beyond error pairing (preview-level only; the full tool results stay in Claude Code's transcript).
- Metadata entries (file-history-snapshot, permission-mode, ai-title, hook attachments, etc.).

## Trigger surfaces

Three triggers, one capture function. The triple is intentional — no single hook covers every exit mode reliably:

| Exit mode | Trigger | Calls |
|---|---|---|
| Normal turn end | `Stop` hook | `contextvault capture --mode incremental` |
| `/clear` typed | `UserPromptSubmit` hook with matcher `^/clear` | `contextvault capture --mode final` |
| Ctrl+C, kill, crash, terminal close | (planned) sweeper daemon every 120s | `contextvault capture --mode sweep` per stale JSONL |

The sweeper isn't shipped in v0.1 — it lives in the launchd plist that Phase 3.5 will write. For now, the Stop + UserPromptSubmit hooks cover the common cases. If you Ctrl+C mid-prompt and `contextvault capture` never fired, just run it manually with `--cwd $PWD` to backfill.

## Checkpoint and idempotency

Every capture stores `{sessionId → last_captured_uuid}` in `.vault-meta/captured.json`. On the next capture for the same session, only entries *after* that uuid are processed. Running `contextvault capture` ten times on the same transcript writes the note once and then reports `new_entries=0`.

If the session note is deleted but the checkpoint isn't, the next capture won't re-create it — delete the corresponding entry from `captured.json` to force a full re-capture.

## Secret redaction

Before any extracted text is written, every line passes through five regex patterns:

1. AWS access keys (`AKIA…`, `ASIA…`)
2. `Authorization: Bearer …`
3. `*PASSWORD=`, `*SECRET=`, `*TOKEN=`, `*API_KEY=`, `*PRIVATE_KEY=` etc.
4. JSON secret fields (`"api_key": "value"`)
5. JWTs (three-part base64url)

On match, the entire line is replaced with `[REDACTED]`. An entry is appended to `.vault-meta/redacted.log` recording the *line offset and pattern name* — never the original content.

This is best-effort. It will miss custom secret naming and inline-in-prose secrets. **Don't paste production credentials into a chat session and rely on this**. Treat the vault as carefully as you treat the transcripts themselves.

## File layout written

```
~/Documents/ContextVault/
└── workspaces/-Users-you-some-project/
    ├── sessions/
    │   └── 2026-06-02-abcd1234.md      ← per-session note (frontmatter + 8 sections)
    ├── hot.md                          ← workspace recent-context cache (~500 words)
    ├── log.md                          ← append-only one-line-per-capture event log
    └── Workspace Map.canvas            ← auto-regenerated Obsidian canvas
```

The session note's filename is `<YYYY-MM-DD>-<short-id>.md` (first 8 chars of sessionId).

## Manual capture

If you want to re-capture a session manually:

```bash
contextvault capture --cwd /Users/you/some/project
# →  workspace=-Users-you-some-project session=abcd1234 new_entries=12
#    redactions=0 note=workspaces/-Users-you-some-project/sessions/2026-06-02-abcd1234.md
```

Useful when:
- A hook silently failed and you want to backfill.
- You're testing the capture pipeline against a fresh transcript.
- You want to force a re-capture after editing the redaction patterns.

## Phase 1 / Phase 2 distinction

The capture pipeline is *write-only*. It doesn't trigger retrieval indexing — that happens at query time via `contextvault recall`. In v0.2 we'll persist the BM25 index under `.vault-meta/bm25/` and update it incrementally as capture writes, so recall doesn't re-walk the vault every call.
