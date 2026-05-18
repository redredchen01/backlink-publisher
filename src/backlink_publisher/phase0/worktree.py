"""Worktree HEAD discovery for Phase 0 staged branches (R10).

`git worktree list --porcelain` primary; `git for-each-ref` fallback for
branches that exist as refs but are not currently a worktree HEAD (e.g.,
operator deleted the worktree but the local ref persists, then checked out
the branch in the main repo).

velog Phase 0 forks this by changing TELEGRAPH_BRANCH_PATTERN in validation.py
or constructing a different branch glob.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeEntry:
    """One staged-branch worktree entry.

    *path* is ``None`` when the branch ref exists but is not currently a
    worktree HEAD (fallback discovery via ``git for-each-ref``).
    *is_clean* / *has_rebase_in_progress* / *is_detached* are best-effort
    queried only when *path* is set; ``None`` for ref-only entries.
    """

    unit: str
    branch: str
    sha: str
    path: Path | None
    is_clean: bool | None = None
    has_rebase_in_progress: bool | None = None
    is_detached: bool | None = None


def discover_worktree_heads(
    branch_pattern: str,
    *,
    repo_root: Path | None = None,
    unit_name_re: re.Pattern[str] | None = None,
) -> list[WorktreeEntry]:
    """Return one entry per staged-branch ref matching *branch_pattern*.

    *branch_pattern* is a shell glob (e.g., ``local/telegraph-unit*-staged``).
    *unit_name_re* extracts the symbolic unit name from the branch (default:
    captures the segment between ``-`` separators after the last ``unit``).

    Resolution order:
        1. ``git worktree list --porcelain`` — every entry with a branch
           matching the pattern becomes a WorktreeEntry with ``path`` set.
        2. ``git for-each-ref`` — any branch matching the pattern that wasn't
           found in step 1 becomes a WorktreeEntry with ``path=None``.

    Entries are deduplicated by branch (worktree-listed wins over ref-only).
    """
    if unit_name_re is None:
        unit_name_re = re.compile(r"unit(\d+)")

    cwd = repo_root if repo_root is not None else Path.cwd()
    entries: dict[str, WorktreeEntry] = {}

    # --- Primary: git worktree list --porcelain ---
    try:
        out = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = ""

    for record in _split_worktree_records(out):
        path_str = record.get("worktree")
        branch_ref = record.get("branch")  # e.g. "refs/heads/local/telegraph-unit2-staged"
        sha = record.get("HEAD")
        if not (path_str and branch_ref and sha):
            continue
        branch = branch_ref[len("refs/heads/"):] if branch_ref.startswith("refs/heads/") else branch_ref
        if not fnmatch.fnmatch(branch, branch_pattern):
            continue
        unit = _extract_unit(branch, unit_name_re)
        wt_path = Path(path_str)
        entries[branch] = WorktreeEntry(
            unit=unit,
            branch=branch,
            sha=sha,
            path=wt_path,
            is_clean=_check_clean(wt_path),
            has_rebase_in_progress=_check_rebase(wt_path),
            is_detached=_check_detached(wt_path),
        )

    # --- Fallback: git for-each-ref refs/heads/<pattern> ---
    try:
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/"],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = ""

    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        branch, sha = parts
        if not fnmatch.fnmatch(branch, branch_pattern):
            continue
        if branch in entries:
            continue  # already discovered via worktree list (path-bearing entry wins)
        unit = _extract_unit(branch, unit_name_re)
        entries[branch] = WorktreeEntry(
            unit=unit, branch=branch, sha=sha,
            path=None, is_clean=None, has_rebase_in_progress=None, is_detached=None,
        )

    return sorted(entries.values(), key=lambda e: e.branch)


def _split_worktree_records(out: str) -> list[dict[str, str]]:
    """Parse ``git worktree list --porcelain`` output into per-record dicts.

    Records are blank-line-separated. Each line is ``key value`` except
    ``bare`` and ``detached`` which are key-only (we surface as empty string).
    """
    records: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                records.append(cur)
                cur = {}
            continue
        if " " in line:
            k, v = line.split(" ", 1)
            cur[k] = v
        else:
            cur[line] = ""
    if cur:
        records.append(cur)
    return records


def _extract_unit(branch: str, unit_re: re.Pattern[str]) -> str:
    m = unit_re.search(branch)
    return f"unit{m.group(1)}" if m else branch


def _check_clean(path: Path) -> bool | None:
    if not path.exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        return out.strip() == ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _check_rebase(path: Path) -> bool | None:
    if not path.exists():
        return None
    git_dir = path / ".git"
    # In a linked worktree, .git is a file pointing at .git/worktrees/<name>/
    if git_dir.is_file():
        try:
            ref = git_dir.read_text(encoding="utf-8").strip()
            if ref.startswith("gitdir: "):
                git_dir = Path(ref[len("gitdir: "):])
        except OSError:
            return None
    return (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists()


def _check_detached(path: Path) -> bool | None:
    if not path.exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "symbolic-ref", "--quiet", "HEAD"],
            capture_output=True, text=True,
        )
        return proc.returncode != 0
    except FileNotFoundError:
        return None
