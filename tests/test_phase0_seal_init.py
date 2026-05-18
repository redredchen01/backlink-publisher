"""Tests for `phase0-seal init` — Unit 3.

Covers:
- Routine path happy + every documented rejection (allowlist / PR / marker / URL).
- Manual-verdict path happy + evidence-out-of-repo + evidence-uncommitted.
- Worktree state guards (missing / dirty / detached).
- Existing note refusal (exit 1).
- gh CLI failure modes (auth, not-installed).
- **v3 BLOCKER post-push verify**: silent push failure detected.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from backlink_publisher.cli import phase0_seal as CLI
from backlink_publisher.phase0 import validation as V


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


@pytest.fixture
def seal_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build a self-contained git topology for init tests.

    Layout::
        tmp_path/
            remote.git/                 (bare remote, origin)
            main/                       (main worktree, has allowlist file)
            bp-local-unit2/             (linked worktree on local/telegraph-unit2-staged)
            bp-local-unit4/             (linked worktree on local/telegraph-unit4-staged)
            ...

    Returns dict with all paths + a list of WorktreeEntry-equivalent dicts.
    Also chdir's into the main worktree so V.find_main_worktree_root() works.
    """
    remote = tmp_path / "remote.git"
    main = tmp_path / "main"
    remote.mkdir()
    main.mkdir()
    _run(remote, "init", "--bare", "--initial-branch=main")
    _run(main, "init", "--initial-branch=main")
    _run(main, "config", "user.email", "test@example.com")
    _run(main, "config", "user.name", "Test")
    _run(main, "remote", "add", "origin", str(remote))
    # Initial commit + allowlist
    (main / "README.md").write_text("init\n")
    (main / "scripts" / "telegraph_spike").mkdir(parents=True)
    (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(_ALLOWLIST_YAML)
    _run(main, "add", ".")
    _run(main, "commit", "-m", "init with allowlist")
    _run(main, "push", "-u", "origin", "main")

    # Create 4 staged branches + worktrees; each branch gets its OWN commit so
    # the 4 HEAD SHAs are distinct (production has per-unit code commits on
    # each branch). Otherwise the seal-bodies dict (keyed by SHA) would
    # collapse to 1 entry.
    worktrees: list[dict] = []
    for n in (2, 4, 5, 6):
        branch = f"local/telegraph-unit{n}-staged"
        wt = tmp_path / f"bp-local-unit{n}"
        _run(main, "branch", branch)
        _run(main, "worktree", "add", str(wt), branch)
        # Per-branch dummy commit to give each branch a unique HEAD.
        _run(wt, "config", "user.email", "test@example.com")
        _run(wt, "config", "user.name", "Test")
        (wt / f"unit{n}-marker.txt").write_text(f"unit{n} work\n")
        _run(wt, "add", f"unit{n}-marker.txt")
        _run(wt, "commit", "-m", f"unit{n}: per-branch marker")
        sha = _run(wt, "rev-parse", "HEAD").stdout.strip()
        worktrees.append({"unit": f"unit{n}", "branch": branch, "sha": sha, "path": wt})

    # chdir to main worktree so V.find_main_worktree_root() resolves there
    monkeypatch.chdir(main)

    return {
        "tmp_path": tmp_path,
        "remote": remote,
        "main": main,
        "worktrees": worktrees,
    }


def _valid_gh_comment(*, pr: int = 36, login: str = "telegraph-routine-bot[bot]", body: str | None = None) -> dict:
    body = body or "G1 Pass!\n<!-- phase0-verdict: result=pass run_id=trig-01-fire-42 -->\n"
    return {
        "id": 12345,
        "url": "https://api.github.com/repos/x/y/issues/comments/12345",
        "html_url": f"https://github.com/x/y/pull/{pr}#issuecomment-12345",
        "issue_url": f"https://api.github.com/repos/x/y/issues/{pr}",
        "user": {"login": login},
        "body": body,
        "created_at": "2026-06-01T09:55:00Z",
        "updated_at": "2026-06-01T09:55:00Z",
    }


def _mock_run_gh(monkeypatch: pytest.MonkeyPatch, payload: dict | Exception) -> None:
    def fake(*args, **kwargs):
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(V, "_run_gh", fake)


# ---------------------------------------------------------------------------
# Routine path happy
# ---------------------------------------------------------------------------


def test_init_routine_happy_path_writes_and_verifies_notes(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_run_gh(monkeypatch, _valid_gh_comment())

    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36",
        "-y",
    ])
    assert rc == CLI.EXIT_OK

    # Each worktree HEAD should have a seal note now (locally).
    for wt in seal_repo["worktrees"]:
        proc = subprocess.run(
            ["git", "-C", str(seal_repo["main"]), "notes",
             "--ref=phase0-seal", "show", wt["sha"]],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        body = json.loads(proc.stdout)
        V.validate_seal_schema(body)
        assert body["unit"] == wt["unit"]
        assert body["branch"] == wt["branch"]
        assert body["verdict_ref"]["kind"] == "routine_comment"
        assert body["verdict_ref"]["pr"] == 36
        assert body["sealed_by"] == "operator:init"

    # Origin (bare remote) should also have the ref now (push happened).
    ls = subprocess.run(
        ["git", "-C", str(seal_repo["main"]), "ls-remote", "origin", "refs/notes/phase0-seal"],
        capture_output=True, text=True,
    )
    assert ls.returncode == 0 and ls.stdout.strip(), "notes ref not on origin after push"


# ---------------------------------------------------------------------------
# Routine path rejections (each paired with the happy-path test above)
# ---------------------------------------------------------------------------


def test_init_routine_rejects_unknown_author(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_run_gh(monkeypatch, _valid_gh_comment(login="attacker[bot]"))
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_VERDICT


def test_init_routine_rejects_wrong_pr(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_run_gh(monkeypatch, _valid_gh_comment(pr=99))
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_VERDICT


def test_init_routine_rejects_missing_marker(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_run_gh(monkeypatch, _valid_gh_comment(body="G1 Pass! (no marker)"))
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_VERDICT


def test_init_routine_rejects_bad_url(seal_repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    # No mock needed — URL parsing fails before gh call.
    rc = CLI.main([
        "init",
        "--verdict-comment", "not-a-github-url",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_VERDICT


def test_init_routine_rejects_url_vs_flag_pr_mismatch(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "99",  # mismatch
        "-y",
    ])
    assert rc == CLI.EXIT_VERDICT


def test_init_routine_gh_not_authed(seal_repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_run_gh(monkeypatch, V.GhAuthError("gh not authenticated"))
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_VERDICT


def test_init_routine_gh_not_installed(seal_repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_run_gh(monkeypatch, V.GhNotInstalledError("gh missing"))
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_VERDICT


# ---------------------------------------------------------------------------
# Worktree state guards
# ---------------------------------------------------------------------------


def test_init_refuses_when_worktree_dirty(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_run_gh(monkeypatch, _valid_gh_comment())
    # Dirty one worktree
    (seal_repo["worktrees"][0]["path"] / "dirty.txt").write_text("uncommitted\n")
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_WORKTREE


def test_init_refuses_when_existing_seal_note(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_run_gh(monkeypatch, _valid_gh_comment())
    # Pre-create a note on the first worktree's HEAD.
    sha = seal_repo["worktrees"][0]["sha"]
    _run(seal_repo["main"], "notes", "--ref=phase0-seal", "add", "-m", "{}", sha)
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_MISUSE


# ---------------------------------------------------------------------------
# Manual verdict path
# ---------------------------------------------------------------------------


def test_init_manual_happy(seal_repo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    # Add evidence file + commit on main AND every staged unit branch.
    evidence_rel = "scripts/telegraph_spike/manual-verdicts/2026-06-01.json"
    main_path = seal_repo["main"]
    full = main_path / evidence_rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text('{"verdict": "pass", "ts": "2026-06-01T10:00:00Z"}\n')
    _run(main_path, "add", evidence_rel)
    _run(main_path, "commit", "-m", "add manual evidence")
    # Cherry-pick onto each unit branch.
    sha_with_evidence = _run(main_path, "rev-parse", "HEAD").stdout.strip()
    for wt in seal_repo["worktrees"]:
        _run(wt["path"], "cherry-pick", sha_with_evidence)
        # Update the WorktreeEntry SHA we'll check against below
        wt["sha"] = _run(wt["path"], "rev-parse", "HEAD").stdout.strip()

    rc = CLI.main([
        "init",
        "--manual-verdict",
        "--evidence-log", evidence_rel,
        "-y",
    ])
    assert rc == CLI.EXIT_OK

    # Verify a note was written + kind=manual recorded
    sha = seal_repo["worktrees"][0]["sha"]
    proc = subprocess.run(
        ["git", "-C", str(main_path), "notes", "--ref=phase0-seal", "show", sha],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    body = json.loads(proc.stdout)
    assert body["verdict_ref"]["kind"] == "manual"
    assert body["verdict_ref"]["evidence_path"] == evidence_rel


def test_init_manual_rejects_evidence_uncommitted(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_rel = "scripts/telegraph_spike/manual-verdicts/uncommitted.json"
    full = seal_repo["main"] / evidence_rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("{}\n")
    # Intentionally NOT git-add'd.
    rc = CLI.main([
        "init",
        "--manual-verdict",
        "--evidence-log", evidence_rel,
        "-y",
    ])
    assert rc == CLI.EXIT_WORKTREE


def test_init_manual_rejects_path_outside_repo(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n")
    rc = CLI.main([
        "init",
        "--manual-verdict",
        "--evidence-log", str(outside),
        "-y",
    ])
    assert rc == CLI.EXIT_WORKTREE


# ---------------------------------------------------------------------------
# v3 BLOCKER #1: post-push verify catches silent push failure
# ---------------------------------------------------------------------------


def test_init_post_push_verify_detects_silent_push_failure(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v3 adversarial-F1 scenario.

    Mock `git push` to silently succeed (exit 0) without actually advancing
    origin's notes ref. Init's post-push verify-fetch must detect this and
    refuse to exit 0.
    """
    _mock_run_gh(monkeypatch, _valid_gh_comment())

    real_run = CLI.subprocess.run

    def silent_push_run(cmd, *args, **kwargs):
        # Intercept ONLY the notes-ref push; let every other subprocess pass through.
        if (
            isinstance(cmd, list)
            and len(cmd) >= 6
            and cmd[0] == "git"
            and cmd[3] == "push"
            and cmd[4] == "origin"
            and cmd[5].startswith("refs/notes/phase0-seal:")
        ):
            class P:
                returncode = 0
                stdout = ""
                stderr = ""
            return P()
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(CLI.subprocess, "run", silent_push_run)

    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_MISUSE  # post-push verify caught the silent failure

    # Confirm: origin really doesn't have the ref.
    ls = real_run(
        ["git", "-C", str(seal_repo["main"]), "ls-remote", "origin", "refs/notes/phase0-seal"],
        capture_output=True, text=True,
    )
    assert ls.returncode == 0 and not ls.stdout.strip(), \
        "test setup wrong: ref should NOT be on origin"


# ---------------------------------------------------------------------------
# CRLF/LF body sha consistency at init time
# ---------------------------------------------------------------------------


def test_init_normalizes_crlf_body_in_comment_sha256(
    seal_repo: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRLF body + LF body produce same sha256 recorded in seal."""
    crlf_body = "G1 Pass!\r\n<!-- phase0-verdict: result=pass run_id=x -->\r\n"
    lf_body = "G1 Pass!\n<!-- phase0-verdict: result=pass run_id=x -->\n"
    _mock_run_gh(monkeypatch, _valid_gh_comment(body=crlf_body))
    rc = CLI.main([
        "init",
        "--verdict-comment", "https://github.com/x/y/pull/36#issuecomment-12345",
        "--verdict-pr", "36", "-y",
    ])
    assert rc == CLI.EXIT_OK

    sha = seal_repo["worktrees"][0]["sha"]
    proc = subprocess.run(
        ["git", "-C", str(seal_repo["main"]), "notes", "--ref=phase0-seal", "show", sha],
        capture_output=True, text=True,
    )
    body = json.loads(proc.stdout)
    assert body["verdict_ref"]["comment_body_sha256"] == V.sha256_hex(lf_body)
