"""Workspace identity and path resolution.

A *workspace* is an absolute working directory. ContextVault scopes session
notes, hot caches, and indexes per workspace so context from one project
does not bleed into another.

The encoding is `slash → hyphen` on the absolute, resolved cwd:

    /Users/lsingh/Desktop/experiments  →  -Users-lsingh-Desktop-experiments

This matches the convention Claude Code itself uses for
``~/.claude/projects/<encoded-cwd>/``, so a workspace id is a 1:1 mapping
between a Claude Code project dir and a ContextVault subtree.

The encoding is deterministic and (slightly) lossy — a path containing
literal hyphens cannot be losslessly reversed. That is acceptable: we
encode forward at write time and never decode back. The lossiness is only
visible if a user tries to read a workspace id like a path.

Adapted from claude-obsidian/scripts/wiki-mode.py (`safe_name` / `slugify`
hardening) for the path-traversal guards.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = [
    "WorkspaceError",
    "_strip_root",
    "current",
    "encode",
    "is_valid_id",
    "resolve",
]


class WorkspaceError(ValueError):
    """Raised when a cwd or workspace id cannot be safely resolved."""


# Workspace ids are: leading hyphen, then [A-Za-z0-9._-] and unicode word
# characters, plus optional spaces (rare but legal in macOS paths like
# ``/Users/lsingh/My Drive``). No path separators, no control chars, no
# parent-directory traversal markers.
_ID_PATTERN = re.compile(r"^-[\w\- .]*$", re.UNICODE)
_FORBIDDEN_RUN = re.compile(r"(^|-)\.\.(-|$)")


def encode(cwd: str | os.PathLike[str]) -> str:
    """Encode an absolute cwd into a workspace id.

    Resolves symlinks and ``..`` segments first so traversal is impossible
    from input. Raises :class:`WorkspaceError` for relative paths, empty
    input, or paths containing null / control characters.
    """
    if cwd is None or str(cwd) == "":
        raise WorkspaceError("cwd is empty")

    raw = str(cwd)
    if "\x00" in raw:
        raise WorkspaceError("cwd contains null byte")

    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        raise WorkspaceError(f"cwd must be absolute, got {raw!r}")

    # ``normpath`` collapses ``..``/``.``/trailing slashes without following
    # symlinks. We deliberately do NOT call ``Path.resolve()`` here because
    # Claude Code itself encodes the user's intended PWD (not the canonical
    # symlink target), and ContextVault must match that encoding to share
    # the same workspace dirs under ``~/.claude/projects/``.
    normalized = os.path.normpath(expanded)
    parts = Path(normalized).parts
    stripped = _strip_root(parts)

    # /Users/lsingh/Desktop/experiments  →  -Users-lsingh-Desktop-experiments
    return "-" + "-".join(stripped) if stripped else "-"


def _strip_root(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Remove the filesystem root element from a path's parts tuple.

    Handles both POSIX roots (``('/',)``) and Windows drive letters
    (``('C:\\\\',)``). Raises :class:`WorkspaceError` if the root element
    is unrecognised.
    """
    if not parts:
        raise WorkspaceError("path has no parts")

    root = parts[0]
    # POSIX root
    if root == os.sep:
        return parts[1:]
    # Windows drive letter: e.g. 'C:\\' or 'D:\\'
    if len(root) == 3 and root[1] == ":" and root[2] in ("/", "\\"):
        return parts[1:]
    # Mounted POSIX root on Windows ('\\')?
    if root == "\\":
        return parts[1:]

    raise WorkspaceError(f"cwd does not start at filesystem root: {root!r}")


def is_valid_id(workspace_id: str) -> bool:
    """Return True iff ``workspace_id`` is a syntactically safe encoded cwd.

    Used by callers that accept a workspace id from untrusted input (HTTP
    request, MCP tool arg). Rejects ids with path separators, ``..``
    components, control chars, or anything else that could escape the
    ``workspaces/`` subtree.
    """
    if not isinstance(workspace_id, str) or workspace_id == "":
        return False
    if "\x00" in workspace_id or "/" in workspace_id or "\\" in workspace_id:
        return False
    if not _ID_PATTERN.match(workspace_id):
        return False
    return not _FORBIDDEN_RUN.search(workspace_id)


def resolve(vault_root: str | os.PathLike[str], cwd: str | os.PathLike[str]) -> Path:
    """Return the absolute workspace directory under ``vault_root``.

    Guarantees the returned path is strictly inside ``<vault_root>/workspaces/``
    even if a future change to :func:`encode` regresses — we re-validate
    containment with ``relative_to`` and raise on escape.
    """
    root = Path(vault_root).expanduser().resolve(strict=False)
    ws_id = encode(cwd)
    target = (root / "workspaces" / ws_id).resolve(strict=False)
    try:
        target.relative_to(root / "workspaces")
    except ValueError as exc:
        raise WorkspaceError(
            f"resolved workspace path {target!s} escapes {root!s}/workspaces"
        ) from exc
    return target


def current(vault_root: str | os.PathLike[str]) -> Path:
    """Resolve the workspace dir for the current process's PWD.

    Prefers the ``PWD`` env var (preserves the user's intended cwd through
    symlinked checkouts) and falls back to :func:`os.getcwd`.
    """
    cwd = os.environ.get("PWD") or os.getcwd()
    return resolve(vault_root, cwd)
