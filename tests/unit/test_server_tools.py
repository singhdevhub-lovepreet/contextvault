"""Tests for contextvault.server.tools — pure backend functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextvault import config
from contextvault.server import tools
from contextvault.vault import Vault


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    config.bootstrap_vault(v)
    vault = Vault(v)
    vault.write(
        "workspaces/-Users-alice-foo/sessions/2026-06-01-auth.md",
        "---\ntype: session\nworkspace: -Users-alice-foo\n"
        "sessionId: aaa11111-2222-3333-4444-555566667777\n"
        "started: 2026-06-01T10:00:00Z\nupdated: 2026-06-01T11:00:00Z\n---\n\n"
        "## Goal\n\nrefactor authentication\n\n"
        "Related: [[Anthropic]] and [[NonexistentPage]]\n",
    )
    vault.write(
        "workspaces/-Users-alice-foo/sessions/2026-06-02-tests.md",
        "---\ntype: session\nworkspace: -Users-alice-foo\n---\n\n"
        "## Goal\n\nadd login tests\n\nLinks to [[Anthropic]].\n",
    )
    vault.write(
        "workspaces/-Users-bob-bar/sessions/2026-06-02-billing.md",
        "---\ntype: session\nworkspace: -Users-bob-bar\n---\n\n"
        "## Goal\n\nstripe billing integration\n",
    )
    vault.write(
        "entities/Anthropic.md",
        "---\ntype: entity\n---\n\nAuthentication research collaborators.\n",
    )
    vault.write(
        "concepts/Orphan.md",
        "---\ntype: concept\n---\n\nNo inbound links.\n",
    )
    return v


class TestRecall:
    def test_workspace_scope(self, vault_path: Path) -> None:
        hits = tools.recall(
            vault_path,
            "authentication",
            cwd="/Users/alice/foo",
            scope="workspace",
        )
        paths = {h["path"] for h in hits}
        assert any("alice" in p for p in paths)
        assert not any("bob" in p for p in paths)

    def test_global_scope(self, vault_path: Path) -> None:
        hits = tools.recall(
            vault_path, "billing", scope="global", cwd=None
        )
        paths = {h["path"] for h in hits}
        assert any("bob" in p for p in paths)

    def test_empty_query_rejected(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError, match="empty"):
            tools.recall(vault_path, "", cwd="/Users/x", scope="workspace")

    def test_scope_workspace_requires_cwd(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError, match="cwd is required"):
            tools.recall(vault_path, "anything", scope="workspace")

    def test_unknown_scope(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError, match="unknown scope"):
            tools.recall(vault_path, "x", scope="bogus")

    def test_top_k_bounds(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError, match="top_k"):
            tools.recall(vault_path, "x", scope="global", top_k=0)
        with pytest.raises(tools.ToolError, match="top_k"):
            tools.recall(vault_path, "x", scope="global", top_k=101)


class TestRecentSessions:
    def test_per_workspace(self, vault_path: Path) -> None:
        items = tools.recent_sessions(vault_path, cwd="/Users/alice/foo", limit=10)
        paths = [i["path"] for i in items]
        # Two session notes in alice's workspace, no bob
        assert len(items) == 2
        assert all("alice" in p for p in paths)
        # Frontmatter parsed
        assert items[0]["workspace"] == "-Users-alice-foo"
        assert items[0]["goal"]  # extracted from ## Goal

    def test_global_across_workspaces(self, vault_path: Path) -> None:
        items = tools.recent_sessions(vault_path, cwd=None, limit=10)
        workspaces = {i["workspace"] for i in items}
        assert workspaces == {"-Users-alice-foo", "-Users-bob-bar"}

    def test_limit_bounds(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError):
            tools.recent_sessions(vault_path, limit=0)


class TestSaveNote:
    def test_workspace_current(self, vault_path: Path) -> None:
        result = tools.save_note(
            vault_path,
            "this is the body of my note",
            title="My Note",
            cwd="/Users/x/y",
            workspace="current",
        )
        assert result["path"] == "workspaces/-Users-x-y/notes/My-Note.md"
        assert result["workspace"] == "-Users-x-y"
        content = Vault(vault_path).read(result["path"])
        assert content is not None
        assert "title: My Note" in content
        assert "this is the body" in content

    def test_workspace_global(self, vault_path: Path) -> None:
        result = tools.save_note(
            vault_path,
            "shared body",
            title="Shared",
            workspace="global",
        )
        assert result["path"] == "notes/Shared.md"
        assert result["workspace"] is None

    def test_workspace_explicit_id(self, vault_path: Path) -> None:
        result = tools.save_note(
            vault_path,
            "body",
            title="Pinned",
            workspace="-Users-x-explicit",
        )
        assert "workspaces/-Users-x-explicit" in result["path"]

    def test_invalid_workspace_id(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError, match="invalid workspace"):
            tools.save_note(
                vault_path, "b", title="t", workspace="../escape"
            )

    def test_empty_body_rejected(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError, match="empty"):
            tools.save_note(vault_path, "", title="t", workspace="global")

    def test_workspace_save_creates_canvas(self, vault_path: Path) -> None:
        tools.save_note(
            vault_path,
            "workspace-scoped body",
            title="CanvasTest",
            cwd="/Users/alice/foo",
            workspace="current",
        )
        canvas = vault_path / "workspaces" / "-Users-alice-foo" / "Workspace Map.canvas"
        assert canvas.is_file()

    def test_global_save_does_not_create_canvas(self, vault_path: Path) -> None:
        tools.save_note(
            vault_path,
            "global body",
            title="GlobalNote",
            workspace="global",
        )
        # No workspace canvas should be created for global notes
        notes_dir = vault_path / "notes"
        assert not (notes_dir / "Workspace Map.canvas").exists()


class TestListWorkspaces:
    def test_enumerates(self, vault_path: Path) -> None:
        out = tools.list_workspaces(vault_path)
        ids = {w["workspace"] for w in out}
        assert ids == {"-Users-alice-foo", "-Users-bob-bar"}
        alice = next(w for w in out if w["workspace"] == "-Users-alice-foo")
        assert alice["session_count"] == 2


class TestGraphNeighborhood:
    def test_depth_one(self, vault_path: Path) -> None:
        result = tools.graph_neighborhood(
            vault_path,
            "workspaces/-Users-alice-foo/sessions/2026-06-01-auth.md",
            depth=1,
        )
        # Anthropic exists → an edge to entities/Anthropic.md should appear.
        # NonexistentPage is a dead link and should NOT appear in edges.
        nodes = set(result["nodes"])
        assert "entities/Anthropic.md" in nodes
        assert "NonexistentPage.md" not in nodes
        # Edges include the (source → Anthropic) hop
        assert any("Anthropic" in e[1] for e in result["edges"])

    def test_missing_note(self, vault_path: Path) -> None:
        with pytest.raises(tools.ToolError, match="not found"):
            tools.graph_neighborhood(vault_path, "no-such-note.md")


class TestLint:
    def test_finds_dead_link_and_orphan(self, vault_path: Path) -> None:
        findings = tools.lint(vault_path, scope="global")
        categories = {f["category"] for f in findings}
        assert "dead_link" in categories
        assert "orphan" in categories
        dead = [f for f in findings if f["category"] == "dead_link"]
        # NonexistentPage from the alice auth note must show up
        assert any("NonexistentPage" in f["message"] for f in dead)
        orphans = [f for f in findings if f["category"] == "orphan"]
        # concepts/Orphan.md has no inbound links → flagged
        assert any("concepts/Orphan.md" in f["path"] for f in orphans)
