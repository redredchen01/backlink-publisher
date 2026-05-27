"""Tests for the opt-in cell admission gate in plan-backlinks (R7-minimal).

Blast-radius Phase 1 Unit 3.

The gate is located in ``cli/plan_backlinks/core.py`` after
``validate_input_payload`` and before ``_dispatch_row``. It is opt-in:
only money sites with a ``[cells.*]`` config entry are gated. A row
whose ``(main_domain, platform)`` is not in its site's cell is **dropped**
with an always-on ``recon`` warning; exit stays 0.  Sites without a cell
entry pass through unchanged.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from backlink_publisher.cli.plan_backlinks import main


# ---------------------------------------------------------------------------
# Test helpers (mirror test_plan_backlinks.py conventions)
# ---------------------------------------------------------------------------


def _run_plan(
    input_data: str,
    argv: list[str] | None = None,
) -> tuple[str, str, int]:
    """Run plan-backlinks with given stdin.  Returns (stdout, stderr, exit_code)."""
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


def _seed(
    main_domain: str = "https://example.com",
    platform: str = "medium",
    target_url: str = "https://example.com/article",
) -> dict:
    return {
        "target_url": target_url,
        "main_domain": main_domain,
        "language": "en",
        "platform": platform,
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Test Topic",
    }


def _jsonl(*rows) -> str:
    return "\n".join(json.dumps(r) for r in rows)


def _recon_lines(stderr: str) -> list[dict]:
    """Extract RECON-level JSON log lines from stderr."""
    result = []
    for line in stderr.splitlines():
        try:
            obj = json.loads(line)
            if obj.get("level") == "RECON":
                result.append(obj)
        except (json.JSONDecodeError, AttributeError):
            pass
    return result


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """Isolated config dir for tests that need [cells.*] config."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Helper: _check_cell_gate (unit test against the helper directly)
# ---------------------------------------------------------------------------


def test_cell_gate_helper_drops_out_of_cell_platform():
    """Unit: _check_cell_gate returns True (drop) when platform not in cell."""
    from backlink_publisher.cli.plan_backlinks.core import _cell_gate_drop
    cells = {"https://example.com": ["blogger", "medium"]}
    # platform NOT in cell
    assert _cell_gate_drop("https://example.com", "velog", cells) is True


def test_cell_gate_helper_allows_in_cell_platform():
    """Unit: _check_cell_gate returns False (keep) when platform in cell."""
    from backlink_publisher.cli.plan_backlinks.core import _cell_gate_drop
    cells = {"https://example.com": ["blogger", "medium"]}
    assert _cell_gate_drop("https://example.com", "medium", cells) is False


def test_cell_gate_helper_unenrolled_site_passes_through():
    """Unit: a site with no cell entry is unrestricted."""
    from backlink_publisher.cli.plan_backlinks.core import _cell_gate_drop
    cells = {"https://other-site.com": ["blogger"]}
    # https://example.com not in cells → unrestricted
    assert _cell_gate_drop("https://example.com", "velog", cells) is False


def test_cell_gate_helper_empty_cells_passes_all():
    """Unit: empty cell_assignments → no restriction."""
    from backlink_publisher.cli.plan_backlinks.core import _cell_gate_drop
    assert _cell_gate_drop("https://example.com", "velog", {}) is False


# ---------------------------------------------------------------------------
# Integration: gate in the plan-backlinks pipeline
# ---------------------------------------------------------------------------


def test_no_cells_config_all_rows_pass(cfg_dir: Path):
    """Happy path: no [cells.*] config → all rows pass through (opt-in semantics)."""
    (cfg_dir / "config.toml").write_text("[blogger]\n[medium]\n", encoding="utf-8")

    seeds = [
        _seed(main_domain="https://example.com", platform="medium"),
        _seed(main_domain="https://example.com", platform="blogger",
              target_url="https://example.com/article2"),
    ]
    stdout, stderr, code = _run_plan(_jsonl(*seeds))

    assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"
    payloads = [json.loads(line) for line in stdout.strip().splitlines() if line.strip()]
    assert len(payloads) == 2, (
        f"Expected 2 payloads (unrestricted), got {len(payloads)}.\nstderr: {stderr}"
    )


def test_in_cell_platform_passes(cfg_dir: Path):
    """Happy path: platform IS in the cell → row produces a payload."""
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://example.com"]\n'
        'channels = ["medium", "blogger"]\n',
        encoding="utf-8",
    )

    stdout, stderr, code = _run_plan(
        _jsonl(_seed(main_domain="https://example.com", platform="medium"))
    )

    assert code == 0, f"Expected exit 0. stderr: {stderr}"
    payloads = [json.loads(line) for line in stdout.strip().splitlines() if line.strip()]
    assert len(payloads) == 1, (
        f"Expected 1 payload (platform in cell), got {len(payloads)}.\nstderr: {stderr}"
    )


def test_out_of_cell_platform_drops_row(cfg_dir: Path):
    """Core: platform NOT in the cell → row dropped, stdout empty, exit 0."""
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://example.com"]\n'
        'channels = ["blogger"]\n',  # medium NOT in cell
        encoding="utf-8",
    )

    stdout, stderr, code = _run_plan(
        _jsonl(_seed(main_domain="https://example.com", platform="medium"))
    )

    assert code == 0, f"Expected exit 0 (gate drop is advisory), got {code}"
    # stdout must be empty (row dropped)
    payloads = [l for l in stdout.strip().splitlines() if l.strip()]
    assert payloads == [], (
        f"Expected 0 payloads (platform not in cell), got {len(payloads)}.\n"
        f"stdout: {stdout}"
    )


