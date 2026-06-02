"""Tests for contextvault.lint.checks — orphans + dead links."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextvault import config
from contextvault.lint.checks import find_dead_links, find_orphans, run
from contextvault.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    config.bootstrap_vault(tmp_path)
    v = Vault(tmp_path)
    v.write("entities/Anthropic.md", "Some content.\n")
    v.write("concepts/Linked.md", "Refers to [[Anthropic]]\n")
    v.write("concepts/Orphan.md", "Nobody links here.\n")
    v.write(
        "workspaces/-w-a/sessions/x.md",
        "Refers to [[Anthropic]] and [[Nonexistent]] and [[Linked]]\n",
    )
    return v


class TestFindOrphans:
    def test_identifies_unreferenced_pages(self, vault: Vault) -> None:
        findings = find_orphans(vault)
        paths = {f.path for f in findings}
        assert "concepts/Orphan.md" in paths

    def test_root_pages_never_orphan(self, vault: Vault) -> None:
        # hot.md and index.md are seeded by bootstrap_vault — never flagged
        findings = find_orphans(vault)
        paths = {f.path for f in findings}
        assert "hot.md" not in paths
        assert "index.md" not in paths

    def test_workspace_scope(self, vault: Vault) -> None:
        findings = find_orphans(vault, scope="-w-a")
        paths = {f.path for f in findings}
        # entities/Anthropic.md is shared (vault-root tier) → not in workspace scope
        assert all(p.startswith("workspaces/-w-a/") or "/" not in p for p in paths)


class TestFindDeadLinks:
    def test_flags_nonexistent_target(self, vault: Vault) -> None:
        findings = find_dead_links(vault)
        messages = [f.message for f in findings]
        assert any("Nonexistent" in m for m in messages)

    def test_existing_links_not_flagged(self, vault: Vault) -> None:
        findings = find_dead_links(vault)
        messages = " ".join(f.message for f in findings)
        assert "Anthropic" not in messages
        assert "Linked" not in messages

    def test_no_findings_when_clean(self, tmp_path: Path) -> None:
        config.bootstrap_vault(tmp_path)
        v = Vault(tmp_path)
        v.write("a.md", "Links to [[b]]\n")
        v.write("b.md", "Links back to [[a]]\n")
        # b → a + a → b: both exist, no dead links
        assert find_dead_links(v) == []


class TestRun:
    def test_combines_checks(self, vault: Vault) -> None:
        findings = run(vault)
        cats = {f.category for f in findings}
        # Phase 4 added 6 more checks; the original two must still appear.
        assert {"dead_link", "orphan"}.issubset(cats)
