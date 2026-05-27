"""Tests for `backlink_publisher.phase0.worktree.discover_worktree_heads` — Unit 2."""

from __future__ import annotations

import subprocess
from pathlib import Path


from backlink_publisher.phase0.worktree import discover_worktree_heads


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout


def _init_repo(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    _run(main, "init", "--initial-branch=main")
    _run(main, "config", "user.email", "test@example.com")
    _run(main, "config", "user.name", "Test")
    (main / "README.md").write_text("init\n")
    _run(main, "add", "README.md")
    _run(main, "commit", "-m", "init")
    return main


def test_discover_finds_two_worktrees(tmp_path: Path) -> None:
    main = _init_repo(tmp_path)
    # Two staged branches with worktrees.
    for n in (2, 4):
        _run(main, "branch", f"local/telegraph-unit{n}-staged")
        wt = tmp_path / f"bp-local-unit{n}"
        _run(main, "worktree", "add", str(wt), f"local/telegraph-unit{n}-staged")

    entries = discover_worktree_heads("local/telegraph-unit*-staged", repo_root=main)
    assert len(entries) == 2
    units = {e.unit for e in entries}
    assert units == {"unit2", "unit4"}
    for e in entries:
        assert e.path is not None and e.path.exists()
        assert e.is_clean is True  # PAIRED with the dirty test below


def test_discover_reports_dirty_worktree(tmp_path: Path) -> None:
    main = _init_repo(tmp_path)
    _run(main, "branch", "local/telegraph-unit2-staged")
    wt = tmp_path / "bp-local-unit2"
    _run(main, "worktree", "add", str(wt), "local/telegraph-unit2-staged")
    (wt / "dirty.txt").write_text("uncommitted change\n")

    entries = discover_worktree_heads("local/telegraph-unit*-staged", repo_root=main)
    assert len(entries) == 1
    assert entries[0].is_clean is False  # PAIRED: above test confirms True on clean


def test_discover_fallback_ref_only(tmp_path: Path) -> None:
    """Branch ref exists but no worktree — path should be None (fallback path)."""
    main = _init_repo(tmp_path)
    _run(main, "branch", "local/telegraph-unit5-staged")
    # NO `git worktree add` — branch exists only as a ref.

    entries = discover_worktree_heads("local/telegraph-unit*-staged", repo_root=main)
    assert len(entries) == 1
    assert entries[0].branch == "local/telegraph-unit5-staged"
    assert entries[0].path is None
    assert entries[0].is_clean is None  # ref-only entry has no path-based state


def test_discover_returns_empty_when_no_matching_branches(tmp_path: Path) -> None:
    main = _init_repo(tmp_path)
    assert discover_worktree_heads("local/telegraph-unit*-staged", repo_root=main) == []


def test_discover_dedupe_worktree_wins_over_ref_only(tmp_path: Path) -> None:
    """When a branch exists both as worktree HEAD and (would also show as ref),
    the worktree-listed entry should win (path set, not None)."""
    main = _init_repo(tmp_path)
    _run(main, "branch", "local/telegraph-unit2-staged")
    wt = tmp_path / "bp-local-unit2"
    _run(main, "worktree", "add", str(wt), "local/telegraph-unit2-staged")

    entries = discover_worktree_heads("local/telegraph-unit*-staged", repo_root=main)
    assert len(entries) == 1
    assert entries[0].path is not None  # worktree-listed entry won
