"""Recall orchestrator: walk vault → build BM25 index → query → format hits.

For Phase 1 the index is rebuilt on every call (acceptable for vaults up
to ~10k notes — the limiting factor is filesystem walk, not BM25 math).
Phase 2 will persist the index under ``.vault-meta/bm25/`` and update it
incrementally as the capture pipeline writes session notes.

A document id is the vault-relative path of the note (e.g.
``workspaces/-Users-foo-bar/sessions/2026-06-02-...md``). The workspace
metadata key is derived from the path: if the path begins with
``workspaces/<id>/`` the workspace is ``<id>``; otherwise ``None``
(shared / global).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

from contextvault.retrieve.bm25 import BM25Index
from contextvault.vault import Vault, VaultError

__all__ = ["RecallHit", "build_index", "extract_workspace_from_path", "run_recall"]


class RecallHit(TypedDict):
    path: str
    workspace: str | None
    score: float
    preview: str


def extract_workspace_from_path(rel_path: str) -> str | None:
    """Return the workspace id if ``rel_path`` lives under ``workspaces/<id>/``.

    Returns ``None`` for any path at the vault root or under non-workspace
    folders (``entities/``, ``concepts/``, ``hot.md``, etc.) — these are
    *shared* and surface to every scope.
    """
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "workspaces":
        return parts[1]
    return None


def _iter_indexable_notes(vault: Vault) -> Iterable[tuple[str, str, str | None]]:
    """Yield ``(doc_id, text, workspace)`` for every Markdown note in the vault.

    Skips the ``.vault-meta`` subtree and any empty file.
    """
    for path in vault.list_files("", pattern="*.md"):
        try:
            rel = path.relative_to(vault.root).as_posix()
        except ValueError:
            continue
        if rel.startswith(".vault-meta/"):
            continue
        text = vault.read(rel)
        if not text or not text.strip():
            continue
        yield rel, text, extract_workspace_from_path(rel)


def build_index(vault: Vault) -> BM25Index:
    """Build a fresh in-memory BM25 index over the vault."""
    return BM25Index.from_documents(_iter_indexable_notes(vault))


def run_recall(
    vault_path: Path,
    query: str,
    *,
    scope: str | None = None,
    top_k: int = 10,
    preview_chars: int = 160,
) -> list[RecallHit]:
    """Recall top-``top_k`` matches against ``query`` from the vault.

    ``scope`` is the workspace id (e.g. ``-Users-lsingh-Desktop-foo``) to
    scope to, or ``None`` for a global search. The caller is expected to
    encode the cwd via :func:`contextvault.workspace.encode` before
    passing it here — keeps this module ignorant of how scopes are named.
    """
    if not vault_path.is_dir():
        raise VaultError(f"vault does not exist: {vault_path!s}")

    vault = Vault(vault_path)
    idx = build_index(vault)
    hits = idx.query(query, top_k=top_k, scope=scope)

    out: list[RecallHit] = []
    for hit in hits:
        text = vault.read(hit["doc_id"]) or ""
        out.append(
            RecallHit(
                path=hit["doc_id"],
                workspace=hit["workspace"],
                score=hit["score"],
                preview=_make_preview(text, preview_chars),
            )
        )
    return out


def _make_preview(text: str, chars: int) -> str:
    """Return the first non-frontmatter line up to ``chars`` chars."""
    body = _strip_frontmatter(text)
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) <= chars:
            return stripped
        return stripped[: chars - 1].rstrip() + "…"
    # All blank/headers — fall back to first non-empty line.
    for line in body.splitlines():
        if line.strip():
            return line.strip()[:chars]
    return ""


def _strip_frontmatter(text: str) -> str:
    """Remove a leading ``---``-delimited YAML frontmatter block if present."""
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return "".join(lines[i + 1 :])
    return text  # malformed frontmatter — leave it alone
