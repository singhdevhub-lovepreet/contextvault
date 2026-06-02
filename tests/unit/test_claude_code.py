"""Tests for contextvault.capture.claude_code — JSONL transcript reader."""

from __future__ import annotations

from pathlib import Path

from contextvault.capture.claude_code import (
    AssistantText,
    AssistantThinking,
    ToolResult,
    ToolUse,
    UserMessage,
    find_transcript,
    read_session_meta,
    read_transcript,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts" / "golden.jsonl"


def test_fixture_exists() -> None:
    assert FIXTURE.is_file()


class TestReadTranscript:
    def test_extracts_user_messages(self) -> None:
        users = [r for r in read_transcript(FIXTURE) if isinstance(r, UserMessage)]
        # 2 string-content user messages (u-001, u-004) — u-002 and u-003 carry
        # tool_result list-content and become ToolResult records instead.
        texts = [u.text for u in users]
        assert any("OAuth2 token rotation" in t for t in texts)
        assert any("TODO: add refresh-token" in t for t in texts)
        # The tool_result-wrapping user messages should NOT appear as UserMessage
        assert all("AssertionError" not in t for t in texts)

    def test_extracts_assistant_text_and_thinking(self) -> None:
        records = list(read_transcript(FIXTURE))
        texts = [r.text for r in records if isinstance(r, AssistantText)]
        thinkings = [r.text for r in records if isinstance(r, AssistantThinking)]
        assert any("PyJWT" in t for t in texts)
        assert any("HS256" in t for t in texts)
        assert any("refactor authentication" in t for t in thinkings)

    def test_extracts_tool_uses(self) -> None:
        records = list(read_transcript(FIXTURE))
        tool_uses = [r for r in records if isinstance(r, ToolUse)]
        names = [t.name for t in tool_uses]
        assert names == ["Read", "Write", "Bash", "Edit"]
        # First Read targets the right file
        first_read = tool_uses[0]
        assert first_read.input["file_path"].endswith("login.py")
        # Bash command captured
        bash = next(t for t in tool_uses if t.name == "Bash")
        assert bash.input["command"] == "pytest tests/test_auth.py -v"

    def test_extracts_tool_results_including_errors(self) -> None:
        records = list(read_transcript(FIXTURE))
        results = [r for r in records if isinstance(r, ToolResult)]
        # u-002 (success) and u-003 (error) carry tool_results
        assert len(results) == 2
        errored = [r for r in results if r.is_error]
        assert len(errored) == 1
        assert "AssertionError" in errored[0].output

    def test_skips_non_content_entry_types(self) -> None:
        # permission-mode and file-history-snapshot lines must not produce records
        records = list(read_transcript(FIXTURE))
        # 2 user + 4 assistant text/thinking + 4 tool_use + 2 tool_result = 12
        assert len(records) == 12

    def test_after_uuid_resumes(self) -> None:
        # Skip up through a-002 — should resume at a-003 (which has the Bash tool_use)
        records = list(read_transcript(FIXTURE, after_uuid="a-002"))
        kinds = [type(r).__name__ for r in records]
        # First record after skip should be the Bash tool_use from a-003
        first_tool = next(r for r in records if isinstance(r, ToolUse))
        assert first_tool.name == "Bash"
        # The Write tool_use from a-002 must not appear
        assert not any(
            isinstance(r, ToolUse) and r.name == "Write" for r in records
        )
        assert "ToolUse" in kinds and "AssistantText" in kinds

    def test_after_uuid_missing_skips_everything(self) -> None:
        records = list(read_transcript(FIXTURE, after_uuid="does-not-exist"))
        # The uuid never matches → we keep skipping → result is empty
        assert records == []

    def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.jsonl"
        p.write_text(
            "this is not json\n"
            + FIXTURE.read_text().splitlines()[1]
            + "\n",
            encoding="utf-8",
        )
        records = list(read_transcript(p))
        assert len(records) == 1


class TestReadSessionMeta:
    def test_extracts_meta(self) -> None:
        meta = read_session_meta(FIXTURE)
        assert meta is not None
        assert meta.session_id == "abcd1234-5678-9abc-def0-123456789abc"
        assert meta.cwd == "/Users/lsingh/Desktop/experiments"
        assert meta.started.startswith("2026-06-02T10:00:00")
        assert meta.last_updated.startswith("2026-06-02T10:00:40")
        assert meta.last_uuid == "u-004"
        assert meta.version == "2.1.140"
        assert meta.entry_count == 10

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        assert read_session_meta(p) is None


class TestFindTranscript:
    def test_finds_by_session_id(self, tmp_path: Path) -> None:
        projects = tmp_path / "projects"
        ws_dir = projects / "-Users-foo-bar"
        ws_dir.mkdir(parents=True)
        target = ws_dir / "sid-123.jsonl"
        target.write_text("{}\n", encoding="utf-8")
        path = find_transcript("-Users-foo-bar", session_id="sid-123", projects_root=projects)
        assert path == target

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        assert find_transcript("-Users-missing", projects_root=tmp_path) is None

    def test_picks_newest_when_no_session_id(self, tmp_path: Path) -> None:
        import os
        import time
        ws_dir = tmp_path / "projects" / "-Users-foo"
        ws_dir.mkdir(parents=True)
        old = ws_dir / "old.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        time.sleep(0.01)
        new = ws_dir / "new.jsonl"
        new.write_text("{}\n", encoding="utf-8")
        # Make 'new' is the newest by mtime
        os.utime(old, (time.time() - 100, time.time() - 100))
        path = find_transcript("-Users-foo", projects_root=tmp_path / "projects")
        assert path == new
