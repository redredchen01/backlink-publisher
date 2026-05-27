"""Tests for `phase0-seal verify-hook` — Plan 009 Unit 5.

In-process tests: monkey-patch stdin + invoke `CLI.main(["verify-hook",
"--stdin-lines"])`; assert exit code + structured stderr JSON.

The bash hook integration (subprocess against tmp repo + real
`.git/hooks/pre-push`) lives in `tests/test_phase0_seal_hook.py`.

Test scenarios drawn from plan v3 §417-429:
- happy multi-ref (2 valid lines → exit 0)
- one valid + one invalid → exit 1
- non-matching remote_ref → fall-through (exit 0, empty stderr)
- note absent at SHA → exit 1 with no-seal-note
- detached HEAD case is enforced in the bash hook, not the Python subcommand
- direct SHA push key on remote_ref: local_ref bypassed
"""

from __future__ import annotations

import io
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from backlink_publisher.cli import phase0_seal as CLI


def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check,
    )


_ALLOWLIST_YAML = textwrap.dedent("""\
    schema_version: 1
    authorized_authors:
      - login: telegraph-routine-bot[bot]
        routine_id: trig_01U8
        captured_at: "2026-05-25T10:00:00Z"
        captured_by: operator
        run_id_observed: trig-01-fire-1
""")


def _make_seal_body(*, unit: str, branch: str, sha: str, main_sha: str) -> dict:
    """Schema-valid seal note body for hook validation."""
    return {
        "unit": unit,
        "branch": branch,
        "main_sha": main_sha,
        "sealed_at": "2026-06-01T10:00:00Z",
        "sealed_by": "operator:init",
        "verdict_ref": {
            "kind": "routine_comment",
            "pr": 36,
            "comment_url": "https://github.com/x/y/pull/36#issuecomment-12345",
            "comment_id": 12345,
            "comment_author": "telegraph-routine-bot[bot]",
            "comment_created_at": "2026-05-25T10:00:00Z",
            "comment_updated_at": "2026-05-25T10:00:00Z",
            "comment_body_sha256": "0" * 64,
        },
    }


