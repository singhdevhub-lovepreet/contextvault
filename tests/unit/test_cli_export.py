"""Tests for `contextvault export --workspace X`."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from contextvault import config
from contextvault.cli import main
from contextvault.vault import Vault


@pytest.fixture
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Bootstrap a vault with a workspace containing some notes."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    vault = tmp_path / "vault"
    config.bootstrap_vault(vault)
    monkeypatch.setenv("VAULT_PATH", str(vault))

    v = Vault(vault)
    ws_id = "-Users-test-project"
    v.write(f"workspaces/{ws_id}/hot.md", "# Hot cache\n")
    v.write(f"workspaces/{ws_id}/sessions/2026-01-01-abcd1234.md", "---\ntype: session\n---\n")
    v.write(f"workspaces/{ws_id}/log.md", "- session log\n")

    return vault


class TestExport:
    def test_creates_zip(
        self, isolated_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        rc = main(["export", "--workspace=-Users-test-project"])
        assert rc == 0
        zip_path = tmp_path / "Users-test-project.zip"
        assert zip_path.is_file()

    def test_zip_contains_manifest(
        self, isolated_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["export", "--workspace=-Users-test-project"])
        zip_path = tmp_path / "Users-test-project.zip"
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any("manifest.json" in n for n in names)

    def test_manifest_has_fields(
        self, isolated_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["export", "--workspace=-Users-test-project"])
        zip_path = tmp_path / "Users-test-project.zip"
        with zipfile.ZipFile(zip_path) as zf:
            manifest_name = next(n for n in zf.namelist() if "manifest.json" in n)
            manifest = json.loads(zf.read(manifest_name))
            assert manifest["workspace_id"] == "-Users-test-project"
            assert "exported_at" in manifest
            assert "file_count" in manifest
            assert manifest["file_count"] >= 3  # hot.md, session note, log.md

    def test_custom_output_path(
        self, isolated_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        custom = str(tmp_path / "custom-export.zip")
        rc = main(["export", "--workspace=-Users-test-project", "--output", custom])
        assert rc == 0
        assert Path(custom).is_file()

    def test_invalid_workspace_returns_2(
        self, isolated_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc = main(["export", "--workspace", "no-leading-hyphen"])
        assert rc == 2

    def test_missing_workspace_returns_2(
        self, isolated_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc = main(["export", "--workspace=-nonexistent-workspace"])
        assert rc == 2
