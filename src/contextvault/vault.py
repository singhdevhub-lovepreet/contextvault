"""Vault file I/O with atomic writes and per-file advisory locks.

The vault is a directory of UTF-8 Markdown notes plus a ``.vault-meta/``
state subtree. Every mutation goes through this module so:

  * writes are atomic (write to a temp file in the same directory, then
    ``os.replace``), avoiding torn writes if the process is killed mid-flush;
  * concurrent writers serialize on per-file advisory locks (``fcntl.flock``
    on a sibling sentinel file), avoiding the multi-writer corruption hole
    that motivated claude-obsidian's ``scripts/wiki-lock.sh``;
  * stale locks (owning PID gone) are auto-released so a crashed writer
    does not wedge the vault.

Path safety: every public method takes a *relative* path and refuses
absolute paths, ``..`` traversal, and paths whose resolved location escapes
the vault root.

Adapted from claude-obsidian/scripts/wiki-lock.sh for the lock semantics,
re-implemented in pure Python so the CLI has no shell dependency.
"""

from __future__ import annotations

import errno
import fcntl
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

__all__ = [
    "Vault",
    "VaultError",
]


class VaultError(Exception):
    """Raised on vault path-safety violations or unrecoverable I/O failures."""


# Per-file lock files live under ``<vault>/.vault-meta/locks/<sha>.lock`` so
# they (a) do not pollute the vault tree, (b) never collide with note paths
# even if a note name contains odd characters.
_LOCK_SUBDIR = ".vault-meta/locks"

# A lock older than this with no live process holding it is considered
# stale. Two minutes is comfortably longer than any legitimate write under
# this module (which holds the lock only across the atomic-rename window).
_STALE_LOCK_SECONDS = 120


class Vault:
    """A vault rooted at ``root``.

    Methods raise :class:`VaultError` for path-safety violations and re-raise
    underlying ``OSError`` for I/O failures the caller is expected to handle.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().absolute()

    # ---- path helpers --------------------------------------------------

    def _safe_join(self, rel: str | os.PathLike[str]) -> Path:
        """Return ``self.root / rel`` after validating ``rel`` is safe.

        Rejects absolute paths, paths containing null bytes, and paths that
        — once joined and normalized — escape ``self.root``.
        """
        rel_str = os.fspath(rel)
        if rel_str == "" or rel_str == ".":
            raise VaultError("relative path is empty")
        if "\x00" in rel_str:
            raise VaultError("path contains null byte")
        if os.path.isabs(rel_str):
            raise VaultError(f"path must be relative, got {rel_str!r}")

        joined = (self.root / rel_str)
        # ``os.path.normpath`` collapses ``..``/``.`` without touching the
        # filesystem (no symlink resolution), matching workspace.encode().
        normalized = Path(os.path.normpath(joined))
        try:
            normalized.relative_to(self.root)
        except ValueError as exc:
            raise VaultError(
                f"path {rel_str!r} escapes vault root {self.root!s}"
            ) from exc
        return normalized

    # ---- read/write ----------------------------------------------------

    def exists(self, rel: str | os.PathLike[str]) -> bool:
        return self._safe_join(rel).is_file()

    def read(self, rel: str | os.PathLike[str]) -> str | None:
        """Return the file contents, or ``None`` if the file does not exist."""
        path = self._safe_join(rel)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write(self, rel: str | os.PathLike[str], content: str) -> None:
        """Atomically write ``content`` to ``rel`` (overwrites if present).

        Writes to ``<target>.<pid>.<rand>.tmp`` in the same directory, then
        ``os.replace`` onto the target so a reader either sees the old file
        or the new file — never a half-written one.
        """
        path = self._safe_join(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            with suppress(OSError):
                os.unlink(tmp)
            raise

    def append(self, rel: str | os.PathLike[str], content: str) -> None:
        """Append ``content`` to ``rel`` (creates the file if missing).

        Not atomic relative to other writers — callers that need ordering
        across writers must hold the file's lock around the append.
        """
        path = self._safe_join(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(content)

    def delete(self, rel: str | os.PathLike[str]) -> bool:
        """Remove ``rel`` if it exists. Returns True if a file was removed."""
        path = self._safe_join(rel)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def list_files(
        self,
        rel_dir: str | os.PathLike[str],
        pattern: str = "*.md",
    ) -> list[Path]:
        """Return absolute paths matching ``pattern`` under ``rel_dir``.

        Returns an empty list if the directory does not exist. Sorted for
        deterministic iteration.
        """
        base = self._safe_join(rel_dir)
        if not base.is_dir():
            return []
        return sorted(base.rglob(pattern))

    # ---- locks ---------------------------------------------------------

    def _lock_path_for(self, rel: str | os.PathLike[str]) -> Path:
        """Return the lock-file path for ``rel``.

        The lock file lives under ``.vault-meta/locks/`` keyed by a slugified
        version of the relative path — separating locks from notes prevents
        odd-character clashes and keeps backups (which often skip dotfiles)
        from copying locks.
        """
        # ``self._safe_join`` validates the path; we then derive a flat key.
        self._safe_join(rel)  # validate, ignore result
        key = os.fspath(rel).replace("/", "_").replace("\\", "_")
        return self.root / _LOCK_SUBDIR / f"{key}.lock"

    @contextmanager
    def lock(
        self,
        rel: str | os.PathLike[str],
        *,
        blocking: bool = True,
    ) -> Iterator[None]:
        """Hold an advisory exclusive lock on ``rel`` for the with-block.

        Stale locks (mtime older than 120s AND owning PID dead) are silently
        cleared before the new lock is taken — a crashed writer does not
        wedge the vault.

        If ``blocking=False`` and the lock is held, raises :class:`VaultError`
        with errno semantics so the caller can retry.
        """
        lock_path = self._lock_path_for(rel)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _clear_if_stale(lock_path)

        # Open the lock file for writing; create if missing. We write our PID
        # so the staleness check can probe the owner.
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError as exc:
                raise VaultError(f"lock held: {rel!s}") from exc

            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --------------------------------------------------------------------------
# Module-private helpers
# --------------------------------------------------------------------------


def _clear_if_stale(lock_path: Path) -> None:
    """Remove ``lock_path`` if its owner is dead and mtime is past the floor."""
    try:
        st = lock_path.stat()
    except FileNotFoundError:
        return
    except OSError:
        return

    age = _now() - st.st_mtime
    if age < _STALE_LOCK_SECONDS:
        return

    pid = _read_pid(lock_path)
    if pid is not None and _pid_alive(pid):
        return

    with suppress(OSError):
        lock_path.unlink()


def _now() -> float:
    import time

    return time.time()


def _read_pid(lock_path: Path) -> int | None:
    try:
        return int(lock_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """Return True iff ``pid`` is a live process. POSIX-only."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack signal permission — still alive.
        return True
    except OSError as exc:
        # ESRCH means no such process; any other errno we conservatively
        # treat as "alive" so we don't yank a lock from a healthy writer.
        return exc.errno != errno.ESRCH
    return True
