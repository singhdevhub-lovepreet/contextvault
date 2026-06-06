"""Vault lint — ten automated checks.

Each check function returns a list of :class:`LintFinding` records. The
first eight checks are cheap and deterministic — no LLM, no network.
The last two use optional embeddings via ``ollama``.

  1.  ``find_orphans``                — notes with zero inbound wikilinks
  2.  ``find_dead_links``             — wikilinks to nonexistent notes
  3.  ``find_missing_frontmatter``    — notes missing required keys
  4.  ``find_empty_sections``         — ``## Heading`` with no content
  5.  ``find_duplicate_titles``       — two notes with the same basename
  6.  ``find_broken_markdown_links``  — ``[text](path)`` to missing files
  7.  ``find_huge_notes``             — > 200KB notes (capture runaway)
  8.  ``find_unused_tags``            — frontmatter tags appearing only once
  9.  ``find_stale_claims``           — citing note older than cited note
  10. ``find_semantic_drift``         — near-duplicate notes (cosine ≥ 0.92)
"""

from __future__ import annotations

import contextlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from contextvault.vault import Vault

__all__ = [
    "LintFinding",
    "find_broken_markdown_links",
    "find_dead_links",
    "find_duplicate_titles",
    "find_empty_sections",
    "find_huge_notes",
    "find_missing_frontmatter",
    "find_orphans",
    "find_semantic_drift",
    "find_stale_claims",
    "find_unused_tags",
    "run",
]


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


_REQUIRED_FRONTMATTER_KEYS = ("type",)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# Markdown link: ``[text](path)`` where path doesn't start with http(s):// or mailto:
_MD_LINK_RE = re.compile(
    r"\[(?P<text>[^\]]+)\]\((?P<target>(?!https?://|mailto:|#)[^)\s]+)\)"
)

_HUGE_NOTE_BYTES = 200 * 1024  # 200KB


def _parse_frontmatter(text: str) -> tuple[dict[str, str], int]:
    """Return ``(metadata, body_start_index)`` for a note body.

    ``body_start_index`` is the line index where the post-frontmatter body
    begins (so callers can scan headings without re-parsing).
    """
    if not text.startswith("---"):
        return {}, 0
    lines = text.splitlines()
    out: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            return out, i + 1
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out, 0  # malformed frontmatter — treat as body


