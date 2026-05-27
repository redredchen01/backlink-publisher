"""Git resolution tier for ``plan-check`` — ``origin/main`` fetch, path/SHA resolution.

Extracted from ``plan_check.py``.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Unit 2: Git subprocess helpers — origin/main resolution + freshness
# ---------------------------------------------------------------------------
#
# All subprocess calls run with ``LC_ALL=C LANG=C`` (D16 / feasibility-reviewer):
# stderr taxonomy regexes must be locale-independent so a localised git
# (``致命錯誤`` / ``fatale`` / ``致命的``) doesn't slip past the classifier.
#
# Freshness detection resolves the common gitdir via ``git rev-parse
# --git-common-dir`` rather than hard-coding ``.git/FETCH_HEAD`` because this
# repo runs in 18+ linked worktrees where ``.git`` is a *file* and
# ``FETCH_HEAD`` lives in the shared common gitdir (D5).
# ---------------------------------------------------------------------------


# Module-local sink for the most recent git stderr captured by the resolution
# functions on exit-128 paths. Unit 3 may surface this via the CLI; tests and
# downstream callers can also inspect it for diagnostics. Reset on each call
# that probes a git subprocess so a stale value never leaks across calls.
_last_git_error: Optional[str] = None


_GIT_ENV: dict[str, str] = {"LC_ALL": "C", "LANG": "C"}


def _git_env() -> dict[str, str]:
    """Return ``os.environ`` overlaid with ``LC_ALL=C`` / ``LANG=C``.

    Computed each call so test ``monkeypatch.setenv`` mutations propagate to the
    git subprocess without us caching a stale snapshot.
    """
    env = os.environ.copy()
    env.update(_GIT_ENV)
    return env


@dataclass(frozen=True)
class FetchOutcome:
    """Result of :func:`_maybe_fetch_origin_main`.

    ``fetched``: True only when ``git fetch origin main`` actually ran and
    exited 0.
    ``fetch_head_age_seconds``: integer seconds since ``FETCH_HEAD`` mtime, or
    ``None`` when ``FETCH_HEAD`` does not exist after the call returns. Always
    populated on every code path per D16.
    ``skip_reason``: ``None`` when fetch succeeded or was unneeded (age under
    threshold). Otherwise one of ``"network" | "auth" | "no_remote" | "other"``
    classified from subprocess stderr per D16 taxonomy.
    """

    fetched: bool
    fetch_head_age_seconds: Optional[int]
    skip_reason: Optional[Literal["network", "auth", "no_remote", "other"]]


def _fetch_head_age_seconds() -> float:
    """Return seconds since the common gitdir's ``FETCH_HEAD`` mtime.

    Returns ``float('inf')`` when ``FETCH_HEAD`` does not exist or the gitdir
    cannot be resolved (cwd is not inside a git repo). The infinity sentinel
    makes the staleness check always re-fetch in the first-run case (D5).

    Resolves the common gitdir via ``git rev-parse --git-common-dir`` so this
    function works correctly in linked worktrees where ``.git`` is a *file*
    pointing at ``<main-gitdir>/worktrees/<name>`` and ``FETCH_HEAD`` lives in
    the *common* gitdir, not the per-worktree one (D5, feasibility-reviewer P0).
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
    except (OSError, FileNotFoundError):
        return float("inf")
    if proc.returncode != 0:
        return float("inf")
    common_dir = Path(proc.stdout.strip())
    if not common_dir.is_absolute():
        # ``git rev-parse --git-common-dir`` returns a path relative to cwd in
        # some configurations; anchor it against cwd to be safe.
        common_dir = Path.cwd() / common_dir
    fetch_head = common_dir / "FETCH_HEAD"
    try:
        mtime = fetch_head.stat().st_mtime
    except (FileNotFoundError, OSError):
        return float("inf")
    return time.time() - mtime


def _classify_fetch_stderr(stderr: str) -> Literal["network", "auth", "no_remote", "other"]:
    """Map a ``git fetch`` stderr blob to the D16 skip-reason taxonomy.

    Patterns chosen from the D16 initial seed: substring-tested against the
    en_US.UTF-8 / C locale stderr we force via ``LC_ALL=C``. Order matters:
    no_remote checks come before auth because ``Repository not found`` over
    HTTPS can also surface auth-style prompts.
    """
    s = stderr or ""
    if "Could not resolve host" in s:
        return "network"
    if (
        "does not appear to be a git repository" in s
        or "No such remote" in s
        or "Repository not found" in s
    ):
        return "no_remote"
    if (
        "Authentication failed" in s
        or "Permission denied" in s
        or "could not read Username" in s
    ):
        return "auth"
    return "other"


