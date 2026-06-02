"""Tests for contextvault.vault — atomic I/O + advisory locks + path safety."""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from contextvault.vault import Vault, VaultError


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path)


class TestPathSafety:
    def test_rejects_absolute(self, vault: Vault) -> None:
        with pytest.raises(VaultError, match="relative"):
            vault.read("/etc/passwd")

    def test_rejects_traversal(self, vault: Vault) -> None:
        with pytest.raises(VaultError, match="escapes vault root"):
            vault.read("../outside")

    def test_rejects_null_byte(self, vault: Vault) -> None:
        with pytest.raises(VaultError, match="null"):
            vault.read("foo\x00bar")

    def test_rejects_empty(self, vault: Vault) -> None:
        with pytest.raises(VaultError):
            vault.read("")

    def test_traversal_via_subdir_clamped(self, vault: Vault) -> None:
        # ``a/b/../../c`` normalizes to ``c`` which is inside — allowed.
        vault.write("a/b/../../c", "ok")
        assert vault.read("c") == "ok"

    def test_traversal_one_step_too_far_rejected(self, vault: Vault) -> None:
        with pytest.raises(VaultError, match="escapes"):
            vault.write("a/../../escape", "no")


class TestReadWrite:
    def test_write_then_read(self, vault: Vault) -> None:
        vault.write("note.md", "hello\n")
        assert vault.read("note.md") == "hello\n"

    def test_read_missing_returns_none(self, vault: Vault) -> None:
        assert vault.read("missing.md") is None

    def test_write_creates_intermediate_dirs(self, vault: Vault) -> None:
        vault.write("a/b/c/note.md", "x")
        assert vault.read("a/b/c/note.md") == "x"

    def test_write_is_atomic_no_temp_left_on_success(
        self, vault: Vault, tmp_path: Path
    ) -> None:
        vault.write("note.md", "ok")
        leftovers = list(tmp_path.glob(".note.md.*.tmp"))
        assert leftovers == []

    def test_write_overwrites(self, vault: Vault) -> None:
        vault.write("note.md", "v1")
        vault.write("note.md", "v2")
        assert vault.read("note.md") == "v2"

    def test_append_creates_file(self, vault: Vault) -> None:
        vault.append("log.md", "line1\n")
        vault.append("log.md", "line2\n")
        assert vault.read("log.md") == "line1\nline2\n"

    def test_delete_returns_true_when_removed(self, vault: Vault) -> None:
        vault.write("doomed.md", "x")
        assert vault.delete("doomed.md") is True
        assert vault.read("doomed.md") is None

    def test_delete_returns_false_when_absent(self, vault: Vault) -> None:
        assert vault.delete("never.md") is False

    def test_exists(self, vault: Vault) -> None:
        assert vault.exists("foo.md") is False
        vault.write("foo.md", "x")
        assert vault.exists("foo.md") is True

    def test_unicode_content(self, vault: Vault) -> None:
        vault.write("note.md", "café résumé 中文\n")
        assert vault.read("note.md") == "café résumé 中文\n"


class TestListFiles:
    def test_returns_empty_for_missing_dir(self, vault: Vault) -> None:
        assert vault.list_files("missing") == []

    def test_returns_sorted_matches(self, vault: Vault) -> None:
        vault.write("notes/b.md", "")
        vault.write("notes/a.md", "")
        vault.write("notes/c.md", "")
        names = [p.name for p in vault.list_files("notes")]
        assert names == ["a.md", "b.md", "c.md"]

    def test_recursive_glob(self, vault: Vault) -> None:
        vault.write("notes/a.md", "")
        vault.write("notes/sub/b.md", "")
        names = sorted(p.name for p in vault.list_files("notes"))
        assert names == ["a.md", "b.md"]

    def test_pattern_filter(self, vault: Vault) -> None:
        vault.write("notes/a.md", "")
        vault.write("notes/b.txt", "")
        names = [p.name for p in vault.list_files("notes", "*.md")]
        assert names == ["a.md"]


class TestLock:
    def test_basic_lock_acquire_release(self, vault: Vault) -> None:
        with vault.lock("note.md"):
            pass  # acquired and released without raising

    def test_non_blocking_fails_when_held(self, vault: Vault) -> None:
        with vault.lock("note.md"), pytest.raises(VaultError, match="lock held"):
            _try_non_blocking_lock(vault, "note.md")

    def test_lock_path_inside_meta_dir(self, vault: Vault, tmp_path: Path) -> None:
        with vault.lock("notes/foo.md"):
            locks = list((tmp_path / ".vault-meta" / "locks").iterdir())
            assert len(locks) == 1
            assert locks[0].suffix == ".lock"

    def test_stale_lock_is_cleared(self, vault: Vault, tmp_path: Path) -> None:
        # Create a lock file by a fake-dead PID with old mtime
        lock_dir = tmp_path / ".vault-meta" / "locks"
        lock_dir.mkdir(parents=True)
        stale = lock_dir / "note.md.lock"
        stale.write_text("999999\n")  # PID extremely unlikely to exist
        old = time.time() - 600  # 10 minutes ago
        os.utime(stale, (old, old))

        # New lock should clear the stale one and acquire cleanly
        with vault.lock("note.md"):
            pass

    def test_fresh_lock_is_not_cleared(self, vault: Vault, tmp_path: Path) -> None:
        # A lock file from a live PID (us) with fresh mtime must NOT be
        # cleared, even though we created it manually.
        lock_dir = tmp_path / ".vault-meta" / "locks"
        lock_dir.mkdir(parents=True)
        live = lock_dir / "note.md.lock"
        live.write_text(f"{os.getpid()}\n")
        # mtime is now (fresh). Acquire should succeed because nobody holds
        # a flock on the fd — but the file should not have been unlinked.
        with vault.lock("note.md"):
            assert live.exists()


def _try_non_blocking_lock(vault: Vault, rel: str) -> None:
    with vault.lock(rel, blocking=False):
        pass


# ----- Integration: a worker process holds the lock -----------------------


def _worker_holds_lock(
    vault_root: str, rel: str, ready_file: str, release_file: str
) -> None:
    """Spawned child: acquire the lock, signal the parent via ``ready_file``,
    then poll for ``release_file`` before releasing the lock.

    Synchronizing via files avoids a sleep race with the parent on slow
    systems (esp. macOS spawn-start).
    """
    v = Vault(vault_root)
    with v.lock(rel):
        Path(ready_file).touch()
        deadline = time.time() + 5.0
        while not Path(release_file).exists() and time.time() < deadline:
            time.sleep(0.02)


@pytest.mark.integration
def test_lock_serializes_across_processes(vault: Vault, tmp_path: Path) -> None:
    """Two processes can't hold the same lock simultaneously."""
    rel = "shared.md"
    ready = tmp_path / "_ready"
    release = tmp_path / "_release"
    ctx = multiprocessing.get_context("spawn")  # explicit; matches macOS default
    proc = ctx.Process(
        target=_worker_holds_lock,
        args=(str(tmp_path), rel, str(ready), str(release)),
    )
    proc.start()
    try:
        deadline = time.time() + 5.0
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "worker did not signal lock acquisition in time"

        with pytest.raises(VaultError, match="lock held"):
            _try_non_blocking_lock(vault, rel)
    finally:
        release.touch()
        proc.join(timeout=5.0)
        assert proc.exitcode == 0, f"worker exited with {proc.exitcode}"

    # After the child exits the lock is releasable
    with vault.lock(rel):
        pass
