"""Tests for llm_refine_summary — Anthropic API re-summarization."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from contextvault.capture.summarize import SessionSummary, llm_refine_summary


def _make_summary(**kwargs: object) -> SessionSummary:
    defaults = {
        "goal": "Implement OAuth2 token rotation",
        "summary_sentences": ["Implemented OAuth2 token rotation for the API."],
        "decisions": ["Going with PyJWT for token signing"],
        "files_touched": ["src/oauth.py"],
        "commands": ["pytest tests/"],
        "errors": [],
        "open_todos": [],
        "entities": ["PyJWT"],
    }
    defaults.update(kwargs)
    return SessionSummary(**defaults)  # type: ignore[arg-type]


class TestLlmRefineSummary:
    def test_refine_with_mock_anthropic_client(self) -> None:
        """When anthropic is available, the summary is refined via API."""
        summary = _make_summary()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Refined narrative summary of the session.")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with (
            patch("contextvault.capture.summarize._HAS_ANTHROPIC", True),
            patch("contextvault.capture.summarize._anthropic") as mock_mod,
        ):
            mock_mod.Anthropic.return_value = mock_client
            result = llm_refine_summary(summary)

        assert result.summary_sentences == ["Refined narrative summary of the session."]
        assert result.goal == "Implement OAuth2 token rotation"  # unchanged

    def test_fallback_when_not_installed(self) -> None:
        """When anthropic is not installed, return original summary."""
        summary = _make_summary()
        original_sentences = list(summary.summary_sentences)

        with patch("contextvault.capture.summarize._HAS_ANTHROPIC", False):
            result = llm_refine_summary(summary)

        assert result.summary_sentences == original_sentences
        assert result is summary  # same object, not a copy

    def test_fallback_on_api_error(self) -> None:
        """On API error, return the original summary unchanged."""
        summary = _make_summary()
        original_sentences = list(summary.summary_sentences)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API unavailable")

        with (
            patch("contextvault.capture.summarize._HAS_ANTHROPIC", True),
            patch("contextvault.capture.summarize._anthropic") as mock_mod,
        ):
            mock_mod.Anthropic.return_value = mock_client
            result = llm_refine_summary(summary)

        assert result.summary_sentences == original_sentences

    def test_empty_summary_passes_through(self) -> None:
        """An empty summary should not trigger the API call."""
        summary = SessionSummary()
        assert summary.is_empty

        with patch("contextvault.capture.summarize._HAS_ANTHROPIC", True):
            result = llm_refine_summary(summary)

        assert result is summary
        assert result.is_empty
