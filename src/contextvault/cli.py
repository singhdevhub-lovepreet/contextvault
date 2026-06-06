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


def _add_sweep(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("sweep", help="capture sessions killed before Stop hook fired")
    p.add_argument(
        "--stable-seconds",
        type=int,
        default=90,
        help="minimum seconds since last transcript write before capturing (default: 90)",
    )


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
    _add_sweep(sub)
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

    dispatch = {
        "init": _run_init,
        "recall": _run_recall,
        "capture": _run_capture,
        "serve": _run_serve,
        "adapter": _run_adapter,
        "lint": _run_lint,
        "workspaces": _run_workspaces,
        "hot": _run_hot,
        "ingest": _run_ingest,
        "save": _run_save,
        "sweep": _run_sweep,
    }
    handler = dispatch.get(args.command)
    if handler is not None:
        return handler(args)

    sys.stderr.write(f"contextvault: unknown command '{args.command}'\n")
    return 64


def _run_init(args: argparse.Namespace) -> int:
    from contextvault import config

    vault_path = config.resolve_vault_path(args.vault)
    config.bootstrap_vault(vault_path)
    cfg_path = config.write_default_config(vault_path)
    token = config.generate_token()
    sys.stdout.write(
        f"vault:  {vault_path}\n"
        f"config: {cfg_path}\n"
        f"token:  {token}  (chmod 600, used by HTTP server only)\n"
    )
    return 0


def _run_capture(args: argparse.Namespace) -> int:
    from contextvault import config
    from contextvault.capture.runner import run_capture
    from contextvault.vault import VaultError
    from contextvault.workspace import WorkspaceError

    vault_path = config.resolve_vault_path(None)
    if not vault_path.is_dir():
        sys.stderr.write(
            f"contextvault capture: vault does not exist: {vault_path!s}\n"
            f"  Run `contextvault init` first.\n"
        )
        return 3

    try:
        result = run_capture(vault_path, args.cwd)
    except WorkspaceError as exc:
        sys.stderr.write(f"contextvault capture: {exc}\n")
        return 2
    except VaultError as exc:
        sys.stderr.write(f"contextvault capture: {exc}\n")
        return 3

    if result is None:
        sys.stdout.write("no transcript found for workspace\n")
        return 0

    if not result.wrote_note:
        sys.stdout.write(
            f"workspace={result.workspace} session={result.session_id[:8]} "
            f"new_entries={result.new_entries} (no signal extracted; nothing written)\n"
        )
        return 0

    sys.stdout.write(
        f"workspace={result.workspace} session={result.session_id[:8]} "
        f"new_entries={result.new_entries} redactions={result.redactions} "
        f"note={result.session_note_path}\n"
    )
    return 0


def _run_recall(args: argparse.Namespace) -> int:
    import json
    import os

    from contextvault import config, workspace
    from contextvault.retrieve.query import run_recall
    from contextvault.vault import VaultError

    vault_path = config.resolve_vault_path(None)
    cwd = args.cwd or os.environ.get("PWD") or os.getcwd()
    scope: str | None = None
    if args.scope == "workspace":
        try:
            scope = workspace.encode(cwd)
        except workspace.WorkspaceError as exc:
            sys.stderr.write(f"contextvault recall: {exc}\n")
            return 2

    try:
        hits = run_recall(vault_path, args.query, scope=scope, top_k=args.k)
    except VaultError as exc:
        sys.stderr.write(f"contextvault recall: {exc}\n")
        return 3

    json.dump(hits, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    from contextvault import config
    from contextvault.server import auth as server_auth
    from contextvault.server.http import LoopbackHTTPServer
    from contextvault.server.mcp import MCPServer

    vault_path = config.resolve_vault_path(None)
    if not vault_path.is_dir():
        sys.stderr.write(
            f"contextvault serve: vault not found at {vault_path}\n"
            f"  Run `contextvault init` first.\n"
        )
        return 3

    # If no transport flag specified, default to --both.
    run_mcp = bool(args.mcp or args.both or (not args.mcp and not args.http))
    run_http = bool(args.http or args.both)

    http_server: LoopbackHTTPServer | None = None
    if run_http:
        token = server_auth.load_expected_token()
        if not token:
            sys.stderr.write(
                "contextvault serve: no token at ~/.config/contextvault/token\n"
                "  Run `contextvault init` to generate one.\n"
            )
            return 3
        http_server = LoopbackHTTPServer(
            vault_path=vault_path, expected_token=token, port=args.port
        )
        http_server.start()
        sys.stderr.write(
            f"contextvault: HTTP serving on http://127.0.0.1:{http_server.address[1]}\n"
        )

    try:
        if run_mcp:
            MCPServer(vault_path).run()
        elif run_http:
            # HTTP-only: block until killed
            import signal
            stop = threading.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
            stop.wait()
    finally:
        if http_server is not None:
            http_server.stop()
    return 0


def _run_adapter(args: argparse.Namespace) -> int:
    from contextvault import adapters

    if args.adapter_cmd == "add":
        try:
            if args.client == "claude-code":
                lines = adapters.install_claude_code()
            elif args.client == "cursor":
                lines = adapters.install_cursor()
            else:
                sys.stderr.write(f"contextvault adapter add: unknown client {args.client!r}\n")
                return 2
        except RuntimeError as exc:
            sys.stderr.write(f"contextvault adapter add: {exc}\n")
            return 3
        for line in lines:
            sys.stdout.write(line + "\n")
        return 0

    if args.adapter_cmd == "remove":
        if args.client == "claude-code":
            lines = adapters.remove_claude_code()
        elif args.client == "cursor":
            lines = ["Cursor MCP entries are user-managed in ~/.cursor/mcp.json — remove manually."]
        else:
            sys.stderr.write(f"contextvault adapter remove: unknown client {args.client!r}\n")
            return 2
        for line in lines:
            sys.stdout.write(line + "\n")
        return 0

    sys.stderr.write("contextvault adapter: unknown subcommand\n")
    return 2


def _run_lint(args: argparse.Namespace) -> int:
    import json as _json
    import os

    from contextvault import config
    from contextvault.server import tools

    vault_path = config.resolve_vault_path(None)
    if not vault_path.is_dir():
        sys.stderr.write(f"contextvault lint: vault not found at {vault_path}\n")
        return 3

    cwd = args.cwd or os.environ.get("PWD") or os.getcwd()
    try:
        findings = tools.lint(vault_path, cwd=cwd, scope=args.scope)
    except tools.ToolError as exc:
        sys.stderr.write(f"contextvault lint: {exc}\n")
        return 2

    _json.dump(findings, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if findings else 0


def _run_workspaces(args: argparse.Namespace) -> int:
    import json as _json

    from contextvault import config
    from contextvault.server import tools

    if args.workspaces_cmd == "ls":
        vault_path = config.resolve_vault_path(None)
        if not vault_path.is_dir():
            sys.stderr.write(f"contextvault workspaces: vault not found at {vault_path}\n")
            return 3
        entries = tools.list_workspaces(vault_path)
        _json.dump(entries, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    sys.stderr.write("contextvault workspaces: unknown subcommand\n")
    return 2


def _run_hot(args: argparse.Namespace) -> int:
    import os

    from contextvault import config, workspace
    from contextvault.vault import Vault

    vault_path = config.resolve_vault_path(None)
    if not vault_path.is_dir():
        sys.stderr.write(f"contextvault hot: vault not found at {vault_path}\n")
        return 3

    if args.workspace:
        ws_id = args.workspace
        if not workspace.is_valid_id(ws_id):
            sys.stderr.write(f"contextvault hot: invalid workspace id {ws_id!r}\n")
            return 2
    else:
        cwd = os.environ.get("PWD") or os.getcwd()
        try:
            ws_id = workspace.encode(cwd)
        except workspace.WorkspaceError as exc:
            sys.stderr.write(f"contextvault hot: {exc}\n")
            return 2

    vault = Vault(vault_path)
    content = vault.read(f"workspaces/{ws_id}/hot.md")
    if content is None:
        content = vault.read("hot.md") or ""
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _run_ingest(args: argparse.Namespace) -> int:
    import os
    from urllib.parse import urlparse

    from contextvault import config, workspace
    from contextvault.server import tools

    vault_path = config.resolve_vault_path(None)
    if not vault_path.is_dir():
        sys.stderr.write(f"contextvault ingest: vault not found at {vault_path}\n")
        return 3

    source = args.source
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        sys.stderr.write(
            "contextvault ingest: URL ingestion is not yet supported "
            "(needs WebFetch + defuddle, planned for v0.2).\n"
        )
        return 64

    path = os.path.abspath(os.path.expanduser(source))
    if not os.path.isfile(path):
        sys.stderr.write(f"contextvault ingest: file not found: {source}\n")
        return 3

    with open(path, encoding="utf-8") as fh:
        body = fh.read()

    title = os.path.splitext(os.path.basename(path))[0]
    cwd = os.environ.get("PWD") or os.getcwd()
    if args.workspace:
        ws_arg = args.workspace
    else:
        try:
            workspace.encode(cwd)
            ws_arg = "current"
        except workspace.WorkspaceError:
            ws_arg = "global"

    try:
        result = tools.save_note(
            vault_path,
            body,
            title=title,
            note_type="source",
            cwd=cwd,
            workspace=ws_arg,
        )
    except tools.ToolError as exc:
        sys.stderr.write(f"contextvault ingest: {exc}\n")
        return 2

    sys.stdout.write(f"ingested → {result['path']}\n")
    return 0


def _run_save(args: argparse.Namespace) -> int:
    import os

    from contextvault import config
    from contextvault.server import tools

    vault_path = config.resolve_vault_path(None)
    if not vault_path.is_dir():
        sys.stderr.write(f"contextvault save: vault not found at {vault_path}\n")
        return 3

    body = sys.stdin.read()
    if not body.strip():
        sys.stderr.write("contextvault save: stdin is empty\n")
        return 2

    cwd = os.environ.get("PWD") or os.getcwd()
    ws_arg = args.workspace or "current"
    try:
        result = tools.save_note(
            vault_path,
            body,
            title=args.title,
            note_type=args.note_type,
            tags=args.tags or [],
            cwd=cwd,
            workspace=ws_arg,
        )
    except tools.ToolError as exc:
        sys.stderr.write(f"contextvault save: {exc}\n")
        return 2

    sys.stdout.write(f"saved → {result['path']}\n")
    return 0


def _run_sweep(args: argparse.Namespace) -> int:
    from contextvault import config
    from contextvault.capture.sweeper import run_sweep
    from contextvault.vault import VaultError

    vault_path = config.resolve_vault_path(None)
    if not vault_path.is_dir():
        sys.stderr.write(
            f"contextvault sweep: vault does not exist: {vault_path!s}\n"
            f"  Run `contextvault init` first.\n"
        )
        return 3

    try:
        captured = run_sweep(vault_path, stable_seconds=args.stable_seconds)
    except VaultError as exc:
        sys.stderr.write(f"contextvault sweep: {exc}\n")
        return 3

    if captured:
        sys.stdout.write(f"captured {len(captured)} session(s): {', '.join(s[:8] for s in captured)}\n")
    else:
        sys.stdout.write("no new sessions to capture\n")
    return 0


# A late stdlib import so the help-only path stays light.
import threading  # noqa: E402  — top-of-file would slow `--help` slightly

if __name__ == "__main__":
    raise SystemExit(main())
