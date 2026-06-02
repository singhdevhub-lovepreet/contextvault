"""Config resolution, vault bootstrap, and token generation.

The config has three layers, last writer wins:

  1. Built-in defaults                                          (always)
  2. ``~/.config/contextvault/config.toml`` (if present)        (user)
  3. CLI ``--vault`` flag or ``VAULT_PATH`` env var             (call)

A bearer token for the loopback HTTP server is generated at first ``init``
and stored at ``~/.config/contextvault/token`` with ``0600`` perms. The MCP
stdio server inherits parent-process trust and does not consult the token.
"""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_VAULT_PATH",
    "Config",
    "bootstrap_vault",
    "config_dir",
    "config_path",
    "generate_token",
    "load",
    "resolve_vault_path",
    "token_path",
    "write_default_config",
]


DEFAULT_VAULT_PATH = Path("~/Documents/ContextVault").expanduser()
_DEFAULT_HTTP_PORT = 7842


@dataclass(frozen=True, slots=True)
class Config:
    vault_path: Path
    http_port: int = _DEFAULT_HTTP_PORT


# ---- paths ----------------------------------------------------------------


def config_dir() -> Path:
    """Return ``~/.config/contextvault/`` (XDG-style, even on macOS).

    macOS lacks a strict XDG home but ``~/.config/`` is the de-facto
    portable choice for developer CLIs — keeps configs out of
    ``~/Library/`` where Time Machine and Spotlight scrape them.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path("~/.config").expanduser()
    return base / "contextvault"


def config_path() -> Path:
    return config_dir() / "config.toml"


def token_path() -> Path:
    return config_dir() / "token"


# ---- read -----------------------------------------------------------------


def load() -> Config:
    """Load the on-disk config, falling back to defaults.

    Does NOT consult the CLI / env layers — that's :func:`resolve_vault_path`'s
    job. This function exists so callers that already have CLI args can
    decide on layering themselves.
    """
    path = config_path()
    if not path.is_file():
        return Config(vault_path=DEFAULT_VAULT_PATH)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    vault_section = raw.get("vault") or {}
    server_section = raw.get("server") or {}
    return Config(
        vault_path=Path(vault_section.get("path", DEFAULT_VAULT_PATH)).expanduser(),
        http_port=int(server_section.get("port", _DEFAULT_HTTP_PORT)),
    )


def resolve_vault_path(cli_override: str | None) -> Path:
    """Resolve the vault path with full layering.

    Order: CLI flag → ``VAULT_PATH`` env → config.toml → default.
    """
    if cli_override:
        return Path(cli_override).expanduser().absolute()
    env = os.environ.get("VAULT_PATH")
    if env:
        return Path(env).expanduser().absolute()
    return load().vault_path.absolute()


# ---- write ----------------------------------------------------------------


def write_default_config(vault_path: Path) -> Path:
    """Write a starter ``config.toml`` pointing at ``vault_path``.

    Does not overwrite an existing file — returns the existing path
    unchanged. Callers that want to force-overwrite should ``unlink``
    first.
    """
    target = config_path()
    if target.is_file():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f'[vault]\npath = "{vault_path!s}"\n\n'
        f"[server]\nport = {_DEFAULT_HTTP_PORT}\n",
        encoding="utf-8",
    )
    return target


def generate_token(*, force: bool = False) -> Path:
    """Generate and persist a bearer token at ``token_path()`` with 0600 perms.

    If a token already exists and ``force=False``, the existing token is
    left in place (idempotent ``init``). Returns the token file path.
    """
    target = token_path()
    if target.is_file() and not force:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    # Write with strict perms from the start — never create the file with
    # default mode and then chmod, which leaves a window where the token
    # is world-readable.
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    return target


# ---- vault bootstrap ------------------------------------------------------


_HOT_STARTER = """\
# Hot cache

This is the global hot cache. ContextVault auto-updates it after every
session capture. The format is intentionally short (~500 words) so it
fits comfortably in a model's context window when loaded at SessionStart.

## Last updated
{now}

## Recent context
_Nothing captured yet. Run a Claude Code session in any workspace and
the capture pipeline will populate this file._
"""

_INDEX_STARTER = """\
# Index

Top-level pointers across the vault.

## Workspaces
_None yet. The capture pipeline creates one per cwd you work in._

## Shared entities
_People, orgs, products that span workspaces. Lives in `entities/`._

## Shared concepts
_Frameworks, ideas that span workspaces. Lives in `concepts/`._
"""


def bootstrap_vault(vault_path: Path) -> None:
    """Create the standard vault subtree under ``vault_path``.

    Idempotent: rerunning over an existing vault touches no existing file,
    only creates missing directories and missing starter files.
    """
    from datetime import UTC, datetime

    vault_path.mkdir(parents=True, exist_ok=True)

    # Top-level dirs
    for sub in ("workspaces", "entities", "concepts"):
        (vault_path / sub).mkdir(exist_ok=True)

    # Meta dirs
    meta = vault_path / ".vault-meta"
    meta.mkdir(exist_ok=True)
    (meta / "locks").mkdir(exist_ok=True)
    (meta / "bm25").mkdir(exist_ok=True)
    (meta / "chunks").mkdir(exist_ok=True)

    # Starter files (only if missing)
    hot = vault_path / "hot.md"
    if not hot.exists():
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        hot.write_text(_HOT_STARTER.format(now=now), encoding="utf-8")

    index = vault_path / "index.md"
    if not index.exists():
        index.write_text(_INDEX_STARTER, encoding="utf-8")
