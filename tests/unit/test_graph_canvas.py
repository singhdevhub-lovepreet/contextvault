"""Tests for contextvault.graph.canvas + neighborhood module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextvault import config
from contextvault.graph.canvas import build_workspace_canvas, regenerate_workspace_canvas
from contextvault.graph.neighborhood import expand, extract_wikilinks, resolve_wikilink
from contextvault.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    config.bootstrap_vault(tmp_path)
    return Vault(tmp_path)


class TestNeighborhood:
    def test_extract_wikilinks_basic(self) -> None:
        assert extract_wikilinks("see [[Foo]] and [[Bar|alias]] and [[Baz#anchor]]") == [
            "Foo",
            "Bar",
            "Baz",
        ]

    def test_extract_strips_aliases_and_anchors(self) -> None:
        assert extract_wikilinks("see [[Hello|world]]") == ["Hello"]

    def test_resolve_sibling_first(self, vault: Vault) -> None:
        vault.write("a/Note.md", "x")
        vault.write("a/Other.md", "x")
        assert resolve_wikilink(vault, "a/Note.md", "Other") == "a/Other.md"

    def test_resolve_anywhere_fallback(self, vault: Vault) -> None:
        vault.write("a/src.md", "x")
        vault.write("b/Target.md", "x")
        assert resolve_wikilink(vault, "a/src.md", "Target") == "b/Target.md"

    def test_resolve_returns_none_for_missing(self, vault: Vault) -> None:
        vault.write("a/src.md", "x")
        assert resolve_wikilink(vault, "a/src.md", "Nonexistent") is None

    def test_expand_depth_one(self, vault: Vault) -> None:
        vault.write("a.md", "links to [[b]]")
        vault.write("b.md", "links to [[c]]")
        vault.write("c.md", "leaf")
        n = expand(vault, "a.md", depth=1)
        assert "b.md" in n.nodes
        # depth=1 must NOT reach c.md
        assert "c.md" not in n.nodes

    def test_expand_depth_two(self, vault: Vault) -> None:
        vault.write("a.md", "[[b]]")
        vault.write("b.md", "[[c]]")
        vault.write("c.md", "leaf")
        n = expand(vault, "a.md", depth=2)
        assert "c.md" in n.nodes


class TestWorkspaceCanvas:
    def test_empty_workspace_returns_empty(self, vault: Vault) -> None:
        payload = build_workspace_canvas(vault, "-Users-missing-ws")
        assert payload == {"nodes": [], "edges": []}

    def test_canvas_contains_header_and_pillars(self, vault: Vault) -> None:
        ws = "-Users-test-proj"
        ws_dir = vault.root / "workspaces" / ws
        ws_dir.mkdir(parents=True)
        (ws_dir / "hot.md").write_text("hot")
        (ws_dir / "index.md").write_text("index")
        (ws_dir / "log.md").write_text("log")

        payload = build_workspace_canvas(vault, ws)
        node_ids = {n["id"] for n in payload["nodes"]}  # type: ignore[index]
        assert "header" in node_ids
        assert "hot" in node_ids
        assert "index" in node_ids
        assert "log" in node_ids

    def test_canvas_lists_sessions(self, vault: Vault) -> None:
        ws = "-Users-test-proj"
        sess_dir = vault.root / "workspaces" / ws / "sessions"
        sess_dir.mkdir(parents=True)
        for i in range(3):
            (sess_dir / f"2026-06-0{i+1}-x.md").write_text("session\n")
        payload = build_workspace_canvas(vault, ws, max_sessions=2)
        session_nodes = [
            n
            for n in payload["nodes"]  # type: ignore[index]
            if isinstance(n, dict) and str(n.get("id", "")).startswith("session-")
        ]
        assert len(session_nodes) == 2

    def test_regenerate_writes_valid_json(self, vault: Vault) -> None:
        ws = "-Users-test-proj"
        (vault.root / "workspaces" / ws).mkdir(parents=True)
        (vault.root / "workspaces" / ws / "hot.md").write_text("hot")

        rel = regenerate_workspace_canvas(vault, ws)
        assert rel == "workspaces/-Users-test-proj/Workspace Map.canvas"
        content = vault.read(rel)
        assert content is not None
        # Round-trip parse
        parsed = json.loads(content)
        assert "nodes" in parsed and "edges" in parsed