def _maybe_fetch_origin_main(threshold_seconds: int = 300) -> FetchOutcome:
    """Refresh ``origin/main`` if ``FETCH_HEAD`` is older than *threshold_seconds*.

    Never raises on fetch failure (D16); classifies stderr into the skip-reason
    taxonomy and returns a :class:`FetchOutcome` so the caller can decide how to
    handle stale claims. Plan §D3/§D16 reserve exit 9 (stale-pass) and the
    ``--strict-fetch`` flag for the skip path, but the v1 dispatch returns 0
    instead and emits the RECON warn line only — exit 9 + ``--strict-fetch``
    are deferred to v1.1 (Plan 2026-05-19-010 P1 #3).

    ``fetch_head_age_seconds`` is always populated (or explicitly ``None``) on
    every return path, including the happy "no fetch needed" branch.
    """
    age = _fetch_head_age_seconds()
    if age < threshold_seconds:
        # Under threshold — skip the network round-trip. Age is a finite float
        # here (only ``inf`` would push us past any sane positive threshold).
        return FetchOutcome(
            fetched=False,
            fetch_head_age_seconds=int(age),
            skip_reason=None,
        )
    # Either no FETCH_HEAD (age == inf) or stale — attempt a real fetch.
    try:
        proc = subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
    except (OSError, FileNotFoundError):
        # ``git`` not on PATH or other OS-level failure — treat as "other".
        return FetchOutcome(
            fetched=False,
            fetch_head_age_seconds=None,
            skip_reason="other",
        )
    if proc.returncode == 0:
        # Fetch succeeded: re-stat FETCH_HEAD to get a fresh age (likely ~0).
        new_age = _fetch_head_age_seconds()
        if new_age == float("inf"):
            # Edge: fetch reported success but FETCH_HEAD still absent (e.g., a
            # broken pipe or a remote that returned no refs). Surface ``None``
            # rather than ``inf`` so the JSON contract is well-typed.
            return FetchOutcome(
                fetched=True, fetch_head_age_seconds=None, skip_reason=None
            )
        return FetchOutcome(
            fetched=True, fetch_head_age_seconds=int(new_age), skip_reason=None
        )
    # Non-zero exit — classify and return without raising (D16).
    reason = _classify_fetch_stderr(proc.stderr or "")
    final_age = _fetch_head_age_seconds()
    age_field: Optional[int]
    if final_age == float("inf"):
        age_field = None
    else:
        age_field = int(final_age)
    return FetchOutcome(
        fetched=False, fetch_head_age_seconds=age_field, skip_reason=reason
    )


def _path_exists_on_main(
    path: str,
) -> tuple[bool, Literal["exists", "missing", "git_error"]]:
    """Check whether *path* resolves as a blob/tree on ``origin/main``.

    Uses ``git cat-file -e origin/main:<path>``. Exit-code discrimination
    matters: 1 means git ran cleanly and the path is not on main (real drift);
    128 means git failed (object DB error, corrupt repo, missing ref). The
    plan and feasibility-reviewer both flagged collapsing the two as a bug
    (would mask infra failures as "drift").
    """
    global _last_git_error
    _last_git_error = None
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"origin/main:{path}"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
    except (OSError, FileNotFoundError) as exc:
        _last_git_error = str(exc)
        return (False, "git_error")
    if proc.returncode == 0:
        return (True, "exists")
    # Real git emits exit 128 for BOTH "path not in tree" and "infra failure"
    # (bad ref / corrupt object DB), distinguishing only via stderr message.
    # Treat the documented "does not exist in" stderr as a genuine drift signal
    # ("missing"); anything else on a non-zero exit is a real git error. Some
    # builds of git also exit 1 for the missing-path case — surface as missing.
    stderr = proc.stderr or ""
    if proc.returncode == 1:
        return (False, "missing")
    if "does not exist in" in stderr:
        return (False, "missing")
    _last_git_error = stderr
    return (False, "git_error")


def _sha_reachable_from_main(
    sha: str,
) -> tuple[bool, Literal["reachable", "unreachable", "unknown_object", "git_error"]]:
    """Check whether *sha* is an ancestor of ``origin/main``.

    Uses ``git merge-base --is-ancestor <sha> origin/main``. Exit 0 → reachable.
    Exit 1 → unreachable (sha is a known commit, just not on main; most common
    case is a force-pushed branch). Exit 128 → object not in DB at all (typo or
    abandoned commit GC'd away). Anything else → ``git_error``.
    """
    global _last_git_error
    _last_git_error = None
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
            capture_output=True,
            text=True,
            env=_git_env(),
            check=False,
        )
    except (OSError, FileNotFoundError) as exc:
        _last_git_error = str(exc)
        return (False, "git_error")
    if proc.returncode == 0:
        return (True, "reachable")
    if proc.returncode == 1:
        return (False, "unreachable")
    if proc.returncode == 128:
        _last_git_error = proc.stderr or ""
        return (False, "unknown_object")
    _last_git_error = proc.stderr or ""
    return (False, "git_error")
