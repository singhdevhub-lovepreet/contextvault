"""Sweeper daemon: catch sessions killed before their Stop hook fires.

Scans ~/.claude/projects/*/ for .jsonl transcripts whose mtime is stable
(>= 90s since last write) but whose session_id is not in the captured
checkpoint. Captures them via run_capture().

Designed to run from launchd every few minutes.
"""

from __future__ import annotations

import time
from pathlib import Path

from contextvault.capture.claude_code import read_session_meta
from contextvault.capture.runner import _load_checkpoint, run_capture
from contextvault.vault import Vault

__all__ = ["run_sweep"]

# Minimum seconds since last modification before we consider a transcript
# "stable enough" to capture. Prevents capturing mid-session.
_STABLE_SECONDS = 90

# Default projects root
_DEFAULT_PROJECTS_ROOT = Path("~/.claude/projects").expanduser()


def run_sweep(
    vault_path: Path,
    *,
    projects_root: Path | None = None,
    stable_seconds: int = _STABLE_SECONDS,
) -> list[str]:
    """Scan for and capture missed sessions.

    Returns a list of session_ids that were newly captured.
    """
    root = (projects_root or _DEFAULT_PROJECTS_ROOT).expanduser()
    if not root.is_dir():
        return []

    vault = Vault(vault_path)
    checkpoint = _load_checkpoint(vault)
    captured: list[str] = []
    now = time.time()

    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            # Check mtime stability
            try:
                mtime = jsonl_file.stat().st_mtime
            except OSError:
                continue

            if now - mtime < stable_seconds:
                # Still being written — skip this pass
                continue

            meta = read_session_meta(jsonl_file)
            if meta is None:
                continue

            # Already captured?
            if meta.session_id in checkpoint:
                continue

            # Capture it
            result = run_capture(
                vault_path=vault_path,
                cwd=meta.cwd,
                projects_root=projects_root,
                transcript_path=jsonl_file,
            )
            if result and result.wrote_note:
                captured.append(meta.session_id)

    return captured
