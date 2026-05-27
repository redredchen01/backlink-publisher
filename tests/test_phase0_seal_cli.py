"""Tests for the `phase0-seal` CLI dispatcher — Unit 2 (parser) + Unit 4 smoke.

Smoke: parser builds, all subcommands dispatch correctly. Unit 4 landed
show/verify/reseal; verify-hook stub still returns EXIT_NOT_IMPLEMENTED.
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


def test_show_no_notes_returns_misuse_not_not_implemented(tmp_path, monkeypatch) -> None:
    """show is now implemented: returns EXIT_MISUSE (no notes), not EXIT_NOT_IMPLEMENTED."""
    import subprocess
    bare = tmp_path / "r.git"
    main = tmp_path / "main"
    bare.mkdir(); main.mkdir()
    subprocess.run(["git", "init", "--bare", "--initial-branch=main"], cwd=bare, check=True, capture_output=True)
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=main, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=main, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=main, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=main, check=True, capture_output=True)
    (main / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=main, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=main, check=True, capture_output=True)
    monkeypatch.chdir(main)
    rc = CLI.main(["show"])
    assert rc == CLI.EXIT_MISUSE
    assert rc != CLI.EXIT_NOT_IMPLEMENTED


def test_show_format_choice_validation() -> None:
    """argparse rejects invalid --format choices."""
    with pytest.raises(SystemExit) as excinfo:
        CLI.main(["show", "--format", "xml"])
    assert excinfo.value.code == 2
