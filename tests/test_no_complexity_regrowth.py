"""Enforce per-function cyclomatic-complexity (CC) ceilings against complexity_budget.toml.

Sibling to tests/test_no_monolith_regrowth.py (which gates file SLOC). See:
    docs/plans/2026-05-29-005-feat-cyclomatic-complexity-budget-plan.md
    docs/brainstorms/2026-05-29-cyclomatic-complexity-budget-requirements.md

Enforcement model = named set + high backstop:
  (a) every function listed in complexity_budget.toml is held to its `ceiling` (no regrowth);
  (b) every function NOT listed is held to a global BACKSTOP (CC 30). Seed floor =
      BACKSTOP + 1, so there is no un-gated over-backstop gap.

Schema (R5): budget entries must have integer `ceiling` and string `rationale` >=80 chars.
CC canary: a hand-crafted fixture pins radon's CC counter so a measurement-tool bump is
caught before it silently shifts ceilings.

Adapted in structure (not policy) from test_no_monolith_regrowth.py: CC uses
radon.complexity.cc_visit() (per-function) instead of radon.raw.analyze().sloc (per-file),
keys are "<relpath>::<fullname>", and the seed convention is exact current CC (zero headroom)
rather than round_up_to_10(SLOC+30).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from radon.complexity import cc_visit
from radon.visitors import Class

REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGET_FILE = REPO_ROOT / "complexity_budget.toml"
RATIONALE_MIN_CHARS = 80
# Unlisted functions are capped here; seeded functions are capped by their own ceiling.
BACKSTOP = 30
# Max allowed gap (ceiling - current_CC) for a healthy seed. A seeded function that has
# drifted far below its ceiling should be re-tightened or its entry removed (if it dropped
# below the backstop). CC deltas are small integers, so this cap is tight.
CC_HEADROOM_MAX = 10

# Load at module-collection time so pytest's parametrize sees concrete budget keys.
# Missing/malformed budget file raises during collection -- pytest reports a clear error.
BUDGET = tomllib.loads(BUDGET_FILE.read_text())
MONITORED_KEYS = list(BUDGET["functions"].keys())

# CC canary: pins radon's complexity counter against the hand-crafted fixture. Re-baseline
# only on a deliberate radon bump (which also re-measures every monitored ceiling).
CC_CANARY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "cc_canary.py"
CC_CANARY_FUNC = "cc_canary_branchy"
CC_CANARY_EXPECTED = 12


def _split_key(key: str) -> tuple[str, str]:
    """Split a budget key '<relpath>::<fullname>' into (relpath, fullname).

    rsplit on the last '::' -- radon fullnames never contain '::' and POSIX paths
    never do either, so a single split is unambiguous.
    """
    relpath, _, fullname = key.rpartition("::")
    if not relpath or not fullname:
        pytest.fail(
            f"Budget key {key!r} is malformed: expected '<relpath>::<fullname>'. "
            f"Keys are str(path.relative_to(repo_root)) joined by '::' to the radon "
            f"block fullname (e.g. 'src/pkg/mod.py::Class.method')."
        )
    return relpath, fullname


def _function_blocks(text: str) -> list:
    """Return radon Function/Method blocks for a source text, skipping Class-aggregate blocks.

    Methods are Function instances with a classname; module functions have classname None;
    classes are Class instances (skipped, since their CC roughly aggregates already-gated
    methods). Only top-level blocks are returned -- nested defs live in block.closures and
    are intentionally invisible (the plan requires extracted helpers to be module-level).
    """
    return [b for b in cc_visit(text) if not isinstance(b, Class)]


def _measure_function_cc(repo_root: Path, key: str) -> int:
    """Find the budgeted function in the tree and return its radon CC.

    pytest.fail (rather than raising) on a missing file or a missing function so the
    failure names the stale budget entry instead of dumping a traceback.
    """
    relpath, fullname = _split_key(key)
    full_path = repo_root / relpath
    try:
        text = full_path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        pytest.fail(
            f"Budgeted file {relpath} not readable ({exc.__class__.__name__}: {exc}). "
            f"Update complexity_budget.toml (delete the entry, or fix the path if renamed)."
        )
    try:
        blocks = _function_blocks(text)
    except SyntaxError as exc:
        pytest.fail(
            f"Budgeted file {relpath} has a syntax error -- radon cannot parse it ({exc!r}). "
            f"The CI py_compile sweep will surface the underlying error."
        )
    for block in blocks:
        if block.fullname == fullname:
            return block.complexity
    pytest.fail(
        f"Budgeted function {fullname!r} not found in {relpath} "
        f"(it may have been renamed, removed, or decomposed). Update complexity_budget.toml: "
        f"remove the entry if the function is gone, or fix the fullname if renamed."
    )


def _assert_within_ceiling(key: str, actual: int, ceiling: int, rationale: str) -> None:
    """Assert one monitored function's current CC <= its budgeted ceiling.

    Extracted so the parametrized real-repo test and the synthetic-tmp red-path test share
    the same assertion shape and failure-message format (mirrors the monolith test's
    _check_file_within_ceiling).
    """
    delta = actual - ceiling
    assert actual <= ceiling, (
        f"{key}: CC={actual} exceeds ceiling={ceiling} by {delta}. "
        f"Rationale: '{rationale}'. To resolve: lower the function's complexity, "
        f"or raise the ceiling in complexity_budget.toml (with an updated >=80-char rationale) "
        f"in this same PR."
    )


def _scan_unlisted_over_backstop(
    scan_root: Path, declared_keys: set[str], repo_root: Path, backstop: int
) -> list[str]:
    """Walk scan_root for .py files; return 'relpath::fullname (CC=N)' for any unlisted
    Function/Method block whose CC exceeds the backstop."""
    violations: list[str] = []
    for path in scan_root.rglob("*.py"):
        try:
            relative = str(path.relative_to(repo_root))
        except ValueError:
            continue
        try:
            blocks = _function_blocks(path.read_text())
        except (SyntaxError, OSError):
            continue
        for block in blocks:
            key = f"{relative}::{block.fullname}"
            if key in declared_keys:
                continue
            if block.complexity > backstop:
                violations.append(f"{key} (CC={block.complexity})")
    return violations


# ---------- Tests ----------


def test_budget_file_loads_and_has_functions_table() -> None:
    """The budget file must parse as TOML and contain a non-empty [functions] table."""
    assert BUDGET_FILE.exists(), f"{BUDGET_FILE} not found at repo root"
    assert "functions" in BUDGET, "complexity_budget.toml missing top-level [functions] table"
    assert isinstance(BUDGET["functions"], dict)
    assert len(BUDGET["functions"]) > 0, "complexity_budget.toml has zero monitored functions"


@pytest.mark.parametrize("key", MONITORED_KEYS)
def test_entry_schema(key: str) -> None:
    """Each entry key must be '<relpath>::<fullname>' with int ceiling + str rationale >=80."""
    _split_key(key)  # asserts key shape
    entry = BUDGET["functions"][key]
    assert "ceiling" in entry, f"Entry '{key}' missing required field 'ceiling'"
    assert isinstance(entry["ceiling"], int), (
        f"Entry '{key}' field 'ceiling' must be int, got {type(entry['ceiling']).__name__}"
    )
    assert "rationale" in entry, f"Entry '{key}' missing required field 'rationale'"
    assert isinstance(entry["rationale"], str), (
        f"Entry '{key}' field 'rationale' must be str, got {type(entry['rationale']).__name__}"
    )
    n = len(entry["rationale"])
    assert n >= RATIONALE_MIN_CHARS, (
        f"Entry '{key}' rationale length {n} < {RATIONALE_MIN_CHARS} minimum. Expand the "
        f"rationale to explain the function's current shape and why the CC is high."
    )


@pytest.mark.parametrize("key", MONITORED_KEYS)
def test_seeded_ceiling_above_backstop(key: str) -> None:
    """Named entries exist only for functions above the backstop.

    A function at or below the backstop needs no entry (the backstop covers it); a stale
    entry for such a function loosens the gate. After a decomposition drops a function
    <= backstop, its entry should be removed in the same PR.
    """
    ceiling = BUDGET["functions"][key]["ceiling"]
    assert ceiling > BACKSTOP, (
        f"Entry '{key}' has ceiling={ceiling} <= backstop={BACKSTOP}. Functions at/below the "
        f"backstop need no entry -- remove it; the global backstop already covers them."
    )


@pytest.mark.parametrize("key", MONITORED_KEYS)
def test_seeded_function_within_ceiling(key: str) -> None:
    """Each monitored function's current CC must not exceed its budgeted ceiling."""
    entry = BUDGET["functions"][key]
    actual = _measure_function_cc(REPO_ROOT, key)
    _assert_within_ceiling(key, actual, entry["ceiling"], entry["rationale"])


