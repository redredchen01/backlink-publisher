"""Shared validation logic for Telegraph Phase 0 ship-seal notes.

Single source of truth for:
- Seal block JSON schema (R2 in plan)
- Verdict comment validation (R4c + R15a)
- Body sha256 normalization (LF throughout)
- Allowlist loader (R4a)
- `gh api` subprocess chokepoint (monkeypatchable for tests)

This module is imported by both:
- `backlink_publisher.cli.phase0_seal` — the operator-side CLI (Units 3/4/5)
- `scripts/telegraph_spike/verify_seal.py` — the server-side sidecar (Unit 6)

See docs/plans/2026-05-18-009-feat-telegraph-phase0-ship-seal-plan.md (Unit 2).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

# Marker regex required in any verdict comment body for routine_comment kind.
# MUST stay aligned with the marker template appended to routine prompts (Unit 1).
# Maintenance gate: tests/test_phase0_marker_alignment.py asserts alignment.
MARKER_RE = re.compile(r"<!--\s*phase0-verdict:\s*result=pass\s+run_id=(\S+)\s*-->")

# Branch pattern for Telegraph staged-branch discovery (R10).
# velog Phase 0 forks the CLI by changing this constant.
TELEGRAPH_BRANCH_PATTERN = "local/telegraph-unit*-staged"

# Allowlist file relative to MAIN worktree root (NOT current worktree —
# closes v2-review adversarial F2).
ALLOWLIST_FILENAME = "scripts/telegraph_spike/authorized-routine-bots.yaml"

# Field allowlist for `phase0-seal show --format=markdown` output (R15b).
# Future seal-block fields default to EXCLUDED; add here to include.
MARKDOWN_FIELDS = (
    "unit",
    "branch",
    "main_sha",
    "sealed_at",
    "last_resealed_at",
    "sealed_by",
    "verdict_ref.kind",
    "verdict_ref.pr",
    "verdict_ref.comment_url",
    "verdict_ref.comment_author",
    "verdict_ref.comment_created_at",
    "verdict_ref.comment_body_sha256_short",
)

# Required top-level fields in every seal note JSON body.
_REQUIRED_SEAL_FIELDS = ("unit", "branch", "main_sha", "sealed_at", "sealed_by", "verdict_ref")
# Required fields inside verdict_ref for kind="routine_comment".
_REQUIRED_VERDICT_REF_ROUTINE = (
    "kind", "pr", "comment_url", "comment_id", "comment_author",
    "comment_created_at", "comment_updated_at", "comment_body_sha256",
)
# Required fields inside verdict_ref for kind="manual".
_REQUIRED_VERDICT_REF_MANUAL = ("kind", "evidence_path", "evidence_sha256")
# Allowed values for sealed_by (informational; not enforced in trust chain).
_SEALED_BY_VALUES = ("operator:init", "operator:reseal")
# Allowed values for verdict_ref.kind.
_VERDICT_KIND_VALUES = ("routine_comment", "manual")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SealValidationError(ValueError):
    """Seal note JSON body fails schema validation."""


class AllowlistFileMissingError(FileNotFoundError):
    """Allowlist file does not exist at the resolved main-worktree path."""


class EmptyAllowlistError(ValueError):
    """Allowlist file is present but contains zero authorized authors.

    Empty allowlist must NOT silently allow nothing — refuse to load
    rather than silently produce a permissive-by-omission state.
    """


class AllowlistSchemaError(ValueError):
    """Allowlist YAML structure does not match the expected schema."""


class GhNotInstalledError(FileNotFoundError):
    """`gh` CLI is not installed or not on PATH."""


class GhAuthError(RuntimeError):
    """`gh` CLI is installed but not authenticated for this repo."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_body(s: str) -> str:
    """Normalize line endings: CRLF/CR -> LF. Idempotent on LF-only input."""
    return s.replace("\r\n", "\n").replace("\r", "\n")


def sha256_hex(s: str) -> str:
    """Compute sha256 over the LF-normalized UTF-8 bytes of *s*."""
    return hashlib.sha256(normalize_body(s).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Main-worktree path resolution (v3 BLOCKER fix — closes v2-review adv F2)
# ---------------------------------------------------------------------------


def find_main_worktree_root(start: Path | None = None) -> Path:
    """Return the absolute path to the MAIN worktree's root.

    ``git rev-parse --show-toplevel`` (v2's broken approach) returns the
    CURRENT worktree's root. From a linked worktree like ``bp-local-unit2/``
    that returns the worktree's own path, NOT the main repo path — and the
    linked worktree's HEAD may predate the allowlist-file commit so the
    file is not present at that tree.

    Correct: parse ``git worktree list --porcelain``; the first record
    (the one without a ``branch`` entry pointing through ``.git/worktrees/``)
    is the main worktree.

    Fallback: ``git rev-parse --path-format=absolute --git-common-dir`` returns
    the ``.git`` directory of the MAIN worktree (linked worktrees share it).
    Its parent is the main worktree root.
    """
    cwd = start if start is not None else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = ""

    # First record is the main worktree. Each record starts with "worktree <path>".
    for line in out.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):]).resolve()

    # Fallback via --git-common-dir.
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout.strip()
        if common:
            return Path(common).parent.resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Last resort: cwd. Allowlist may not be findable from here, which is OK —
    # load_allowlist will report the path it tried.
    return Path(cwd).resolve()


