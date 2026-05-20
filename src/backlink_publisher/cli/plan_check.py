"""``plan-check`` — schema layer for plan-doc claims contract.

Unit 1 of `docs/plans/2026-05-19-009-feat-plan-claims-and-head-drift-gate-plan.md`.
Provides frontmatter parsing, claims-schema validation, grandfather-cutoff check,
and the R11b filename-date lock that defeats backdate exploits.

Unit 2 (git resolution) and Unit 3 (CLI dispatch) extend this module; this file
is the schema tier only — no ``main()``, no argparse, no git subprocess calls.

R4 forward-compat is one-directional: a v1 tool reading a future plan with a new
``claims.<unknown_key>`` key fails on unknown-key. Bump ``SCHEMA_VERSION`` when
changing the accepted-key set.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

# Schema-version pin (mirror ``cli/footprint.py``); see plan §D14.
SCHEMA_VERSION: int = 1

# Grandfather cutoff per plan §R9 / D15: plans dated `< 2026-05-20` are exempt.
_GRANDFATHER_CUTOFF: _dt.date = _dt.date(2026, 5, 20)

# Lowercase hex, 7-to-40 chars (short SHA up to full SHA), per plan §D17/G3.
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

# Filename prefix lock: ``YYYY-MM-DD-`` at the start of the file basename (R11b/D17).
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")

# Glob characters rejected in claims.paths (D10).
_GLOB_CHARS: frozenset[str] = frozenset("*?[")

# Only these two keys are accepted under ``claims:`` (R1).
_ALLOWED_CLAIMS_KEYS: frozenset[str] = frozenset({"paths", "shas"})


# ---------------------------------------------------------------------------
# Named module-local exceptions (mirror ``_util/errors.py``).
#
# Module-local rather than added to ``_util/errors.py`` because they describe
# plan-check-domain concepts (frontmatter schema, claims block, filename
# lock). Each carries an ``exit_code`` class attribute matching the existing
# 0-6 contract extended to 7 (drift, Unit 3) and 8 (missing-claims, Unit 3).
# ---------------------------------------------------------------------------


class PlanClaimsFrontmatterSchemaError(Exception):
    """Frontmatter is missing, malformed, or violates the claims schema."""

    exit_code: int = 2


class PlanClaimsMissingOnPostCutoff(Exception):
    """Post-cutoff plan-doc has no ``claims:`` block (R10)."""

    exit_code: int = 8


class PlanClaimsGlobUnsupported(Exception):
    """``claims.paths`` entry contains a glob character (D10)."""

    exit_code: int = 2


class PlanClaimsFilenameDateMismatch(Exception):
    """Filename ``YYYY-MM-DD-`` prefix disagrees with ``frontmatter.date`` (R11b/D17)."""

    exit_code: int = 2


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def _read_plan_text(plan_path: Path) -> str:
    """Read a plan-doc as UTF-8, stripping any leading BOM.

    Non-UTF-8 input raises :class:`PlanClaimsFrontmatterSchemaError` — frontmatter
    parsing requires text the YAML loader can consume, and binary corruption
    should fail loud rather than silently mis-parse.
    """
    try:
        text = plan_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PlanClaimsFrontmatterSchemaError(
            f"{plan_path}: file is not valid UTF-8 ({exc.reason}); "
            f"plan-docs must be UTF-8 encoded"
        ) from exc
    # Strip UTF-8 BOM (U+FEFF) if present so the leading `---` is still detected.
    if text.startswith("﻿"):
        text = text.lstrip("﻿")
    return text


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Split a plan-doc on its ``---`` fences and return the parsed frontmatter.

    Raises :class:`PlanClaimsFrontmatterSchemaError` if:
      - the doc has no leading ``---`` fence (no frontmatter at all);
      - the closing ``---`` is missing;
      - the middle block is empty or doesn't parse to a top-level mapping.

    Mirrors :func:`backlink_publisher.phase0.validation.load_allowlist` —
    ``yaml.safe_load`` + ``isinstance(dict)`` + explicit schema validation.
    """
    if not text.startswith("---"):
        raise PlanClaimsFrontmatterSchemaError("plan-doc missing YAML frontmatter (no leading `---`)")
    # Drop the leading fence line, then split on the next `---` line.
    after_open = text[3:]
    # First newline after opening fence
    if after_open.startswith("\n"):
        after_open = after_open[1:]
    elif after_open.startswith("\r\n"):
        after_open = after_open[2:]
    # Locate closing fence: a line that is exactly `---`
    closing_match = re.search(r"(?m)^---\s*$", after_open)
    if closing_match is None:
        raise PlanClaimsFrontmatterSchemaError(
            "plan-doc missing closing `---` for YAML frontmatter"
        )
    fm_text = after_open[: closing_match.start()]
    try:
        raw = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc frontmatter is not valid YAML: {exc}"
        ) from exc
    if raw is None:
        raise PlanClaimsFrontmatterSchemaError(
            "plan-doc frontmatter is empty (must be a YAML mapping)"
        )
    if not isinstance(raw, dict):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc frontmatter must be a top-level mapping, got {type(raw).__name__}"
        )
    return raw


