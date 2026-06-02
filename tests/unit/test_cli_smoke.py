"""Phase 0 smoke: parser builds, --version works, every subcommand exits 64."""

from __future__ import annotations

import pytest

from contextvault.cli import build_parser, main


def test_parser_builds() -> None:
    parser = build_parser()
    assert parser.prog == "contextvault"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "contextvault" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["serve", "--mcp"],
        ["capture", "--cwd", "/tmp"],
        ["lint"],
        ["hot"],
        ["workspaces", "ls"],
        ["adapter", "add", "claude-code"],
        ["ingest", "/tmp/x.md"],
        ["save", "--title", "T", "--type", "session"],
    ],
)
def test_stubbed_subcommand_returns_64(argv: list[str]) -> None:
    """Stub subcommands return 64 (EX_USAGE) until their phase lands."""
    assert main(argv) == 64


def test_unknown_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["nonexistent"])
    assert exc.value.code == 2  # argparse default


def test_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
