"""Tests for the six new lint checks introduced in Phase 4."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextvault import config
from contextvault.lint.checks import (
    find_broken_markdown_links,
    find_duplicate_titles,
    find_empty_sections,
    find_huge_notes,
    find_missing_frontmatter,
    find_unused_tags,
    run,
)
from contextvault.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    config.bootstrap_vault(tmp_path)
    return Vault(tmp_path)


class TestFindMissingFrontmatter:
    def test_flags_note_without_type(self, vault: Vault) -> None:
        vault.write("notes/a.md", "no frontmatter here\n")
        vault.write(
            "notes/b.md", "---\ntype: note\n---\n\nhas type\n"
        )
        findings = find_missing_frontmatter(vault)
        paths = {f.path for f in findings}
        assert "notes/a.md" in paths
        assert "notes/b.md" not in paths

    def test_skips_meta_dir(self, vault: Vault) -> None:
        # .vault-meta files must not appear in lint output
        findings = find_missing_frontmatter(vault)
        assert all(not f.path.startswith(".vault-meta/") for f in findings)


class TestFindEmptySections:
    def test_heading_with_no_body(self, vault: Vault) -> None:
        vault.write(
            "notes/x.md",
            "---\ntype: note\n---\n\n## Has content\n\nbody\n\n## Empty\n\n## Next\n\nfoo\n",
        )
        findings = find_empty_sections(vault)
        messages = [f.message for f in findings]
        assert any("Empty" in m for m in messages)
        # Sections with content must not be flagged
        assert not any("Has content" in m for m in messages)

    def test_heading_at_end_of_file(self, vault: Vault) -> None:
        vault.write(
            "notes/y.md", "---\ntype: note\n---\n\n## Trailing\n"
        )
        findings = find_empty_sections(vault)
        assert any("Trailing" in f.message for f in findings)


class TestFindDuplicateTitles:
    def test_flags_duplicates(self, vault: Vault) -> None:
        vault.write("a/Foo.md", "x\n")
        vault.write("b/Foo.md", "y\n")
        findings = find_duplicate_titles(vault)
        paths = {f.path for f in findings}
        assert "a/Foo.md" in paths
        assert "b/Foo.md" in paths

    def test_unique_titles_not_flagged(self, vault: Vault) -> None:
        vault.write("Unique.md", "x\n")
        findings = find_duplicate_titles(vault)
        assert not any(f.path == "Unique.md" for f in findings)


class TestFindBrokenMarkdownLinks:
    def test_relative_link_to_missing_file(self, vault: Vault) -> None:
        vault.write(
            "notes/src.md",
            "see [other](missing.md) for details\n",
        )
        findings = find_broken_markdown_links(vault)
        assert any("missing.md" in f.message for f in findings)

    def test_existing_link_not_flagged(self, vault: Vault) -> None:
        vault.write("notes/src.md", "see [t](target.md)\n")
        vault.write("notes/target.md", "ok\n")
        findings = find_broken_markdown_links(vault)
        assert not any("target.md" in f.message for f in findings)

    def test_http_link_ignored(self, vault: Vault) -> None:
        vault.write("notes/src.md", "see [external](https://example.com)\n")
        findings = find_broken_markdown_links(vault)
        assert findings == []


class TestFindHugeNotes:
    def test_flags_oversized(self, vault: Vault) -> None:
        # Just under the threshold → not flagged; just over → flagged
        small_body = "x" * (100 * 1024)
        big_body = "x" * (210 * 1024)
        vault.write("small.md", small_body)
        vault.write("big.md", big_body)
        findings = find_huge_notes(vault)
        paths = {f.path for f in findings}
        assert "big.md" in paths
        assert "small.md" not in paths


class TestFindUnusedTags:
    def test_single_use_tag(self, vault: Vault) -> None:
        vault.write(
            "notes/a.md",
            "---\ntype: note\ntags: [authentication, lonely-tag]\n---\n\nbody\n",
        )
        vault.write(
            "notes/b.md",
            "---\ntype: note\ntags: [authentication]\n---\n\nbody\n",
        )
        findings = find_unused_tags(vault)
        messages = [f.message for f in findings]
        # 'lonely-tag' appears once → flagged
        assert any("lonely-tag" in m for m in messages)
        # 'authentication' appears twice → not flagged
        assert not any("authentication" in m for m in messages)

    def test_infrastructural_tags_ignored(self, vault: Vault) -> None:
        vault.write(
            "notes/a.md",
            "---\ntype: session\ntags: [session]\n---\n\nbody\n",
        )
        findings = find_unused_tags(vault)
        assert findings == []


class TestRun:
    def test_combines_all_checks(self, vault: Vault) -> None:
        # Build a vault that triggers each category
        vault.write("orphan.md", "nobody links here\n")  # orphan + missing frontmatter
        vault.write("dead.md", "[[Nonexistent]]\n")  # dead link + missing frontmatter
        vault.write(
            "section.md", "---\ntype: note\n---\n\n## Empty\n\n## Next\n\nx\n"
        )
        findings = run(vault)
        cats = {f.category for f in findings}
        expected = {"orphan", "dead_link", "missing_frontmatter", "empty_section"}
        assert expected.issubset(cats)
