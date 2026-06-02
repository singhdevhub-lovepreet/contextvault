# ContextVault — project state & handoff

_Last updated: 2026-06-02 (end of Phase 6 ship)_

This is the "pick this up in a new session" doc. Read it first if you're returning to the project after a gap, handing it off, or briefing another AI on what's done and what's next.

---

## TL;DR

- **Version**: `0.1.0-alpha`, 7 phases shipped, **285 tests** green, ruff + mypy strict clean across **26 source files**.
- **Working end-to-end**: init → manual capture → recall (CLI, HTTP, MCP) → vault visible in Obsidian.
- **Auto-capture hooks work** with the v2 hooks.json (uses `$CLAUDE_PROJECT_DIR`, no `UserPromptSubmit` matcher). The first version (`$PWD` + `^/clear` matcher) was buggy and is fixed.
- **No remote pushed.** Local-only repo at `/Users/lsingh/Desktop/experiments/contextvault/`. Seven commits on `main`.

---

## What's shipped (per phase)

| Phase | Scope | Status |
|---|---|---|
| 0 | Repo scaffold, `pyproject.toml`, CI, CLI surface (10 stub subcommands), MIT + NOTICE | ✅ |
| 1 | `vault.py`, `workspace.py`, `retrieve/bm25.py`, `retrieve/query.py`, `config.py`. CLI `init` + `recall`. | ✅ |
| 2 | `capture/{claude_code,redact,summarize,runner}.py`. CLI `capture`. Adapter `claude_code/hooks.json` template. | ✅ |
| 3 | `server/{tools,mcp,http,auth}.py`. `adapters/__init__.py` (claude-code installer, cursor snippet). CLI `serve`, `adapter add/remove`, `lint`, `workspaces ls`. | ✅ |
| 4 | `lint/checks.py` (8 checks). `graph/{neighborhood,canvas}.py`. Capture auto-regenerates canvas. CLI `hot`, `ingest`, `save`. | ✅ |
| 5 | `obsidian-plugin/` — TypeScript Obsidian companion (status bar, 3 commands, settings tab). | ✅ |
| 6 | `docs/*` (quickstart, architecture, api, privacy, session-capture, adapters/*). CHANGELOG. | ✅ |

Full module inventory: see [`docs/architecture.md`](architecture.md) for the 4-layer diagram.

---

## Known bugs (live)

### 1. ⚠️ `claude-obsidian` plugin's SessionStart `prompt` hook crashes

Not our bug — that plugin's hook is broken (`ToolUseContext is required for prompt hooks. This is a bug.`). Cosmetic noise, doesn't block anything.

**Workaround**: disable the plugin (`/plugins` in Claude Code → toggle off, or strip from `~/.claude/settings.json`'s `enabledPlugins`).

### 2. ✅ FIXED: hooks used `$PWD` (wrong cwd) and `UserPromptSubmit` matcher fired on every prompt

The v1 hooks.json shipped these bugs. The v2 in `src/contextvault/adapters/claude_code/hooks.json` fixes both — uses `${CLAUDE_PROJECT_DIR:-$PWD}` and drops the `UserPromptSubmit` event entirely (Stop hook covers the same window).

If a user installed the v1 hooks, they should:

```bash
contextvault adapter remove claude-code
contextvault adapter add claude-code
# Restart Claude Code
```

### 3. Extractive summarizer clips backtick-wrapped decisions

Example seen in a real capture: decisions list contains `use a \`` and `debug before touching \`~/`. The regex in `summarize.py::_DECISION_PATTERNS` matches at backtick boundaries instead of stopping at sentence boundaries.

**Fix scope**: small. Tighten the regex to require a closing backtick or sentence-end punctuation. ~10 lines, ~3 new test cases. Lives in `src/contextvault/capture/summarize.py:39-46`.

### 4. Recall index rebuilt on every call

`retrieve/query.py::build_index` walks the entire vault on every `recall` invocation. Fine for vaults under ~10k notes; slow above that.

**Fix scope**: persist `.vault-meta/bm25/index.json`, update incrementally from `capture/runner.py` after each session write. ~80 lines + 8 tests. Tracked as v0.2 below.

### 5. No sweeper daemon for Ctrl+C / crash captures

If a Claude Code session is killed before its Stop hook fires (Ctrl+C, kill -9, terminal close), the transcript is on disk but never captured.

**Workaround today**: `contextvault capture --cwd /path/to/project` manually. The checkpoint is idempotent so re-running is safe.

**Proper fix**: ship the sweeper (Phase 2 plan: launchd plist + `capture/sweeper.py`). The scaffold is in the plan doc but the module isn't written.

---

## v0.2 backlog (rank-ordered by user impact)

| # | Item | Effort | Files |
|---|---|---|---|
| 1 | **Sweeper daemon** for Ctrl+C / killed sessions | ~1 day | New `capture/sweeper.py` + `LaunchAgents/com.contextvault.sweeper.plist` + `bin/install-launchd.sh`. 90s mtime-stable scan over `~/.claude/projects/*.jsonl`, capture missed ones. |
| 2 | **Persistent BM25 index** updated incrementally on capture | ~1 day | `retrieve/persist.py` (new). Update `retrieve/query.py::run_recall` to load-if-present + fall-back-to-rebuild. Hook capture/runner to `add_document`/`save`. |
| 3 | **`--session-id` flag on capture** to re-process a specific transcript | ~1 hour | `cli.py::_run_capture` + `capture/runner.py::run_capture`. Mostly arg plumbing. |
| 4 | **Cosine rerank via ollama** as opt-in retrieval-quality tier | ~½ day | `retrieve/rerank.py` (new). Wire from `retrieve/query.py`. Feature-gate behind ollama import; identity fallback if absent. Add `[rerank]` extra to pyproject. |
| 5 | **`--llm-summarize` mode** for capture (Anthropic-API re-summary) | ~½ day | Egress-gated (`--allow-egress`). Re-summarize after extractive pass. Update `capture/runner.py` + `capture/summarize.py`. Add `[egress]` extra. |
| 6 | **URL ingestion** (`contextvault ingest https://...`) | ~½ day | Replace the exit-64 stub in `cli.py::_run_ingest`. Fetch via stdlib `urllib`, optional defuddle via a regex pass (no extra deps). |
| 7 | **Stale-claim lint check** | ~1 day | New `lint/checks.py::find_stale_claims`. Compare frontmatter `updated` against sources cited in same note's body. |
| 8 | **Semantic-tiling lint check** (cosine drift) | ~1 day | Requires embedding cache. Hard-gate behind `[rerank]` extra. |
| 9 | **Per-workspace canvas auto-refresh on save_note too** | ~1 hour | `server/tools.py::save_note` calls `graph/canvas.py::regenerate_workspace_canvas` after write. Tests: add 2 cases. |
| 10 | **Obsidian plugin: community-browser submission** | ~2 days (PR review) | Already build-ready in `obsidian-plugin/`. Just `npm install && npm run build`, then submit to `obsidianmd/obsidian-releases`. |
| 11 | **Tune extractive decision regex** | ~½ hour | See bug 3. |
| 12 | **`contextvault export --workspace X`** (zip a workspace for sharing) | ~2 hours | New CLI subcommand. Bundle `workspaces/<id>/` into a zip; include manifest. |
| 13 | **Windows path-encoding support** | ~1 day | `workspace.py::encode` assumes POSIX `/` separators. Add `\\` handling. Add CI matrix entry for windows-latest. |

---

## How to pick up cleanly

```bash
cd /Users/lsingh/Desktop/experiments/contextvault
.venv/bin/pytest tests/ -q                          # should be 285/285 green
.venv/bin/ruff check src/ tests/                    # should be clean
.venv/bin/mypy src/                                 # should be clean
git log --oneline                                   # 7 commits, all on main, no remote
```

If you want a working install on your PATH:

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/contextvault" ~/.local/bin/contextvault
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
echo 'export VAULT_PATH="$HOME/Documents/ContextVault-test"' >> ~/.zshrc
exec zsh
contextvault --version                              # → contextvault 0.1.0
```

The original plan that drove everything: `~/.claude/plans/i-want-to-integrate-toasty-melody.md`. Read for design rationale and the build-phases table.

---

## Key file paths cheat sheet

```
src/contextvault/
├── cli.py                                          ← 10-subcommand dispatcher
├── config.py                                       ← vault path + token + XDG
├── workspace.py                                    ← cwd → workspace-id encoder
├── vault.py                                        ← atomic write + fcntl locks
├── retrieve/{bm25,query}.py                        ← Okapi BM25 + scope-filtered recall
├── capture/{claude_code,summarize,redact,runner}.py ← transcript → session note
├── lint/checks.py                                  ← 8 checks
├── graph/{neighborhood,canvas}.py                  ← wikilinks + Obsidian canvas
├── server/{tools,mcp,http,auth}.py                 ← MCP stdio + HTTP loopback
└── adapters/                                       ← claude-code installer, cursor snippet
    └── claude_code/hooks.json                      ← THE hooks template (v2, fixed)

tests/                                              ← 285 cases, hermetic
obsidian-plugin/                                    ← TypeScript companion (esbuild)
docs/                                               ← quickstart, architecture, api, privacy
```

The plan doc that drove this build: `~/.claude/plans/i-want-to-integrate-toasty-melody.md`.

---

## Test discipline (don't regress these)

1. **Hermetic by default**. No test makes a real network call. Anthropic API + ollama are gated behind `@pytest.mark.egress` (registered in `pyproject.toml`) — never enabled in default `pytest` runs.
2. **HTTP server tests must hit `127.0.0.1` only**. The `LoopbackHTTPServer` explicitly rejects non-loopback binds at startup; tests for the rejection live in `tests/integration/test_http_server.py::TestLoopbackOnly`.
3. **Capture tests use fixture transcripts** at `tests/fixtures/transcripts/`. Don't expand the canonical fixture without expanding the matching assertions — extractor tests pattern-match on its exact content.
4. **Idempotency invariants**: re-running `init`, `capture`, `adapter add` on a populated state must not corrupt or duplicate. There's a test for each (`test_config.py::test_idempotent_preserves_user_edit`, `test_capture_runner.py::test_idempotent_second_run_no_new_entries`, `test_adapters.py::test_idempotent_does_not_duplicate`).
5. **Path safety**: anything that takes a user-supplied path goes through `Vault._safe_join` or `workspace.encode`. Both reject null bytes, absolute paths where relative is expected, and post-normpath traversal escapes. Coverage in `test_vault.py::TestPathSafety` and `test_workspace.py::TestEncode`.

If you touch the adapter installer or the hooks.json template, **re-run the end-to-end smoke** documented in [`docs/quickstart.md`](quickstart.md) — pytest doesn't exercise the real `~/.claude/settings.json` merge against a live Claude Code.

---

## Mental model for designing v0.2 features

Every new feature should answer four questions:

1. **What's the egress story?** If it touches the network, it's behind `--allow-egress` and an extra (`[rerank]`, `[egress]`). Default install stays offline.
2. **What's the failure mode?** A failing rerank falls back to BM25-only. A failing canvas regen doesn't abort the capture. A failing redaction (regex bug) prevents the write entirely (block-and-warn).
3. **Is it workspace-scoped or global?** Default to workspace; cross-workspace is opt-in (`--scope global` or workspace=None).
4. **Does it touch the vault?** If yes, write through `Vault.write` (atomic) inside a `vault.lock(rel)` context. Don't reach for `open()` directly.

Phases 1-6 followed these rules; v0.2 should too.

---

## Quick context-rebuild for a future AI

If you're a fresh AI starting on this project, read in this order:

1. **`~/.claude/plans/i-want-to-integrate-toasty-melody.md`** — the original spec + rationale.
2. **This file** (`docs/STATE.md`) — what's done, what's next, what's broken.
3. **`docs/architecture.md`** — the 4-layer model, why the layers split where they do.
4. **`README.md`** — the user-facing pitch.
5. **`git log -p --stat -- src/contextvault/`** — last 7 commits walk you through the build phase by phase.

Then pick a v0.2 item and run `pytest tests/ -q` to confirm a clean baseline before changing anything.
