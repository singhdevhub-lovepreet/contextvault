"""Tool backend functions shared by both HTTP and MCP transports.

Each function is pure — it takes a :class:`Vault` (or a vault path), the
caller's intent, and returns a JSON-serializable dict / list. No transport
concerns leak in here: HTTP request parsing and MCP envelope wrapping live
in their respective modules.

The six tools mirror what the planning doc declared:

  * :func:`recall`              — search the vault, scope-filtered
  * :func:`recent_sessions`     — last-N session notes by date
  * :func:`save_note`           — write a note into the vault
  * :func:`list_workspaces`     — enumerate known workspaces
  * :func:`graph_neighborhood`  — wikilink-graph expansion around a note
  * :func:`lint`                — find orphans + dead links (Phase 3 cut)

Pattern: the tools accept a ``Vault`` and primitives (strings, ints).
They never read CLI args or env directly — callers shape that.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contextvault import workspace as workspace_mod
from contextvault.graph import neighborhood as graph_neighborhood_mod
from contextvault.lint import checks as lint_checks
from contextvault.retrieve.query import run_recall
from contextvault.vault import Vault, VaultError

__all__ = [
    "ToolError",
    "graph_neighborhood",
    "lint",
    "list_workspaces",
    "recall",
    "recent_sessions",
    "save_note",
]


class ToolError(Exception):
    """Raised when a tool argument is invalid or a vault op fails recoverably."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# recall
# --------------------------------------------------------------------------


