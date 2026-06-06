"""Orchestrate a session capture: read → summarize → redact → write.

The :func:`run_capture` function is the entrypoint the CLI ``capture``
subcommand calls. It owns the side-effects:

  * Reading ``.vault-meta/captured.json`` to find the last-captured uuid.
  * Locking ``workspaces/<ws>/sessions/<sid>.md`` for the write.
  * Appending to ``workspaces/<ws>/log.md``.
  * Rewriting ``workspaces/<ws>/hot.md`` with the latest summary preview.
  * Updating the checkpoint.

Pure-data extraction lives in :mod:`.summarize`, transcript parsing in
:mod:`.claude_code`, secret masking in :mod:`.redact`. This module is the
*orchestrator* — it should remain thin.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from contextvault.capture.claude_code import (
    SessionMeta,
    find_transcript,
    read_session_meta,
    read_transcript,
)
from contextvault.capture.redact import RedactionEvent, redact_text
from contextvault.capture.summarize import SessionSummary, llm_refine_summary, summarize
from contextvault.retrieve.persist import update_index
from contextvault.vault import Vault
from contextvault.workspace import encode

__all__ = ["CaptureResult", "render_session_note", "run_capture"]


_CHECKPOINT_REL = ".vault-meta/captured.json"
_REDACTED_LOG_REL = ".vault-meta/redacted.log"


@dataclass(frozen=True, slots=True)
class CaptureResult:
    workspace: str
    session_id: str
    session_note_path: str
    summary: SessionSummary
    new_entries: int
    redactions: int
    wrote_note: bool


def run_capture(
    vault_path: Path,
    cwd: str,
    *,
    session_id: str | None = None,
    projects_root: Path | None = None,
    transcript_path: Path | None = None,
    allow_egress: bool = False,
) -> CaptureResult | None:
    """Capture the latest session in ``cwd`` into ``vault_path``.

    Returns ``None`` if no transcript exists yet for the workspace.

    ``projects_root`` defaults to ``~/.claude/projects/``; tests pass a
    sandbox path. ``transcript_path`` is a direct override that bypasses
    the lookup (also primarily for tests).
    """
    workspace = encode(cwd)

    if transcript_path is None:
        transcript_path = find_transcript(
            workspace, session_id=session_id, projects_root=projects_root
        )
    if transcript_path is None or not transcript_path.is_file():
        return None

    meta = read_session_meta(transcript_path)
    if meta is None:
        return None

    vault = Vault(vault_path)
    checkpoint = _load_checkpoint(vault)
    after_uuid = checkpoint.get(meta.session_id)

    records = list(read_transcript(transcript_path, after_uuid=after_uuid))
    if not records:
        return CaptureResult(
            workspace=workspace,
            session_id=meta.session_id,
            session_note_path="",
            summary=SessionSummary(),
            new_entries=0,
            redactions=0,
            wrote_note=False,
        )

    summary = summarize(iter(records))

    if allow_egress and not summary.is_empty:
        with contextlib.suppress(Exception):
            summary = llm_refine_summary(summary)

    if summary.is_empty:
        # Records existed but contained no signal worth filing — bump
        # checkpoint so we don't reprocess them next time, but write
        # nothing.
        _save_checkpoint(vault, checkpoint, meta.session_id, meta.last_uuid)
        return CaptureResult(
            workspace=workspace,
            session_id=meta.session_id,
            session_note_path="",
            summary=summary,
            new_entries=len(records),
            redactions=0,
            wrote_note=False,
        )

    note_rel = _session_note_rel_path(workspace, meta)
    note_body, redactions = _build_and_redact_note(summary, meta, workspace)

    with vault.lock(note_rel):
        vault.write(note_rel, note_body)
        _append_log(vault, workspace, meta, summary)
        _rewrite_hot(vault, workspace, meta, summary)
        if redactions:
            _append_redacted_log(vault, meta.session_id, redactions)
        _save_checkpoint(vault, checkpoint, meta.session_id, meta.last_uuid)
        _regenerate_canvas(vault, workspace)

    # Update persistent BM25 index
    with contextlib.suppress(Exception):
        update_index(vault, note_rel, note_body, workspace=workspace)

    return CaptureResult(
        workspace=workspace,
        session_id=meta.session_id,
        session_note_path=note_rel,
        summary=summary,
        new_entries=len(records),
        redactions=len(redactions),
        wrote_note=True,
    )


# --------------------------------------------------------------------------
# Rendering — pure functions, no I/O
# --------------------------------------------------------------------------


def render_session_note(
    summary: SessionSummary, meta: SessionMeta, workspace: str
) -> str:
    """Render a session note (frontmatter + body) as a Markdown string."""
    short_sid = meta.session_id[:8]
    slug = _slug_from_goal(summary.goal) or "session"
    date = (meta.started or _now_iso())[:10]

    fm_lines = [
        "---",
        "type: session",
        f"workspace: {workspace}",
        f"sessionId: {meta.session_id}",
        f"started: {meta.started}",
        f"updated: {meta.last_updated}",
        f"cwd: {meta.cwd}",
        f"slug: {slug}",
        f"date: {date}",
        f"short_id: {short_sid}",
        "tags: [session]",
        "---",
    ]
    body_parts = ["\n".join(fm_lines), ""]

    if summary.goal:
        body_parts.extend(["## Goal", "", summary.goal, ""])

    if summary.summary_sentences:
        body_parts.append("## Summary")
        body_parts.append("")
        for sent in summary.summary_sentences:
            body_parts.append(sent)
        body_parts.append("")

    if summary.decisions:
        body_parts.append("## Decisions")
        body_parts.append("")
        for d in summary.decisions:
            body_parts.append(f"- {d}")
        body_parts.append("")

    if summary.files_touched:
        body_parts.append("## Files touched")
        body_parts.append("")
        for f in summary.files_touched:
            body_parts.append(f"- `{f}`")
        body_parts.append("")

    if summary.commands:
        body_parts.append("## Commands")
        body_parts.append("")
        for c in summary.commands:
            body_parts.append(f"- `{c}`")
        body_parts.append("")

    if summary.errors:
        body_parts.append("## Errors")
        body_parts.append("")
        for e in summary.errors:
            body_parts.append(f"- {e}")
        body_parts.append("")

    if summary.open_todos:
        body_parts.append("## Open TODOs")
        body_parts.append("")
        for t in summary.open_todos:
            body_parts.append(f"- {t}")
        body_parts.append("")

    if summary.entities:
        body_parts.append("## Entities")
        body_parts.append("")
        for e in summary.entities:
            body_parts.append(f"- [[{e}]]")
        body_parts.append("")

    return "\n".join(body_parts)


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------


def _session_note_rel_path(workspace: str, meta: SessionMeta) -> str:
    date = (meta.started or _now_iso())[:10]
    short = meta.session_id[:8] or "session"
    return f"workspaces/{workspace}/sessions/{date}-{short}.md"


def _build_and_redact_note(
    summary: SessionSummary, meta: SessionMeta, workspace: str
) -> tuple[str, list[RedactionEvent]]:
    """Render the note, then run the redaction pass over the whole thing.

    Redacting after rendering means anything that ends up in the on-disk
    note is scanned — including text we synthesized (decisions list,
    summary). Belt and suspenders.
    """
    raw = render_session_note(summary, meta, workspace)
    return redact_text(raw)


def _load_checkpoint(vault: Vault) -> dict[str, str]:
    raw = vault.read(_CHECKPOINT_REL)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _save_checkpoint(
    vault: Vault, current: dict[str, str], session_id: str, last_uuid: str
) -> None:
    current[session_id] = last_uuid
    vault.write(_CHECKPOINT_REL, json.dumps(current, indent=2, sort_keys=True) + "\n")


def _append_log(
    vault: Vault, workspace: str, meta: SessionMeta, summary: SessionSummary
) -> None:
    line = (
        f"- [{meta.last_updated or _now_iso()}] "
        f"session `{meta.session_id[:8]}` — "
        f"{summary.goal or '(no goal extracted)'}\n"
    )
    vault.append(f"workspaces/{workspace}/log.md", line)


def _rewrite_hot(
    vault: Vault, workspace: str, meta: SessionMeta, summary: SessionSummary
) -> None:
    body = _render_workspace_hot(workspace, meta, summary)
    vault.write(f"workspaces/{workspace}/hot.md", body)


def _render_workspace_hot(
    workspace: str, meta: SessionMeta, summary: SessionSummary
) -> str:
    lines = [
        f"# Hot cache — workspace `{workspace}`",
        "",
        f"_Last updated: {meta.last_updated or _now_iso()}_",
        "",
        "## Most recent session",
        "",
        f"- Session: `{meta.session_id[:8]}`  cwd: `{meta.cwd}`",
    ]
    if summary.goal:
        lines += ["", "**Goal:** " + summary.goal]
    if summary.files_touched:
        lines += ["", "**Files touched:**"]
        for f in summary.files_touched[:5]:
            lines.append(f"- `{f}`")
    if summary.open_todos:
        lines += ["", "## Open TODOs"]
        for t in summary.open_todos[:5]:
            lines.append(f"- {t}")
    lines.append("")
    return "\n".join(lines)


def _regenerate_canvas(vault: Vault, workspace: str) -> None:
    """Refresh the workspace's Obsidian canvas map.

    Best-effort: a canvas-write failure must not abort an otherwise-good
    capture (the session note + hot cache + log + checkpoint are
    load-bearing; the canvas is decoration).
    """
    try:
        from contextvault.graph.canvas import regenerate_workspace_canvas

        regenerate_workspace_canvas(vault, workspace)
    except Exception:
        pass


def _append_redacted_log(
    vault: Vault, session_id: str, events: list[RedactionEvent]
) -> None:
    """Audit-log redaction events. Stores offsets + pattern names, never content."""
    now = _now_iso()
    lines = [
        f"{now} session={session_id[:8]} line_offset={ev.line_offset} pattern={ev.pattern}\n"
        for ev in events
    ]
    for line in lines:
        vault.append(_REDACTED_LOG_REL, line)


_SLUG_RE_BAD = re.compile(r"[^\w\-]+")


def _slug_from_goal(goal: str) -> str:
    """Best-effort filesystem-safe slug from the goal sentence (max 40 chars)."""
    s = _SLUG_RE_BAD.sub("-", goal.lower()).strip("-")
    return s[:40].rstrip("-")


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
