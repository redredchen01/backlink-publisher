"""Tests for `phase0-seal show / verify / reseal` — Unit 4.

Tests pair positive+negative per the inverted-negative-assertion rule:
each positive test has a counterpart that asserts the opposite outcome
on a structurally different input.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from backlink_publisher.cli import phase0_seal as CLI

_ALLOWLIST_YAML = textwrap.dedent("""\
    schema_version: 1
    authorized_authors:
      - login: telegraph-routine-bot[bot]
        routine_id: trig_01U8
        captured_at: "2026-05-25T10:00:00Z"
        captured_by: operator
        run_id_observed: trig-01-fire-1
""")

_VERDICT_REF = {
    "kind": "routine_comment",
    "pr": 36,
    "comment_url": "https://github.com/owner/repo/pull/36#issuecomment-1",
    "comment_id": 1,
    "comment_author": "telegraph-routine-bot[bot]",
    "comment_created_at": "2026-05-25T10:00:00Z",
    "comment_updated_at": "2026-05-25T10:00:00Z",
    "comment_body_sha256": "abcdef1234567890" * 4,
}


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=check,
    )


@pytest.fixture
def sealed_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build a minimal git repo with seal notes pre-written.

    Topology:
        tmp_path/remote.git/  — bare remote (origin)
        tmp_path/main/        — main worktree with allowlist + 2 unit worktrees
        tmp_path/bp-local-unit2/  — linked worktree on local/telegraph-unit2-staged
        tmp_path/bp-local-unit4/  — linked worktree on local/telegraph-unit4-staged

    Returns dict:
        repo_root, unit2_sha, unit4_sha, unit2_branch, unit4_branch,
        unit2_seal_body, unit4_seal_body, main_sha
    """
    remote = tmp_path / "remote.git"
    main = tmp_path / "main"
    remote.mkdir(); main.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")
    _git(main, "init", "--initial-branch=main")
    for key, val in [("user.email", "t@t.com"), ("user.name", "T"), ("notes.rewriteRef", "")]:
        _git(main, "config", key, val)
    _git(main, "remote", "add", "origin", str(remote))
    (main / "scripts" / "telegraph_spike").mkdir(parents=True)
    (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(_ALLOWLIST_YAML)
    (main / "README.md").write_text("init\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "init")
    _git(main, "push", "-u", "origin", "main")
    main_sha = _git(main, "rev-parse", "origin/main").stdout.strip()

    unit_info = {}
    for n in (2, 4):
        branch = f"local/telegraph-unit{n}-staged"
        wt_path = tmp_path / f"bp-local-unit{n}"
        _git(main, "checkout", "-b", branch)
        (main / f"unit{n}.txt").write_text(f"unit{n}\n")
        _git(main, "add", ".")
        _git(main, "commit", "-m", f"unit{n} commit")
        sha = _git(main, "rev-parse", "HEAD").stdout.strip()
        _git(main, "push", "origin", branch)
        _git(main, "checkout", "main")  # must leave branch before worktree add
        _git(main, "worktree", "add", str(wt_path), branch)
        unit_info[n] = {"sha": sha, "branch": branch, "path": wt_path}

    # Write seal notes for each unit
    sealed_at = "2026-05-25T10:00:00Z"
    bodies = {}
    for n, info in unit_info.items():
        body = {
            "unit": f"unit{n}",
            "branch": info["branch"],
            "main_sha": main_sha,
            "sealed_at": sealed_at,
            "last_resealed_at": None,
            "sealed_by": "operator:init",
            "verdict_ref": _VERDICT_REF,
        }
        body_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
        _git(main, "notes", f"--ref={CLI._NOTES_REF}", "add", "-m", body_json, info["sha"])
        bodies[n] = body

    _git(main, "push", "origin", f"{CLI._NOTES_REF}:{CLI._NOTES_REF}")

    monkeypatch.chdir(main)

    return {
        "repo_root": main,
        "main_sha": main_sha,
        "sealed_at": sealed_at,
        "unit_info": unit_info,
        "bodies": bodies,
    }


# ---------------------------------------------------------------------------
# show — positive
# ---------------------------------------------------------------------------

def test_show_markdown_prints_all_units(sealed_repo: dict, capsys: pytest.CaptureFixture[str]) -> None:
    rc = CLI.main(["show"])
    assert rc == CLI.EXIT_OK
    out = capsys.readouterr().out
    assert "unit2" in out
    assert "unit4" in out


def test_show_json_contains_verdict_ref(sealed_repo: dict, capsys: pytest.CaptureFixture[str]) -> None:
    rc = CLI.main(["show", "--format", "json"])
    assert rc == CLI.EXIT_OK
    out = capsys.readouterr().out
    # Both JSON blocks are on stdout; first parses fine
    blocks = [b for b in out.strip().split("\n}\n") if b.strip()]
    parsed = json.loads(blocks[0] + "\n}")
    assert "verdict_ref" in parsed
    assert parsed["verdict_ref"]["kind"] == "routine_comment"


def test_show_unit_filter_restricts_output(sealed_repo: dict, capsys: pytest.CaptureFixture[str]) -> None:
    rc = CLI.main(["show", "--unit", "unit2"])
    assert rc == CLI.EXIT_OK
    out = capsys.readouterr().out
    assert "unit2" in out
    assert "unit4" not in out


# ---------------------------------------------------------------------------
# show — negative
# ---------------------------------------------------------------------------

def test_show_no_notes_returns_misuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare = tmp_path / "r.git"
    main = tmp_path / "main"
    bare.mkdir(); main.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")
    _git(main, "init", "--initial-branch=main")
    _git(main, "config", "user.email", "t@t.com")
    _git(main, "config", "user.name", "T")
    _git(main, "remote", "add", "origin", str(bare))
    (main / "f.txt").write_text("x\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "init")
    monkeypatch.chdir(main)
    rc = CLI.main(["show"])
    assert rc == CLI.EXIT_MISUSE


def test_show_unknown_unit_filter_returns_misuse(sealed_repo: dict) -> None:
    rc = CLI.main(["show", "--unit", "unit99"])
    assert rc == CLI.EXIT_MISUSE


# ---------------------------------------------------------------------------
# verify — positive
# ---------------------------------------------------------------------------

def test_verify_clean_seals_exits_ok(sealed_repo: dict, capsys: pytest.CaptureFixture[str]) -> None:
    rc = CLI.main(["verify"])
    assert rc == CLI.EXIT_OK
    out = capsys.readouterr().out
    assert "OK" in out
    assert "DRIFT" not in out


def test_verify_no_notes_returns_misuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare = tmp_path / "r.git"
    main = tmp_path / "main"
    bare.mkdir(); main.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")
    _git(main, "init", "--initial-branch=main")
    _git(main, "config", "user.email", "t@t.com")
    _git(main, "config", "user.name", "T")
    _git(main, "remote", "add", "origin", str(bare))
    (main / "f.txt").write_text("x\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "init")
    monkeypatch.chdir(main)
    rc = CLI.main(["verify"])
    assert rc == CLI.EXIT_MISUSE


# ---------------------------------------------------------------------------
# verify — negative (SHA drift detected)
# ---------------------------------------------------------------------------

def test_verify_detects_sha_drift(sealed_repo: dict, capsys: pytest.CaptureFixture[str]) -> None:
    # Commit a new change to unit2's worktree — SHA drifts from the sealed value
    info = sealed_repo["unit_info"][2]
    wt = info["path"]
    _git(wt, "config", "user.email", "t@t.com")
    _git(wt, "config", "user.name", "T")
    (wt / "extra.txt").write_text("drift\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "drift commit")
    rc = CLI.main(["verify"])
    assert rc == CLI.EXIT_MISUSE
    out = capsys.readouterr().out
    assert "DRIFT" in out


# ---------------------------------------------------------------------------
# reseal — positive (SHA changed)
# ---------------------------------------------------------------------------

def test_reseal_sha_changed_writes_new_note(sealed_repo: dict) -> None:
    info = sealed_repo["unit_info"][2]
    wt = info["path"]
    _git(wt, "config", "user.email", "t@t.com")
    _git(wt, "config", "user.name", "T")
    (wt / "extra.txt").write_text("drift\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "drift")
    new_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()

    rc = CLI.main(["reseal", "-y"])
    assert rc == CLI.EXIT_OK

    # New note must be at new_sha
    show = subprocess.run(
        ["git", "-C", str(sealed_repo["repo_root"]), "notes",
         f"--ref={CLI._NOTES_REF}", "show", new_sha],
        capture_output=True, text=True,
    )
    assert show.returncode == 0
    body = json.loads(show.stdout.strip())
    assert body["sealed_by"] == "operator:reseal"
    assert body["last_resealed_at"] is not None
    # verdict_ref and sealed_at MUST be preserved
    assert body["verdict_ref"] == _VERDICT_REF
    assert body["sealed_at"] == sealed_repo["sealed_at"]


def test_reseal_same_sha_force_overwrites(sealed_repo: dict) -> None:
    # No commits since sealing — same SHA; reseal should update metadata only
    rc = CLI.main(["reseal", "-y"])
    assert rc == CLI.EXIT_OK
    sha2 = sealed_repo["unit_info"][2]["sha"]
    show = subprocess.run(
        ["git", "-C", str(sealed_repo["repo_root"]), "notes",
         f"--ref={CLI._NOTES_REF}", "show", sha2],
        capture_output=True, text=True,
    )
    assert show.returncode == 0
    body = json.loads(show.stdout.strip())
    assert body["sealed_by"] == "operator:reseal"
    assert body["sealed_at"] == sealed_repo["sealed_at"]  # preserved


# ---------------------------------------------------------------------------
# reseal — negative
# ---------------------------------------------------------------------------

def test_reseal_no_notes_returns_misuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare = tmp_path / "r.git"
    main = tmp_path / "main"
    bare.mkdir(); main.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")
    _git(main, "init", "--initial-branch=main")
    _git(main, "config", "user.email", "t@t.com")
    _git(main, "config", "user.name", "T")
    _git(main, "remote", "add", "origin", str(bare))
    (main / "f.txt").write_text("x\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "init")
    monkeypatch.chdir(main)
    rc = CLI.main(["reseal", "-y"])
    assert rc == CLI.EXIT_MISUSE


def test_reseal_verdict_ref_preserved_after_sha_change(sealed_repo: dict) -> None:
    # Ensure verdict_ref is byte-for-byte identical after reseal
    info = sealed_repo["unit_info"][4]
    wt = info["path"]
    _git(wt, "config", "user.email", "t@t.com")
    _git(wt, "config", "user.name", "T")
    (wt / "x.txt").write_text("y\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "bump")
    new_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
    CLI.main(["reseal", "-y"])
    show = subprocess.run(
        ["git", "-C", str(sealed_repo["repo_root"]), "notes",
         f"--ref={CLI._NOTES_REF}", "show", new_sha],
        capture_output=True, text=True,
    )
    body = json.loads(show.stdout.strip())
    assert body["verdict_ref"] == _VERDICT_REF
    # Negative: sealed_by must NOT be "operator:init" after reseal
    assert body["sealed_by"] != "operator:init"
