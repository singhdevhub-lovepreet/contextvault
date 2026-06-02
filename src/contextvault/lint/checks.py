"""Vault lint — Phase 3 cut.

Two checks today: orphan pages (no inbound wikilinks) and dead wikilinks
(``[[Page]]`` referencing a note that doesn't exist). Phase 4 expands
this to the full 10-check pass from the upstream ``wiki-lint`` skill.

Each check returns a list of :class:`LintFinding` records that the
server/CLI render as JSON or Markdown.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from contextvault.vault import Vault

__all__ = ["LintFinding", "find_dead_links", "find_orphans", "run"]


@dataclass(frozen=True, slots=True)
class LintFinding:
    category: str  # e.g. 'orphan', 'dead_link'
    severity: str  # 'info' | 'warn' | 'error'
    path: str  # vault-relative
    message: str


_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?(?:#[^\]]+)?\]\]")

# Pages that are inherently "rooted" — never orphans even with no inbound
# links. These are the entrypoints users start at.
_ROOT_PAGES = frozenset({"hot.md", "index.md"})


def _scope_filter(rel_path: str, scope: str | None) -> bool:
    """Return True iff ``rel_path`` is within the lint scope."""
    if rel_path.startswith(".vault-meta/"):
        return False
    if scope is None:
        return True
    # Workspace-scoped: only files under workspaces/<scope>/ plus shared roots
    if rel_path.startswith(f"workspaces/{scope}/"):
        return True
    # Vault-root files are shared (no slash means it lives at the root).
    return "/" not in rel_path and rel_path.endswith(".md")


def _all_notes(vault: Vault, scope: str | None) -> list[str]:
    """Vault-relative paths of every Markdown note in scope."""
    out: list[str] = []
    for path in vault.list_files("", pattern="*.md"):
        try:
            rel = path.relative_to(vault.root).as_posix()
        except ValueError:
            continue
        if _scope_filter(rel, scope):
            out.append(rel)
    return out


def _basename_index(notes: list[str]) -> dict[str, list[str]]:
    """Map ``<filename-without-.md>`` → [vault-relative paths]."""
    idx: dict[str, list[str]] = defaultdict(list)
    for rel in notes:
        name = Path(rel).stem
        idx[name].append(rel)
    return dict(idx)


def find_orphans(vault: Vault, scope: str | None = None) -> list[LintFinding]:
    """Notes with zero inbound wikilinks (excluding root pages)."""
    notes = _all_notes(vault, scope)
    basenames = _basename_index(notes)
    referenced: set[str] = set()

    for rel in notes:
        text = vault.read(rel) or ""
        for match in _WIKILINK_RE.finditer(text):
            target = match.group(1).strip()
            for resolved in basenames.get(target, []):
                if resolved != rel:
                    referenced.add(resolved)

    findings: list[LintFinding] = []
    for rel in notes:
        name = Path(rel).name
        if name in _ROOT_PAGES:
            continue
        if rel in referenced:
            continue
        findings.append(
            LintFinding(
                category="orphan",
                severity="info",
                path=rel,
                message="no inbound wikilinks",
            )
        )
    return findings


def find_dead_links(vault: Vault, scope: str | None = None) -> list[LintFinding]:
    """Wikilinks (``[[Page]]``) pointing at a note that doesn't exist."""
    notes = _all_notes(vault, scope)
    basenames = _basename_index(notes)

    findings: list[LintFinding] = []
    seen: set[tuple[str, str]] = set()  # dedupe per (source, target)
    for rel in notes:
        text = vault.read(rel) or ""
        for match in _WIKILINK_RE.finditer(text):
            target = match.group(1).strip()
            if not target:
                continue
            if target in basenames:
                continue
            key = (rel, target)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                LintFinding(
                    category="dead_link",
                    severity="warn",
                    path=rel,
                    message=f"dead wikilink: [[{target}]]",
                )
            )
    return findings


def run(vault: Vault, *, scope: str | None = None) -> list[LintFinding]:
    """Run every available check and return the combined list of findings."""
    return [
        *find_dead_links(vault, scope=scope),
        *find_orphans(vault, scope=scope),
    ]