def find_missing_frontmatter(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """Notes missing any of the required frontmatter keys."""
    notes = _all_notes(vault, scope)
    findings: list[LintFinding] = []
    for rel in notes:
        text = vault.read(rel) or ""
        meta, _ = _parse_frontmatter(text)
        missing = [k for k in _REQUIRED_FRONTMATTER_KEYS if k not in meta]
        if not missing:
            continue
        findings.append(
            LintFinding(
                category="missing_frontmatter",
                severity="warn",
                path=rel,
                message=f"missing required keys: {', '.join(missing)}",
            )
        )
    return findings


def find_empty_sections(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """``## Heading`` immediately followed by another heading or EOF."""
    notes = _all_notes(vault, scope)
    findings: list[LintFinding] = []
    for rel in notes:
        text = vault.read(rel) or ""
        _, body_start = _parse_frontmatter(text)
        lines = text.splitlines()[body_start:]
        for i, line in enumerate(lines):
            match = _HEADING_RE.match(line)
            if not match:
                continue
            heading = match.group(2)
            # Look at the next non-blank, non-heading line
            j = i + 1
            content_seen = False
            while j < len(lines):
                nxt = lines[j].strip()
                if nxt == "":
                    j += 1
                    continue
                if _HEADING_RE.match(lines[j]):
                    break  # ran into next heading without seeing content
                content_seen = True
                break
            if not content_seen:
                findings.append(
                    LintFinding(
                        category="empty_section",
                        severity="info",
                        path=rel,
                        message=f"empty section: '{heading}'",
                    )
                )
    return findings


def find_duplicate_titles(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """Two or more notes sharing the same basename (without ``.md``).

    Obsidian's bare-name wikilink resolution is ambiguous when two notes
    share a name. We flag every duplicate so the user can rename one.
    """
    notes = _all_notes(vault, scope)
    by_name: dict[str, list[str]] = defaultdict(list)
    for rel in notes:
        by_name[Path(rel).stem].append(rel)

    findings: list[LintFinding] = []
    for name, paths in by_name.items():
        if len(paths) < 2:
            continue
        for rel in paths:
            findings.append(
                LintFinding(
                    category="duplicate_title",
                    severity="warn",
                    path=rel,
                    message=(
                        f"title '{name}' shared by {len(paths)} notes; "
                        f"wikilinks become ambiguous"
                    ),
                )
            )
    return findings


def find_broken_markdown_links(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """``[text](relative/path.md)`` pointing at a missing file."""
    notes = _all_notes(vault, scope)
    findings: list[LintFinding] = []
    for rel in notes:
        text = vault.read(rel) or ""
        source_dir = (vault.root / rel).parent
        for match in _MD_LINK_RE.finditer(text):
            target = match.group("target").split("#", 1)[0]  # drop anchors
            if not target:
                continue
            resolved = (source_dir / target).resolve()
            try:
                resolved.relative_to(vault.root.resolve())
            except ValueError:
                continue  # outside vault — ignore (user knows what they're doing)
            if not resolved.is_file():
                findings.append(
                    LintFinding(
                        category="broken_link",
                        severity="warn",
                        path=rel,
                        message=f"broken link: ({target})",
                    )
                )
    return findings


def find_huge_notes(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """Notes exceeding :data:`_HUGE_NOTE_BYTES` — usually a capture runaway."""
    notes = _all_notes(vault, scope)
    findings: list[LintFinding] = []
    for rel in notes:
        try:
            size = (vault.root / rel).stat().st_size
        except OSError:
            continue
        if size > _HUGE_NOTE_BYTES:
            findings.append(
                LintFinding(
                    category="huge_note",
                    severity="info",
                    path=rel,
                    message=f"{size // 1024} KB > {_HUGE_NOTE_BYTES // 1024} KB threshold",
                )
            )
    return findings


def find_unused_tags(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """Frontmatter ``tags: [...]`` values that appear in only one note.

    A single-use tag is usually a typo (e.g. ``#authentcation`` vs
    ``#authentication``). Tags from list-style frontmatter parsed via
    the small substring split — not a YAML parser.
    """
    notes = _all_notes(vault, scope)
    tag_count: Counter[str] = Counter()
    tag_sources: dict[str, str] = {}
    for rel in notes:
        text = vault.read(rel) or ""
        meta, _ = _parse_frontmatter(text)
        raw = meta.get("tags", "")
        if not raw:
            continue
        # Strip leading/trailing brackets if present
        raw = raw.strip().lstrip("[").rstrip("]")
        for tag in [t.strip().strip("'\"") for t in raw.split(",")]:
            if not tag:
                continue
            tag_count[tag] += 1
            tag_sources.setdefault(tag, rel)

    findings: list[LintFinding] = []
    for tag, count in tag_count.items():
        if count != 1:
            continue
        # Skip workspace/session "type" autotags — those are infrastructural
        if tag in {"session", "note", "entity", "concept"} or tag.startswith("workspace/"):
            continue
        findings.append(
            LintFinding(
                category="unused_tag",
                severity="info",
                path=tag_sources[tag],
                message=f"tag '{tag}' appears in only one note (typo?)",
            )
        )
    return findings


def find_stale_claims(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """Citing note older than the cited note — claim may be stale.

    Uses frontmatter ``updated`` fields (ISO8601 strings compare
    lexicographically). Notes without an ``updated`` field are skipped.
    """
    notes = _all_notes(vault, scope)
    basenames = _basename_index(notes)

    # Build updated-timestamp map
    updated_map: dict[str, str] = {}
    for rel in notes:
        text = vault.read(rel) or ""
        meta, _ = _parse_frontmatter(text)
        ts = meta.get("updated", "")
        if ts:
            updated_map[rel] = ts

    findings: list[LintFinding] = []
    for rel in notes:
        citer_ts = updated_map.get(rel)
        if not citer_ts:
            continue
        text = vault.read(rel) or ""
        for match in _WIKILINK_RE.finditer(text):
            target = match.group(1).strip()
            for resolved in basenames.get(target, []):
                if resolved == rel:
                    continue
                cited_ts = updated_map.get(resolved)
                if not cited_ts:
                    continue
                if cited_ts > citer_ts:
                    findings.append(
                        LintFinding(
                            category="stale_claim",
                            severity="warn",
                            path=rel,
                            message=(
                                f"cites [[{target}]] (updated {cited_ts}) "
                                f"but this note was last updated {citer_ts}"
                            ),
                        )
                    )
    return findings


# --- Embedding-based checks (optional ollama dependency) -----------------

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


def find_semantic_drift(
    vault: Vault, scope: str | None = None
) -> list[LintFinding]:
    """Near-duplicate notes detected via cosine similarity of embeddings.

    Requires ``ollama`` with ``nomic-embed-text`` model. Returns an empty
    list if ollama is not installed or embedding fails.
    """
    if not _HAS_OLLAMA:
        return []

    notes = _all_notes(vault, scope)
    # Filter out very short notes (< 50 chars of content)
    note_texts: list[tuple[str, str]] = []
    for rel in notes:
        text = vault.read(rel) or ""
        if len(text) < 50:
            continue
        note_texts.append((rel, text))

    if len(note_texts) < 2:
        return []

    # Load embedding cache
    import json

    cache_rel = ".vault-meta/embeddings/cache.json"
    cache: dict[str, dict[str, object]] = {}
    cache_raw = vault.read(cache_rel)
    if cache_raw:
        try:
            cache = json.loads(cache_raw)
        except json.JSONDecodeError:
            cache = {}

    # Determine which notes need fresh embeddings
    embeddings: dict[str, list[float]] = {}
    uncached_rels: list[str] = []
    uncached_texts: list[str] = []

    for rel, text in note_texts:
        try:
            mtime = str((vault.root / rel).stat().st_mtime)
        except OSError:
            mtime = ""
        cached = cache.get(rel)
        if cached and cached.get("mtime") == mtime and "embedding" in cached:
            embeddings[rel] = cached["embedding"]  # type: ignore[assignment]
        else:
            uncached_rels.append(rel)
            uncached_texts.append(text)

    # Compute fresh embeddings
    if uncached_texts:
        try:
            resp = _ollama.embed(model="nomic-embed-text", input=uncached_texts)
            new_embeddings = resp["embeddings"] if isinstance(resp, dict) else resp.embeddings
            for rel_path, emb in zip(uncached_rels, new_embeddings, strict=False):
                embeddings[rel_path] = emb
                try:
                    mtime = str((vault.root / rel_path).stat().st_mtime)
                except OSError:
                    mtime = ""
                cache[rel_path] = {"mtime": mtime, "embedding": emb}
        except Exception:
            # Embedding failure — skip semantic drift entirely
            return []

    # Save updated cache
    with contextlib.suppress(Exception):
        vault.write(cache_rel, json.dumps(cache, ensure_ascii=False) + "\n")

    # Compare all pairs
    findings: list[LintFinding] = []
    rels = [rel for rel, _ in note_texts if rel in embeddings]
    seen_pairs: set[tuple[str, str]] = set()
    for i, rel_a in enumerate(rels):
        emb_a = embeddings[rel_a]
        for rel_b in rels[i + 1 :]:
            emb_b = embeddings[rel_b]
            if len(emb_a) != len(emb_b):
                continue
            sim = _cosine_similarity(emb_a, emb_b)
            if sim >= 0.92:
                pair = (min(rel_a, rel_b), max(rel_a, rel_b))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                findings.append(
                    LintFinding(
                        category="semantic_drift",
                        severity="info",
                        path=rel_a,
                        message=(
                            f"high similarity ({sim:.2f}) with {rel_b} — "
                            f"possible near-duplicate"
                        ),
                    )
                )
    return findings


def run(vault: Vault, *, scope: str | None = None) -> list[LintFinding]:
    """Run every available check and return the combined list of findings.

    Checks run in roughly increasing-cost order. Output order is
    deterministic — each check's findings are appended in source order,
    so re-running on an unchanged vault produces byte-identical output.
    """
    return [
        *find_dead_links(vault, scope=scope),
        *find_orphans(vault, scope=scope),
        *find_missing_frontmatter(vault, scope=scope),
        *find_empty_sections(vault, scope=scope),
        *find_duplicate_titles(vault, scope=scope),
        *find_broken_markdown_links(vault, scope=scope),
        *find_huge_notes(vault, scope=scope),
        *find_unused_tags(vault, scope=scope),
        *find_stale_claims(vault, scope=scope),
        *find_semantic_drift(vault, scope=scope),
    ]
