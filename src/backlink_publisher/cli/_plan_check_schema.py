"""Schema tier for ``plan-check`` — frontmatter parsing, claims validation,
grandfather cutoff, filename-date lock.

Extracted from ``plan_check.py`` to keep the CLI module focused on dispatch.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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
    # Strip UTF-8 BOM (U+FEFF) if present so the leading ``---`` is still detected.
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
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
        raise PlanClaimsFrontmatterSchemaError(
            "plan-doc missing YAML frontmatter (no leading ``---``)"
        )
    # Drop the leading fence line, then split on the next ``---`` line.
    after_open = text[3:]
    # First newline after opening fence
    if after_open.startswith("\n"):
        after_open = after_open[1:]
    elif after_open.startswith("\r\n"):
        after_open = after_open[2:]
    # Locate closing fence: a line that is exactly ``---``
    closing_match = re.search(r"(?m)^---\s*$", after_open)
    if closing_match is None:
        raise PlanClaimsFrontmatterSchemaError(
            "plan-doc missing closing ``---`` for YAML frontmatter"
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
    # Accept only ``datetime.date`` (PyYAML emits this for ISO-8601 dates).
    # ``datetime.datetime`` is a subclass of ``date``; if a plan-doc carries a full
    # timestamp, we coerce to its ``.date()`` component. Strings are rejected.
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
        # by the ``_grandfathered`` check upstream, so reaching this branch means
        # the caller (Unit 3) already decided the plan-doc is in scope.
        raise PlanClaimsMissingOnPostCutoff(
            "plan-doc post-cutoff requires a ``claims:`` block "
            "(use ``claims: {}`` to opt out explicitly)"
        )
    claims = fm["claims"]
    # ``claims: {}`` parses as an empty dict — the explicit opt-out. ``claims:`` with
    # no value parses as None; we accept that as equivalent for ergonomics.
    if claims is None:
        return ClaimsBlock(paths=[], shas=[], is_explicit_optout=True)
    if not isinstance(claims, dict):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc ``claims:`` must be a mapping, got {type(claims).__name__}"
        )
    unknown = set(claims.keys()) - _ALLOWED_CLAIMS_KEYS
    if unknown:
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc ``claims:`` has unknown key(s) {sorted(unknown)}; "
            f"allowed: {sorted(_ALLOWED_CLAIMS_KEYS)}"
        )
    paths_raw = claims.get("paths", [])
    shas_raw = claims.get("shas", [])
    if not isinstance(paths_raw, list):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc ``claims.paths`` must be a list, got {type(paths_raw).__name__}"
        )
    if not isinstance(shas_raw, list):
        raise PlanClaimsFrontmatterSchemaError(
            f"plan-doc ``claims.shas`` must be a list, got {type(shas_raw).__name__}"
        )
    paths: list[str] = []
    for entry in paths_raw:
        if not isinstance(entry, str):
            raise PlanClaimsFrontmatterSchemaError(
                f"plan-doc ``claims.paths`` entries must be strings, got "
                f"{type(entry).__name__}: {entry!r}"
            )
        bad = _GLOB_CHARS.intersection(entry)
        if bad:
            raise PlanClaimsGlobUnsupported(
                f"plan-doc ``claims.paths`` entry {entry!r} contains glob character(s) "
                f"{sorted(bad)}; globs unsupported in v1"
            )
        paths.append(entry)
    shas: list[str] = []
    for entry in shas_raw:
        if not isinstance(entry, str):
            raise PlanClaimsFrontmatterSchemaError(
                f"plan-doc ``claims.shas`` entries must be strings, got "
                f"{type(entry).__name__}: {entry!r}"
            )
        if not _validate_sha_format(entry):
            raise PlanClaimsFrontmatterSchemaError(
                f"plan-doc ``claims.shas`` entry {entry!r} is not a valid sha "
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
            f"{plan_path.name}: filename does not match required ``YYYY-MM-DD-NNN-`` "
            f"prefix pattern"
        )
    filename_date = match.group(1)
    # Reuse the same typed-date check as ``_grandfathered`` so we get a single
    # source of truth for what counts as a valid ``date:`` field.
    raw_date = fm.get("date")
    if isinstance(raw_date, _dt.datetime):
        raw_date = raw_date.date()
    if not isinstance(raw_date, _dt.date):
        raise PlanClaimsFrontmatterSchemaError(
            "plan-doc ``date:`` must be ISO-8601 (YYYY-MM-DD); "
            "cannot enforce filename-date lock without a typed date"
        )
    fm_date = raw_date.isoformat()
    if filename_date != fm_date:
        raise PlanClaimsFilenameDateMismatch(
            f"{plan_path.name}: filename date {filename_date!r} disagrees with "
            f"frontmatter.date {fm_date!r}; both must be identical (D17 backdate lock)"
        )
