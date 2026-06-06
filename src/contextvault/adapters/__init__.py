"""Per-client adapter install/remove helpers.

Each ``install_*`` function returns a list of strings to print to the
user — typically the path it modified and any post-install action they
need to take (e.g. "restart Cursor", "paste this MCP snippet into …").

Adapters are intentionally thin: their job is to drop a single config
fragment in the right place so the LLM client knows how to invoke
``contextvault`` for MCP or hit the HTTP endpoint. No daemons, no
agents — the actual work happens in the contextvault process itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

__all__ = [
    "SUPPORTED_CLIENTS",
    "install_claude_code",
    "install_cursor",
    "install_hermes",
    "remove_claude_code",
    "remove_hermes",
]


SUPPORTED_CLIENTS = ("claude-code", "cursor", "hermes")


def _claude_settings_path() -> Path:
    return Path("~/.claude/settings.json").expanduser()


def install_claude_code(*, settings_path: Path | None = None) -> list[str]:
    """Merge ContextVault hooks + MCP server entry into ``~/.claude/settings.json``.

    Idempotent: re-installing detects existing entries (by their
    ``contextvault`` command substring) and replaces them in place. Other
    user-added hooks are preserved.
    """
    target = settings_path or _claude_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = _read_json(target) if target.exists() else {}
    if not isinstance(existing, dict):
        raise RuntimeError(f"{target} is not a JSON object")

    template = _load_hooks_template()
    merged = _merge_claude_code_settings(existing, template)

    _atomic_write_json(target, merged)
    return [
        f"installed contextvault hooks → {target}",
        "  Stop hook: capture-on-turn-end",
        "  UserPromptSubmit (^/clear): capture-final",
        "  SessionStart: load workspace hot cache",
        "MCP server registered as 'contextvault'.",
        "",
        "Reload Claude Code to pick up the new settings.",
    ]


def remove_claude_code(*, settings_path: Path | None = None) -> list[str]:
    """Remove ContextVault entries from ``~/.claude/settings.json``."""
    target = settings_path or _claude_settings_path()
    if not target.exists():
        return [f"nothing to remove ({target} does not exist)"]

    existing = _read_json(target)
    if not isinstance(existing, dict):
        raise RuntimeError(f"{target} is not a JSON object")

    cleaned = _strip_claude_code_settings(existing)
    _atomic_write_json(target, cleaned)
    return [f"removed contextvault hooks from {target}"]


def install_cursor() -> list[str]:
    """Print the Cursor MCP snippet — Cursor's config is interactive only."""
    snippet = json.dumps(
        {
            "mcpServers": {
                "contextvault": {
                    "command": "contextvault",
                    "args": ["serve", "--mcp"],
                }
            }
        },
        indent=2,
    )
    return [
        "Add the following to ~/.cursor/mcp.json (merge if it exists):",
        "",
        snippet,
        "",
        "Then restart Cursor. The 'contextvault' MCP server will appear under MCP Tools.",
    ]


def install_hermes(*, plist_path: Path | None = None) -> list[str]:
    """Generate Hermes system prompt + launchd plist for HTTP server.

    Hermes doesn't support MCP — it uses the HTTP loopback endpoint.
    This adapter creates:
      1. A launchd plist to keep the HTTP server running (optional, user activates)
      2. The system prompt template to paste into Hermes Chat settings
    """
    plist_path = plist_path or Path("~/Library/LaunchAgents/contextvault.plist").expanduser()
    token_path = Path("~/.config/contextvault/token").expanduser()

    # Read token if exists for the system prompt
    token = ""
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()

    # System prompt template (from docs/adapters/hermes.md)
    system_prompt = f"""You have access to a persistent memory at http://127.0.0.1:7842.

Tools available (HTTP GET unless noted):

  /recall?query=<terms>&cwd=<absolute path>&scope=workspace
      → JSON array of {{path, workspace, score, preview}}
  /recent_sessions?cwd=<absolute path>&limit=5
      → JSON array of recent session frontmatters
  /list_workspaces
      → JSON array of {{workspace, session_count, updated_at}}
  /lint?scope=workspace&cwd=<absolute path>
      → JSON array of lint findings
  POST /save_note  body: {{"title": "...", "body": "...", "workspace": "current", "cwd": "..."}}
      → {{"path": "..."}}

Auth: every request needs `Authorization: Bearer {token}` where TOKEN is
in ~/.config/contextvault/token.

Before answering questions about past sessions, recent decisions, or
files-touched-yesterday, call /recall and ground your answer in the
preview text. Cite paths when you do."""

    # launchd plist for background HTTP server (port 7842)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>contextvault</string>
    <key>ProgramArguments</key>
    <array>
      <string>{shutil.which('contextvault') or '/usr/local/bin/contextvault'}</string>
      <string>serve</string>
      <string>--http</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{os.environ.get('PATH', '/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin')}</string>
    </dict>