@pytest.fixture
def hook_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Minimal tmp repo with 2 staged-branch worktrees + a seal note on each
    HEAD. Mirrors seal_repo from test_phase0_seal_init.py but without the
    bare remote (verify-hook doesn't push; only reads local notes)."""
    remote = tmp_path / "remote.git"
    main = tmp_path / "main"
    remote.mkdir()
    main.mkdir()
    _run(remote, "init", "--bare", "--initial-branch=main")
    _run(main, "init", "--initial-branch=main")
    _run(main, "config", "user.email", "test@example.com")
    _run(main, "config", "user.name", "Test")
    _run(main, "remote", "add", "origin", str(remote))
    (main / "README.md").write_text("init\n")
    (main / "scripts" / "telegraph_spike").mkdir(parents=True)
    (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(_ALLOWLIST_YAML)
    _run(main, "add", ".")
    _run(main, "commit", "-m", "init")
    _run(main, "push", "-u", "origin", "main")
    main_sha = _run(main, "rev-parse", "HEAD").stdout.strip()

    worktrees: list[dict] = []
    for n in (2, 4):
        branch = f"local/telegraph-unit{n}-staged"
        wt = tmp_path / f"bp-local-unit{n}"
        _run(main, "branch", branch)
        _run(main, "worktree", "add", str(wt), branch)
        _run(wt, "config", "user.email", "test@example.com")
        _run(wt, "config", "user.name", "Test")
        (wt / f"unit{n}.txt").write_text(f"unit{n}\n")
        _run(wt, "add", f"unit{n}.txt")
        _run(wt, "commit", "-m", f"unit{n}")
        sha = _run(wt, "rev-parse", "HEAD").stdout.strip()
        body = _make_seal_body(unit=f"unit{n}", branch=branch, sha=sha, main_sha=main_sha)
        _run(main, "notes", "--ref=phase0-seal", "add",
             "-m", json.dumps(body), sha)
        worktrees.append({"unit": f"unit{n}", "branch": branch, "sha": sha, "path": wt})

    monkeypatch.chdir(main)
    return {
        "tmp_path": tmp_path,
        "remote": remote,
        "main": main,
        "main_sha": main_sha,
        "worktrees": worktrees,
    }


def _feed_stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def _capture_stderr(capsys: pytest.CaptureFixture) -> list[dict]:
    """Read captured stderr, parse each non-empty line as JSON, return list."""
    captured = capsys.readouterr()
    out: list[dict] = []
    for line in captured.err.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestVerifyHookHappy:
    def test_two_valid_refs_exit_0(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        lines = []
        for wt in hook_repo["worktrees"]:
            lines.append(
                f"refs/heads/{wt['branch']} {wt['sha']} "
                f"refs/heads/{wt['branch']} 0000000000000000000000000000000000000000"
            )
        _feed_stdin(monkeypatch, "\n".join(lines) + "\n")

        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_OK
        records = _capture_stderr(capsys)
        assert len(records) == 2
        assert all(r["result"] == "pass" for r in records)
        assert {r["unit"] for r in records} == {"unit2", "unit4"}

    def test_no_matching_refs_falls_through_exit_0(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """Non-Telegraph pushes (feature branches, main, etc.) get no records
        and exit 0 — the hook leaves them alone."""
        _feed_stdin(monkeypatch,
            "refs/heads/feat/foo abc123 refs/heads/feat/foo def456\n"
            "refs/heads/main 1234567 refs/heads/main 9876543\n"
        )

        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_OK
        records = _capture_stderr(capsys)
        assert records == []


# ---------------------------------------------------------------------------
# Failure paths — each paired with the happy variant above
# ---------------------------------------------------------------------------


class TestVerifyHookFailures:
    def test_one_valid_one_invalid_exit_1(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        wt_ok = hook_repo["worktrees"][0]
        # Synthesize a bogus SHA that has no seal note
        bogus_sha = "deadbeef" * 5
        lines = [
            f"refs/heads/{wt_ok['branch']} {wt_ok['sha']} "
            f"refs/heads/{wt_ok['branch']} 0000000000000000000000000000000000000000",
            f"refs/heads/local/telegraph-unit5-staged {bogus_sha} "
            f"refs/heads/local/telegraph-unit5-staged 0000000000000000000000000000000000000000",
        ]
        _feed_stdin(monkeypatch, "\n".join(lines) + "\n")

        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_MISUSE  # non-zero
        records = _capture_stderr(capsys)
        passes = [r for r in records if r["result"] == "pass"]
        fails = [r for r in records if r["result"] == "fail"]
        assert len(passes) == 1
        assert len(fails) == 1
        assert fails[0]["reason"] == "no-seal-note"

    def test_no_seal_note_at_sha(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        bogus_sha = "f" * 40
        _feed_stdin(monkeypatch,
            f"refs/heads/local/telegraph-unit2-staged {bogus_sha} "
            f"refs/heads/local/telegraph-unit2-staged 0000000000000000000000000000000000000000\n"
        )
        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_MISUSE
        records = _capture_stderr(capsys)
        assert records[0]["reason"] == "no-seal-note"
        assert records[0]["sha"] == bogus_sha

    def test_unit_mismatch_fails(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """Pushing unit2's SHA AT unit4's remote_ref must fail with
        unit-mismatch (the seal says unit2 but the remote_ref says unit4)."""
        wt2 = next(w for w in hook_repo["worktrees"] if w["unit"] == "unit2")
        _feed_stdin(monkeypatch,
            f"refs/heads/{wt2['branch']} {wt2['sha']} "
            f"refs/heads/local/telegraph-unit4-staged 0000000000000000000000000000000000000000\n"
        )
        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_MISUSE
        records = _capture_stderr(capsys)
        assert "unit-mismatch" in records[0]["reason"]
        assert "unit2" in records[0]["reason"]
        assert "unit4" in records[0]["reason"]

    def test_direct_sha_push_via_local_ref_bypass_still_validated(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """v1 adversarial probe #5: push the SHA via a non-Telegraph local_ref
        like HEAD or a feature branch, but target a staged remote_ref. Hook
        keys on remote_ref so the seal-validation MUST still fire. Verify
        the SAME seal note (unit2 on unit2 staged branch) passes regardless
        of local_ref."""
        wt2 = next(w for w in hook_repo["worktrees"] if w["unit"] == "unit2")
        # Use a non-Telegraph local_ref to prove remote_ref drives validation
        _feed_stdin(monkeypatch,
            f"HEAD {wt2['sha']} refs/heads/{wt2['branch']} "
            f"0000000000000000000000000000000000000000\n"
        )
        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_OK
        records = _capture_stderr(capsys)
        assert records[0]["result"] == "pass"
        assert records[0]["unit"] == "unit2"

    def test_delete_telegraph_staged_branch_refused(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """A push that deletes a staged branch arrives as local_sha=zero;
        post-G1 the hook must refuse — staged branches are immutable history."""
        wt2 = next(w for w in hook_repo["worktrees"] if w["unit"] == "unit2")
        zero_sha = "0" * 40
        _feed_stdin(monkeypatch,
            f"(delete) {zero_sha} refs/heads/{wt2['branch']} {wt2['sha']}\n"
        )
        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_MISUSE
        records = _capture_stderr(capsys)
        assert records[0]["result"] == "fail"
        assert "delete" in records[0]["reason"]


# ---------------------------------------------------------------------------
# Argument / invocation contract
# ---------------------------------------------------------------------------


class TestVerifyHookInvocation:
    def test_without_stdin_lines_flag_is_misuse(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """The bash hook always passes --stdin-lines. Calling verify-hook
        without it is operator misuse; fail loud rather than read stdin
        silently."""
        rc = CLI.main(["verify-hook"])
        assert rc == CLI.EXIT_MISUSE
        records = _capture_stderr(capsys)
        assert records and records[0]["result"] == "misuse"

    def test_malformed_line_skipped_with_reason(
        self, hook_repo: dict,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """Lines with the wrong field count are reported as `skip` (not fail);
        no Telegraph-matched lines means exit 0 (fall-through)."""
        _feed_stdin(monkeypatch, "only-two-fields here\n")
        rc = CLI.main(["verify-hook", "--stdin-lines"])
        assert rc == CLI.EXIT_OK  # malformed line + no Telegraph match → fall-through
        records = _capture_stderr(capsys)
        assert records and records[0]["result"] == "skip"
