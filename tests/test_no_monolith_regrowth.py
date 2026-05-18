"""R5 F7: enforce monolith SLOC ceilings against monolith_budget.toml.

See:
    docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md
    docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md

Hard-fail enforcement (R4): current SLOC must not exceed the budgeted ceiling
for any monitored file. Schema enforcement (R5): budget entries must have
integer `ceiling` and string `rationale` >=80 chars. Warning-only canary (R7):
any src/ file > 500 SLOC and not in the budget emits a UserWarning (does not
fail). Radon counter pinning: a hand-crafted fixture catches measurement-tool
drift before it silently shifts ceiling math.
"""

from __future__ import annotations

import tomllib
import warnings
from pathlib import Path

import pytest
import radon.raw

REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGET_FILE = REPO_ROOT / "monolith_budget.toml"
RATIONALE_MIN_CHARS = 80
SEED_HEADROOM_MAX = 50  # Max allowed gap (ceiling - current_SLOC) for a healthy seed.
WARNING_CANARY_SLOC_THRESHOLD = 500

# Load at module-collection time so pytest's parametrize sees concrete budget keys.
# Missing/malformed budget file raises during collection -- pytest reports a clear error.
BUDGET = tomllib.loads(BUDGET_FILE.read_text())
MONITORED_PATHS = list(BUDGET["files"].keys())

# SLOC canary: pins radon's counter against a hand-crafted fixture. Re-baseline
# only on a deliberate radon bump (also re-measures every monitored ceiling).
SLOC_CANARY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sloc_canary.py"
SLOC_CANARY_EXPECTED = 31


def _measure_sloc(path: Path) -> int:
    """Read and analyze a Python source file; return its radon SLOC.

    Raises pytest.fail with an explicit, action-suggesting message on read or
    syntax errors so failures name the offending file rather than dumping a
    Python traceback.
    """
    try:
        text = path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        pytest.fail(
            f"Monitored file {path} not readable "
            f"({exc.__class__.__name__}: {exc}). "
            f"Update monolith_budget.toml (delete the entry, or fix the path if renamed) "
            f"or restore the file."
        )
    try:
        return radon.raw.analyze(text).sloc
    except SyntaxError as exc:
        pytest.fail(
            f"Monitored file {path} contains a syntax error -- radon cannot parse it "
            f"({exc!r}). The repo's CI py_compile sweep will surface the underlying error. "
            f"Fix the syntax error and re-run."
        )


def _check_file_within_ceiling(
    repo_root: Path, rel_path: str, ceiling: int, rationale: str
) -> None:
    """Assert one monitored file's current SLOC <= its budgeted ceiling.

    Extracted so the parametrized real-repo test and the synthetic-tmp-path
    red-path test share the same assertion shape and failure message format.
    """
    full_path = repo_root / rel_path
    actual = _measure_sloc(full_path)
    delta = actual - ceiling
    assert actual <= ceiling, (
        f"{rel_path}: SLOC={actual} exceeds ceiling={ceiling} by {delta}. "
        f"Rationale: '{rationale}'. "
        f"To resolve: lower ceiling in monolith_budget.toml (with updated rationale) "
        f"or extract code from this file."
    )


def _scan_for_undeclared_monoliths(
    scan_root: Path, declared_paths: set[str], repo_root: Path
) -> None:
    """Walk scan_root for .py files; emit UserWarning for any > threshold not declared.

    Calls warnings.warn directly. Callers wrap with pytest.warns only when a
    warning is guaranteed (synthetic-tmp-path test); the real-tree caller does
    NOT wrap, because the steady state may have zero candidates and pytest.warns
    would fail with DID NOT WARN.
    """
    for path in scan_root.rglob("*.py"):
        try:
            relative = str(path.relative_to(repo_root))
        except ValueError:
            continue
        if relative in declared_paths:
            continue
        try:
            sloc = radon.raw.analyze(path.read_text()).sloc
        except (SyntaxError, OSError):
            continue
        if sloc > WARNING_CANARY_SLOC_THRESHOLD:
            warnings.warn(
                f"Undeclared monolith candidate: {relative} has SLOC={sloc} "
                f"(>{WARNING_CANARY_SLOC_THRESHOLD}). Consider adding to "
                f"monolith_budget.toml or extracting.",
                UserWarning,
                stacklevel=2,
            )


# ---------- Tests ----------


def test_budget_file_loads_and_has_files_table() -> None:
    """The budget file must parse as TOML and contain a non-empty [files] table."""
    assert BUDGET_FILE.exists(), f"{BUDGET_FILE} not found at repo root"
    assert "files" in BUDGET, "monolith_budget.toml missing top-level [files] table"
    assert isinstance(BUDGET["files"], dict)
    assert len(BUDGET["files"]) > 0, "monolith_budget.toml has zero monitored files"


