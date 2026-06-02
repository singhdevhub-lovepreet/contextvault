"""Integration test: `contextvault capture --cwd PATH` via main()."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from contextvault.cli import main

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "transcripts" / "golden.jsonl"
)
FIXTURE_CWD = "/Users/lsingh/Desktop/experiments"
FIXTURE_WORKSPACE = "-Users-lsingh-Desktop-experiments"
FIXTURE_SID = "abcd1234-5678-9abc-def0-123456789abc"


@pytest.fixture
def isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Set up a vault + a sandboxed ~/.claude/projects/ + redirected config dir.

    Returns ``(vault_path, projects_root)``.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    vault = tmp_path / "vault"
    rc = main(["init", "--vault", str(vault)])
    assert rc == 0
    monkeypatch.setenv("VAULT_PATH", str(vault))

    # Spoof ~/.claude/projects/<encoded>/<sid>.jsonl by pointing HOME at tmp.
    home = tmp_path / "home"
    proj_dir = home / ".claude" / "projects" / FIXTURE_WORKSPACE
    proj_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, proj_dir / f"{FIXTURE_SID}.jsonl")
    monkeypatch.setenv("HOME", str(home))

    return vault, home / ".claude" / "projects"


def test_capture_creates_session_note(
    isolated_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    vault, _ = isolated_env
    capsys.readouterr()  # drain init output
    rc = main(["capture", "--cwd", FIXTURE_CWD])
    assert rc == 0
    out = capsys.readouterr().out
    assert "workspace=" in out
    assert "abcd1234" in out
    assert "note=workspaces/" in out

    sessions_dir = vault / "workspaces" / FIXTURE_WORKSPACE / "sessions"
    assert sessions_dir.is_dir()
    notes = list(sessions_dir.glob("*.md"))
    assert len(notes) == 1


def test_capture_no_vault_returns_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "nonexistent"))
    rc = main(["capture", "--cwd", "/some/path"])
    assert rc == 3


def test_capture_invalid_cwd_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    vault = tmp_path / "vault"
    rc = main(["init", "--vault", str(vault)])
    assert rc == 0
    monkeypatch.setenv("VAULT_PATH", str(vault))
    rc = main(["capture", "--cwd", "relative/path"])  # not absolute → encoder error
    assert rc == 2


def test_capture_idempotent_second_run(
    isolated_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _vault, _ = isolated_env
    capsys.readouterr()
    main(["capture", "--cwd", FIXTURE_CWD])
    capsys.readouterr()
    rc = main(["capture", "--cwd", FIXTURE_CWD])
    assert rc == 0
    out = capsys.readouterr().out
    assert "new_entries=0" in out