def recall(
    vault_path: Path,
    query: str,
    *,
    cwd: str | None = None,
    scope: str = "workspace",
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Search the vault and return top-``top_k`` hits as plain dicts.

    ``scope='workspace'`` requires ``cwd`` (the caller's working dir, which we
    encode to a workspace id). ``scope='global'`` searches everything.
    """
    if not query or not query.strip():
        raise ToolError("query is empty")
    if scope not in ("workspace", "global"):
        raise ToolError(f"unknown scope: {scope!r}")
    if top_k < 1 or top_k > 100:
        raise ToolError("top_k must be between 1 and 100")

    scope_id: str | None = None
    if scope == "workspace":
        if not cwd:
            raise ToolError("cwd is required for workspace scope")
        try:
            scope_id = workspace_mod.encode(cwd)
        except workspace_mod.WorkspaceError as exc:
            raise ToolError(f"invalid cwd: {exc}") from exc

    try:
        hits = run_recall(vault_path, query, scope=scope_id, top_k=top_k)
    except VaultError as exc:
        raise ToolError(str(exc), status=500) from exc

    return [dict(h) for h in hits]


# --------------------------------------------------------------------------
# recent_sessions
# --------------------------------------------------------------------------


def recent_sessions(
    vault_path: Path, *, cwd: str | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    """Return the most recent session notes for the workspace at ``cwd``.

    If ``cwd`` is omitted, returns recent sessions across all workspaces.
    """
    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100")

    vault = Vault(vault_path)
    if cwd:
        try:
            ws_id = workspace_mod.encode(cwd)
        except workspace_mod.WorkspaceError as exc:
            raise ToolError(f"invalid cwd: {exc}") from exc
        roots = [f"workspaces/{ws_id}/sessions"]
    else:
        roots = [
            f"workspaces/{p.name}/sessions"
            for p in (vault.root / "workspaces").glob("*")
            if p.is_dir()
        ]

    candidates: list[tuple[float, Path, str]] = []
    for rel in roots:
        for path in vault.list_files(rel, pattern="*.md"):
            try:
                rel_path = path.relative_to(vault.root).as_posix()
            except ValueError:
                continue
            candidates.append((path.stat().st_mtime, path, rel_path))

    candidates.sort(reverse=True)
    out: list[dict[str, Any]] = []
    for _, path, rel_path in candidates[:limit]:
        meta = _read_frontmatter(path)
        out.append(
            {
                "path": rel_path,
                "workspace": meta.get("workspace"),
                "sessionId": meta.get("sessionId"),
                "started": meta.get("started"),
                "updated": meta.get("updated"),
                "goal": _extract_goal(path),
            }
        )
    return out


# --------------------------------------------------------------------------
# save_note
# --------------------------------------------------------------------------


_TITLE_RE_BAD = re.compile(r"[^\w\-]+", re.UNICODE)


def save_note(
    vault_path: Path,
    body: str,
    *,
    title: str,
    note_type: str = "note",
    tags: list[str] | None = None,
    cwd: str | None = None,
    workspace: str = "current",
) -> dict[str, Any]:
    """Write ``body`` as a Markdown note with frontmatter.

    ``workspace`` is either ``"current"`` (derive from ``cwd``), ``"global"``
    (write at vault root under ``notes/``), or a literal workspace id.
    """
    if not title.strip():
        raise ToolError("title is required")
    if not body.strip():
        raise ToolError("body is empty")

    vault = Vault(vault_path)
    # Slugify: any run of non-word/non-hyphen chars (including spaces, dots,
    # punctuation) collapses to a single hyphen. ``--`` runs collapse, leading/
    # trailing hyphens stripped. Empty input falls back to "untitled".
    safe_title = re.sub(r"-+", "-", _TITLE_RE_BAD.sub("-", title)).strip("-") or "untitled"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if workspace == "current":
        if not cwd:
            raise ToolError("cwd is required when workspace='current'")
        try:
            ws_id = workspace_mod.encode(cwd)
        except workspace_mod.WorkspaceError as exc:
            raise ToolError(f"invalid cwd: {exc}") from exc
        rel = f"workspaces/{ws_id}/notes/{safe_title}.md"
        ws_frontmatter = ws_id
    elif workspace == "global":
        rel = f"notes/{safe_title}.md"
        ws_frontmatter = None
    else:
        if not workspace_mod.is_valid_id(workspace):
            raise ToolError(f"invalid workspace id: {workspace!r}")
        rel = f"workspaces/{workspace}/notes/{safe_title}.md"
        ws_frontmatter = workspace

    frontmatter_lines = [
        "---",
        f"type: {note_type}",
        f"title: {title}",
        f"created: {now}",
        f"updated: {now}",
    ]
    if ws_frontmatter is not None:
        frontmatter_lines.append(f"workspace: {ws_frontmatter}")
    if tags:
        tag_list = ", ".join(tags)
        frontmatter_lines.append(f"tags: [{tag_list}]")
    frontmatter_lines.append("---")

    full = "\n".join(frontmatter_lines) + "\n\n" + body.rstrip() + "\n"

    with vault.lock(rel):
        vault.write(rel, full)

    # Best-effort canvas refresh for workspace-scoped notes (same pattern
    # as capture/runner.py:313-325 — a canvas failure must not break saves).
    if ws_frontmatter is not None:
        try:
            from contextvault.graph.canvas import regenerate_workspace_canvas

            regenerate_workspace_canvas(vault, ws_frontmatter)
        except Exception:
            pass

    return {"path": rel, "workspace": ws_frontmatter, "bytes": len(full)}


# --------------------------------------------------------------------------
# list_workspaces
# --------------------------------------------------------------------------


def list_workspaces(vault_path: Path) -> list[dict[str, Any]]:
    """Enumerate every ``workspaces/<id>/`` with its last-update + session count."""
    vault = Vault(vault_path)
    workspaces_dir = vault.root / "workspaces"
    out: list[dict[str, Any]] = []
    if not workspaces_dir.is_dir():
        return out

    for child in sorted(workspaces_dir.glob("*")):
        if not child.is_dir():
            continue
        ws_id = child.name
        sessions_dir = child / "sessions"
        session_count = (
            sum(1 for _ in sessions_dir.glob("*.md")) if sessions_dir.is_dir() else 0
        )
        try:
            updated = max(
                (p.stat().st_mtime for p in child.rglob("*.md")), default=0.0
            )
        except OSError:
            updated = 0.0
        out.append(
            {
                "workspace": ws_id,
                "session_count": session_count,
                "updated_at": (
                    datetime.fromtimestamp(updated, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if updated
                    else None
                ),
            }
        )
    return out


# --------------------------------------------------------------------------
# graph_neighborhood
# --------------------------------------------------------------------------


def graph_neighborhood(
    vault_path: Path, note_path: str, *, depth: int = 1
) -> dict[str, Any]:
    """BFS-expand the wikilink neighborhood of ``note_path`` up to ``depth``.

    Returns ``{root, nodes: [path...], edges: [[from, to], ...]}``. Only
    edges to *existing* notes are returned — dead wikilinks are dropped
    here (use :func:`lint` to surface those).
    """
    if depth < 1 or depth > 4:
        raise ToolError("depth must be 1..4")

    vault = Vault(vault_path)
    if not vault.exists(note_path):
        raise ToolError(f"note not found: {note_path}", status=404)

    n = graph_neighborhood_mod.expand(vault, note_path, depth=depth)
    return {
        "root": n.root,
        "nodes": list(n.nodes),
        "edges": [list(e) for e in n.edges],
    }


# --------------------------------------------------------------------------
# lint  (Phase 3 cut: orphans + dead links; full 10-check pass lands in Phase 4)
# --------------------------------------------------------------------------


def lint(
    vault_path: Path,
    *,
    cwd: str | None = None,
    scope: str = "workspace",
) -> list[dict[str, Any]]:
    """Return a list of lint findings as dicts.

    Phase 3 minimum: orphan pages (no inbound wikilinks) and dead
    wikilinks. Phase 4 adds the remaining 8 checks from the upstream
    wiki-lint skill.
    """
    if scope not in ("workspace", "global"):
        raise ToolError(f"unknown scope: {scope!r}")

    scope_id: str | None = None
    if scope == "workspace":
        if not cwd:
            raise ToolError("cwd is required for workspace scope")
        try:
            scope_id = workspace_mod.encode(cwd)
        except workspace_mod.WorkspaceError as exc:
            raise ToolError(f"invalid cwd: {exc}") from exc

    findings = lint_checks.run(Vault(vault_path), scope=scope_id)
    return [
        {
            "category": f.category,
            "severity": f.severity,
            "path": f.path,
            "message": f.message,
        }
        for f in findings
    ]


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------


def _read_frontmatter(path: Path) -> dict[str, str]:
    """Naïve YAML frontmatter parser — handles ``key: value`` lines only.

    We do not pull in PyYAML for this: the frontmatter we write is line-
    structured by construction, and we only need to surface a small set
    of scalar keys back to clients. A real YAML parser would be overkill
    and add a dep that the privacy story doesn't want.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.rstrip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def _extract_goal(path: Path) -> str:
    """Extract the line under ``## Goal`` if present, else empty string."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == "## goal":
            for j in range(i + 1, min(i + 5, len(lines))):
                content = lines[j].strip()
                if content and not content.startswith("#"):
                    return content
    return ""