@pytest.mark.parametrize("path", MONITORED_PATHS)
def test_entry_schema(path: str) -> None:
    """Each entry must have integer `ceiling` and string `rationale` >=80 chars."""
    entry = BUDGET["files"][path]
    assert "ceiling" in entry, f"Entry '{path}' missing required field 'ceiling'"
    assert isinstance(entry["ceiling"], int), (
        f"Entry '{path}' field 'ceiling' must be int, "
        f"got {type(entry['ceiling']).__name__}"
    )
    assert "rationale" in entry, f"Entry '{path}' missing required field 'rationale'"
    assert isinstance(entry["rationale"], str), (
        f"Entry '{path}' field 'rationale' must be str, "
        f"got {type(entry['rationale']).__name__}"
    )
    n = len(entry["rationale"])
    assert n >= RATIONALE_MIN_CHARS, (
        f"Entry '{path}' rationale length {n} < {RATIONALE_MIN_CHARS} minimum. "
        f"Expand the rationale to explain what motivated the ceiling and the "
        f"file's expected settling shape."
    )


@pytest.mark.parametrize("path", MONITORED_PATHS)
def test_sloc_within_ceiling(path: str) -> None:
    """R4: each monitored file's current SLOC must not exceed its budgeted ceiling."""
    entry = BUDGET["files"][path]
    _check_file_within_ceiling(REPO_ROOT, path, entry["ceiling"], entry["rationale"])


@pytest.mark.parametrize("path", MONITORED_PATHS)
def test_policy_to_seed_drift(path: str) -> None:
    """Catch typo-class ceiling errors (e.g. +300 instead of +30 headroom).

    Per origin policy: ceiling = round_up_to_10(current_SLOC + 30). After a
    ratchet-down PR this still holds; only the rationale changes. Wide
    headroom is a smell, not a bug -- the failure message tells the operator
    to either tighten the ceiling or document the gap in the rationale.
    """
    entry = BUDGET["files"][path]
    actual = _measure_sloc(REPO_ROOT / path)
    headroom = entry["ceiling"] - actual
    assert headroom >= 0, (
        f"{path}: ceiling={entry['ceiling']} below current SLOC={actual} "
        f"(headroom={headroom}). Should have been caught by test_sloc_within_ceiling. "
        f"Likely a measurement error in the seed PR."
    )
    assert headroom <= SEED_HEADROOM_MAX, (
        f"{path}: ceiling={entry['ceiling']} has headroom {headroom} > "
        f"{SEED_HEADROOM_MAX} vs current SLOC={actual}. Policy is "
        f"ceiling = round_up_to_10(current_SLOC + 30). Either re-measure and "
        f"tighten the ceiling, or document the wide headroom in the rationale "
        f"(e.g. 'awaiting feature X, accept N SLOC headroom for one sprint')."
    )


def test_warning_canary_for_undeclared_large_files() -> None:
    """R7: scan real src/ tree; emit UserWarnings for undeclared >500-SLOC files.

    Asserts nothing about warning count -- visibility comes from pytest's
    default warning summary in CI output. The steady state with zero
    candidates produces no warnings and the test still passes; the
    synthetic-tmp-path test below proves the scanner itself fires correctly.
    """
    declared = set(MONITORED_PATHS)
    _scan_for_undeclared_monoliths(
        scan_root=REPO_ROOT / "src" / "backlink_publisher",
        declared_paths=declared,
        repo_root=REPO_ROOT,
    )


def test_warning_canary_fires_for_synthetic_large_file(tmp_path: Path) -> None:
    """Verify the canary scanner emits UserWarning when a >500-SLOC file is undeclared."""
    fake_src = tmp_path / "src"
    fake_src.mkdir()
    big_file = fake_src / "big.py"
    # 600 bare assignments -- well above the 500-SLOC threshold.
    big_file.write_text("\n".join(f"v{i} = {i}" for i in range(600)))
    with pytest.warns(UserWarning, match=r"Undeclared monolith candidate"):
        _scan_for_undeclared_monoliths(
            scan_root=fake_src, declared_paths=set(), repo_root=tmp_path
        )


def test_assertion_fires_when_synthetic_ceiling_exceeded(tmp_path: Path) -> None:
    """Verify the R4 assertion fires with the documented failure-message format."""
    fake_src = tmp_path / "src"
    fake_src.mkdir()
    growthy = fake_src / "growthy.py"
    # 100 bare assignments -- exceeds the synthetic ceiling=50 by 50.
    growthy.write_text("\n".join(f"v{i} = {i}" for i in range(100)))
    with pytest.raises(AssertionError, match=r"exceeds ceiling"):
        _check_file_within_ceiling(
            repo_root=tmp_path,
            rel_path="src/growthy.py",
            ceiling=50,
            rationale=(
                "synthetic test rationale that is intentionally longer "
                "than 80 characters to satisfy schema"
            ),
        )


def test_radon_sloc_behavior_pinned() -> None:
    """SLOC canary: pin radon's counter against the hand-crafted fixture.

    Failure means radon's counter logic shifted (new radon version, new Python
    AST shape). Re-baseline only on a deliberate radon bump -- bumping the
    pinned version is also treated as a budget edit per origin R6 (re-measure
    every monitored ceiling and update SLOC_CANARY_EXPECTED in the same PR).
    """
    actual = radon.raw.analyze(SLOC_CANARY_FIXTURE.read_text()).sloc
    assert actual == SLOC_CANARY_EXPECTED, (
        f"SLOC fixture expected={SLOC_CANARY_EXPECTED} but radon returned={actual}. "
        f"Counter behavior changed -- re-baseline all 5 monitored ceilings and "
        f"update SLOC_CANARY_EXPECTED in tests/test_no_monolith_regrowth.py."
    )