# ---------------------------------------------------------------------------
# Allowlist loader
# ---------------------------------------------------------------------------


def load_allowlist(repo_root: Path | None = None) -> dict:
    """Load the authorized-routine-bots allowlist.

    Resolution order:
        1. Explicit ``repo_root`` argument (preferred for tests — inject tmp dir).
        2. Auto-detect MAIN worktree root via ``find_main_worktree_root()``.

    Returns a dict with keys::

        {
            "schema_version": int,
            "authorized_authors": [
                {"login": str, "routine_id": str, "captured_at": str,
                 "captured_by": str, "run_id_observed": str},
                ...
            ],
            "_path": str,            # absolute path the loader resolved (debug aid)
            "_logins": frozenset[str],  # normalized set for quick membership tests
        }

    Raises:
        AllowlistFileMissingError: when the resolved file does not exist.
        EmptyAllowlistError: when the file parses but has zero authors.
        AllowlistSchemaError: when the YAML structure does not match expectations.
    """
    root = Path(repo_root).resolve() if repo_root is not None else find_main_worktree_root()
    path = root / ALLOWLIST_FILENAME
    if not path.exists():
        raise AllowlistFileMissingError(
            f"allowlist not found at {path} "
            f"(resolved from main-worktree root {root}); "
            f"see Unit 0 in docs/plans/2026-05-18-009-...-plan.md to bootstrap"
        )

    try:
        import yaml  # local import — keeps cold-import surface small
    except ImportError as exc:  # pragma: no cover - environment hygiene
        raise RuntimeError("PyYAML required to load allowlist; install pyyaml") from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AllowlistSchemaError(f"{path}: top-level must be a mapping, got {type(raw).__name__}")

    authors = raw.get("authorized_authors")
    if not isinstance(authors, list):
        raise AllowlistSchemaError(f"{path}: 'authorized_authors' must be a list")
    if not authors:
        raise EmptyAllowlistError(f"{path}: authorized_authors is empty (refusing to allow nothing)")

    logins: set[str] = set()
    for i, entry in enumerate(authors):
        if not isinstance(entry, dict):
            raise AllowlistSchemaError(f"{path}: authorized_authors[{i}] must be a mapping")
        login = entry.get("login")
        if not isinstance(login, str) or not login.strip():
            raise AllowlistSchemaError(f"{path}: authorized_authors[{i}].login must be non-empty string")
        if "routine_id" not in entry:
            raise AllowlistSchemaError(f"{path}: authorized_authors[{i}].routine_id is required")
        logins.add(login.strip())

    return {
        "schema_version": raw.get("schema_version", 1),
        "authorized_authors": authors,
        "_path": str(path),
        "_logins": frozenset(logins),
    }


# ---------------------------------------------------------------------------
# Seal schema validation
# ---------------------------------------------------------------------------


def validate_seal_schema(d: dict) -> None:
    """Strict-positive validation of a seal note JSON body (R2).

    Raises SealValidationError with a specific field name on first violation.
    Every required field must be present with the correct type — never silently
    accept partial data (per `save-config-write-paths-bypass-preservation`
    learning).
    """
    if not isinstance(d, dict):
        raise SealValidationError(f"seal body must be an object, got {type(d).__name__}")

    for field in _REQUIRED_SEAL_FIELDS:
        if field not in d:
            raise SealValidationError(f"missing field: {field}")

    if d["sealed_by"] not in _SEALED_BY_VALUES:
        raise SealValidationError(
            f"sealed_by must be one of {_SEALED_BY_VALUES}, got {d['sealed_by']!r}"
        )

    if not _looks_like_sha(d["main_sha"]):
        raise SealValidationError(f"main_sha must be 40-char hex, got {d['main_sha']!r}")

    if not isinstance(d["branch"], str) or not d["branch"]:
        raise SealValidationError("branch must be non-empty string")

    if not isinstance(d["unit"], str) or not d["unit"]:
        raise SealValidationError("unit must be non-empty string")

    if not isinstance(d["sealed_at"], str) or not d["sealed_at"]:
        raise SealValidationError("sealed_at must be a non-empty ISO 8601 string")

    if "last_resealed_at" in d and d["last_resealed_at"] is not None and not isinstance(d["last_resealed_at"], str):
        raise SealValidationError("last_resealed_at must be a string or null")

    ref = d["verdict_ref"]
    if not isinstance(ref, dict):
        raise SealValidationError("verdict_ref must be an object")

    kind = ref.get("kind")
    if kind == "routine_comment":
        required = _REQUIRED_VERDICT_REF_ROUTINE
    elif kind == "manual":
        required = _REQUIRED_VERDICT_REF_MANUAL
    else:
        raise SealValidationError(
            f"verdict_ref.kind must be one of {_VERDICT_KIND_VALUES}, got {kind!r}"
        )
    for f in required:
        if f not in ref:
            raise SealValidationError(f"missing field: verdict_ref.{f}")

    if kind == "routine_comment":
        for hex_field in ("comment_body_sha256",):
            v = ref[hex_field]
            if not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v):
                raise SealValidationError(f"verdict_ref.{hex_field} must be 64-char hex, got {v!r}")
    elif kind == "manual":
        v = ref["evidence_sha256"]
        if not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v):
            raise SealValidationError(f"verdict_ref.evidence_sha256 must be 64-char hex, got {v!r}")


