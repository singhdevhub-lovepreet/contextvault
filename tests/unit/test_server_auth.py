"""Tests for contextvault.server.auth — bearer-token validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextvault import config
from contextvault.server import auth


@pytest.fixture(autouse=True)
def isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


class TestLoadExpectedToken:
    def test_missing_returns_none(self) -> None:
        assert auth.load_expected_token() is None

    def test_reads_token(self) -> None:
        config.generate_token()
        token = auth.load_expected_token()
        assert token is not None
        assert len(token) >= 32


class TestCheckBearer:
    def test_no_expected_always_rejects(self) -> None:
        assert auth.check_bearer("Bearer foo", expected=None) is False
        assert auth.check_bearer("Bearer foo", expected="") is False

    def test_missing_header_rejects(self) -> None:
        assert auth.check_bearer(None, expected="abc") is False
        assert auth.check_bearer("", expected="abc") is False

    def test_wrong_prefix_rejects(self) -> None:
        assert auth.check_bearer("Basic abc", expected="abc") is False
        assert auth.check_bearer("bearer abc", expected="abc") is False  # lowercase

    def test_wrong_token_rejects(self) -> None:
        assert auth.check_bearer("Bearer wrong", expected="abc") is False

    def test_correct_token_accepts(self) -> None:
        assert auth.check_bearer("Bearer abc", expected="abc") is True

    def test_extra_whitespace_tolerated(self) -> None:
        assert auth.check_bearer("Bearer abc  ", expected="abc") is True
