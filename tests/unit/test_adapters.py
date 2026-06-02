"""Tests for contextvault.adapters — Claude Code hooks install + Cursor snippet."""

from __future__ import annotations

import json
from pathlib import Path

from contextvault import adapters


class TestInstallClaudeCode:
    def test_creates_settings_file_when_missing(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        out = adapters.install_claude_code(settings_path=settings)
        assert settings.is_file()
        data = json.loads(settings.read_text())
        assert "hooks" in data
        # All three lifecycle hooks present
        events = set(data["hooks"].keys())
        assert {"SessionStart", "UserPromptSubmit", "Stop"}.issubset(events)
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
        for event in ("Stop", "UserPromptSubmit", "SessionStart"):
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