# ---------------------------------------------------------------------------
# Date / grandfather
# ---------------------------------------------------------------------------


def _grandfathered(fm: dict[str, Any]) -> bool:
    """Return True if the plan-doc is dated before the R9 grandfather cutoff.

    Comparison is date-typed per D15: the ``date:`` field must already be a
    :class:`datetime.date` (PyYAML's default for ISO-8601 date scalars). A
    string here indicates a non-ISO-format that PyYAML did not auto-convert
    (e.g. ``May 19 2026``), which is a schema error.
    """
    if "date" not in fm:
        raise PlanClaimsFrontmatterSchemaError(
            "plan-doc frontmatter missing required `date:` field"
        )
    raw_date = fm["date"]
    # Accept only `datetime.date` (PyYAML emits this for ISO-8601 dates).
    # `datetime.datetime` is a subclass of `date`; if a plan-doc carries a full
    # timestamp, we coerce to its `.date()` component. Strings are rejected.
    if isinstance(raw_date, _dt.datetime):
        raw_date = raw_date.date()
    if not isinstance(raw_date, _dt.date):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc `date:` must be ISO-8601 (YYYY-MM-DD), got {type(raw_date).__name__}: "
            f"{raw_date!r}"
        )
    return raw_date < _GRANDFATHER_CUTOFF


# ---------------------------------------------------------------------------
# SHA format validation (D17/G3)
# ---------------------------------------------------------------------------


def _validate_sha_format(s: str) -> bool:
    """Return True iff *s* is a lowercase hex SHA of length 7-40.

    Mixed-case, non-hex characters, and out-of-range lengths all fail.
    This is the schema-tier check; Unit 2 will validate reachability against
    ``origin/main`` separately so git stderr never leaks into our error path.
    """
    if not isinstance(s, str):
        return False
    return bool(_SHA_RE.fullmatch(s))


# ---------------------------------------------------------------------------
# Claims block
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimsBlock:
    """Validated ``claims:`` sub-block from a plan-doc frontmatter."""

    paths: list[str] = field(default_factory=list)
    shas: list[str] = field(default_factory=list)
    is_explicit_optout: bool = False


