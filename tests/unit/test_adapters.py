"""Tests for contextvault.adapters — Claude Code hooks install + Cursor + Hermes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from contextvault import adapters


class TestInstallClaudeCode:
    def test_creates_settings_file_when_missing(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        out = adapters.install_claude_code(settings_path=settings)
        assert settings.is_file()
        data = json.loads(settings.read_text())
        assert "hooks" in data
        # SessionStart + Stop are present. UserPromptSubmit was removed in
        # v0.1.0 — its matcher fired on every prompt and the Stop hook
        # already covers the pre-/clear capture window.
        events = set(data["hooks"].keys())
        assert {"SessionStart", "Stop"}.issubset(events)
        # MCP server registered
        assert data["mcpServers"]["contextvault"]["command"] == "contextvault"
        assert any("Stop hook" in line for line in out)

    def test_merges_with_existing_user_hooks(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "echo user-hook"}
                                ],
                            }
                        ]
                    },
                    "mcpServers": {
                        "myserver": {"command": "/usr/bin/myserver"}
                    },
                }
            )
        )
        adapters.install_claude_code(settings_path=settings)
        data = json.loads(settings.read_text())
        stop = data["hooks"]["Stop"]
        # User's echo hook preserved, our contextvault hook appended
        commands = [json.dumps(h) for h in stop]
        assert any("echo user-hook" in c for c in commands)
        assert any("contextvault" in c for c in commands)
        # User's other MCP server preserved
        assert "myserver" in data["mcpServers"]
        assert "contextvault" in data["mcpServers"]

    def test_idempotent_does_not_duplicate(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        adapters.install_claude_code(settings_path=settings)
        adapters.install_claude_code(settings_path=settings)
        data = json.loads(settings.read_text())
        for event in ("Stop", "SessionStart"):
            cv_count = sum(
                1
                for entry in data["hooks"].get(event, [])
                if "contextvault" in json.dumps(entry)
            )
            assert cv_count == 1, f"{event} got {cv_count} contextvault entries"

    def test_rejects_non_object_root(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text('"a string, not an object"')
        import pytest

        with pytest.raises(RuntimeError, match="not a JSON object"):
            adapters.install_claude_code(settings_path=settings)


class TestRemoveClaudeCode:
    def test_strips_contextvault_entries_only(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {"hooks": [{"command": "echo user"}]},
                            {"hooks": [{"command": "contextvault capture"}]},
                        ]
                    },
                    "mcpServers": {
                        "contextvault": {"command": "contextvault"},
                        "other": {"command": "/bin/other"},
                    },
                }
            )
        )
        adapters.remove_claude_code(settings_path=settings)
        data = json.loads(settings.read_text())
        commands = [json.dumps(h) for h in data["hooks"]["Stop"]]
        assert any("echo user" in c for c in commands)
        assert not any("contextvault" in c for c in commands)
        assert "contextvault" not in data["mcpServers"]
        assert "other" in data["mcpServers"]

    def test_missing_file_safe(self, tmp_path: Path) -> None:
        out = adapters.remove_claude_code(settings_path=tmp_path / "absent.json")
        assert any("nothing to remove" in line for line in out)


class TestInstallCursor:
    def test_prints_snippet(self) -> None:
        out = adapters.install_cursor()
        joined = "\n".join(out)
        assert "~/.cursor/mcp.json" in joined
        assert '"contextvault"' in joined
        assert '"serve"' in joined
        assert '"--mcp"' in joined


class TestInstallHermes:
    def test_creates_plist_file(self, tmp_path: Path) -> None:
        plist = tmp_path / "contextvault.plist"
        out = adapters.install_hermes(plist_path=plist)
        assert plist.is_file()
        content = plist.read_text()
        assert "<string>serve</string>" in content
        assert "<string>--http</string>" in content
        assert any("Hermes" in line for line in out)

    def test_plist_contains_contextvault_label(self, tmp_path: Path) -> None:
        plist = tmp_path / "contextvault.plist"
        adapters.install_hermes(plist_path=plist)
        content = plist.read_text()
        assert "<string>contextvault</string>" in content
        assert "<key>KeepAlive</key><true/>" in content

    def test_output_includes_system_prompt(self, tmp_path: Path) -> None:
        plist = tmp_path / "contextvault.plist"
        out = adapters.install_hermes(plist_path=plist)
        joined = "\n".join(out)
        assert "/recall?" in joined
        assert "/recent_sessions?" in joined
        assert "/list_workspaces" in joined
        assert "Authorization: Bearer" in joined

    def test_idempotent_overwrites(self, tmp_path: Path) -> None:
        plist = tmp_path / "contextvault.plist"
        adapters.install_hermes(plist_path=plist)
        adapters.install_hermes(plist_path=plist)
        assert plist.is_file()
        # Should still be valid — not doubled
        content = plist.read_text()
        assert content.count("<key>Label</key>") == 1


class TestRemoveHermes:
    @mock.patch("contextvault.adapters.subprocess.run")
    def test_removes_existing_plist(self, mock_run: mock.Mock, tmp_path: Path) -> None:
        plist = tmp_path / "contextvault.plist"
        plist.write_text("<plist>stub</plist>")
        out = adapters.remove_hermes(plist_path=plist)
        assert not plist.exists()
        assert any("removed" in line for line in out)
        mock_run.assert_called_once()

    def test_missing_plist_safe(self, tmp_path: Path) -> None:
        out = adapters.remove_hermes(plist_path=tmp_path / "absent.plist")
        assert any("nothing to remove" in line for line in out)
