"""Footprint Regression Gate — Arm A (Plan 2026-05-18-007).

# ARM_B_GATE_REDESIGN_REQUIRED — see docs/brainstorms/2026-05-18-footprint-regression-gate-requirements.md
# Key Decision #1. When Arm B (renderer self-vary) lands, this point-baseline
# gate must be REPLACED with per-dimension expected-ranges, not just re-frozen.
# Removing this comment is the Arm B PR's responsibility.

This module is the gate itself plus tests OF the gate. On every CI run, the
three corpora (work_themed / zh_short / markdown_it) are regenerated via
pure-function entry points, analyzed via ``footprint.analyze_corpus``, and
compared against committed baselines under ``tests/baselines/``. Drift, alarm
crossings, schema mismatches, zero-link engine breakage, and missing fixtures
all raise distinguishable error classes the operator can recognize at 2am.

Break-glass: drop ``tests/baselines/footprint_concentration.OVERRIDE.md``
with a ``reason:`` line and the gate downgrades failures to a loud warning.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.footprint import (
    DEFAULT_THRESHOLD_ALARM_PCT,
    DEFAULT_THRESHOLD_DRIFT_PP,
    FootprintReport,
    SCHEMA_VERSION,
    THRESHOLD_OVERRIDES,
    analyze_corpus,
)
from backlink_publisher.footprint_corpus import (
    CORPUS_NAMES,
    compute_fixture_set_id,
    make_corpus,
)


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINES_DIR = _REPO_ROOT / "tests" / "baselines"
_OVERRIDE_PATH = _BASELINES_DIR / "footprint_concentration.OVERRIDE.md"
_DIMENSIONS: tuple[str, ...] = ("attr_order", "rel_value", "target_value", "preceding_char")


def _baseline_path(corpus_name: str) -> Path:
    return _BASELINES_DIR / f"footprint_concentration_{corpus_name}.json"


# ---------------------------------------------------------------------------
# Error classes (Plan R4 + G3.3)
# ---------------------------------------------------------------------------


class FootprintGateError(AssertionError):
    """Base class for gate failures. Subclasses pinpoint the failure mode."""


class FootprintGateDrift(FootprintGateError):
    """A dimension drifted more than THRESHOLD_DRIFT_PP from baseline."""


class FootprintGateAlarmCrossed(FootprintGateError):
    """A dimension crossed THRESHOLD_ALARM_PCT from below — new cluster-key risk."""


class FootprintGateSchemaMismatch(FootprintGateError):
    """Baseline ``schema_version`` or ``fixture_set_id`` does not match engine."""


class FootprintGateZeroLinks(FootprintGateError):
    """Engine sanity check failed — corpus produced zero links."""


class FootprintGateOverrideMalformed(FootprintGateError):
    """OVERRIDE.md present but missing required ``reason:`` line."""


class FootprintGateBaselineMissing(FootprintGateError):
    """Baseline file does not exist (distinct from schema_version mismatch).

    Distinguishable so a 2am operator hitting this on a fresh branch knows
    to run `footprint baseline regenerate` without hunting for a phantom
    engine bump that doesn't exist."""


# ---------------------------------------------------------------------------
# Failure-report structured tuple (Plan deferred-resolved: failure-message contract)
# ---------------------------------------------------------------------------


@dataclass
class _DimFailure:
    renderer_path: str
    dimension: str
    baseline_value: float
    observed_value: float
    delta: float
    failure_mode: str  # "drift" | "alarm_crossed"


