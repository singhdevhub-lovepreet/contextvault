"""Tests for contextvault.retrieve.query — vault walk + scoped recall."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextvault import config
from contextvault.retrieve.query import (
    build_index,
    extract_workspace_from_path,
    run_recall,
)
from contextvault.vault import Vault, VaultError


@pytest.fixture
def vault_with_notes(tmp_path: Path) -> Path:
    """Build a populated vault tree for recall to walk."""
    config.bootstrap_vault(tmp_path)
    v = Vault(tmp_path)
    v.write(
        "workspaces/-Users-alice-foo/sessions/2026-06-01-auth.md",
        "---\ntype: session\n---\n\n# Goal\n\nrefactor authentication module\n",
    )
    v.write(
        "workspaces/-Users-alice-foo/sessions/2026-06-02-tests.md",
        "added tests for the login flow\n",
    )
    v.write(
        "workspaces/-Users-bob-bar/sessions/2026-06-02-billing.md",
        "billing refactor for stripe integration\n",
    )
    v.write(
        "entities/Anthropic.md",
        "---\ntype: entity\n---\n\nAI safety company. The authentication"
        " research team collaborates often.\n",
    )
    return tmp_path


class TestExtractWorkspace:
    def test_workspace_path(self) -> None:
        assert (
            extract_workspace_from_path("workspaces/-Users-foo-bar/sessions/x.md")
            == "-Users-foo-bar"
        )

    def test_entity_is_shared(self) -> None:
        assert extract_workspace_from_path("entities/Anthropic.md") is None

    def test_root_file_is_shared(self) -> None:
        assert extract_workspace_from_path("hot.md") is None

    def test_workspace_root_is_shared(self) -> None:
        # bare `workspaces/foo.md` (no subdir) — degenerate, but treat as shared
        assert extract_workspace_from_path("workspaces/orphan.md") == "orphan.md"


class TestBuildIndex:
    def test_indexes_all_notes(self, vault_with_notes: Path) -> None:
        idx = build_index(Vault(vault_with_notes))
        # 2 sessions in alice's workspace + 1 in bob's + 1 entity +
        # the starter hot.md + index.md = 6
        assert idx.doc_count == 6

    def test_workspace_metadata_set(self, vault_with_notes: Path) -> None:
        idx = build_index(Vault(vault_with_notes))
        # Query for an alice-specific term, confirm we get alice's docs
        alice_hits = idx.query("authentication", scope="-Users-alice-foo")
        alice_ids = {h["doc_id"] for h in alice_hits}
        # Should include alice's auth note AND the entities/Anthropic.md (shared)
        assert "workspaces/-Users-alice-foo/sessions/2026-06-01-auth.md" in alice_ids
        assert "entities/Anthropic.md" in alice_ids
        # Should NOT include bob's billing note
        assert "workspaces/-Users-bob-bar/sessions/2026-06-02-billing.md" not in alice_ids


class TestRunRecall:
    def test_returns_workspace_hits_when_scoped(self, vault_with_notes: Path) -> None:
        hits = run_recall(
            vault_with_notes, "authentication", scope="-Users-alice-foo"
        )
        paths = {h["path"] for h in hits}
        assert any("alice" in p for p in paths)
        assert not any("bob" in p for p in paths)

    def test_global_scope_sees_cross_workspace(
        self, vault_with_notes: Path
    ) -> None:
        hits = run_recall(vault_with_notes, "refactor", scope=None)
        paths = {h["path"] for h in hits}
        assert any("alice" in p for p in paths)
        assert any("bob" in p for p in paths)

    def test_returns_preview_text(self, vault_with_notes: Path) -> None:
        hits = run_recall(vault_with_notes, "stripe", scope=None)
        assert hits, "should hit bob's billing note"
        assert "stripe" in hits[0]["preview"].lower()

    def test_missing_vault_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VaultError, match="vault does not exist"):
            run_recall(tmp_path / "nonexistent", "query")

    def test_top_k_respected(self, vault_with_notes: Path) -> None:
        # 'authentication' should hit multiple notes; limit to 2
        hits = run_recall(vault_with_notes, "authentication", scope=None, top_k=2)
        assert len(hits) <= 2

    def test_preview_strips_frontmatter(self, vault_with_notes: Path) -> None:
        hits = run_recall(vault_with_notes, "authentication", scope=None)
        # The alice/auth note has a `---` frontmatter block. The preview
        # must not contain it.
        alice = next(
            h for h in hits
            if h["path"] == "workspaces/-Users-alice-foo/sessions/2026-06-01-auth.md"
        )
        assert "---" not in alice["preview"]
        assert "type: session" not in alice["preview"]
