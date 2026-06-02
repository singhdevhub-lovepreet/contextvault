"""Integration tests for the Phase-4 CLI additions: `hot`, `ingest`, `save`,
plus a smoke test that the Phase 5 Obsidian plugin tree is well-formed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextvault.cli import main


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    v = tmp_path / "vault"
    assert main(["init", "--vault", str(v)]) == 0
    monkeypatch.setenv("VAULT_PATH", str(v))
    return v


class TestHot:
    def test_global_hot_printed_when_workspace_missing(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PWD", "/no/such/workspace")
        capsys.readouterr()
        rc = main(["hot"])
        assert rc == 0
        out = capsys.readouterr().out
        # Falls back to vault-root hot.md
        assert "Hot cache" in out

    def test_workspace_hot_printed_when_present(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (vault / "workspaces" / "-tmp-proj").mkdir(parents=True)
        (vault / "workspaces" / "-tmp-proj" / "hot.md").write_text("WORKSPACE HOT\n")
        monkeypatch.setenv("PWD", "/tmp/proj")
        capsys.readouterr()
        rc = main(["hot"])
        assert rc == 0
        assert "WORKSPACE HOT" in capsys.readouterr().out

    def test_invalid_workspace_id_returns_2(
        self, vault: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        capsys.readouterr()
        rc = main(["hot", "--workspace", "../escape"])
        assert rc == 2


class TestIngest:
    def test_local_file(
        self,
        vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        src = tmp_path / "src.md"
        src.write_text("# Source content\n\nbody.\n")
        monkeypatch.setenv("PWD", "/tmp/proj")
        capsys.readouterr()
        rc = main(["ingest", str(src)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "ingested →" in out

    def test_missing_file_returns_3(
        self, vault: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["ingest", "/nonexistent/path.md"])
        assert rc == 3

    def test_url_returns_64(
        self, vault: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["ingest", "https://example.com/x.md"])
        assert rc == 64


class TestSave:
    def test_writes_note_from_stdin(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            __import__("io").StringIO("This is the body from stdin.\n"),
        )
        monkeypatch.setenv("PWD", "/tmp/proj")
        capsys.readouterr()
        rc = main(["save", "--title", "Quick Note", "--type", "note"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "saved →" in out
        # Confirm written
        note_path = vault / "workspaces" / "-tmp-proj" / "notes" / "Quick-Note.md"
        assert note_path.is_file()
        assert "stdin" in note_path.read_text()

    def test_empty_stdin_returns_2(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
        rc = main(["save", "--title", "t", "--type", "note"])
        assert rc == 2


# ---------------------------------------------------------------------------
# Obsidian plugin smoke — Phase 5 deliverable
# ---------------------------------------------------------------------------


PLUGIN_DIR = Path(__file__).resolve().parents[2] / "obsidian-plugin"


class TestObsidianPluginAssets:
    def test_manifest_valid(self) -> None:
        manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text())
        for key in ("id", "name", "version", "minAppVersion", "description", "author"):
            assert key in manifest, f"manifest missing {key}"
        assert manifest["id"] == "contextvault"
        assert manifest["isDesktopOnly"] is True

    def test_package_json_has_build_script(self) -> None:
        pkg = json.loads((PLUGIN_DIR / "package.json").read_text())
        assert "scripts" in pkg
        assert "build" in pkg["scripts"]
        assert "esbuild" in pkg["scripts"]["build"]

    def test_main_ts_exists_and_is_typescript(self) -> None:
        main_ts = PLUGIN_DIR / "main.ts"
        content = main_ts.read_text()
        assert "export default class" in content
        assert "extends Plugin" in content
        # Plugin exposes the three commands declared in docs
        assert "open-current-workspace-hot" in content
        assert "open-workspace-map" in content
        assert "list-workspaces" in content

    def test_styles_css_exists(self) -> None:
        assert (PLUGIN_DIR / "styles.css").is_file()

    def test_readme_exists(self) -> None:
        assert (PLUGIN_DIR / "README.md").is_file()

    def test_tsconfig_exists(self) -> None:
        tsconfig = json.loads((PLUGIN_DIR / "tsconfig.json").read_text())
        assert tsconfig["compilerOptions"]["strict"] is True
