"""Best-effort secret redaction.

Before any captured text is written to the vault, every line passes
through this filter. Lines matching one of the patterns below are masked
to ``[REDACTED]`` and an entry is appended to the redaction audit log
recording the *offset and pattern name* — never the original content.

Patterns covered:

  * AWS access keys (``AKIA...``, ``ASIA...``)
  * Authorization bearer tokens (``Authorization: Bearer ...``)
  * Env-style assignments where the key implies a secret (
    ``API_KEY=``, ``SECRET=``, ``PASSWORD=``, ``TOKEN=``)
  * JWTs (three base64url-encoded segments separated by ``.``)

This is *best-effort*. It will miss novel patterns, custom-named env
vars, and inline secrets in prose. Document this limitation prominently
in :doc:`/privacy` and never rely on it as a substitute for hygiene.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["RedactionEvent", "redact_line", "redact_text"]


REDACTED_MARKER = "[REDACTED]"


@dataclass(frozen=True, slots=True)
class RedactionEvent:
    """Audit record for a redaction. The original content is never stored."""

    line_offset: int
    pattern: str


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("authorization_bearer", re.compile(r"Authorization\s*:\s*Bearer\s+\S+", re.IGNORECASE)),
    # KEY=value form. The key name must signal "secret" — we keep this list
    # conservative to limit false positives on legitimate config like
    # PATH=, USER=, etc. The optional ``[A-Za-z0-9]*[_-]`` prefix catches
    # qualified names like ``DATABASE_PASSWORD`` or ``GH_TOKEN`` while
    # word-boundary anchored to avoid swallowing unrelated text.
    (
        "secret_env_assignment",
        re.compile(
            r"\b(?:[A-Za-z][A-Za-z0-9]*[_-])?"
            r"(?:API[_-]?KEY|SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE[_-]?KEY)"
            r"\s*=\s*\S+",
            re.IGNORECASE,
        ),
    ),
    # JSON-shaped: "api_key": "value", "token": "value"
    (
        "secret_json_field",
        re.compile(
            r'"(?:api[_-]?key|secret|password|token|private[_-]?key)"\s*:\s*"[^"]+"',
            re.IGNORECASE,
        ),
    ),
    ("jwt", re.compile(r"\bey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]


def redact_line(line: str) -> tuple[str, str | None]:
    """Return ``(possibly-redacted-line, pattern-name-or-None)``.

    The whole line is replaced with :data:`REDACTED_MARKER` on a match
    rather than just the secret — we err on the side of redacting too
    much because the surrounding context often leaks the secret too.
    """
    for name, pattern in _PATTERNS:
        if pattern.search(line):
            return REDACTED_MARKER, name
    return line, None


def redact_text(text: str) -> tuple[str, list[RedactionEvent]]:
    """Redact ``text`` line-by-line. Returns ``(text, events)``.

    ``line_offset`` is the 0-indexed line position within ``text``.
    """
    out_lines: list[str] = []
    events: list[RedactionEvent] = []
    for i, line in enumerate(text.splitlines(keepends=True)):
        had_trailing_newline = line.endswith(("\n", "\r"))
        stripped = line.rstrip("\r\n")
        new_line, pattern = redact_line(stripped)
        if pattern is not None:
            events.append(RedactionEvent(line_offset=i, pattern=pattern))
        if had_trailing_newline:
            # Preserve the original newline character(s)
            tail = line[len(stripped):]
            out_lines.append(new_line + tail)
        else:
            out_lines.append(new_line)
    return "".join(out_lines), events
