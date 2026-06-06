"""Tests for contextvault.capture.runner — orchestration end-to-end."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from contextvault import config
from contextvault.capture.runner import run_capture

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts" / "golden.jsonl"
FIXTURE_CWD = "/Users/lsingh/Desktop/experiments"
FIXTURE_WORKSPACE = "-Users-lsingh-Desktop-experiments"
FIXTURE_SID = "abcd1234-5678-9abc-def0-123456789abc"


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    config.bootstrap_vault(v)
    return v


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    """Mirror Claude Code's ~/.claude/projects/<encoded-cwd>/<sid>.jsonl layout."""
    root = tmp_path / "projects"
    workspace_dir = root / FIXTURE_WORKSPACE
    workspace_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, workspace_dir / f"{FIXTURE_SID}.jsonl")
    return root


class TestRunCapture:
    def test_writes_session_note(
        self, vault_path: Path, projects_root: Path
    ) -> None:
        result = run_capture(vault_path, FIXTURE_CWD, projects_root=projects_root)
        assert result is not None
        assert result.wrote_note is True
        assert result.workspace == FIXTURE_WORKSPACE
        assert result.session_id == FIXTURE_SID

        note = vault_path / result.session_note_path
        assert note.is_file()
        content = note.read_text(encoding="utf-8")
        assert "type: session" in content
        assert "OAuth2 token rotation" in content
        assert "PyJWT" in content  # decision survived
        assert "## Files touched" in content
        assert "oauth.py" in content
        assert "## Commands" in content
        assert "pytest" in content
        assert "## Errors" in content
        assert "## Open TODOs" in content
        assert "refresh-token" in content
        assert "## Entities" in content

    def test_updates_hot_cache(
        self, vault_path: Path, projects_root: Path
    ) -> None:
        run_capture(vault_path, FIXTURE_CWD, projects_root=projects_root)
        hot = vault_path / "workspaces" / FIXTURE_WORKSPACE / "hot.md"
        assert hot.is_file()
        assert "OAuth2 token rotation" in hot.read_text(encoding="utf-8")

    def test_appends_log(self, vault_path: Path, projects_root: Path) -> None:
        run_capture(vault_path, FIXTURE_CWD, projects_root=projects_root)
        log_path = vault_path / "workspaces" / FIXTURE_WORKSPACE / "log.md"
        assert log_path.is_file()
        assert "session `abcd1234`" in log_path.read_text(encoding="utf-8")

    def test_advances_checkpoint(
        self, vault_path: Path, projects_root: Path
    ) -> None:
        run_capture(vault_path, FIXTURE_CWD, projects_root=projects_root)
        cp = vault_path / ".vault-meta" / "captured.json"
        assert cp.is_file()
        data = json.loads(cp.read_text(encoding="utf-8"))
        # Last uuid in fixture is u-004 (the TODO message)
        assert data[FIXTURE_SID] == "u-004"

    def test_idempotent_second_run_no_new_entries(
        self, vault_path: Path, projects_root: Path
    ) -> None:
        first = run_capture(vault_path, FIXTURE_CWD, projects_root=projects_root)
        assert first is not None and first.wrote_note
        # Re-running with no new transcript lines must short-circuit
        second = run_capture(vault_path, FIXTURE_CWD, projects_root=projects_root)
        assert second is not None
        assert second.new_entries == 0
        assert second.wrote_note is False

    def test_no_transcript_returns_none(
        self, vault_path: Path, tmp_path: Path
    ) -> None:
        empty_projects = tmp_path / "empty-projects"
        empty_projects.mkdir()
        result = run_capture(
            vault_path, FIXTURE_CWD, projects_root=empty_projects
        )
        assert result is None

    def test_explicit_session_id_hit(
        self, vault_path: Path, projects_root: Path
    ) -> None:
        result = run_capture(
            vault_path, FIXTURE_CWD, session_id=FIXTURE_SID, projects_root=projects_root
        )
        assert result is not None
        assert result.wrote_note is True
        assert result.session_id == FIXTURE_SID

    def test_nonexistent_session_id_returns_none(
        self, vault_path: Path, projects_root: Path
    ) -> None:
        result = run_capture(
            vault_path,
            FIXTURE_CWD,
            session_id="00000000-0000-0000-0000-000000000000",
            projects_root=projects_root,
        )
        assert result is None

    def test_redaction_masks_aws_key(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """When a secret appears in a user message, the on-disk note must redact it."""
        # Build a tiny transcript with a leaked AWS key in a user message
        proj = tmp_path / "p" / "-Users-test-secret"
        proj.mkdir(parents=True)
        leaked_sid = "leak1234-aaaa-bbbb-cccc-ffffffffffff"
        line1 = (
            '{"parentUuid": null, "isSidechain": false, "type": "user",'
            ' "message": {"role": "user", "content":'
            ' "set up env: API_KEY=sk-leaked-1234567890abc"},'
            f' "uuid": "u-x", "timestamp": "2026-06-02T10:00:00Z",'
            ' "userType": "external", "entrypoint": "cli",'
            ' "cwd": "/Users/test/secret",'
            f' "sessionId": "{leaked_sid}"}}\n'
        )
        (proj / f"{leaked_sid}.jsonl").write_text(line1, encoding="utf-8")

        result = run_capture(
            vault_path, "/Users/test/secret", projects_root=tmp_path / "p"
        )
        assert result is not None and result.wrote_note
        assert result.redactions >= 1
        # Goal line, which contained the secret, must be redacted in output
        note = vault_path / result.session_note_path
        content = note.read_text(encoding="utf-8")
        assert "sk-leaked-1234567890abc" not in content
        assert "API_KEY=" not in content
        # Audit log present
        redacted_log = vault_path / ".vault-meta" / "redacted.log"
        assert redacted_log.is_file()
        log_content = redacted_log.read_text(encoding="utf-8")
        assert "secret_env_assignment" in log_content
        assert leaked_sid[:8] in log_content
