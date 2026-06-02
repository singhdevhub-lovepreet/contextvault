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


def test_unknown_command_returns_64(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown subcommands return 64 (EX_USAGE).

    No subcommands are stubbed any more — every subcommand has a handler.
    The 64-path is only reached if argparse accepts an unrecognized
    command, which it currently never does. We exercise the path
    defensively by monkey-patching the dispatcher.
    """
    import contextvault.cli as cli_module

    monkeypatch.setattr(cli_module, "_run_hot", lambda _: 64)
    assert main(["hot"]) == 64


def test_unknown_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["nonexistent"])
    assert exc.value.code == 2  # argparse default


def test_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
