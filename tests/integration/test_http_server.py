"""Integration tests for the loopback HTTP server.

Spins the server up in a background thread and exercises every endpoint
via stdlib ``http.client``. Hermetic — never touches the network beyond
``127.0.0.1``.
"""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import pytest

from contextvault import config
from contextvault.server.http import LoopbackHTTPServer
from contextvault.vault import Vault


@pytest.fixture
def server(tmp_path: Path) -> LoopbackHTTPServer:
    vault_dir = tmp_path / "vault"
    config.bootstrap_vault(vault_dir)
    Vault(vault_dir).write(
        "workspaces/-Users-alice-foo/sessions/2026-06-01-x.md",
        "---\nworkspace: -Users-alice-foo\n---\n\n## Goal\n\nrefactor auth\n",
    )

    srv = LoopbackHTTPServer(
        vault_path=vault_dir,
        expected_token="t-secret-123",
        port=0,  # ephemeral
    )
    srv.start()
    yield srv
    srv.stop()


def _request(
    server: LoopbackHTTPServer,
    method: str,
    path: str,
    *,
    token: str | None = "t-secret-123",
    body: bytes | None = None,
) -> tuple[int, dict[str, object]]:
    host, port = server.address
    conn = HTTPConnection(host, port, timeout=5.0)
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    conn.close()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"_raw": raw}
    return resp.status, payload


class TestAuth:
    def test_missing_token_rejected(self, server: LoopbackHTTPServer) -> None:
        status, payload = _request(server, "GET", "/list_workspaces", token=None)
        assert status == 401
        assert payload == {"error": "unauthorized"}

    def test_wrong_token_rejected(self, server: LoopbackHTTPServer) -> None:
        status, _ = _request(server, "GET", "/list_workspaces", token="wrong")
        assert status == 401

    def test_correct_token_accepted(self, server: LoopbackHTTPServer) -> None:
        status, payload = _request(server, "GET", "/list_workspaces")
        assert status == 200
        assert isinstance(payload, list)


class TestEndpoints:
    def test_recall_workspace(self, server: LoopbackHTTPServer) -> None:
        qs = urlencode({"query": "auth", "cwd": "/Users/alice/foo", "scope": "workspace"})
        status, payload = _request(server, "GET", f"/recall?{qs}")
        assert status == 200
        assert isinstance(payload, list)
        assert any("alice" in h["path"] for h in payload)

    def test_recall_missing_query_400(self, server: LoopbackHTTPServer) -> None:
        status, payload = _request(server, "GET", "/recall?cwd=/Users/x")
        assert status == 400
        assert "error" in payload

    def test_recent_sessions(self, server: LoopbackHTTPServer) -> None:
        qs = urlencode({"cwd": "/Users/alice/foo", "limit": "3"})
        status, payload = _request(server, "GET", f"/recent_sessions?{qs}")
        assert status == 200
        assert isinstance(payload, list)
        assert len(payload) == 1

    def test_list_workspaces(self, server: LoopbackHTTPServer) -> None:
        status, payload = _request(server, "GET", "/list_workspaces")
        assert status == 200
        assert isinstance(payload, list)
        assert len(payload) == 1

    def test_save_note_round_trip(self, server: LoopbackHTTPServer) -> None:
        body = json.dumps(
            {
                "title": "From HTTP",
                "body": "this came from the HTTP endpoint",
                "workspace": "global",
            }
        ).encode()
        status, payload = _request(server, "POST", "/save_note", body=body)
        assert status == 200
        assert payload["path"].startswith("notes/")

        # Confirm it surfaces via recall
        qs = urlencode({"query": "HTTP endpoint", "scope": "global"})
        status, recall_payload = _request(server, "GET", f"/recall?{qs}")
        assert status == 200
        assert any("From-HTTP" in h["path"] for h in recall_payload)

    def test_save_note_invalid_json_400(self, server: LoopbackHTTPServer) -> None:
        status, _payload = _request(
            server, "POST", "/save_note", body=b"not json"
        )
        assert status == 400

    def test_unknown_path_404(self, server: LoopbackHTTPServer) -> None:
        status, _ = _request(server, "GET", "/nonexistent")
        assert status == 404

    def test_lint(self, server: LoopbackHTTPServer) -> None:
        qs = urlencode({"scope": "global"})
        status, payload = _request(server, "GET", f"/lint?{qs}")
        assert status == 200
        assert isinstance(payload, list)


class TestLoopbackOnly:
    def test_binds_to_loopback(self, server: LoopbackHTTPServer) -> None:
        host, _ = server.address
        assert host == "127.0.0.1"
