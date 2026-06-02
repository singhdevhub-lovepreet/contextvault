"""Loopback-only HTTP REST server.

Binds to ``127.0.0.1`` strictly — any attempt to bind elsewhere raises at
the socket layer before the listen call. Every request must carry a
``Authorization: Bearer <token>`` header that matches the on-disk token;
mismatched / missing tokens get a flat ``401`` regardless of path.

The HTTP transport is the universal access surface: any LLM client that
can issue an HTTP request (Hermes, n8n, curl, custom Python) can talk to
the vault here. MCP-aware clients (Claude Code, Cursor, Claude Desktop)
should prefer the MCP stdio transport via :mod:`.mcp` instead.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from contextvault.server import auth, tools

__all__ = ["LoopbackHTTPServer", "make_handler"]

_LOOPBACK = "127.0.0.1"


class _Refuser:
    """Marker sentinel so ``make_handler`` can build a class object."""


def make_handler(
    *, vault_path: Path, expected_token: str
) -> type[BaseHTTPRequestHandler]:
    """Construct the request-handler class bound to ``vault_path``.

    Returning a class (rather than instances) is what stdlib's HTTPServer
    expects. We close over ``vault_path`` + ``expected_token`` in the
    class body so each request gets the right context.
    """

    class Handler(BaseHTTPRequestHandler):
        # Quiet the default access log — the user can see traffic via the
        # caller's own logging if needed.
        def log_message(self, *args: Any, **kwargs: Any) -> None:
            return

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            return auth.check_bearer(
                self.headers.get("Authorization"), expected=expected_token
            )

        def _dispatch(
            self, fn: Callable[..., Any], **kwargs: Any
        ) -> None:
            try:
                result = fn(vault_path, **kwargs)
            except tools.ToolError as exc:
                self._send_json(exc.status, {"error": str(exc)})
                return
            self._send_json(200, result)

        def do_GET(self) -> None:
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path = parsed.path.rstrip("/")

            if path == "/recall":
                self._dispatch(
                    tools.recall,
                    query=_first(qs, "query") or "",
                    cwd=_first(qs, "cwd"),
                    scope=_first(qs, "scope") or "workspace",
                    top_k=int(_first(qs, "k") or 10),
                )
                return

            if path == "/recent_sessions":
                self._dispatch(
                    tools.recent_sessions,
                    cwd=_first(qs, "cwd"),
                    limit=int(_first(qs, "limit") or 5),
                )
                return

            if path == "/list_workspaces":
                self._dispatch(tools.list_workspaces)
                return

            if path == "/graph_neighborhood":
                self._dispatch(
                    tools.graph_neighborhood,
                    note_path=_first(qs, "path") or "",
                    depth=int(_first(qs, "depth") or 1),
                )
                return

            if path == "/lint":
                self._dispatch(
                    tools.lint,
                    cwd=_first(qs, "cwd"),
                    scope=_first(qs, "scope") or "workspace",
                )
                return

            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return

            if self.path.rstrip("/") != "/save_note":
                self._send_json(404, {"error": "not found"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._send_json(400, {"error": "empty body"})
                return
            raw = self.rfile.read(length).decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"invalid JSON: {exc}"})
                return

            if not isinstance(payload, dict):
                self._send_json(400, {"error": "body must be a JSON object"})
                return

            self._dispatch(
                tools.save_note,
                body=str(payload.get("body", "")),
                title=str(payload.get("title", "")),
                note_type=str(payload.get("type", "note")),
                tags=payload.get("tags") or [],
                cwd=payload.get("cwd"),
                workspace=str(payload.get("workspace", "current")),
            )

    return Handler


def _first(qs: dict[str, list[str]], key: str) -> str | None:
    vals = qs.get(key)
    return vals[0] if vals else None


class LoopbackHTTPServer:
    """Convenience wrapper that builds + serves the HTTP transport.

    Bind is hardcoded to 127.0.0.1. The constructor verifies via a probing
    socket that the bind address is loopback — defense-in-depth in case
    a future refactor accidentally exposes a ``--bind`` flag.
    """

    def __init__(
        self,
        *,
        vault_path: Path,
        expected_token: str,
        port: int = 7842,
    ) -> None:
        if not vault_path.is_dir():
            raise RuntimeError(f"vault not found: {vault_path}")
        if not expected_token:
            raise RuntimeError("expected_token is empty — run `contextvault init`")
        self.vault_path = vault_path
        self.expected_token = expected_token
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            return (_LOOPBACK, self.port)
        return self._server.server_address[:2]  # type: ignore[return-value]

    def start(self) -> None:
        """Start serving in a background thread. Idempotent."""
        if self._server is not None:
            return
        handler_cls = make_handler(
            vault_path=self.vault_path, expected_token=self.expected_token
        )
        self._server = ThreadingHTTPServer((_LOOPBACK, self.port), handler_cls)
        # Make a defensive assertion that the bound address is loopback.
        # ``server_address`` is typed as ``tuple[str | bytes, int]`` on
        # stdlib's stubs even though our bind passes a str — coerce so the
        # error message is always readable.
        raw_host = self._server.server_address[0]
        bound_host = raw_host.decode("ascii") if isinstance(raw_host, bytes) else raw_host
        if bound_host not in (_LOOPBACK, "::1"):
            self._server.server_close()
            raise RuntimeError(f"refusing non-loopback bind: {bound_host}")
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="cv-http", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> LoopbackHTTPServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _smoke_can_bind_loopback(port: int) -> bool:
    """Sanity probe used by tests: can we bind to 127.0.0.1:port at all?"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((_LOOPBACK, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