@pytest.mark.parametrize("key", MONITORED_KEYS)
def test_seeded_cc_drift(key: str) -> None:
    """Catch a ceiling left far above current CC (e.g. after a refactor lowered the function).

    CC seed convention is exact current CC (zero headroom); a healthy entry has the ceiling
    within CC_HEADROOM_MAX of the measured CC. Wide headroom means re-tighten (or, if the
    function dropped below the backstop, remove the entry).
    """
    entry = BUDGET["functions"][key]
    actual = _measure_function_cc(REPO_ROOT, key)
    headroom = entry["ceiling"] - actual
    assert headroom >= 0, (
        f"{key}: ceiling={entry['ceiling']} below current CC={actual}. Should have been "
        f"caught by test_seeded_function_within_ceiling -- likely a measurement error."
    )
    assert headroom <= CC_HEADROOM_MAX, (
        f"{key}: ceiling={entry['ceiling']} has headroom {headroom} > {CC_HEADROOM_MAX} vs "
        f"current CC={actual}. Re-tighten the ceiling to the current CC (or remove the entry "
        f"if the function is now at/below the backstop={BACKSTOP})."
    )


def test_unlisted_functions_within_backstop() -> None:
    """Rule (b): every unlisted Function/Method block in src/ must have CC <= backstop."""
    declared = set(MONITORED_KEYS)
    violations = _scan_unlisted_over_backstop(
        scan_root=REPO_ROOT / "src" / "backlink_publisher",
        declared_keys=declared,
        repo_root=REPO_ROOT,
        backstop=BACKSTOP,
    )
    assert not violations, (
        f"{len(violations)} unlisted function(s) exceed the CC backstop ({BACKSTOP}):\n  "
        + "\n  ".join(sorted(violations))
        + "\nEither reduce the function's complexity, or add an explicit "
        "complexity_budget.toml entry (with a >=80-char rationale) in this same PR."
    )


