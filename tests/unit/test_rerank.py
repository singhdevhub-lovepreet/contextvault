"""Tests for contextvault.retrieve.rerank — cosine reranking."""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import patch

import pytest

from contextvault import config
from contextvault.retrieve.bm25 import QueryHit
from contextvault.retrieve.rerank import _cosine_similarity, rerank_hits
from contextvault.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    config.bootstrap_vault(tmp_path)
    v = Vault(tmp_path)
    v.write("a.md", "alpha bravo charlie delta echo")
    v.write("b.md", "foxtrot golf hotel india juliet")
    v.write("c.md", "kilo lima mike november oscar")
    return v


def _make_hits(*doc_ids: str) -> list[QueryHit]:
    return [
        QueryHit(doc_id=d, score=1.0 / (i + 1), workspace=None)
        for i, d in enumerate(doc_ids)
    ]


class TestRerankFallback:
    def test_identity_fallback_when_no_ollama(self, vault: Vault) -> None:
        """When ollama is not installed, return hits unchanged (truncated)."""
        hits = _make_hits("a.md", "b.md", "c.md")
        with patch("contextvault.retrieve.rerank._HAS_OLLAMA", False):
            result = rerank_hits("test query", hits, vault, top_k=2)
        assert len(result) == 2
        assert result[0]["doc_id"] == "a.md"
        assert result[1]["doc_id"] == "b.md"

    def test_handles_embedding_failure(self, vault: Vault) -> None:
        """On API error, fall back to original order."""
        hits = _make_hits("a.md", "b.md")
        with (
            patch("contextvault.retrieve.rerank._HAS_OLLAMA", True),
            patch("contextvault.retrieve.rerank._get_embeddings", side_effect=RuntimeError("fail")),
        ):
            result = rerank_hits("test query", hits, vault, top_k=10)
        assert len(result) == 2
        assert result[0]["doc_id"] == "a.md"

    def test_empty_hits_returns_empty(self, vault: Vault) -> None:
        result = rerank_hits("query", [], vault, top_k=10)
        assert result == []


class TestRerankWithMock:
    def test_reranked_order_with_mock(self, vault: Vault) -> None:
        """Mock embeddings to verify reranking reverses BM25 order."""
        hits = _make_hits("a.md", "b.md", "c.md")

        # Query embedding is close to c.md, far from a.md
        mock_embeddings = [
            [0.0, 0.0, 1.0],  # query
            [1.0, 0.0, 0.0],  # a.md (orthogonal to query)
            [0.5, 0.5, 0.5],  # b.md (moderate similarity)
            [0.0, 0.1, 0.9],  # c.md (very similar to query)
        ]

        with (
            patch("contextvault.retrieve.rerank._HAS_OLLAMA", True),
            patch("contextvault.retrieve.rerank._get_embeddings", return_value=mock_embeddings),
        ):
            result = rerank_hits("test query", hits, vault, top_k=3)

        # c.md should be first (closest to query)
        assert result[0]["doc_id"] == "c.md"
        # a.md should be last (orthogonal)
        assert result[-1]["doc_id"] == "a.md"

    def test_respects_top_k(self, vault: Vault) -> None:
        """Reranked results are truncated to top_k."""
        hits = _make_hits("a.md", "b.md", "c.md")
        mock_embeddings = [
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.5, 0.5, 0.0],
            [0.0, 1.0, 0.0],
        ]

        with (
            patch("contextvault.retrieve.rerank._HAS_OLLAMA", True),
            patch("contextvault.retrieve.rerank._get_embeddings", return_value=mock_embeddings),
        ):
            result = rerank_hits("test", hits, vault, top_k=1)

        assert len(result) == 1


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector(self) -> None:
        assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_known_value(self) -> None:
        # cos(45°) = √2/2 ≈ 0.7071
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2)
        assert _cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)
