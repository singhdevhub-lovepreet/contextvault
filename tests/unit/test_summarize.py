"""Tests for contextvault.capture.summarize — extractive summarizer."""

from __future__ import annotations

from pathlib import Path

from contextvault.capture.claude_code import read_transcript
from contextvault.capture.summarize import summarize

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts" / "golden.jsonl"


def _summarize_fixture():
    return summarize(read_transcript(FIXTURE))


class TestSummarize:
    def test_goal_from_first_user_message(self) -> None:
        s = _summarize_fixture()
        assert "OAuth2 token rotation" in s.goal

    def test_files_touched_includes_edits_writes_reads(self) -> None:
        s = _summarize_fixture()
        paths = {p.split("/")[-1] for p in s.files_touched}
        assert "login.py" in paths
        assert "oauth.py" in paths

    def test_files_deduped(self) -> None:
        s = _summarize_fixture()
        assert len(s.files_touched) == len(set(s.files_touched))

    def test_commands_filter_readonly(self) -> None:
        s = _summarize_fixture()
        # pytest invocation should be kept (mutating in our heuristic)
        assert any("pytest" in c for c in s.commands)
        # No 'git status' / 'ls' should ever appear (they aren't in the fixture
        # but the filter should drop them if present)
        assert not any(c.startswith("git status") for c in s.commands)
        assert not any(c.startswith("ls") for c in s.commands)

    def test_decisions_extracted(self) -> None:
        s = _summarize_fixture()
        # "Going with PyJWT for token rotation." → decision
        assert any("PyJWT" in d for d in s.decisions)

    def test_errors_pair_with_resolution(self) -> None:
        s = _summarize_fixture()
        # The Bash error "AssertionError: token mismatch" should be paired with
        # the next assistant text about the HS256 fix.
        assert s.errors
        joined = " ".join(s.errors)
        assert "AssertionError" in joined or "tool error" in joined
        assert "HS256" in joined

    def test_todos_extracted(self) -> None:
        s = _summarize_fixture()
        # User typed "TODO: add refresh-token expiry tests"
        assert any("refresh-token" in t for t in s.open_todos)

    def test_entities_skip_stopwords(self) -> None:
        s = _summarize_fixture()
        # "PyJWT" should appear; common starter words must not
        assert "PyJWT" in s.entities
        for stop in ("The", "This", "When", "Phase"):
            assert stop not in s.entities

    def test_empty_input_returns_empty_summary(self) -> None:
        s = summarize(iter(()))
        assert s.is_empty


class TestBacktickInDecisions:
    """Regression tests for backtick handling in decision extraction."""

    def test_dangling_backtick_stripped(self) -> None:
        from contextvault.capture.claude_code import UserMessage
        records = [
            UserMessage(
                uuid="1", timestamp="2026-06-02T10:00:00Z", text="I'll use a `debug approach"
            )
        ]
        s = summarize(records)
        assert s.decisions == ["use a debug approach"]

    def test_dangling_tilde_path_backtick_stripped(self) -> None:
        from contextvault.capture.claude_code import UserMessage
        records = [
            UserMessage(
                uuid="1",
                timestamp="2026-06-02T10:00:00Z",
                text="We'll debug before touching `~/some/path",
            )
        ]
        s = summarize(records)
        assert s.decisions == ["debug before touching ~/some/path"]

    def test_backticks_in_middle_stripped(self) -> None:
        from contextvault.capture.claude_code import UserMessage
        records = [
            UserMessage(
                uuid="1",
                timestamp="2026-06-02T10:00:00Z",
                text="Let's use a `command` here and then continue",
            )
        ]
        s = summarize(records)
        assert s.decisions == ["use a command here and then continue"]


class TestReadonlyBashFilter:
    def test_filters_common_readonly(self) -> None:
        from contextvault.capture.claude_code import ToolUse

        records = [
            ToolUse(uuid="u", timestamp="t", tool_use_id="tid", name="Bash",
                    input={"command": "ls -la"}),
            ToolUse(uuid="u", timestamp="t", tool_use_id="tid", name="Bash",
                    input={"command": "pwd"}),
            ToolUse(uuid="u", timestamp="t", tool_use_id="tid", name="Bash",
                    input={"command": "git status"}),
            ToolUse(uuid="u", timestamp="t", tool_use_id="tid", name="Bash",
                    input={"command": "git diff HEAD"}),
            ToolUse(uuid="u", timestamp="t", tool_use_id="tid", name="Bash",
                    input={"command": "rm -rf build/"}),
            ToolUse(uuid="u", timestamp="t", tool_use_id="tid", name="Bash",
                    input={"command": "pytest"}),
        ]
        s = summarize(iter(records))
        # rm -rf and pytest are mutating; the rest are read-only.
        assert "rm -rf build/" in s.commands
        assert "pytest" in s.commands
        assert "ls -la" not in s.commands
        assert "git status" not in s.commands