@dataclass
class FailureReport:
    """Structured per-tuple failure report rendered as flat human-readable block."""

    failures: list[_DimFailure] = field(default_factory=list)

    def add(self, fail: _DimFailure) -> None:
        self.failures.append(fail)

    def render(self) -> str:
        # Sort largest delta first so the 2am operator sees the worst offender at top.
        ordered = sorted(self.failures, key=lambda f: -abs(f.delta))
        lines = [
            "Footprint Gate failure(s) — sorted by largest delta:",
            "",
        ]
        for f in ordered:
            lines.append(
                f"  [{f.failure_mode}] {f.renderer_path}/{f.dimension}: "
                f"baseline={f.baseline_value:.2f}% observed={f.observed_value:.2f}% "
                f"(Δ={f.delta:+.2f}pp)"
            )
        lines.append("")
        lines.append(
            "Fix: either (a) restore renderer to its prior shape, or "
            "(b) run `PYTHONHASHSEED=0 footprint baseline regenerate --path "
            "<which> --reason 'why this drift is correct (>15 chars, "
            "substantive)'` and commit the new baseline in this PR."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PYTHONHASHSEED guard — module-scoped autouse fixture (Plan R10 + G5.3)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _require_pythonhashseed_zero():
    """Fail fast if PYTHONHASHSEED isn't pinned. Module-scoped + autouse so it
    only fires when THIS test module is collected."""
    val = os.environ.get("PYTHONHASHSEED")
    if val != "0":
        raise pytest.UsageError(
            f"footprint regression gate requires PYTHONHASHSEED=0 (got {val!r}). "
            "Run as: PYTHONHASHSEED=0 pytest tests/test_footprint_regression.py "
            "(or rely on pytest-env via [tool.pytest.ini_options].env in pyproject.toml)."
        )
    yield


# ---------------------------------------------------------------------------
# OVERRIDE.md handling (Plan R14 + G3.3)
# ---------------------------------------------------------------------------


def _override_active() -> tuple[bool, str | None]:
    """Return ``(active, file_contents)``. Active iff file exists AND has a
    non-empty ``reason:`` line. Raises ``FootprintGateOverrideMalformed`` if
    the file exists but lacks ``reason:``."""
    if not _OVERRIDE_PATH.exists():
        return (False, None)
    contents = _OVERRIDE_PATH.read_text(encoding="utf-8")
    has_reason = any(line.lstrip().lower().startswith("reason:") for line in contents.splitlines())
    if not has_reason:
        try:
            display_path = str(_OVERRIDE_PATH.relative_to(_REPO_ROOT))
        except ValueError:
            display_path = str(_OVERRIDE_PATH)
        raise FootprintGateOverrideMalformed(
            f"{display_path} exists but lacks a `reason:` "
            "line. Add `reason: <why this override is acceptable>` or remove the file."
        )
    return (True, contents)


def _override_age_days() -> int | None:
    """Best-effort: days since the OVERRIDE.md was committed (per Plan G3.2)."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(_OVERRIDE_PATH)],
            capture_output=True, text=True, cwd=_REPO_ROOT, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        commit_date = result.stdout.strip()
        from datetime import date
        ymd = [int(x) for x in commit_date.split("-")]
        committed = date(ymd[0], ymd[1], ymd[2])
        return (date.today() - committed).days
    except (FileNotFoundError, ValueError, OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------------------
# Baseline loading + comparison (Plan R3, R4, R6, R11)
# ---------------------------------------------------------------------------


def _load_baseline(corpus_name: str) -> dict[str, Any]:
    path = _baseline_path(corpus_name)
    if not path.exists():
        raise FootprintGateBaselineMissing(
            f"baseline file does NOT exist (not a schema bump, just a missing file): "
            f"{path.relative_to(_REPO_ROOT)}. This is normal on a fresh branch / "
            "first-time setup. Run: PYTHONHASHSEED=0 footprint baseline regenerate "
            "--path all --reason 'Initial Arm A baseline — SCHEMA_VERSION=1, "
            "deterministic tie-break per R11'"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _thresholds(corpus_name: str, dimension: str) -> tuple[float, float]:
    """Per-(corpus, dimension) drift/alarm thresholds with default fallback."""
    return THRESHOLD_OVERRIDES.get(
        (corpus_name, dimension),
        (DEFAULT_THRESHOLD_DRIFT_PP, DEFAULT_THRESHOLD_ALARM_PCT),
    )


def _check_corpus(corpus_name: str, report: FootprintReport, baseline: dict[str, Any]) -> FailureReport:
    """Apply R4 drift + crossing-from-below + schema/fixture checks."""
    fr = FailureReport()

    baseline_sv = baseline.get("schema_version")
    if baseline_sv != SCHEMA_VERSION:
        raise FootprintGateSchemaMismatch(
            f"{corpus_name}: schema_version mismatch — baseline={baseline_sv} "
            f"engine={SCHEMA_VERSION}. Run: PYTHONHASHSEED=0 footprint baseline "
            f"regenerate --path {corpus_name} --reason 'Engine schema bump to v{SCHEMA_VERSION}'"
        )
    expected_fsid = compute_fixture_set_id(corpus_name)
    baseline_fsid = baseline.get("fixture_set_id")
    if baseline_fsid != expected_fsid:
        raise FootprintGateSchemaMismatch(
            f"{corpus_name}: fixture_set_id mismatch — baseline={baseline_fsid!r} "
            f"current={expected_fsid!r}. Fixture pool changed since baseline; "
            f"regenerate via: PYTHONHASHSEED=0 footprint baseline regenerate "
            f"--path {corpus_name} --reason 'Refreshed fixture inputs'"
        )

    if report.total_links == 0:
        raise FootprintGateZeroLinks(
            f"{corpus_name}: footprint.analyze_corpus returned total_links=0. "
            "Engine regex breakage suspected — investigate footprint.py "
            "extract_link_signatures before touching baselines."
        )

    baseline_conc = baseline["concentration_pct"]
    for dim in _DIMENSIONS:
        observed = report.concentration_pct(dim)
        recorded = float(baseline_conc.get(dim, 0.0))
        delta = observed - recorded
        drift_pp, alarm_pct = _thresholds(corpus_name, dim)

        if abs(delta) > drift_pp:
            fr.add(
                _DimFailure(
                    renderer_path=corpus_name, dimension=dim,
                    baseline_value=recorded, observed_value=observed,
                    delta=delta, failure_mode="drift",
                )
            )
            continue  # drift trumps alarm-crossing for the same dimension

        # Crossing-from-below: only fires when baseline was below the alarm
        # and current value crosses to/above it.
        if recorded < alarm_pct <= observed:
            fr.add(
                _DimFailure(
                    renderer_path=corpus_name, dimension=dim,
                    baseline_value=recorded, observed_value=observed,
                    delta=delta, failure_mode="alarm_crossed",
                )
            )

    return fr


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("corpus_name", CORPUS_NAMES)
def test_footprint_gate(corpus_name: str) -> None:
    """One parameterized test per corpus."""
    baseline = _load_baseline(corpus_name)
    corpus = make_corpus(corpus_name)
    report = analyze_corpus(corpus)
    failure_report = _check_corpus(corpus_name, report, baseline)

    if not failure_report.failures:
        return

    override_active, contents = _override_active()
    if override_active:
        age = _override_age_days()
        age_str = f"{age} days" if age is not None else "unknown age"
        msg = (
            f"\n⚠ FOOTPRINT GATE OVERRIDDEN — active for {age_str}\n"
            f"File contents:\n{contents}\n"
            f"Suppressed failures:\n{failure_report.render()}\n"
        )
        import warnings
        warnings.warn(msg, stacklevel=1)
        print(msg, file=sys.stderr)
        return

    raise (
        FootprintGateAlarmCrossed
        if any(f.failure_mode == "alarm_crossed" for f in failure_report.failures)
        else FootprintGateDrift
    )(failure_report.render())


# ---------------------------------------------------------------------------
# Engine attack fixtures (Plan R12)
# ---------------------------------------------------------------------------


def test_engine_handles_multi_line_anchor_fixture():
    """R12: multi-line <a> tags + single-line <a> in one fixture.

    Today's engine handles BOTH (re.DOTALL on `_A_TAG_RE`). Asserting exact
    count makes any future regression to single-line-only parsing observable.
    """
    fixture = (_REPO_ROOT / "tests" / "fixtures" / "footprint_attack" / "multi_line_anchor.html").read_text(encoding="utf-8")
    report = analyze_corpus([fixture])
    assert report.total_links == 2, (
        f"engine detected {report.total_links} links in attack fixture (expected 2). "
        f"Multi-line `<a>` handling may have regressed."
    )


def test_engine_currently_collapses_missing_vs_empty_rel():
    """R12: missing rel and rel="" CURRENTLY collapse into the same bucket."""
    fixture = (_REPO_ROOT / "tests" / "fixtures" / "footprint_attack" / "missing_vs_empty_rel.html").read_text(encoding="utf-8")
    report = analyze_corpus([fixture])
    assert report.total_links == 2
    assert report.rel_value_counts == {"": 2}, (
        f"engine no longer collapses missing/empty rel — rel_value_counts="
        f"{dict(report.rel_value_counts)}. Update this test."
    )


# ---------------------------------------------------------------------------
# Tests OF the gate
# ---------------------------------------------------------------------------


def test_check_corpus_passes_on_aligned_baseline():
    report = analyze_corpus(make_corpus("work_themed"))
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": compute_fixture_set_id("work_themed"),
        "concentration_pct": {dim: report.concentration_pct(dim) for dim in _DIMENSIONS},
    }
    fr = _check_corpus("work_themed", report, baseline)
    assert fr.failures == []


def test_check_corpus_raises_schema_mismatch_on_bumped_version():
    report = analyze_corpus(make_corpus("work_themed"))
    baseline = {
        "schema_version": SCHEMA_VERSION + 1,
        "fixture_set_id": compute_fixture_set_id("work_themed"),
        "concentration_pct": {dim: 50.0 for dim in _DIMENSIONS},
    }
    with pytest.raises(FootprintGateSchemaMismatch) as exc_info:
        _check_corpus("work_themed", report, baseline)
    msg = str(exc_info.value)
    assert "schema_version mismatch" in msg
    assert "footprint baseline regenerate" in msg


def test_check_corpus_raises_schema_mismatch_on_fixture_drift():
    report = analyze_corpus(make_corpus("work_themed"))
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": "deadbeefdeadbeef",
        "concentration_pct": {dim: 50.0 for dim in _DIMENSIONS},
    }
    with pytest.raises(FootprintGateSchemaMismatch, match="fixture_set_id mismatch"):
        _check_corpus("work_themed", report, baseline)


def test_check_corpus_records_drift_when_dimension_moves_too_far():
    report = analyze_corpus(make_corpus("zh_short"))
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": compute_fixture_set_id("zh_short"),
        "concentration_pct": {
            "attr_order": report.concentration_pct("attr_order"),
            "rel_value": report.concentration_pct("rel_value"),
            "target_value": report.concentration_pct("target_value"),
            "preceding_char": report.concentration_pct("preceding_char") - 50.0,
        },
    }
    fr = _check_corpus("zh_short", report, baseline)
    assert len(fr.failures) == 1
    assert fr.failures[0].dimension == "preceding_char"
    assert fr.failures[0].failure_mode == "drift"


def test_check_corpus_records_alarm_crossing_when_dim_crosses_from_below():
    report = FootprintReport(total_links=100, total_payloads=10)
    report.attr_order_counts[("href",)] = 96
    report.attr_order_counts[("other",)] = 4
    report.rel_value_counts["x"] = 100
    report.target_value_counts["_blank"] = 100
    report.preceding_char_counts[" "] = 100
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": compute_fixture_set_id("zh_short"),
        "concentration_pct": {
            "attr_order": 93.0,
            "rel_value": 100.0,
            "target_value": 100.0,
            "preceding_char": 100.0,
        },
    }
    fr = _check_corpus("zh_short", report, baseline)
    assert any(f.failure_mode == "alarm_crossed" and f.dimension == "attr_order" for f in fr.failures)


def test_check_corpus_already_high_dimension_drift_only_no_alarm():
    """Edge case (R4): baseline ≥ alarm + drift → drift only, not alarm_crossed."""
    report = FootprintReport(total_links=100, total_payloads=10)
    report.attr_order_counts[("href",)] = 100
    report.rel_value_counts["x"] = 100
    report.target_value_counts["_blank"] = 100
    report.preceding_char_counts[" "] = 100
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": compute_fixture_set_id("zh_short"),
        "concentration_pct": {
            "attr_order": 90.0,
            "rel_value": 100.0,
            "target_value": 100.0,
            "preceding_char": 100.0,
        },
    }
    fr = _check_corpus("zh_short", report, baseline)
    drift_fails = [f for f in fr.failures if f.failure_mode == "drift"]
    alarm_fails = [f for f in fr.failures if f.failure_mode == "alarm_crossed"]
    assert len(drift_fails) == 1
    assert drift_fails[0].dimension == "attr_order"
    assert not alarm_fails


def test_check_corpus_already_high_baseline_within_drift_passes():
    """Edge case (R4): baseline 100% + observed 100% → no failure."""
    report = FootprintReport(total_links=100, total_payloads=10)
    report.attr_order_counts[("href",)] = 100
    report.rel_value_counts["x"] = 100
    report.target_value_counts["_blank"] = 100
    report.preceding_char_counts[" "] = 100
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": compute_fixture_set_id("zh_short"),
        "concentration_pct": {dim: 100.0 for dim in _DIMENSIONS},
    }
    fr = _check_corpus("zh_short", report, baseline)
    assert fr.failures == []


def test_zero_links_raises_distinct_error():
    """Edge case (R12): total_links=0 → FootprintGateZeroLinks."""
    report = FootprintReport(total_links=0, total_payloads=5, payloads_without_links=5)
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": compute_fixture_set_id("zh_short"),
        "concentration_pct": {dim: 50.0 for dim in _DIMENSIONS},
    }
    with pytest.raises(FootprintGateZeroLinks, match="total_links=0"):
        _check_corpus("zh_short", report, baseline)


def test_override_active_returns_false_when_absent(tmp_path, monkeypatch):
    """No OVERRIDE.md → not active."""
    # tests/ is not a package (no __init__.py) so we use sys.modules[__name__]
    monkeypatch.setattr(sys.modules[__name__], "_OVERRIDE_PATH", tmp_path / "missing.md")
    active, contents = _override_active()
    assert not active
    assert contents is None


def test_override_active_returns_true_with_reason(tmp_path, monkeypatch):
    """OVERRIDE.md with reason → active."""
    p = tmp_path / "override.md"
    p.write_text("reason: testing the override flow\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_OVERRIDE_PATH", p)
    active, contents = _override_active()
    assert active
    assert "reason:" in contents


def test_override_malformed_without_reason_raises(tmp_path, monkeypatch):
    """OVERRIDE.md without reason → FootprintGateOverrideMalformed."""
    p = tmp_path / "override.md"
    p.write_text("just a body, no reason key\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_OVERRIDE_PATH", p)
    with pytest.raises(FootprintGateOverrideMalformed, match="lacks a `reason:` line"):
        _override_active()


def test_failure_report_renders_sorted_by_largest_delta():
    fr = FailureReport()
    fr.add(_DimFailure("zh_short", "preceding_char", 50.0, 53.0, 3.0, "drift"))
    fr.add(_DimFailure("zh_short", "attr_order", 90.0, 96.0, 6.0, "alarm_crossed"))
    fr.add(_DimFailure("zh_short", "rel_value", 80.0, 88.0, 8.0, "drift"))
    rendered = fr.render()
    rel_pos = rendered.find("rel_value")
    attr_pos = rendered.find("attr_order")
    prec_pos = rendered.find("preceding_char")
    assert 0 < rel_pos < attr_pos < prec_pos


def test_corpus_generators_do_not_touch_network_or_disk(monkeypatch):
    """Integration: pure-function path. work_scraper.fetch_work_metadata and
    anchor_profile IO must NOT be invoked by make_corpus."""
    import backlink_publisher.content.scraper as scraper

    def _exploding(*args, **kwargs):
        raise RuntimeError("work_scraper.fetch_work_metadata called — violates pure-function contract")

    if hasattr(scraper, "fetch_work_metadata"):
        monkeypatch.setattr(scraper, "fetch_work_metadata", _exploding)

    try:
        from backlink_publisher.anchor import profile as anchor_profile
        for name in ("load_profile", "record_article"):
            if hasattr(anchor_profile, name):
                monkeypatch.setattr(anchor_profile, name, _exploding)
    except ImportError:
        pass

    for name in CORPUS_NAMES:
        corpus = make_corpus(name)
        assert len(corpus) > 0
