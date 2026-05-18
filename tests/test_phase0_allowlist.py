"""Tests for `backlink_publisher.phase0.validation.load_allowlist` — Unit 2.

CRITICAL: the v3 BLOCKER fix for adversarial-review F2 — when CLI is invoked
from inside a linked worktree whose HEAD predates the allowlist file commit,
`load_allowlist` MUST still resolve to the MAIN worktree's allowlist file,
NOT the linked worktree's tree (where the file does not exist).

`git rev-parse --show-toplevel` (v2's broken approach) returns the CURRENT
worktree root and would fail. `git worktree list --porcelain` returns the
MAIN worktree path as its first record — that's what `find_main_worktree_root`
uses now.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from backlink_publisher.phase0 import validation as V


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout


_ALLOWLIST_YAML = textwrap.dedent("""\
    schema_version: 1
    authorized_authors:
      - login: telegraph-routine-bot[bot]
        routine_id: trig_01
        captured_at: "2026-05-25T10:00:00Z"
        captured_by: operator
        run_id_observed: trig-01-fire-1
""")


def _init_repo_with_allowlist(tmp_path: Path) -> Path:
    """Create a tmp git repo with a tracked allowlist file at HEAD."""
    main = tmp_path / "main"
    main.mkdir()
    _run(main, "init", "--initial-branch=main")
    _run(main, "config", "user.email", "test@example.com")
    _run(main, "config", "user.name", "Test")

    (main / "scripts" / "telegraph_spike").mkdir(parents=True)
    (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(_ALLOWLIST_YAML)
    _run(main, "add", ".")
    _run(main, "commit", "-m", "initial: add allowlist")
    return main


def test_load_allowlist_from_main_worktree(tmp_path: Path) -> None:
    main = _init_repo_with_allowlist(tmp_path)
    # Run with cwd == main worktree; find_main_worktree_root should resolve here.
    import os
    cwd = Path.cwd()
    try:
        os.chdir(main)
        result = V.load_allowlist()
    finally:
        os.chdir(cwd)
    assert "telegraph-routine-bot[bot]" in result["_logins"]
    assert result["schema_version"] == 1


def test_load_allowlist_from_linked_worktree_predating_allowlist(tmp_path: Path) -> None:
    """The v3 BLOCKER scenario.

    Build topology:
        1. Init repo, make initial commit WITHOUT the allowlist file.
        2. Branch the initial commit as `pre-allowlist`.
        3. Create a worktree on that branch (its HEAD lacks the allowlist).
        4. Back on main, ADD the allowlist file and commit.
        5. Invoke load_allowlist() from inside the linked worktree.
        6. Assert: MAIN worktree's allowlist loaded (NOT FileMissingError),
           and resolved path is under the MAIN worktree, NOT the linked one.

    This proves `git rev-parse --show-toplevel` would have failed (it returns
    the linked worktree's path, where the allowlist file doesn't exist) and
    that `find_main_worktree_root` correctly finds the main repo path.
    """
    main = tmp_path / "main"
    main.mkdir()
    _run(main, "init", "--initial-branch=main")
    _run(main, "config", "user.email", "test@example.com")
    _run(main, "config", "user.name", "Test")
    # Initial commit WITHOUT the allowlist file.
    (main / "README.md").write_text("init\n")
    _run(main, "add", "README.md")
    _run(main, "commit", "-m", "init (pre-allowlist)")
    # Branch the initial commit; create worktree on it.
    _run(main, "branch", "pre-allowlist")
    wt = tmp_path / "wt-a"
    _run(main, "worktree", "add", str(wt), "pre-allowlist")
    # Now add the allowlist file on main only.
    (main / "scripts" / "telegraph_spike").mkdir(parents=True)
    (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(_ALLOWLIST_YAML)
    _run(main, "add", ".")
    _run(main, "commit", "-m", "add allowlist")

    # Sanity check the topology.
    assert (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").exists()
    assert not (wt / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").exists()

    # Invoke from inside the linked worktree.
    import os
    cwd = Path.cwd()
    try:
        os.chdir(wt)
        result = V.load_allowlist()
    finally:
        os.chdir(cwd)

    assert "telegraph-routine-bot[bot]" in result["_logins"]
    # Resolved path MUST be under main worktree, NOT linked worktree.
    resolved = Path(result["_path"]).resolve()
    assert resolved.is_relative_to(main.resolve())
    assert not resolved.is_relative_to(wt.resolve())


def test_load_allowlist_missing_file_raises(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    _run(main, "init", "--initial-branch=main")
    _run(main, "config", "user.email", "test@example.com")
    _run(main, "config", "user.name", "Test")
    (main / "README.md").write_text("no allowlist here\n")
    _run(main, "add", ".")
    _run(main, "commit", "-m", "init")

    with pytest.raises(V.AllowlistFileMissingError, match="allowlist not found"):
        V.load_allowlist(repo_root=main)


def test_load_allowlist_empty_authors_raises(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    (main / "scripts" / "telegraph_spike").mkdir(parents=True)
    (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(
        "schema_version: 1\nauthorized_authors: []\n"
    )
    with pytest.raises(V.EmptyAllowlistError):
        V.load_allowlist(repo_root=main)


def test_load_allowlist_bad_schema_raises(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    (main / "scripts" / "telegraph_spike").mkdir(parents=True)
    (main / "scripts" / "telegraph_spike" / "authorized-routine-bots.yaml").write_text(
        "schema_version: 1\nauthorized_authors:\n  - notlogin: foo\n"
    )
    with pytest.raises(V.AllowlistSchemaError, match="login"):
        V.load_allowlist(repo_root=main)