def _validate_claims_schema(fm: dict[str, Any]) -> ClaimsBlock:
    """Validate the ``claims:`` sub-block and return a :class:`ClaimsBlock`.

    Schema (R1-R4):
      - ``claims`` must be a mapping, accepted keys are ``paths`` and ``shas``.
      - Unknown keys raise :class:`PlanClaimsFrontmatterSchemaError` (R4).
      - ``paths`` entries are strings; glob characters raise
        :class:`PlanClaimsGlobUnsupported` (D10).
      - ``shas`` entries are validated via :func:`_validate_sha_format`.
      - Missing ``claims`` key (with a post-cutoff date) raises
        :class:`PlanClaimsMissingOnPostCutoff` (R10).
      - An empty mapping ``claims: {}`` is the explicit opt-out (R11).
    """
    if "claims" not in fm:
        # Post-cutoff plan-docs must include claims (R10). Pre-cutoff is filtered
        # by the `_grandfathered` check upstream, so reaching this branch means
        # the caller (Unit 3) already decided the plan-doc is in scope.
        raise PlanClaimsMissingOnPostCutoff(
            "plan-doc post-cutoff requires a `claims:` block "
            "(use `claims: {}` to opt out explicitly)"
        )
    claims = fm["claims"]
    # `claims: {}` parses as an empty dict — the explicit opt-out. `claims:` with
    # no value parses as None; we accept that as equivalent for ergonomics.
    if claims is None:
        return ClaimsBlock(paths=[], shas=[], is_explicit_optout=True)
    if not isinstance(claims, dict):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc `claims:` must be a mapping, got {type(claims).__name__}"
        )
    unknown = set(claims.keys()) - _ALLOWED_CLAIMS_KEYS
    if unknown:
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc `claims:` has unknown key(s) {sorted(unknown)}; "
            f"allowed: {sorted(_ALLOWED_CLAIMS_KEYS)}"
        )
    paths_raw = claims.get("paths", [])
    shas_raw = claims.get("shas", [])
    if not isinstance(paths_raw, list):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc `claims.paths` must be a list, got {type(paths_raw).__name__}"
        )
    if not isinstance(shas_raw, list):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc `claims.shas` must be a list, got {type(shas_raw).__name__}"
        )
    paths: list[str] = []
    for entry in paths_raw:
        if not isinstance(entry, str):
            raise PlanClaimsFrontmatterSchemaError(
                f"plan-doc `claims.paths` entries must be strings, got "
                f"{type(entry).__name__}: {entry!r}"
            )
        bad = _GLOB_CHARS.intersection(entry)
        if bad:
            raise PlanClaimsGlobUnsupported(
                f"plan-doc `claims.paths` entry {entry!r} contains glob character(s) "
                f"{sorted(bad)}; globs unsupported in v1"
            )
        paths.append(entry)
    shas: list[str] = []
    for entry in shas_raw:
        if not isinstance(entry, str):
            raise PlanClaimsFrontmatterSchemaError(
                f"plan-doc `claims.shas` entries must be strings, got "
                f"{type(entry).__name__}: {entry!r}"
            )
        if not _validate_sha_format(entry):
            raise PlanClaimsFrontmatterSchemaError(
                f"plan-doc `claims.shas` entry {entry!r} is not a valid sha "
                f"(must be 7-40 lowercase hex characters)"
            )
        shas.append(entry)
    is_explicit_optout = len(paths) == 0 and len(shas) == 0
    return ClaimsBlock(paths=paths, shas=shas, is_explicit_optout=is_explicit_optout)


# ---------------------------------------------------------------------------
# Filename ↔ frontmatter.date lock (R11b / D17)
# ---------------------------------------------------------------------------


