"""Wikilink graph traversal.

Given a vault and a starting note, expand its wikilink neighborhood as
an adjacency set. Used by the ``graph_neighborhood`` MCP/HTTP tool and
the canvas generator.

Only edges to *existing* notes are returned — dead wikilinks are dropped
here so the graph never points at phantoms. :mod:`contextvault.lint`
surfaces dead links separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contextvault.vault import Vault

__all__ = ["Neighborhood", "expand", "extract_wikilinks", "resolve_wikilink"]


_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?(?:#[^\]]+)?\]\]")


@dataclass(frozen=True, slots=True)
class Neighborhood:
    root: str
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]


def extract_wikilinks(text: str) -> list[str]:
    """Return wikilink targets (post-pipe / pre-anchor) in source order."""
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(text)]


def resolve_wikilink(vault: Vault, source_rel: str, target: str) -> str | None:
    """Resolve a bare wikilink target to a vault-relative path if it exists.

    Resolution order matches Obsidian's default: (1) sibling of source,
    (2) first ``.md`` file anywhere in the vault matching the target's
    basename. Returns ``None`` for dead links.
    """
    target_filename = f"{target}.md"

    source_path = vault.root / source_rel
    sibling = source_path.parent / target_filename
    if sibling.is_file():
        try:
            return sibling.relative_to(vault.root).as_posix()
        except ValueError:
            pass

    for match in vault.root.rglob(target_filename):
        if ".vault-meta" in match.parts:
            continue
        try:
            return match.relative_to(vault.root).as_posix()
        except ValueError:
            continue
    return None


def expand(vault: Vault, note_path: str, *, depth: int = 1) -> Neighborhood:
    """BFS over wikilinks from ``note_path`` up to ``depth`` hops."""
    nodes: set[str] = {note_path}
    edges: set[tuple[str, str]] = set()
    frontier: list[str] = [note_path]

    for _ in range(depth):
        next_frontier: list[str] = []
        for current in frontier:
            text = vault.read(current) or ""
            for link in extract_wikilinks(text):
                resolved = resolve_wikilink(vault, current, link)
                if resolved is None:
                    continue
                edges.add((current, resolved))
                if resolved not in nodes:
                    nodes.add(resolved)
                    next_frontier.append(resolved)
        frontier = next_frontier
        if not frontier:
            break

    return Neighborhood(
        root=note_path,
        nodes=tuple(sorted(nodes)),
        edges=tuple(sorted(edges)),
    )
