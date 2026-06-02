"""Read Claude Code JSONL transcripts and yield typed records.

Claude Code writes one JSON-per-line transcript per session at
``~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl``. Entries are not
homogeneous — alongside the user and assistant turns there are 8+ metadata
record types (file-history snapshots, hook output attachments, permission
mode markers, etc.). This module isolates the parts we care about for
session capture and discards the rest.

Records we surface, in transcript order:

  * :class:`UserMessage`        — what the user typed
  * :class:`AssistantText`      — assistant text output (the visible reply)
  * :class:`AssistantThinking`  — the model's hidden reasoning
  * :class:`ToolUse`            — Bash / Edit / Write / Read / ... invocations
  * :class:`ToolResult`         — return values of those tool calls

We do *not* surface: ``attachment``, ``file-history-snapshot``,
``last-prompt``, ``permission-mode``, ``ai-title``, ``agent-name``,
``queue-operation``, ``system``. These are useful to Claude Code itself
but contain no session-content signal worth preserving.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "AssistantText",
    "AssistantThinking",
    "Record",
    "SessionMeta",
    "ToolResult",
    "ToolUse",
    "UserMessage",
    "find_transcript",
    "read_session_meta",
    "read_transcript",
]


@dataclass(frozen=True, slots=True)
class UserMessage:
    uuid: str
    timestamp: str
    text: str


@dataclass(frozen=True, slots=True)
class AssistantText:
    uuid: str
    timestamp: str
    text: str


@dataclass(frozen=True, slots=True)
class AssistantThinking:
    uuid: str
    timestamp: str
    text: str


@dataclass(frozen=True, slots=True)
class ToolUse:
    uuid: str  # uuid of the enclosing assistant message
    timestamp: str
    tool_use_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    uuid: str
    timestamp: str
    tool_use_id: str
    output: str
    is_error: bool


Record = UserMessage | AssistantText | AssistantThinking | ToolUse | ToolResult


@dataclass(frozen=True, slots=True)
class SessionMeta:
    session_id: str
    cwd: str
    started: str
    last_updated: str
    last_uuid: str
    version: str | None
    entry_count: int


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def read_transcript(
    path: str | os.PathLike[str],
    *,
    after_uuid: str | None = None,
) -> Iterator[Record]:
    """Yield :data:`Record`-typed entries from a JSONL transcript.

    If ``after_uuid`` is set, skip every entry up to and including the one
    with that uuid. This is how the capture pipeline incrementally
    processes transcripts: it stores the last-captured uuid per session and
    passes it back on subsequent calls.

    Malformed lines (bad JSON or unexpected shape) are skipped silently
    rather than raising — a single broken line should not abort a capture
    pass over an otherwise valid transcript.
    """
    skip = after_uuid is not None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry: dict[str, Any] = json.loads(stripped)
            except json.JSONDecodeError:
                continue

            if skip:
                if entry.get("uuid") == after_uuid:
                    skip = False
                continue

            yield from _records_from_entry(entry)


def read_session_meta(path: str | os.PathLike[str]) -> SessionMeta | None:
    """Return summary metadata for a transcript, or ``None`` if empty/invalid.

    One streaming pass — never loads the full transcript into memory.
    """
    session_id: str | None = None
    cwd: str | None = None
    started: str | None = None
    last_updated: str | None = None
    last_uuid: str | None = None
    version: str | None = None
    count = 0

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry: dict[str, Any] = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            count += 1
            if session_id is None:
                session_id = entry.get("sessionId")
            if cwd is None:
                cwd = entry.get("cwd")
            ts = entry.get("timestamp")
            if ts:
                if started is None:
                    started = ts
                last_updated = ts
            if entry.get("uuid"):
                last_uuid = entry["uuid"]
            if version is None:
                version = entry.get("version")

    if session_id is None or count == 0:
        return None
    return SessionMeta(
        session_id=session_id,
        cwd=cwd or "",
        started=started or "",
        last_updated=last_updated or "",
        last_uuid=last_uuid or "",
        version=version,
        entry_count=count,
    )


def find_transcript(
    encoded_cwd: str,
    *,
    session_id: str | None = None,
    projects_root: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Locate a Claude Code transcript file.

    With ``session_id`` set: returns ``~/.claude/projects/<enc>/<sid>.jsonl``
    if it exists, else ``None``. Without it: returns the most recently
    modified ``.jsonl`` in the project dir.
    """
    root = (
        Path(projects_root).expanduser()
        if projects_root
        else Path("~/.claude/projects").expanduser()
    )
    project_dir = root / encoded_cwd
    if not project_dir.is_dir():
        return None

    if session_id is not None:
        target = project_dir / f"{session_id}.jsonl"
        return target if target.is_file() else None

    jsonl_files = list(project_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------
# Entry → Record(s) translation
# --------------------------------------------------------------------------


def _records_from_entry(entry: dict[str, Any]) -> Iterator[Record]:
    entry_type = entry.get("type")
    if entry_type == "user":
        yield from _user_records(entry)
    elif entry_type == "assistant":
        yield from _assistant_records(entry)
    # All other entry types (attachment, system, file-history-snapshot,
    # last-prompt, permission-mode, ai-title, agent-name, queue-operation)
    # carry no session-content signal worth preserving.


def _user_records(entry: dict[str, Any]) -> Iterator[Record]:
    uuid = entry.get("uuid", "")
    ts = entry.get("timestamp", "")
    msg = entry.get("message") or {}
    content = msg.get("content")

    if isinstance(content, str):
        # The common case: user typed something.
        if content.strip():
            yield UserMessage(uuid=uuid, timestamp=ts, text=content)
        return

    if isinstance(content, list):
        # Less common: tool_result blocks injected into the user channel.
        # These represent tool outputs that Claude Code routes back through
        # the user role per the Anthropic content-block convention.
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_result":
                yield ToolResult(
                    uuid=uuid,
                    timestamp=ts,
                    tool_use_id=item.get("tool_use_id", ""),
                    output=_extract_text(item.get("content")),
                    is_error=bool(item.get("is_error", False)),
                )


def _assistant_records(entry: dict[str, Any]) -> Iterator[Record]:
    uuid = entry.get("uuid", "")
    ts = entry.get("timestamp", "")
    msg = entry.get("message") or {}
    content = msg.get("content")

    if isinstance(content, str):
        if content.strip():
            yield AssistantText(uuid=uuid, timestamp=ts, text=content)
        return

    if not isinstance(content, list):
        return

    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "thinking":
            text = item.get("thinking") or item.get("text") or ""
            if text.strip():
                yield AssistantThinking(uuid=uuid, timestamp=ts, text=text)
        elif item_type == "text":
            text = item.get("text", "")
            if text.strip():
                yield AssistantText(uuid=uuid, timestamp=ts, text=text)
        elif item_type == "tool_use":
            yield ToolUse(
                uuid=uuid,
                timestamp=ts,
                tool_use_id=item.get("id", ""),
                name=item.get("name", ""),
                input=item.get("input") or {},
            )


def _extract_text(content: object) -> str:
    """Best-effort text extraction from a tool_result content payload.

    Anthropic content-block format permits either a string or a list of
    blocks with ``{"type": "text", "text": "..."}`` entries.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""