</dict>
</plist>"""

    # Actually write the plist
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content, encoding="utf-8")

    return [
        "=== Hermes Adapter ===",
        "",
        "1. SYSTEM PROMPT (paste into Hermes → Settings → System Prompt):",
        "",
        system_prompt,
        "",
        "2. OPTIONAL: Background HTTP server via launchd",
        f"   plist created at: {plist_path}",
        "   To enable (runs HTTP server at login on port 7842):",
        f"     launchctl load {plist_path}",
        "",
        "   Or run manually anytime:",
        "     contextvault serve --http",
        "",
        "3. VERIFY: Once server is running, test with:",
        f"   curl -H \\\"Authorization: Bearer {token}\\\" http://127.0.0.1:7842/list_workspaces",
    ]


def remove_hermes(*, plist_path: Path | None = None) -> list[str]:
    """Remove Hermes launchd plist (system prompt is user-managed)."""
    plist_path = plist_path or Path("~/Library/LaunchAgents/contextvault.plist").expanduser()
    if not plist_path.exists():
        return [f"nothing to remove ({plist_path} does not exist)"]

    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)

    plist_path.unlink()
    return [f"removed Hermes launchd plist from {plist_path}"]


def _load_hooks_template() -> dict[str, object]:
    """Read the packaged hooks.json template."""
    pkg = resources.files(__package__) / "claude_code" / "hooks.json"
    data: Any = json.loads(pkg.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("hooks.json template must be a JSON object")
    return data


def _merge_claude_code_settings(
    existing: dict[str, object], template: dict[str, object]
) -> dict[str, object]:
    """Merge ContextVault hooks + mcpServers entry into the existing settings.

    Strategy: detect any pre-existing entry whose command contains
    'contextvault' and replace it with our template's version. Other
    user-added hooks/servers pass through unchanged.
    """
    out: dict[str, object] = dict(existing)

    # --- hooks merge -------------------------------------------------------
    existing_hooks = existing.get("hooks") or {}
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}
    template_hooks = template.get("hooks") or {}
    if not isinstance(template_hooks, dict):
        template_hooks = {}

    merged_hooks: dict[str, list[dict[str, object]]] = {}
    for event in set(existing_hooks) | set(template_hooks):
        ours_for_event = template_hooks.get(event) or []
        theirs_for_event = existing_hooks.get(event) or []
        if not isinstance(theirs_for_event, list):
            theirs_for_event = []
        if not isinstance(ours_for_event, list):
            ours_for_event = []
        # Drop any existing entry that already references contextvault — we
        # are about to re-add it with the latest template.
        non_contextvault = [
            entry for entry in theirs_for_event if not _entry_mentions(entry, "contextvault")
        ]
        merged_hooks[event] = non_contextvault + list(ours_for_event)

    out["hooks"] = merged_hooks

    # --- mcpServers entry --------------------------------------------------
    existing_servers = existing.get("mcpServers") or {}
    if not isinstance(existing_servers, dict):
        existing_servers = {}
    servers = dict(existing_servers)
    servers["contextvault"] = {
        "command": "contextvault",
        "args": ["serve", "--mcp"],
    }
    out["mcpServers"] = servers

    return out


def _strip_claude_code_settings(existing: dict[str, object]) -> dict[str, object]:
    """Inverse of :func:`_merge_claude_code_settings`."""
    out: dict[str, object] = dict(existing)

    hooks = existing.get("hooks") or {}
    if isinstance(hooks, dict):
        cleaned_hooks: dict[str, list[dict[str, object]]] = {}
        for event, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            keep = [
                e for e in entries if not _entry_mentions(e, "contextvault")
            ]
            if keep:
                cleaned_hooks[event] = keep
        out["hooks"] = cleaned_hooks

    servers = existing.get("mcpServers")
    if isinstance(servers, dict):
        cleaned_servers = {k: v for k, v in servers.items() if k != "contextvault"}
        if cleaned_servers:
            out["mcpServers"] = cleaned_servers
        else:
            out.pop("mcpServers", None)

    return out


def _entry_mentions(entry: object, needle: str) -> bool:
    """Return True if any command string within ``entry`` mentions ``needle``."""
    return needle in json.dumps(entry)


def _read_json(path: Path) -> object:
    raw = path.read_text(encoding="utf-8") if path.exists() else "{}"
    return json.loads(raw or "{}")


def _atomic_write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
