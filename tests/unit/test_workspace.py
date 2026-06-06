"""Tests for contextvault.workspace — cwd encoding + traversal hardening."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from contextvault.workspace import (
    WorkspaceError,
    _strip_root,
    current,
    encode,
    is_valid_id,
    resolve,
)


class TestEncode:
    def test_macos_path(self) -> None:
        assert encode("/Users/lsingh/Desktop/experiments") == (
            "-Users-lsingh-Desktop-experiments"
        )

    def test_linux_path(self) -> None:
        assert encode("/home/user/code/project") == "-home-user-code-project"

    def test_path_with_hyphens_in_name(self) -> None:
        # Lossy but deterministic — that's by design.
        assert encode("/Users/lsingh/my-project") == "-Users-lsingh-my-project"

    def test_path_with_spaces(self) -> None:
        assert encode("/Users/lsingh/My Drive/work") == "-Users-lsingh-My Drive-work"

    def test_root(self) -> None:
        assert encode("/") == "-"

    def test_trailing_slash_normalized(self) -> None:
        assert encode("/Users/lsingh/foo/") == "-Users-lsingh-foo"

    def test_dot_dot_resolved(self, tmp_path: Path) -> None:
        # /Users/lsingh/foo/../bar  →  /Users/lsingh/bar
        sub = tmp_path / "sub" / ".." / "other"
        # Resolve before comparing — tmp_path is itself canonical on darwin.
        expected = encode((tmp_path / "other").as_posix())
        assert encode(str(sub)) == expected

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(WorkspaceError, match="absolute"):
            encode("relative/path")

    def test_empty_rejected(self) -> None:
        with pytest.raises(WorkspaceError):
            encode("")

    def test_null_byte_rejected(self) -> None:
        with pytest.raises(WorkspaceError, match="null"):
            encode("/Users/lsingh/foo\x00bar")

    def test_tilde_expanded(self) -> None:
        # Tilde is expanded so callers don't have to.
        home = os.path.expanduser("~")
        assert encode("~/projects") == encode(f"{home}/projects")

    def test_unicode_preserved(self) -> None:
        assert encode("/Users/lsingh/café/résumé") == "-Users-lsingh-café-résumé"


class TestIsValidId:
    @pytest.mark.parametrize(
        "good",
        [
            "-Users-lsingh-Desktop-experiments",
            "-home-user-code",
            "-",
            "-Users-lsingh-my-project",
            "-Users-lsingh-My Drive-work",
            "-Users-lsingh-café-résumé",
        ],
    )
    def test_accepts_legal_ids(self, good: str) -> None:
        assert is_valid_id(good) is True

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "no-leading-hyphen",
            "/absolute-path",
            "..",
            "-..",
            "-foo-..-bar",
            "-foo/bar",
            "-foo\\bar",
            "-foo\x00bar",
            "-foo\nbar",
        ],
    )
    def test_rejects_unsafe_ids(self, bad: str) -> None:
        assert is_valid_id(bad) is False

    def test_rejects_non_string(self) -> None:
        assert is_valid_id(None) is False  # type: ignore[arg-type]
        assert is_valid_id(42) is False  # type: ignore[arg-type]


class TestResolve:
    def test_returns_workspaces_subpath(self, tmp_path: Path) -> None:
        ws = resolve(tmp_path, "/Users/lsingh/foo")
        assert ws == (tmp_path / "workspaces" / "-Users-lsingh-foo")

    def test_uses_vault_root_expansion(self, tmp_path: Path) -> None:
        ws = resolve(str(tmp_path), "/a/b")
        assert ws.is_absolute()
        assert ws.name == "-a-b"

    def test_rejects_traversal_via_cwd(self, tmp_path: Path) -> None:
        # encode() resolves .. before we get here, so this should still land
        # safely under workspaces/.
        ws = resolve(tmp_path, "/a/b/../c")
        assert (tmp_path / "workspaces") in ws.parents

    def test_rejects_empty_cwd(self, tmp_path: Path) -> None:
        with pytest.raises(WorkspaceError):
            resolve(tmp_path, "")


class TestEncodeWindows:
    """Test _strip_root with synthetic tuples — no platform mocking needed."""

    def test_posix_root(self) -> None:
        assert _strip_root(("/", "Users", "foo")) == ("Users", "foo")

    def test_windows_drive(self) -> None:
        assert _strip_root(("C:\\", "Users", "foo")) == ("Users", "foo")

    def test_windows_drive_d(self) -> None:
        assert _strip_root(("D:\\", "Projects", "bar")) == ("Projects", "bar")

    def test_empty_parts_raises(self) -> None:
        with pytest.raises(WorkspaceError, match="no parts"):
            _strip_root(())

    def test_invalid_root_raises(self) -> None:
        with pytest.raises(WorkspaceError, match="filesystem root"):
            _strip_root(("relative", "path"))

    def test_posix_root_only(self) -> None:
        assert _strip_root(("/",)) == ()


class TestCurrent:
    def test_uses_pwd_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PWD", "/Users/lsingh/foo")
        ws = current(tmp_path)
        assert ws.name == "-Users-lsingh-foo"

    def test_falls_back_to_getcwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PWD", raising=False)
        # tmp_path is a usable cwd
        monkeypatch.chdir(tmp_path)
        ws = current(tmp_path)
        assert (tmp_path / "workspaces") in ws.parents
