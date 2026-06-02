"""ContextVault CLI entrypoint.

The CLI is the user-facing surface. Every subcommand maps 1:1 to a function
in the package; there is no shell-layer logic beyond argument parsing and
exit-code translation.

Subcommands are stubbed in this scaffold and filled in over phases 1-6.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def _add_init(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("init", help="scaffold vault, write config, generate token")
    p.add_argument("--vault", help="vault root path (default: ~/Documents/ContextVault)")


def _add_serve(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("serve", help="run MCP and/or HTTP server")
    p.add_argument("--mcp", action="store_true", help="run stdio MCP server")
    p.add_argument("--http", action="store_true", help="run loopback HTTP server")
    p.add_argument("--both", action="store_true", help="run both (default)")
    p.add_argument("--port", type=int, default=7842, help="HTTP port (loopback only)")


def _add_capture(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("capture", help="capture a Claude Code session transcript")
    p.add_argument("--cwd", required=True, help="working directory of the session")
    p.add_argument(
        "--mode",
        choices=["incremental", "final", "sweep"],
        default="incremental",
        help="capture mode (see docs/session-capture.md)",
    )
    p.add_argument("--allow-egress", action="store_true", help="enable LLM-quality summarizer")
    p.add_argument("--allow-redacted", action="store_true", help="proceed past redaction matches")


def _add_recall(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("recall", help="search the vault and return top-k hits")
    p.add_argument("query", help="search query")
    p.add_argument("--cwd", help="working directory (default: $PWD)")
    p.add_argument(
        "--scope", choices=["workspace", "global"], default="workspace", help="search scope"
    )
    p.add_argument("-k", type=int, default=10, help="number of results")


def _add_lint(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("lint", help="health-check the vault")
    p.add_argument("--cwd", help="working directory (default: $PWD)")
    p.add_argument("--scope", choices=["workspace", "global"], default="workspace")


def _add_hot(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("hot", help="print the workspace hot cache to stdout")
    p.add_argument("--workspace", help="workspace id (default: encode $PWD)")


def _add_workspaces(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("workspaces", help="manage workspaces")
    wsub = p.add_subparsers(dest="workspaces_cmd", required=True)
    wsub.add_parser("ls", help="list known workspaces")


def _add_adapter(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("adapter", help="install or remove a client adapter")
    asub = p.add_subparsers(dest="adapter_cmd", required=True)
    clients = ["claude-code", "cursor", "claude-desktop", "continue-dev", "hermes"]
    for op in ("add", "remove"):
        ap = asub.add_parser(op, help=f"{op} an adapter")
        ap.add_argument("client", choices=clients)


def _add_ingest(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("ingest", help="ingest a file or URL into the vault")
    p.add_argument("source", help="file path or URL")
    p.add_argument("--workspace", help="target workspace id")


def _add_save(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("save", help="save stdin as a note")
    p.add_argument("--title", required=True)
    p.add_argument("--type", required=True, dest="note_type")
    p.add_argument("--tags", nargs="*", default=[])
    p.add_argument("--workspace", help="target workspace id (default: current)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextvault",
        description="LLM-agnostic per-workspace memory backed by Obsidian.",
    )
    parser.add_argument("--version", action="version", version=_get_version())
    sub = parser.add_subparsers(dest="command", required=True)
    _add_init(sub)
    _add_serve(sub)
    _add_capture(sub)
    _add_recall(sub)
    _add_lint(sub)
    _add_hot(sub)
    _add_workspaces(sub)
    _add_adapter(sub)
    _add_ingest(sub)
    _add_save(sub)
    return parser


def _get_version() -> str:
    from contextvault import __version__

    return f"contextvault {__version__}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Phase 0 scaffold: every subcommand is unimplemented and exits 64
    # (EX_USAGE) with a clear message. Phases 1-6 fill these in.
    sys.stderr.write(
        f"contextvault: '{args.command}' is not implemented yet (phase 0 scaffold).\n"
        f"Track progress: https://github.com/contextvault/contextvault\n"
    )
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