def _looks_like_sha(s: object) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[0-9a-f]{40}", s))


# ---------------------------------------------------------------------------
# Verdict comment validation
# ---------------------------------------------------------------------------


def validate_verdict_comment(
    comment: dict,
    *,
    expected_pr: int,
    allowlist: dict,
) -> dict:
    """Validate a GitHub PR comment as a routine G1 Pass verdict source.

    *comment* is the parsed JSON response from ``gh api repos/.../issues/comments/<id>``.
    Returns a normalized dict with extracted fields used to build a seal::

        {
            "run_id": <captured from marker regex>,
            "user_login": comment["user"]["login"],
            "comment_id": int(comment["id"]),
            "comment_url": comment["html_url"],
            "comment_created_at": comment["created_at"],
            "comment_updated_at": comment["updated_at"],
            "body_sha256": sha256 of LF-normalized body,
            "body_normalized": LF-normalized body (for caller debug),
        }

    Raises SealValidationError naming the specific check that failed.
    """
    if not isinstance(comment, dict):
        raise SealValidationError(f"comment payload must be an object, got {type(comment).__name__}")

    user = comment.get("user")
    if not isinstance(user, dict):
        raise SealValidationError("comment.user is missing or not an object")
    login = user.get("login")
    if not isinstance(login, str):
        raise SealValidationError("comment.user.login is missing or not a string")
    if login not in allowlist["_logins"]:
        raise SealValidationError(
            f"comment author {login!r} is NOT in authorized-routine-bots allowlist "
            f"({allowlist['_path']}); refusing to accept verdict"
        )

    issue_url = comment.get("issue_url")
    if not isinstance(issue_url, str):
        raise SealValidationError("comment.issue_url is missing or not a string")
    if not issue_url.endswith(f"/pulls/{expected_pr}") and not issue_url.endswith(f"/issues/{expected_pr}"):
        raise SealValidationError(
            f"comment.issue_url does not target PR #{expected_pr}: {issue_url}"
        )

    body = comment.get("body")
    if not isinstance(body, str):
        raise SealValidationError("comment.body is missing or not a string")
    body_norm = normalize_body(body)
    m = MARKER_RE.search(body_norm)
    if not m:
        raise SealValidationError(
            "comment body lacks the routine marker "
            "`<!-- phase0-verdict: result=pass run_id=... -->`"
        )
    run_id = m.group(1)

    return {
        "run_id": run_id,
        "user_login": login,
        "comment_id": int(comment["id"]),
        "comment_url": comment.get("html_url") or comment_url_fallback(comment),
        "comment_created_at": comment.get("created_at"),
        "comment_updated_at": comment.get("updated_at"),
        "body_sha256": sha256_hex(body),
        "body_normalized": body_norm,
    }


def comment_url_fallback(comment: dict) -> str:
    """Derive a comment URL when html_url is missing (rare; some gh-api versions)."""
    return str(comment.get("url", ""))


# ---------------------------------------------------------------------------
# gh CLI subprocess chokepoint (monkeypatchable)
# ---------------------------------------------------------------------------


def _run_gh(*args: str, timeout: float = 30.0) -> dict:
    """Invoke ``gh api <args>`` and return parsed JSON.

    Single chokepoint so tests can ``monkeypatch.setattr(<module>._run_gh, ...)``
    with explicit mock returns (never empty-success — per
    `tests-coupled-to-operator-config-state-2026-05-18` learning).

    Raises:
        GhNotInstalledError: if ``gh`` binary is not on PATH.
        GhAuthError: if gh returns auth failure (exit 4 or stderr-detected).
        RuntimeError: on other gh failures.
    """
    cmd = ["gh", "api", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise GhNotInstalledError(
            "`gh` CLI not found on PATH; install from https://cli.github.com/"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh api timed out after {timeout}s: {' '.join(args)}") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "authentication" in stderr.lower() or "auth status" in stderr.lower() or proc.returncode == 4:
            raise GhAuthError(
                f"`gh` not authenticated for this repo; run `gh auth login` first. "
                f"stderr: {stderr[:200]}"
            )
        raise RuntimeError(
            f"gh api failed (exit {proc.returncode}): {' '.join(cmd)}\n{stderr[:500]}"
        )

    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"gh api returned non-JSON: {proc.stdout[:200]!r}"
        ) from exc
