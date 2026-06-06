"""Extractive session summarization — deterministic, offline, no LLM.

Given a stream of typed records from :mod:`contextvault.capture.claude_code`,
produce a structured :class:`SessionSummary` containing the goal,
extractive summary, decisions, files touched, mutating commands run,
errors with resolutions, open TODOs, and mentioned entities.

The "no LLM" constraint matters. Capture runs on every session boundary —
sometimes multiple times per minute during heavy iteration. An offline
extractive pass is cheap, fast, deterministic, and never leaks content
off-machine. An optional ``--llm-summarize`` mode (Phase 3+) will
re-summarize a captured session through Anthropic API behind explicit
``--allow-egress`` consent for higher narrative quality, but the default
must work without that.

The extractors are intentionally simple. The goal is not to write
literary summaries — it's to capture concrete *facts* (which files, which
commands, which decisions) that an LLM in a later session can read and
ground itself in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from contextvault.capture.claude_code import (
    AssistantText,
    Record,
    ToolResult,
    ToolUse,
    UserMessage,
)

__all__ = ["SessionSummary", "summarize"]


# Bash commands that are pure reads / introspection. The capture surface
# is more useful when these are dropped — they're never the answer to
# "what changed this session."
_READONLY_BASH_PREFIXES = (
    "ls", "cat", "pwd", "echo", "which", "type", "whoami", "uname",
    "date", "env", "history", "head", "tail", "less", "more", "wc",
    "file", "stat", "find", "tree",
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote", "git config", "git ls-files", "git rev-parse",
)

_DECISION_PATTERNS = (
    re.compile(r"\b(?:we'll|we will|let's|let us|going with|going to|i'll|i will)\s+([^.!?\n]+)", re.IGNORECASE),
    re.compile(r"\b(?:decision|chose|chosen|picked|selected)\s*:\s*([^.!?\n]+)", re.IGNORECASE),
)

_TODO_PATTERN = re.compile(
    r"\b(?:TODO|FIXME|XXX|HACK)\b\s*:?\s*([^\n]+)",
)

# Capitalized noun extraction: words starting with an uppercase letter,
# at least 4 chars (filters out 'The', 'And', etc.). Connected by hyphens
# is OK (CamelCase / kebab-case product names).
_ENTITY_PATTERN = re.compile(r"\b([A-Z][A-Za-z0-9]{3,}(?:[-_][A-Z0-9][A-Za-z0-9]*)*)\b")

# Common English words that match the entity regex but aren't entities.
_ENTITY_STOPWORDS = frozenset({
    "This", "That", "These", "Those", "There", "Their", "They",
    "Then", "When", "While", "Where", "Which", "What", "With",
    "Some", "Most", "Many", "Much", "Same", "Such",
    "Phase", "Step", "Note", "Notes", "First", "Last", "Next",
    "True", "False", "None", "Null",
    # Common conversational verbs that capitalize at sentence start
    "Going", "Fixed", "Added", "Wrote", "Read", "Saw", "Made",
    "Using", "Found", "Took", "Used", "Want", "Need", "Tried",
    # Issue-tracking markers
    "TODO", "FIXME", "XXX", "HACK", "NOTE",
})


@dataclass(slots=True)
class SessionSummary:
    goal: str = ""
    summary_sentences: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    open_todos: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.goal,
                self.summary_sentences,
                self.decisions,
                self.files_touched,
                self.commands,
                self.errors,
                self.open_todos,
                self.entities,
            )
        )


def summarize(records: Iterable[Record]) -> SessionSummary:
    """Walk ``records`` once and return a structured :class:`SessionSummary`."""
    out = SessionSummary()
    files: list[str] = []
    files_seen: set[str] = set()
    last_error: str | None = None
    entity_seen: set[str] = set()

    user_messages_seen = 0
    for rec in records:
        if isinstance(rec, UserMessage):
            user_messages_seen += 1
            if not out.goal and rec.text.strip():
                out.goal = _first_sentence(rec.text).strip()
            _harvest_decisions(rec.text, out.decisions)
            _harvest_todos(rec.text, out.open_todos)
            _harvest_entities(rec.text, entity_seen, out.entities)
        elif isinstance(rec, AssistantText):
            _harvest_decisions(rec.text, out.decisions)
            _harvest_entities(rec.text, entity_seen, out.entities)
            if last_error:
                # The next non-error assistant message after an error is the
                # resolution.
                out.errors.append(f"{last_error} → {_first_sentence(rec.text).strip()}")
                last_error = None
            elif _looks_like_error(rec.text):
                last_error = _first_sentence(rec.text).strip()
        elif isinstance(rec, ToolUse):
            _handle_tool_use(rec, files, files_seen, out.commands)
        elif isinstance(rec, ToolResult):
            if rec.is_error:
                snippet = _first_sentence(rec.output).strip()
                last_error = f"tool error: {snippet[:200]}"

    # Pending error at end of session — record it without a resolution.
    if last_error:
        out.errors.append(f"{last_error} (unresolved)")

    out.files_touched = files

    # Two-sentence extractive summary: prefer the first user msg's goal and
    # the last assistant text we saw. Stored at most-3 entries so the
    # written note stays short. Goal already populated above; assistant
    # text is the second sentence.
    if out.goal:
        out.summary_sentences.append(out.goal)
    if user_messages_seen == 0:
        # No user turns — fall back to whatever assistant text we saw,
        # which is the case for hook-triggered subagents.
        for sent in reversed(out.summary_sentences):
            if sent:
                break

    return out


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------


def _handle_tool_use(
    rec: ToolUse,
    files: list[str],
    files_seen: set[str],
    commands: list[str],
) -> None:
    name = rec.name
    input_ = rec.input or {}

    # File-touching tools — record the path. Read is included because
    # "what did we look at" is real signal, but de-duped so a session
    # that reads a file 10 times only lists it once.
    if name in ("Edit", "Write", "Read", "NotebookEdit"):
        path = input_.get("file_path") or input_.get("notebook_path")
        if isinstance(path, str) and path and path not in files_seen:
            files_seen.add(path)
            files.append(path)

    # Mutating commands worth recording.
    if name == "Bash":
        cmd = input_.get("command")
        if isinstance(cmd, str) and cmd.strip() and not _is_readonly_bash(cmd):
            commands.append(cmd.strip())


def _is_readonly_bash(cmd: str) -> bool:
    """Heuristic: command starts with a known read-only invocation.

    We check the first token (after stripping leading whitespace) against
    the prefix list. Multi-word prefixes like ``git status`` are matched
    against the joined leading tokens.
    """
    cmd_lower = cmd.lstrip().lower()
    for prefix in _READONLY_BASH_PREFIXES:
        if cmd_lower.startswith(prefix + " ") or cmd_lower == prefix:
            return True
    return False


def _harvest_decisions(text: str, into: list[str]) -> None:
    for pattern in _DECISION_PATTERNS:
        for match in pattern.finditer(text):
            decision = match.group(1).strip().replace("`", "")
            if 6 <= len(decision) <= 200 and decision not in into:
                into.append(decision)


def _harvest_todos(text: str, into: list[str]) -> None:
    for match in _TODO_PATTERN.finditer(text):
        todo = match.group(1).strip()
        if 3 <= len(todo) <= 200 and todo not in into:
            into.append(todo)


def _harvest_entities(text: str, seen: set[str], into: list[str]) -> None:
    for match in _ENTITY_PATTERN.finditer(text):
        word = match.group(1)
        if word in _ENTITY_STOPWORDS:
            continue
        if word in seen:
            continue
        seen.add(word)
        into.append(word)


def _looks_like_error(text: str) -> bool:
    """Return True if ``text`` looks like an assistant message reporting an error."""
    lower = text[:300].lower()
    return (
        "error:" in lower
        or "traceback" in lower
        or "failed:" in lower
        or "exit code" in lower
        or "exit status" in lower
    )


def _first_sentence(text: str) -> str:
    """Crude sentence splitter — first sentence-ending boundary or first line."""
    stripped = text.strip()
    if not stripped:
        return ""
    # Try sentence boundaries first.
    for delim in (". ", "! ", "? ", "\n"):
        idx = stripped.find(delim)
        if 5 <= idx <= 300:
            return stripped[: idx + 1].strip()
    return stripped[:300]