def test_radon_cc_behavior_pinned() -> None:
    """CC canary: pin radon's complexity counter against the hand-crafted fixture.

    Failure means radon's CC logic shifted (new radon version, new Python AST shape).
    Re-baseline only on a deliberate radon bump -- which is also a budget edit: re-measure
    every monitored ceiling and update CC_CANARY_EXPECTED in the same PR.
    """
    blocks = _function_blocks(CC_CANARY_FIXTURE.read_text())
    match = next((b for b in blocks if b.fullname == CC_CANARY_FUNC), None)
    assert match is not None, f"canary function {CC_CANARY_FUNC!r} not found in {CC_CANARY_FIXTURE}"
    assert match.complexity == CC_CANARY_EXPECTED, (
        f"CC canary expected={CC_CANARY_EXPECTED} but radon returned={match.complexity}. "
        f"radon's counter behavior changed -- re-baseline all monitored ceilings and update "
        f"CC_CANARY_EXPECTED in tests/test_no_complexity_regrowth.py."
    )


def test_seeded_assertion_fires_when_ceiling_exceeded(tmp_path: Path) -> None:
    """Verify rule (a) fires with the documented failure-message format (synthetic tree)."""
    fake = tmp_path / "src"
    fake.mkdir()
    growthy = fake / "growthy.py"
    # A function whose CC exceeds a synthetic ceiling of 2.
    growthy.write_text(
        "def f(x):\n"
        "    if x > 0:\n        return 1\n"
        "    if x < 0:\n        return -1\n"
        "    return 0\n"
    )
    key = "src/growthy.py::f"
    actual = _measure_function_cc(tmp_path, key)
    with pytest.raises(AssertionError, match=r"exceeds ceiling"):
        _assert_within_ceiling(key, actual, ceiling=2, rationale="synthetic")


def test_backstop_catches_unlisted_over_limit(tmp_path: Path) -> None:
    """Verify rule (b) flags an unlisted function whose CC exceeds the backstop."""
    fake_src = tmp_path / "src"
    fake_src.mkdir()
    big = fake_src / "big.py"
    # Build a function with CC well above the backstop: 1 + 40 `if` statements = CC 41.
    body = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(40))
    big.write_text(f"def huge(x):\n{body}\n    return -1\n")
    violations = _scan_unlisted_over_backstop(
        scan_root=fake_src, declared_keys=set(), repo_root=tmp_path, backstop=BACKSTOP
    )
    assert any("src/big.py::huge" in v for v in violations), (
        f"backstop scanner did not flag the synthetic over-backstop function: {violations}"
    )
