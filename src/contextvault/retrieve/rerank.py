"""Cosine-similarity reranking via ollama embeddings.

Best-effort enhancement: if ``ollama`` is not installed or the embedding
call fails, the original BM25 hits are returned unchanged (truncated to
``top_k``). This module is never load-bearing — the extractive BM25
recall is always the fallback.
"""

from __future__ import annotations

import logging

from contextvault.retrieve.bm25 import QueryHit
from contextvault.vault import Vault

__all__ = ["rerank_hits"]

_log = logging.getLogger(__name__)

try:
    import ollama as _ollama  # type: ignore[import-not-found]

    _HAS_OLLAMA = True
except ImportError:
    _ollama = None
    _HAS_OLLAMA = False


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = float(sum(x * y for x, y in zip(a, b, strict=False)))
    norm_a = float(sum(x * x for x in a) ** 0.5)
    norm_b = float(sum(x * x for x in b) ** 0.5)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_embeddings(
    texts: list[str], *, model: str = "nomic-embed-text"
) -> list[list[float]]:
    """Call ollama.embed() and return the embedding vectors."""
    resp = _ollama.embed(model=model, input=texts)
    result: list[list[float]] = resp["embeddings"] if isinstance(resp, dict) else resp.embeddings
    return result


def rerank_hits(
    query: str,
    hits: list[QueryHit],
    vault: Vault,
    *,
    top_k: int = 10,
    model: str = "nomic-embed-text",
) -> list[QueryHit]:
    """Rerank BM25 hits by cosine similarity to ``query`` via ollama embeddings.

    Falls back to ``hits[:top_k]`` if ollama is unavailable or any error
    occurs during embedding.
    """
    if not hits:
        return []

    if not _HAS_OLLAMA:
        return hits[:top_k]

    try:
        # Collect document texts
        doc_texts: list[str] = []
        valid_hits: list[QueryHit] = []
        for hit in hits:
            text = vault.read(hit["doc_id"]) or ""
            if text:
                doc_texts.append(text)
                valid_hits.append(hit)

        if not doc_texts:
            return hits[:top_k]

        # Get embeddings for query + all docs in one batch
        all_texts = [query, *doc_texts]
        embeddings = _get_embeddings(all_texts, model=model)

        if len(embeddings) != len(all_texts):
            _log.warning("embedding dimension mismatch, falling back to BM25 order")
            return hits[:top_k]

        query_emb = embeddings[0]
        doc_embs = embeddings[1:]

        # Score each hit by cosine similarity
        scored: list[tuple[QueryHit, float]] = []
        for hit, emb in zip(valid_hits, doc_embs, strict=False):
            if len(emb) != len(query_emb):
                continue
            sim = _cosine_similarity(query_emb, emb)
            scored.append((hit, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [hit for hit, _ in scored[:top_k]]

    except Exception:
        _log.warning("rerank failed, falling back to BM25 order", exc_info=True)
        return hits[:top_k]
