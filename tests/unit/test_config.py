"""Tests for contextvault.config — paths, layering, vault bootstrap, token."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from contextvault import config


@pytest.fixture(autouse=True)
def isolate_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.config`` to a tmp dir so tests don't touch the real one."""
    fake_xdg = tmp_path / "xdg_config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))
    monkeypatch.delenv("VAULT_PATH", raising=False)
    return fake_xdg


class TestPaths:
    def test_config_dir_uses_xdg(self, isolate_config_home: Path) -> None:
        assert config.config_dir() == isolate_config_home / "contextvault"

    def test_config_path_under_config_dir(self, isolate_config_home: Path) -> None:
        assert config.config_path() == isolate_config_home / "contextvault" / "config.toml"

    def test_token_path_under_config_dir(self, isolate_config_home: Path) -> None:
        assert config.token_path() == isolate_config_home / "contextvault" / "token"


class TestResolveVaultPath:
    def test_cli_override_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", "/from/env")
        cfg_path = config.config_path()
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text('[vault]\npath = "/from/file"\n', encoding="utf-8")
        result = config.resolve_vault_path(str(tmp_path / "from-cli"))
        assert result == (tmp_path / "from-cli").absolute()

    def test_env_beats_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_path = config.config_path()
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text('[vault]\npath = "/from/file"\n', encoding="utf-8")
        monkeypatch.setenv("VAULT_PATH", str(tmp_path / "from-env"))
        assert config.resolve_vault_path(None) == (tmp_path / "from-env").absolute()

    def test_file_beats_default(self, tmp_path: Path) -> None:
        cfg_path = config.config_path()
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text(
            f'[vault]\npath = "{tmp_path / "from-file"!s}"\n', encoding="utf-8"
        )
        assert config.resolve_vault_path(None) == (tmp_path / "from-file").absolute()

    def test_default_when_nothing_set(self) -> None:
        assert config.resolve_vault_path(None) == config.DEFAULT_VAULT_PATH.absolute()


class TestBootstrapVault:
    def test_creates_subtree(self, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        config.bootstrap_vault(vault)
        assert (vault / "hot.md").is_file()
        assert (vault / "index.md").is_file()
        assert (vault / "workspaces").is_dir()
        assert (vault / "entities").is_dir()
        assert (vault / "concepts").is_dir()
        assert (vault / ".vault-meta" / "locks").is_dir()
        assert (vault / ".vault-meta" / "bm25").is_dir()
        assert (vault / ".vault-meta" / "chunks").is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        vault = tmp_path / "v"
        config.bootstrap_vault(vault)
        # Write user content into hot.md
        (vault / "hot.md").write_text("USER EDITED\n", encoding="utf-8")
        config.bootstrap_vault(vault)
        # Re-bootstrap must NOT overwrite user content
        assert (vault / "hot.md").read_text(encoding="utf-8") == "USER EDITED\n"


class TestWriteDefaultConfig:
    def test_writes_when_missing(self, tmp_path: Path) -> None:
        path = config.write_default_config(tmp_path / "vault")
        assert path.is_file()
        assert 'path = "' in path.read_text(encoding="utf-8")

    def test_idempotent_preserves_user_edit(self, tmp_path: Path) -> None:
        first = config.write_default_config(tmp_path / "vault")
        first.write_text("# USER EDITED\n[vault]\npath = '/custom'\n", encoding="utf-8")
        second = config.write_default_config(tmp_path / "vault")
        assert second == first
        assert "USER EDITED" in second.read_text(encoding="utf-8")


class TestGenerateToken:
    def test_creates_token_file(self) -> None:
        path = config.generate_token()
        assert path.is_file()
        token = path.read_text(encoding="utf-8")
        assert len(token) >= 32

    def test_token_has_0600_perms(self) -> None:
        path = config.generate_token()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"token perms must be 0600, got {oct(mode)}"

    def test_idempotent_by_default(self) -> None:
        first = config.generate_token().read_text()
        second = config.generate_token().read_text()
        assert first == second

    def test_force_rotates(self) -> None:
        first = config.generate_token().read_text()
        second = config.generate_token(force=True).read_text()
        assert first != second
