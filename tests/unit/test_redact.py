"""Tests for contextvault.capture.redact — secret regex filter."""

from __future__ import annotations

import pytest

from contextvault.capture.redact import REDACTED_MARKER, redact_line, redact_text


class TestPatterns:
    @pytest.mark.parametrize(
        ("line", "expected_pattern"),
        [
            ("export AWS_KEY=AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
            ("ASIAIOSFODNN7EXAMPLE token", "aws_access_key"),
            ("Authorization: Bearer abc.def.ghi", "authorization_bearer"),
            ("authorization: bearer foo", "authorization_bearer"),
            ("API_KEY=sk-1234567890abcdef", "secret_env_assignment"),
            ("api-key=long-secret-value", "secret_env_assignment"),
            ("DATABASE_PASSWORD=hunter2", "secret_env_assignment"),
            ("PRIVATE_KEY=ssh-rsa AAA...", "secret_env_assignment"),
            ('"api_key": "sk-abc123"', "secret_json_field"),
            ('"token": "ghp_abcdefghij1234"', "secret_json_field"),
            (
                "header eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c here",
                "jwt",
            ),
        ],
    )
    def test_pattern_match(self, line: str, expected_pattern: str) -> None:
        result, pattern = redact_line(line)
        assert pattern == expected_pattern
        assert result == REDACTED_MARKER

    @pytest.mark.parametrize(
        "line",
        [
            "regular text with no secrets",
            "PATH=/usr/local/bin:/usr/bin",
            "USER=lsingh",
            "https://example.com/api?query=foo",
            "the user typed: hello world",
            "AKIASHORT",  # too short to match
            "Authorization: Basic abc",  # only Bearer is matched
        ],
    )
    def test_pattern_no_match(self, line: str) -> None:
        result, pattern = redact_line(line)
        assert pattern is None
        assert result == line


class TestRedactText:
    def test_multi_line_partial_redaction(self) -> None:
        text = (
            "first line is fine\n"
            "API_KEY=secret123\n"
            "third line is fine\n"
        )
        out, events = redact_text(text)
        lines = out.splitlines()
        assert lines[0] == "first line is fine"
        assert lines[1] == REDACTED_MARKER
        assert lines[2] == "third line is fine"
        assert len(events) == 1
        assert events[0].line_offset == 1
        assert events[0].pattern == "secret_env_assignment"

    def test_preserves_trailing_newline_on_redacted_line(self) -> None:
        text = "API_KEY=foo\nplain\n"
        out, _ = redact_text(text)
        assert out == f"{REDACTED_MARKER}\nplain\n"

    def test_no_secrets_returns_input_unchanged(self) -> None:
        text = "alpha\nbeta\ngamma\n"
        out, events = redact_text(text)
        assert out == text
        assert events == []

    def test_audit_log_offsets_are_zero_indexed(self) -> None:
        text = "AKIAIOSFODNN7EXAMPLE\nfine\nAKIAIOSFODNN7EXAMPLE\n"
        _, events = redact_text(text)
        assert [e.line_offset for e in events] == [0, 2]

    def test_audit_log_never_contains_content(self) -> None:
        """The audit record must not store the original secret in any field."""
        import dataclasses

        text = "API_KEY=highly-sensitive-secret-value-do-not-leak\n"
        _, events = redact_text(text)
        assert events[0].pattern == "secret_env_assignment"
        # Iterate every dataclass field — none may contain the secret
        for field in dataclasses.fields(events[0]):
            value = getattr(events[0], field.name)
            assert "sensitive" not in str(value)
            assert "leak" not in str(value)