def test_out_of_cell_drop_emits_recon_warning(cfg_dir: Path):
    """A dropped row emits an always-on recon warning naming the platform."""
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://example.com"]\n'
        'channels = ["blogger"]\n',
        encoding="utf-8",
    )

    _, stderr, _ = _run_plan(
        _jsonl(_seed(main_domain="https://example.com", platform="medium"))
    )

    recon = _recon_lines(stderr)
    drop_events = [r for r in recon if r.get("msg") == "cell_gate_drop"]
    assert drop_events, (
        f"Expected at least one cell_gate_drop RECON event.\nstderr: {stderr}"
    )
    # The warning names the dropped platform and domain
    evt = drop_events[0]
    assert evt.get("platform") == "medium" or "medium" in str(evt)


def test_unenrolled_site_passes_through_enrolled_site_gated(cfg_dir: Path):
    """Edge: two sites in one run — enrolled site is gated, other is unrestricted."""
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://gated.example.com"]\n'
        'channels = ["blogger"]\n',  # "medium" not in gated.example.com cell
        encoding="utf-8",
    )

    seeds = [
        _seed(main_domain="https://gated.example.com", platform="medium",
              target_url="https://gated.example.com/article"),     # should be dropped
        _seed(main_domain="https://free.example.com", platform="medium",
              target_url="https://free.example.com/article"),  # unrestricted
    ]
    stdout, stderr, code = _run_plan(_jsonl(*seeds))

    assert code == 0
    payloads = [json.loads(l) for l in stdout.strip().splitlines() if l.strip()]
    # Only the unrestricted site's row should survive
    assert len(payloads) == 1, (
        f"Expected 1 payload (unenrolled site passes), got {len(payloads)}.\n"
        f"stderr: {stderr}"
    )
    # payload normalises main_domain (may add trailing slash)
    assert payloads[0]["main_domain"].rstrip("/") == "https://free.example.com"


def test_enrolled_vs_unrestricted_recon_summary_emitted(cfg_dir: Path):
    """The gate emits a recon summary of enrolled vs unrestricted sites."""
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://example.com"]\n'
        'channels = ["medium"]\n',
        encoding="utf-8",
    )

    _, stderr, _ = _run_plan(
        _jsonl(_seed(main_domain="https://example.com", platform="medium"))
    )

    recon = _recon_lines(stderr)
    # Should have a cell_gate_summary event
    summary_events = [r for r in recon if r.get("msg") == "cell_gate_summary"]
    assert summary_events, (
        f"Expected cell_gate_summary RECON event.\nstderr: {stderr}"
    )


def test_end_of_run_drop_tally_emitted(cfg_dir: Path):
    """The gate emits an end-of-run tally when rows are dropped."""
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://example.com"]\n'
        'channels = ["blogger"]\n',  # medium not in cell
        encoding="utf-8",
    )

    _, stderr, _ = _run_plan(
        _jsonl(_seed(main_domain="https://example.com", platform="medium"))
    )

    recon = _recon_lines(stderr)
    # plan_reconciliation should reflect cell gate drops
    reconcile_events = [r for r in recon if r.get("msg") == "plan_reconciliation"]
    assert reconcile_events, f"Expected plan_reconciliation event.\nstderr: {stderr}"
    rec = reconcile_events[0]
    # cell_gate drops should appear in the dropped dict
    dropped = rec.get("dropped", {})
    assert dropped.get("cell_gate", 0) >= 1, (
        f"Expected cell_gate drop ≥ 1 in plan_reconciliation, got: {dropped}"
    )


def test_multiple_rows_partial_drop(cfg_dir: Path):
    """Mixed run: some rows in-cell, some out-of-cell → partial output."""
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://example.com"]\n'
        'channels = ["medium"]\n',  # only medium is allowed
        encoding="utf-8",
    )

    seeds = [
        _seed(main_domain="https://example.com", platform="medium",
              target_url="https://example.com/a1"),   # in cell → keep
        _seed(main_domain="https://example.com", platform="blogger",
              target_url="https://example.com/a2"),   # out of cell → drop
        _seed(main_domain="https://example.com", platform="medium",
              target_url="https://example.com/a3"),   # in cell → keep
    ]
    stdout, stderr, code = _run_plan(_jsonl(*seeds))

    assert code == 0
    payloads = [json.loads(l) for l in stdout.strip().splitlines() if l.strip()]
    # 2 keep + 1 drop = 2 payloads
    assert len(payloads) == 2, (
        f"Expected 2 payloads, got {len(payloads)}.\nstderr: {stderr}"
    )
    platforms = {p["platform"] for p in payloads}
    assert platforms == {"medium"}, f"Expected only medium payloads, got {platforms}"


def test_trailing_slash_in_row_does_not_bypass_gate(cfg_dir: Path):
    """P0 regression: main_domain with trailing slash must still be gated.

    Config keys are stripped of trailing slashes at parse time; rows must be
    normalised the same way before lookup, or an enrolled site with a slash
    in its seed row silently bypasses the cell gate.
    """
    (cfg_dir / "config.toml").write_text(
        '[blogger]\n[medium]\n\n'
        '[cells."https://example.com"]\n'
        'channels = ["blogger"]\n',  # medium NOT in cell
        encoding="utf-8",
    )

    # Seed row has trailing slash in main_domain — gate must still fire
    stdout, stderr, code = _run_plan(
        _jsonl(_seed(main_domain="https://example.com/", platform="medium"))
    )

    assert code == 0, f"Expected exit 0, got {code}"
    payloads = [l for l in stdout.strip().splitlines() if l.strip()]
    assert payloads == [], (
        "Row with trailing-slash main_domain must be gated — "
        f"got {len(payloads)} payload(s) instead of 0.\nstderr: {stderr}"
    )
