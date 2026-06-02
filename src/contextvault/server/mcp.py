"""Stdio MCP server — JSON-RPC 2.0 over stdin/stdout.

Implements the minimum slice of the Model Context Protocol needed for
Claude Code / Cursor / Claude Desktop / Continue.dev to call our six
tools:

  * ``initialize`` → return our protocol version + capabilities
  * ``notifications/initialized`` → ack, no response
  * ``tools/list`` → enumerate tools with JSON-schema for arguments
  * ``tools/call`` → dispatch to the backend in :mod:`.tools`

We deliberately avoid pulling in the ``mcp`` PyPI SDK as a hard dep:
the spec is small enough that a stdlib implementation is clearer and
keeps the install footprint minimal. If we later need streaming /
resource subscriptions, switch to the SDK.

Each request is one line of JSON on stdin; each response is one line of
JSON on stdout. Errors are mapped to JSON-RPC error envelopes.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from contextvault.server import tools

__all__ = ["MCPServer", "tool_definitions"]


_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC error codes (per MCP / JSON-RPC 2.0 spec)
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def tool_definitions() -> list[dict[str, Any]]:
    """The static MCP ``tools/list`` payload describing all six tools."""
    return [
        {
            "name": "recall",
            "description": (
                "Search the workspace memory vault and return top-K hits. "
                "Scope defaults to the caller's current workspace."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "cwd": {"type": "string", "description": "absolute working dir"},
                    "scope": {"type": "string", "enum": ["workspace", "global"]},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
            },
        },
        {
            "name": "recent_sessions",
            "description": "Return the N most recent captured session notes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
        },
        {
            "name": "save_note",
            "description": "Write a Markdown note with frontmatter into the vault.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "type": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "workspace": {"type": "string"},
                },
                "required": ["title", "body"],
            },
        },
        {
            "name": "list_workspaces",
            "description": "Enumerate known workspaces with last-updated + session count.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "graph_neighborhood",
            "description": "Expand wikilink neighbors of a note up to a given depth.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 4},
                },
                "required": ["path"],
            },
        },
        {
            "name": "lint",
            "description": "Find orphan pages and dead wikilinks in the vault.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                    "scope": {"type": "string", "enum": ["workspace", "global"]},
                },
            },
        },
    ]


class MCPServer:
    """Stdio JSON-RPC dispatcher. Drives one read/write loop per session."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "initialize": self._initialize,
            "notifications/initialized": self._noop,
            "tools/list": self._list_tools,
            "tools/call": self._call_tool,
            "ping": self._ping,
        }

    # ---- public entry --------------------------------------------------

    def run(self, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        """Block reading line-delimited JSON-RPC from stdin until EOF.

        Designed to be invoked from ``contextvault serve --mcp`` where the
        parent process (Claude Code / Cursor) opens a stdio pipe.
        """
        in_ = stdin or sys.stdin
        out = stdout or sys.stdout
        for line in in_:
            line = line.strip()
            if not line:
                continue
            response = self.handle(line)
            if response is not None:
                out.write(response + "\n")
                out.flush()

    def handle(self, raw: str) -> str | None:
        """Handle one raw JSON-RPC frame. Returns the response line, or None
        for notification messages (which carry no ``id`` and never reply).
        """
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            return self._error(None, _PARSE_ERROR, "invalid JSON")

        if not isinstance(req, dict):
            return self._error(None, _INVALID_REQUEST, "request must be a JSON object")

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}
        is_notification = "id" not in req

        if not isinstance(method, str):
            return None if is_notification else self._error(
                req_id, _INVALID_REQUEST, "missing method"
            )

        handler = self._handlers.get(method)
        if handler is None:
            return None if is_notification else self._error(
                req_id, _METHOD_NOT_FOUND, f"method not found: {method}"
            )

        try:
            result = handler(params if isinstance(params, dict) else {})
        except tools.ToolError as exc:
            return self._error(req_id, _INVALID_PARAMS, str(exc))
        except Exception as exc:  # last-line catch-all so bad input never crashes the loop
            return self._error(req_id, _INTERNAL_ERROR, str(exc))

        if is_notification:
            return None
        return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})

    # ---- handlers ------------------------------------------------------

    def _initialize(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "contextvault",
                "version": __import__("contextvault").__version__,
            },
        }

    def _noop(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def _ping(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def _list_tools(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": tool_definitions()}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise tools.ToolError("arguments must be an object")

        result = self._invoke(name, arguments)
        # MCP expects the result wrapped in a content block list. We
        # serialize the tool's structured result as JSON and present it
        # as a single text content item — the most universally-supported
        # shape across MCP clients.
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
            ],
            "isError": False,
        }

    def _invoke(self, name: object, arguments: dict[str, Any]) -> Any:
        if name == "recall":
            return tools.recall(
                self.vault_path,
                str(arguments.get("query", "")),
                cwd=arguments.get("cwd"),
                scope=str(arguments.get("scope", "workspace")),
                top_k=int(arguments.get("top_k", 10)),
            )
        if name == "recent_sessions":
            return tools.recent_sessions(
                self.vault_path,
                cwd=arguments.get("cwd"),
                limit=int(arguments.get("limit", 5)),
            )
        if name == "save_note":
            return tools.save_note(
                self.vault_path,
                str(arguments.get("body", "")),
                title=str(arguments.get("title", "")),
                note_type=str(arguments.get("type", "note")),
                tags=arguments.get("tags") or [],
                cwd=arguments.get("cwd"),
                workspace=str(arguments.get("workspace", "current")),
            )
        if name == "list_workspaces":
            return tools.list_workspaces(self.vault_path)
        if name == "graph_neighborhood":
            return tools.graph_neighborhood(
                self.vault_path,
                str(arguments.get("path", "")),
                depth=int(arguments.get("depth", 1)),
            )
        if name == "lint":
            return tools.lint(
                self.vault_path,
                cwd=arguments.get("cwd"),
                scope=str(arguments.get("scope", "workspace")),
            )
        raise tools.ToolError(f"unknown tool: {name!r}")

    # ---- error envelope ------------------------------------------------

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> str:
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": code, "message": message},
            }
        )