def _check_filename_date_lock(plan_path: Path, fm: dict[str, Any]) -> None:
    """Assert that the filename's ``YYYY-MM-DD-`` prefix matches ``frontmatter.date``.

    Defeats the backdate exploit (D17): the grandfather cutoff key
    (``frontmatter.date < 2026-05-20``) is operator-typed YAML and trivially
    backdatable. The filename prefix is the stronger anchor — all existing
    plans follow the ``YYYY-MM-DD-NNN-`` pattern. Mismatch is exit 2.

    This lock runs **unconditionally** in the Unit 3 dispatcher (before the
    grandfather check). Skipping it for grandfathered plans would let a
    backdated plan-doc exit 0 as grandfathered before the lock fires.
    """
    match = _FILENAME_DATE_RE.match(plan_path.name)
    if match is None:
        raise PlanClaimsFilenameDateMismatch(
            f"{plan_path.name}: filename does not match required `YYYY-MM-DD-NNN-` "
            f"prefix pattern"
        )
    filename_date = match.group(1)
    # Reuse the same typed-date check as `_grandfathered` so we get a single
    # source of truth for what counts as a valid `date:` field.
    raw_date = fm.get("date")
    if isinstance(raw_date, _dt.datetime):
        raw_date = raw_date.date()
    if not isinstance(raw_date, _dt.date):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc `date:` must be ISO-8601 (YYYY-MM-DD); cannot enforce "
            f"filename-date lock without a typed date"
        )
    fm_date = raw_date.isoformat()
    if filename_date != fm_date:
        raise PlanClaimsFilenameDateMismatch(
            f"{plan_path.name}: filename date {filename_date!r} disagrees with "
            f"frontmatter.date {fm_date!r}; both must be identical (D17 backdate lock)"
        )


# ---------------------------------------------------------------------------
# Unit 2: Git subprocess helpers — origin/main resolution + freshness
# ---------------------------------------------------------------------------
#
# All subprocess calls run with ``LC_ALL=C LANG=C`` (D16 / feasibility-reviewer):
# stderr taxonomy regexes must be locale-independent so a localised git
# (`致命錯誤` / `fatale` / `致命的`) doesn't slip past the classifier.
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
        # `git rev-parse --git-common-dir` returns a path relative to cwd in
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
        "does not appear to be a git repository"
        in s
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
    taxonomy and returns a :class:`FetchOutcome` so the caller can dispatch
    exit 9 (stale-pass) per the plan §D3/D16 contract.

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
    except (OSError, FileNotFoundError) as exc:
        # `git` not on PATH or other OS-level failure — treat as "other".
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


