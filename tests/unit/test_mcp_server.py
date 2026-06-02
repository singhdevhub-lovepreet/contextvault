"""Tests for contextvault.server.mcp — stdio JSON-RPC dispatcher."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from contextvault import config
from contextvault.server.mcp import MCPServer, tool_definitions


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    config.bootstrap_vault(v)
    return v


@pytest.fixture
def server(vault_path: Path) -> MCPServer:
    return MCPServer(vault_path)


def _call(server: MCPServer, method: str, params: object = None, req_id: int = 1) -> dict:
    req = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        req["params"] = params
    raw = server.handle(json.dumps(req))
    assert raw is not None
    return json.loads(raw)


class TestProtocol:
    def test_initialize(self, server: MCPServer) -> None:
        resp = _call(server, "initialize", {})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "contextvault"
        assert "tools" in result["capabilities"]

    def test_notifications_initialized_no_response(self, server: MCPServer) -> None:
        # Notification has no id → never produces a response.
        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert server.handle(notif) is None

    def test_ping(self, server: MCPServer) -> None:
        resp = _call(server, "ping")
        assert resp["result"] == {}

    def test_unknown_method(self, server: MCPServer) -> None:
        resp = _call(server, "bogus")
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_invalid_json(self, server: MCPServer) -> None:
        raw = server.handle("not json")
        assert raw is not None
        resp = json.loads(raw)
        assert resp["error"]["code"] == -32700

    def test_invalid_request_shape(self, server: MCPServer) -> None:
        raw = server.handle('"not an object"')
        assert raw is not None
        resp = json.loads(raw)
        assert resp["error"]["code"] == -32600


class TestToolsList:
    def test_tools_list_returns_six(self, server: MCPServer) -> None:
        resp = _call(server, "tools/list")
        names = [t["name"] for t in resp["result"]["tools"]]
        assert set(names) == {
            "recall",
            "recent_sessions",
            "save_note",
            "list_workspaces",
            "graph_neighborhood",
            "lint",
        }

    def test_definitions_have_schemas(self) -> None:
        for tool in tool_definitions():
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


class TestToolsCall:
    def test_list_workspaces_call(self, server: MCPServer) -> None:
        resp = _call(server, "tools/call", {"name": "list_workspaces", "arguments": {}})
        result = resp["result"]
        assert result["isError"] is False
        content = result["content"][0]
        assert content["type"] == "text"
        # The inner text is JSON; parse to confirm shape
        parsed = json.loads(content["text"])
        assert isinstance(parsed, list)

    def test_recall_call_workspace(self, vault_path: Path, server: MCPServer) -> None:
        from contextvault.vault import Vault

        Vault(vault_path).write(
            "workspaces/-Users-x-y/sessions/n.md",
            "---\nworkspace: -Users-x-y\n---\n\nrefactor authentication module\n",
        )
        resp = _call(
            server,
            "tools/call",
            {
                "name": "recall",
                "arguments": {
                    "query": "authentication",
                    "cwd": "/Users/x/y",
                    "scope": "workspace",
                },
            },
        )
        parsed = json.loads(resp["result"]["content"][0]["text"])
        assert any("Users-x-y" in h["path"] for h in parsed)

    def test_unknown_tool_returns_error(self, server: MCPServer) -> None:
        resp = _call(
            server, "tools/call", {"name": "nonexistent", "arguments": {}}
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    def test_bad_arguments_returns_error(self, server: MCPServer) -> None:
        # recall with empty query → ToolError → mapped to JSON-RPC error
        resp = _call(
            server,
            "tools/call",
            {"name": "recall", "arguments": {"query": "", "scope": "global"}},
        )
        assert "error" in resp
        assert "empty" in resp["error"]["message"]


class TestRunLoop:
    def test_run_processes_stdin_to_stdout(self, server: MCPServer) -> None:
        # Feed two requests + one notification, confirm two responses.
        req1 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        req2 = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        stdin = io.StringIO("\n".join([req1, notif, req2]) + "\n")
        stdout = io.StringIO()
        server.run(stdin=stdin, stdout=stdout)
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["id"] == 1
        assert json.loads(lines[1])["id"] == 2
