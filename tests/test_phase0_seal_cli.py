"""Tests for the `phase0-seal` CLI dispatcher — Unit 2.

Smoke: parser builds, subcommands dispatch, NotImplementedError handlers
return exit code 99 (EXIT_NOT_IMPLEMENTED). Real subcommand behavior lands
in Units 3-5.
"""

from __future__ import annotations

import pytest

from backlink_publisher.cli import phase0_seal as CLI


def test_parser_help_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    """`phase0-seal --help` shows all 5 subcommands."""
    with pytest.raises(SystemExit) as excinfo:
        CLI.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for sub in ("init", "show", "verify", "reseal", "verify-hook"):
        assert sub in out


def test_init_requires_either_verdict_comment_or_manual(capsys: pytest.CaptureFixture[str]) -> None:
    """argparse mutex group rejects neither."""
    with pytest.raises(SystemExit) as excinfo:
        CLI.main(["init"])
    assert excinfo.value.code == 2  # argparse error


def test_show_handler_stub_returns_not_implemented() -> None:
    rc = CLI.main(["show", "--format", "markdown"])
    assert rc == CLI.EXIT_NOT_IMPLEMENTED


def test_verify_handler_stub_returns_not_implemented() -> None:
    rc = CLI.main(["verify"])
    assert rc == CLI.EXIT_NOT_IMPLEMENTED


def test_reseal_handler_stub_returns_not_implemented() -> None:
    rc = CLI.main(["reseal", "-y"])
    assert rc == CLI.EXIT_NOT_IMPLEMENTED


def test_verify_hook_handler_stub_returns_not_implemented() -> None:
    rc = CLI.main(["verify-hook", "--stdin-lines"])
    assert rc == CLI.EXIT_NOT_IMPLEMENTED


def test_show_format_choice_validation() -> None:
    """argparse rejects invalid --format choices."""
    with pytest.raises(SystemExit) as excinfo:
        CLI.main(["show", "--format", "xml"])
    assert excinfo.value.code == 2