# ---------------------------------------------------------------------------
# Unit 3: CLI dispatch — argparse, output formatters, exit-code mapping
# ---------------------------------------------------------------------------
#
# Exit codes (D3):
#   0 — pass (all claims resolved on origin/main, OR grandfathered, OR empty)
#   1 — UsageError (positional missing/not-a-file)
#   2 — schema violation (frontmatter / claims schema / glob / filename-date lock)
#   7 — drift detected (paths missing or shas unreachable)
#   8 — missing claims block on post-cutoff plan-doc
#
# argparse exits 2 on its own errors (missing positional, bad flag); we accept
# that overlap with schema-violation 2 because argparse only fires on usage
# issues, not on our domain validation.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``.

    Per the JSON contract (plan §Open Questions Resolved line 138).
    """
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _format_human_drift(
    plan_path: Path,
    paths_missing: list[str],
    shas_unreachable: list[str],
) -> str:
    """Render the human-readable drift report (goes to stderr).

    Header naming both axes followed by an indented list per axis. The shape is
    intentionally similar to ``footprint``'s drift table so CI log readers
    recognise it (plan §R13).
    """
    lines: list[str] = [f"Drift detected in {plan_path}:"]
    if paths_missing:
        lines.append("  paths_missing:")
        for p in paths_missing:
            lines.append(f"    - {p}")
    if shas_unreachable:
        lines.append("  shas_unreachable:")
        for s in shas_unreachable:
            lines.append(f"    - {s}")
    return "\n".join(lines)


def _build_json_payload(
    *,
    plan_path: Path,
    plan_date: Optional[_dt.date],
    status: Literal["pass", "drift", "schema_violation", "missing_claims"],
    exit_code: int,
    fetch_outcome: Optional[FetchOutcome],
    paths_missing: list[str],
    shas_unreachable: list[str],
) -> dict[str, Any]:
    """Assemble the JSON output dict per plan §line 138.

    ``fetch_outcome`` is ``None`` only on early-exit paths (schema violation,
    missing claims) where we never reached the git resolution layer.
    """
    age: Optional[int]
    skip: Optional[str]
    if fetch_outcome is None:
        age = None
        skip = None
    else:
        age = fetch_outcome.fetch_head_age_seconds
        skip = fetch_outcome.skip_reason
    return {
        "plan": str(plan_path),
        "date": plan_date.isoformat() if plan_date is not None else None,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "exit_code": exit_code,
        "fetched_at": _now_iso(),
        "fetch_head_age_seconds": age,
        "fetch_skip_reason": skip,
        "drift": {
            "paths_missing": list(paths_missing),
            "shas_unreachable": list(shas_unreachable),
        },
    }


def _emit_json(payload: dict[str, Any]) -> None:
    import json
    import sys as _sys

    _sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    _sys.stdout.write("\n")
    _sys.stdout.flush()


def _emit_recon_line(fetch_outcome: FetchOutcome) -> None:
    """Write the structured RECON line to stderr per plan §D16.

    Two shapes:
      ``RECON warn fetch_skipped reason=<...> fetch_head_age_seconds=<n|null>``
      ``RECON info fetch_head_age_seconds=<n|null>``
    """
    import sys as _sys

    age = fetch_outcome.fetch_head_age_seconds
    age_str = "null" if age is None else str(age)
    if fetch_outcome.skip_reason is not None:
        line = (
            f"RECON warn fetch_skipped reason={fetch_outcome.skip_reason} "
            f"fetch_head_age_seconds={age_str}"
        )
    else:
        line = f"RECON info fetch_head_age_seconds={age_str}"
    print(line, file=_sys.stderr, flush=True)


def main(argv: Optional[list[str]] = None) -> None:
    """``plan-check`` CLI entry — validate plan-doc claims against ``origin/main``.

    Exit-code dispatch follows plan §D3: 0/2/7/8 (with 1 reserved for
    :class:`UsageError`). The function never returns a value; success is
    silent ``return`` and failures are :class:`SystemExit` with the code.
    """
    import argparse
    import sys as _sys

    # Local import keeps the module import-cheap (UsageError isn't needed for
    # the schema/git tiers tested in Unit 1/2).
    from backlink_publisher._util.errors import UsageError, handle_error

    parser = argparse.ArgumentParser(
        prog="plan-check",
        description=(
            "Validate a plan-doc's claims block against origin/main. Exits 0 on "
            "pass (or grandfathered/empty claims), 2 on schema violation, 7 on "
            "drift, 8 on missing claims for a post-cutoff plan-doc."
        ),
    )
    parser.add_argument(
        "plan_path",
        help="Path to a plan-doc under docs/plans/*.md",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout instead of a human table",
    )
    args = parser.parse_args(argv)

    # --- Phase 1: positional must point at a real file ---------------------
    plan_path = Path(args.plan_path)
    try:
        if not plan_path.exists():
            raise UsageError(f"{plan_path}: file not found")
        if not plan_path.is_file():
            raise UsageError(f"{plan_path}: not a regular file")
    except UsageError as exc:
        handle_error(exc)
        return  # pragma: no cover — handle_error always exits

    # --- Phase 2: read + parse frontmatter ---------------------------------
    plan_date: Optional[_dt.date] = None
    try:
        text = _read_plan_text(plan_path)
        fm = _parse_frontmatter(text)
    except PlanClaimsFrontmatterSchemaError as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=None,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)

    # Pull the typed date for downstream JSON payload before any other branch
    # can throw — we want the date in the output even when later layers fail.
    raw_date = fm.get("date")
    if isinstance(raw_date, _dt.datetime):
        raw_date = raw_date.date()
    if isinstance(raw_date, _dt.date):
        plan_date = raw_date

    # --- Phase 3: filename ↔ date lock (R11b) runs FIRST per Unit 1 docstring
    try:
        _check_filename_date_lock(plan_path, fm)
    except (PlanClaimsFilenameDateMismatch, PlanClaimsFrontmatterSchemaError) as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)

    # --- Phase 4: grandfather skip ----------------------------------------
    try:
        is_old = _grandfathered(fm)
    except PlanClaimsFrontmatterSchemaError as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)
    if is_old:
        # Pre-cutoff: silent exit 0. No stdout, no stderr, no JSON either —
        # the gate is a no-op for grandfathered plans per plan §step 8.
        return

    # --- Phase 5: claims schema validation --------------------------------
    try:
        claims = _validate_claims_schema(fm)
    except PlanClaimsMissingOnPostCutoff as exc:
        print(
            f"plan-check: missing claims block — {exc}",
            file=_sys.stderr,
            flush=True,
        )
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="missing_claims",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)
    except (
        PlanClaimsFrontmatterSchemaError,
        PlanClaimsGlobUnsupported,
    ) as exc:
        print(f"plan-check: schema violation — {exc}", file=_sys.stderr, flush=True)
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="schema_violation",
                    exit_code=exc.exit_code,
                    fetch_outcome=None,
                    paths_missing=[],
                    shas_unreachable=[],
                )
            )
        raise SystemExit(exc.exit_code)

    # --- Phase 6: empty claims (escape hatch) → silent exit 0 -------------
    if not claims.paths and not claims.shas:
        return

    # --- Phase 7: freshness + RECON line ----------------------------------
    fetch_outcome = _maybe_fetch_origin_main()
    _emit_recon_line(fetch_outcome)

    # --- Phase 8: resolve paths -------------------------------------------
    paths_missing: list[str] = []
    for p in claims.paths:
        ok, _status = _path_exists_on_main(p)
        if not ok:
            # Both "missing" and "git_error" count as drift in v1 (plan §Unit 3
            # step 12 explicitly accepts this — git_error still means we can't
            # prove the path is on main, so it's drift from the operator's POV).
            paths_missing.append(p)

    # --- Phase 9: resolve shas --------------------------------------------
    shas_unreachable: list[str] = []
    for s in claims.shas:
        ok, _status = _sha_reachable_from_main(s)
        if not ok:
            # D11: normalisation is display-only and the resolution functions
            # accept short SHAs natively. Surface as-given.
            shas_unreachable.append(s)

    # --- Phase 10: aggregate + dispatch -----------------------------------
    if paths_missing or shas_unreachable:
        # Drift — stderr table + stdout one-liner.
        print(
            _format_human_drift(plan_path, paths_missing, shas_unreachable),
            file=_sys.stderr,
            flush=True,
        )
        summary = (
            f"{len(paths_missing)} paths missing, "
            f"{len(shas_unreachable)} shas unreachable on origin/main"
        )
        if args.json:
            _emit_json(
                _build_json_payload(
                    plan_path=plan_path,
                    plan_date=plan_date,
                    status="drift",
                    exit_code=7,
                    fetch_outcome=fetch_outcome,
                    paths_missing=paths_missing,
                    shas_unreachable=shas_unreachable,
                )
            )
        else:
            print(summary, flush=True)
        raise SystemExit(7)

    # Pass — stdout one-liner.
    age = fetch_outcome.fetch_head_age_seconds
    age_str = "null" if age is None else str(age)
    summary = (
        f"plan-check: pass — {len(claims.paths)} paths + {len(claims.shas)} shas "
        f"resolved on origin/main (fetch_head_age_seconds={age_str})"
    )
    if args.json:
        _emit_json(
            _build_json_payload(
                plan_path=plan_path,
                plan_date=plan_date,
                status="pass",
                exit_code=0,
                fetch_outcome=fetch_outcome,
                paths_missing=[],
                shas_unreachable=[],
            )
        )
    else:
        print(summary, flush=True)
    return


if __name__ == "__main__":  # pragma: no cover
    main()
