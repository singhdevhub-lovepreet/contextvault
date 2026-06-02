"""Integration tests: `contextvault init` and `contextvault recall` via main()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextvault.cli import main


@pytest.fixture(autouse=True)
def isolate_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect ~/.config and clear $VAULT_PATH so tests don't touch the real home."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("VAULT_PATH", raising=False)


class TestInit:
    def test_init_creates_vault(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        vault = tmp_path / "myvault"
        rc = main(["init", "--vault", str(vault)])
        assert rc == 0
        assert (vault / "hot.md").is_file()
        assert (vault / "index.md").is_file()
        assert (vault / "workspaces").is_dir()
        assert (vault / "entities").is_dir()
        assert (vault / ".vault-meta" / "locks").is_dir()
        out = capsys.readouterr().out
        assert "vault:" in out and str(vault) in out
        assert "config:" in out
        assert "token:" in out

    def test_init_idempotent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = tmp_path / "v"
        assert main(["init", "--vault", str(vault)]) == 0
        # User edits hot.md
        (vault / "hot.md").write_text("USER\n", encoding="utf-8")
        capsys.readouterr()  # clear
        # Re-init must not overwrite
        assert main(["init", "--vault", str(vault)]) == 0
        assert (vault / "hot.md").read_text(encoding="utf-8") == "USER\n"


class TestRecall:
    def _setup_vault(self, root: Path) -> None:
        """Run init then drop a session note that the recall test queries."""
        rc = main(["init", "--vault", str(root)])
        assert rc == 0
        ws_dir = root / "workspaces" / "-Users-test-proj" / "sessions"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "2026-06-02-auth.md").write_text(
            "---\ntype: session\n---\n\nrefactor the authentication module\n",
            encoding="utf-8",
        )

    def test_recall_workspace_scope_finds_note(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        vault = tmp_path / "vault"
        self._setup_vault(vault)
        monkeypatch.setenv("VAULT_PATH", str(vault))
        capsys.readouterr()  # drain init's stdout

        rc = main(["recall", "authentication", "--cwd", "/Users/test/proj"])
        assert rc == 0
        out = capsys.readouterr().out
        hits = json.loads(out)
        assert any("authentication" in h["preview"].lower() for h in hits)
        # Workspace scope filtered to the test cwd
        assert all(
            h["workspace"] in (None, "-Users-test-proj") for h in hits
        )

    def test_recall_global_scope_returns_all(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        vault = tmp_path / "vault"
        self._setup_vault(vault)
        monkeypatch.setenv("VAULT_PATH", str(vault))
        capsys.readouterr()  # drain init's stdout

        rc = main(["recall", "authentication", "--scope", "global"])
        assert rc == 0
        hits = json.loads(capsys.readouterr().out)
        assert hits, "global recall should find the session note"

    def test_recall_missing_vault_returns_3(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("VAULT_PATH", str(tmp_path / "nonexistent"))
        rc = main(["recall", "anything"])
        assert rc == 3

    def test_recall_no_results_returns_empty_list(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        vault = tmp_path / "vault"
        self._setup_vault(vault)
        monkeypatch.setenv("VAULT_PATH", str(vault))
        capsys.readouterr()  # drain init's stdout

        rc = main(["recall", "nonexistent-term-xyzzy", "--scope", "global"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out) == []
