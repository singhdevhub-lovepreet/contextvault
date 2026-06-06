"""Persistent BM25 index: save/load + incremental updates from capture.

Phase 2: index lives at .vault-meta/bm25/index.json, updated on every
capture write. run_recall loads it if present; on missing/corrupt,
falls back to full rebuild and writes the fresh index.
"""

from __future__ import annotations

import json
from pathlib import Path

from contextvault.retrieve.bm25 import BM25Index
from contextvault.vault import Vault

__all__ = ["PERSIST_DIR", "load_or_build", "update_index"]

PERSIST_DIR = ".vault-meta/bm25"


def _index_path(vault: Vault) -> Path:
    return vault.root / PERSIST_DIR / "index.json"


def load_or_build(vault: Vault) -> tuple[BM25Index, bool]:
    """Load index from disk, or build fresh if missing/corrupt.

    Returns (index, was_rebuilt).
    """
    idx_path = _index_path(vault)
    if idx_path.is_file():
        try:
            return BM25Index.load(idx_path), False
        except (json.JSONDecodeError, ValueError, OSError, KeyError):
            # Corrupt or schema mismatch — fall through to rebuild
            pass

    # Full rebuild
    from contextvault.retrieve.query import _iter_indexable_notes

    idx = BM25Index.from_documents(_iter_indexable_notes(vault))
    save_index(vault, idx)
    return idx, True


def save_index(vault: Vault, idx: BM25Index) -> None:
    """Atomically write index to disk."""
    idx_path = _index_path(vault)
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx.save(idx_path)


def update_index(vault: Vault, doc_id: str, text: str, *, workspace: str | None) -> None:
    """Incrementally add/replace one document and persist."""
    idx, _ = load_or_build(vault)
    idx.add_document(doc_id, text, workspace=workspace)
    save_index(vault, idx)


def remove_from_index(vault: Vault, doc_id: str) -> None:
    """Remove a document and persist (best-effort)."""
    idx_path = _index_path(vault)
    if not idx_path.is_file():
        return
    try:
        idx = BM25Index.load(idx_path)
        if idx.remove_document(doc_id):
            save_index(vault, idx)
    except (json.JSONDecodeError, ValueError, OSError, KeyError):
        pass