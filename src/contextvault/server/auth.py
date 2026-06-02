"""Bearer-token authentication for the loopback HTTP server.

Tokens are generated at ``contextvault init`` and stored at
``~/.config/contextvault/token`` with ``0600`` perms. Validation uses
:func:`secrets.compare_digest` to avoid timing-leak side channels.

This module is *only* used by the HTTP transport. The MCP stdio transport
inherits parent-process trust and does not consult tokens.
"""

from __future__ import annotations

import secrets

from contextvault import config

__all__ = ["check_bearer", "load_expected_token"]


def load_expected_token() -> str | None:
    """Return the token from disk, or ``None`` if it doesn't exist."""
    path = config.token_path()
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def check_bearer(authorization_header: str | None, *, expected: str | None) -> bool:
    """Validate ``Authorization: Bearer <token>`` against ``expected``.

    Returns False (never True) when ``expected`` is None — i.e. when no
    token is on disk — so a misconfigured server can never accidentally
    accept all requests.
    """
    if not expected:
        return False
    if not authorization_header:
        return False
    prefix = "Bearer "
    if not authorization_header.startswith(prefix):
        return False
    presented = authorization_header[len(prefix):].strip()
    return secrets.compare_digest(presented, expected)
