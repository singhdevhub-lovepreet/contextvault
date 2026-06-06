"""Tests for find_semantic_drift — cosine drift lint check."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextvault import config
from contextvault.lint.checks import find_semantic_drift
from contextvault.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    config.bootstrap_vault(tmp_path)
    v = Vault(tmp_path)
    v.write(
        "a.md",
        "---\ntype: note\n---\n\nThis is a note about machine learning and neural networks. " * 3,
    )
    v.write(
        "b.md",
        "---\ntype: note\n---\n\nThis is a note about machine learning and neural networks. " * 3,
    )
    v.write(
        "c.md",
        "---\ntype: note\n---\n\nCompletely different content about cooking recipes and ingredients. " * 3,
    )
    return v


class TestFindSemanticDrift:
    def test_returns_empty_when_no_ollama(self, vault: Vault) -> None:
        with patch("contextvault.lint.checks._HAS_OLLAMA", False):
            findings = find_semantic_drift(vault)
        assert findings == []

    def test_flags_high_similarity_pair(self, vault: Vault) -> None:
        """Mock embeddings so a.md and b.md are near-identical."""
        # a.md and b.md get nearly identical embeddings; c.md is distinct
        mock_resp = MagicMock()
        mock_resp.embeddings = [
            [1.0, 0.0, 0.0],  # a.md
            [0.99, 0.1, 0.0],  # b.md — very similar to a.md
            [0.0, 0.0, 1.0],  # c.md — orthogonal
        ]

        with (
            patch("contextvault.lint.checks._HAS_OLLAMA", True),
            patch("contextvault.lint.checks._ollama") as mock_ollama,
        ):
            mock_ollama.embed.return_value = mock_resp
            findings = find_semantic_drift(vault)

        cats = [f.category for f in findings]
        assert "semantic_drift" in cats
        # At least one finding mentioning b.md
        messages = " ".join(f.message for f in findings)
        assert "b.md" in messages or "a.md" in messages

    def test_no_flag_for_distinct_notes(self, vault: Vault) -> None:
        """When all notes are distinct (low cosine), no findings."""
        mock_resp = MagicMock()
        mock_resp.embeddings = [
            [1.0, 0.0, 0.0],  # a.md
            [0.0, 1.0, 0.0],  # b.md — orthogonal
            [0.0, 0.0, 1.0],  # c.md — orthogonal
        ]

        with (
            patch("contextvault.lint.checks._HAS_OLLAMA", True),
            patch("contextvault.lint.checks._ollama") as mock_ollama,
        ):
            mock_ollama.embed.return_value = mock_resp
            findings = find_semantic_drift(vault)

        assert all(f.category != "semantic_drift" for f in findings)

    def test_handles_embedding_failure(self, vault: Vault) -> None:
        """On embedding failure, return empty (no crash)."""
        with (
            patch("contextvault.lint.checks._HAS_OLLAMA", True),
            patch("contextvault.lint.checks._ollama") as mock_ollama,
        ):
            mock_ollama.embed.side_effect = RuntimeError("ollama not running")
            findings = find_semantic_drift(vault)

        assert findings == []
