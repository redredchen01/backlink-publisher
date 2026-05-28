"""Saga contract tests — machine-checkable assertions for documented pipeline contracts.

Each test corresponds to a contract claim in:
  docs/brainstorms/2026-05-28-publish-saga-contracts-requirements.md

Naming convention: test_contract_<step>_<property>
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.linkcheck.verify import VerificationResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _run_publish(
    input_data: str,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    """Run publish-backlinks with given stdin. Returns (stdout, stderr, exit_code)."""
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    old_env = dict(os.environ)
    try:
        if env:
            os.environ.update(env)
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
        os.environ.clear()
        os.environ.update(old_env)


def _parse_recon(stderr: str, event: str) -> dict:
    """Extract a RECON line by event name from stderr; raise AssertionError if absent."""
    for line in stderr.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("level") == "RECON" and obj.get("msg") == event:
            return obj
    raise AssertionError(
        f"RECON event '{event}' not found in stderr.\nstderr was:\n{stderr}"
    )


def _make_payload(platform: str = "medium", row_id: str = "abc123") -> dict:
    return {
        "id": row_id,
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://51acgs.com/article",
        "main_domain": "https://51acgs.com",
        "url_mode": "A",
        "title": "Contract Test Article",
        "slug": "contract-test",
        "excerpt": "Test excerpt.",
        "tags": ["tag1"],
        "content_markdown": "Article body about https://51acgs.com.",
        "links": [
            {"url": "https://51acgs.com", "anchor": "Home", "kind": "main_domain", "required": True},
            {"url": "https://51acgs.com/article", "anchor": "Art", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Contract Test | SEO",
            "description": "SEO",
            "canonical_url": "https://51acgs.com/article",
        },
    }


def _drafted_result(platform: str = "medium") -> AdapterResult:
    return AdapterResult(
        status="drafted",
        adapter=f"{platform}-api",
        platform=platform,
        draft_url=f"https://{platform}.example.com/p/abc",
    )


@pytest.fixture(autouse=True)
def _mock_verify_pass(mocker):
    """Default: verification passes so tests are fast and network-free."""
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


# ---------------------------------------------------------------------------
# Contract: RECON field completeness
# ---------------------------------------------------------------------------

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_recon_dedup_reconciliation_has_required_fields(mock_pub, mock_verify):
    """dedup_reconciliation RECON always contains the four documented keys."""
    mock_pub.return_value = _drafted_result()
    payload = _make_payload()
    _, stderr, code = _run_publish(json.dumps(payload))

    assert code == 0
    recon = _parse_recon(stderr, "dedup_reconciliation")
    assert "skipped_already_published" in recon
    assert "held_uncertain" in recon
    assert "dispatched" in recon
    assert "skipped_canary" in recon  # G4: saga contract field


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_recon_publish_reconciliation_has_required_fields(mock_pub, mock_verify):
    """publish_reconciliation RECON always contains the documented keys."""
    mock_pub.return_value = _drafted_result()
    payload = _make_payload()
    _, stderr, code = _run_publish(json.dumps(payload))

    assert code == 0
    recon = _parse_recon(stderr, "publish_reconciliation")
    assert "input_payloads" in recon
    assert "output_rows" in recon
    assert "delta" in recon
    assert "dropped" in recon
    assert "failed" in recon["dropped"]
    assert "unverified" in recon["dropped"]  # G7: saga contract field


# ---------------------------------------------------------------------------
# Contract: G4 — skipped_canary counter
# ---------------------------------------------------------------------------

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g4_skipped_canary_zero_when_no_quarantine(mock_pub, mock_verify):
    """skipped_canary is 0 when no rows are quarantined (field always present)."""
    mock_pub.return_value = _drafted_result()
    _, stderr, code = _run_publish(json.dumps(_make_payload()))

    assert code == 0
    recon = _parse_recon(stderr, "dedup_reconciliation")
    assert recon["skipped_canary"] == 0


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g4_skipped_canary_nonzero_when_quarantined(
    mock_pub, mock_verify, tmp_path, monkeypatch
):
    """skipped_canary equals the number of hard-skipped quarantined rows."""
    from backlink_publisher.canary import store as canary_store

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        "[canary.blogger]\n"
        'post_url = "https://canary.51acgs.com/p.html"\n'
        'expected_target = "https://51acgs.com/"\n'
        'marker = "cnry-zzz"\n'
        "hard_skip = true\n",
        encoding="utf-8",
    )
    canary_store.canary_health_store.reset()
    canary_store.record_verdict("blogger", canary_store.STATUS_DRIFT_CONFIRMED)
    canary_store.record_verdict("blogger", canary_store.STATUS_DRIFT_CONFIRMED)
    assert canary_store.is_quarantined("blogger")

    payload = _make_payload(platform="blogger")
    _, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "blogger", "--mode", "draft"]
    )

    assert code in (0, 5)
    recon = _parse_recon(stderr, "dedup_reconciliation")
    assert recon["skipped_canary"] == 1, f"Expected 1 canary skip, got {recon}"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g4_row_accounting_completeness(mock_pub, mock_verify):
    """skipped_canary + skipped_already_published + held_uncertain + dispatched = total rows."""
    mock_pub.return_value = _drafted_result()
    rows = [_make_payload(row_id=f"r{i}") for i in range(3)]
    _, stderr, code = _run_publish("\n".join(json.dumps(r) for r in rows))

    assert code == 0
    recon = _parse_recon(stderr, "dedup_reconciliation")
    total_accounted = (
        recon["skipped_canary"]
        + recon["skipped_already_published"]
        + recon["held_uncertain"]
        + recon["dispatched"]
    )
    assert total_accounted == 3, (
        f"Row accounting gap: expected 3 rows accounted for, got {total_accounted}. "
        f"dedup_reconciliation: {recon}"
    )


# ---------------------------------------------------------------------------
# Contract: G3 — checkpoint_disabled flag
# ---------------------------------------------------------------------------

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g3_checkpoint_disabled_when_creation_fails(mock_pub, mock_verify, tmp_path):
    """checkpoint_disabled: true in publish_reconciliation RECON when checkpoint creation raises."""
    mock_pub.return_value = _drafted_result("blogger")
    with patch(
        "backlink_publisher.cli.publish_backlinks.checkpoint.create_checkpoint",
        side_effect=OSError("disk full"),
    ):
        payload = _make_payload(platform="blogger")
        _, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    assert code == 0, f"Expected degraded-gracefully (exit 0), got {code}. stderr: {stderr}"
    recon = _parse_recon(stderr, "publish_reconciliation")
    assert recon.get("checkpoint_disabled") is True, (
        f"Expected checkpoint_disabled=true in publish_reconciliation. recon: {recon}"
    )


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g3_checkpoint_disabled_absent_on_success(mock_pub, mock_verify, tmp_path):
    """checkpoint_disabled is ABSENT from RECON when checkpoint creation succeeds."""
    mock_pub.return_value = _drafted_result("blogger")
    payload = _make_payload(platform="blogger")
    _, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    assert code == 0
    recon = _parse_recon(stderr, "publish_reconciliation")
    assert "checkpoint_disabled" not in recon, (
        f"checkpoint_disabled should be absent on success, got: {recon}"
    )


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g3_dry_run_does_not_set_checkpoint_disabled(mock_pub, mock_verify):
    """dry-run never creates checkpoints; checkpoint_disabled must be absent."""
    mock_pub.return_value = AdapterResult(
        status="draft", adapter="medium-api", platform="medium",
        _dry_run=True, _command="dry-run"
    )
    payload = _make_payload()
    _, stderr, code = _run_publish(json.dumps(payload), ["--dry-run"])

    # dry-run exits 0 or no RECON; either way checkpoint_disabled must not be True
    for line in stderr.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "publish_reconciliation":
            assert obj.get("checkpoint_disabled") is not True, (
                f"dry-run must not set checkpoint_disabled. recon: {obj}"
            )


# ---------------------------------------------------------------------------
# Contract: G7 — unverified rows in dropped.unverified
# ---------------------------------------------------------------------------

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g7_unverified_count_zero_on_success(mock_pub, mock_verify):
    """dropped.unverified is 0 when verification passes on all rows."""
    mock_pub.return_value = _drafted_result("blogger")
    payload = _make_payload(platform="blogger")
    _, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    # exits 5 if no published_url (draft) — that's ok; check the RECON field
    recon = _parse_recon(stderr, "publish_reconciliation")
    assert recon["dropped"]["unverified"] == 0


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_g7_unverified_count_nonzero_on_verify_failure(
    mock_pub, mock_verify, mocker
):
    """dropped.unverified reflects the number of rows where verify_ok=False."""
    # Override the autouse fixture to make verification FAIL
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=False, reason="link not found"),
    )
    mock_pub.return_value = AdapterResult(
        status="published",
        adapter="blogger-api",
        platform="blogger",
        published_url="https://blogger.example.com/p/abc",
    )
    payload = _make_payload(platform="blogger")
    _, stderr, code = _run_publish(json.dumps(payload), ["--mode", "publish"])

    recon = _parse_recon(stderr, "publish_reconciliation")
    assert recon["dropped"]["unverified"] == 1, (
        f"Expected dropped.unverified=1 on verify failure. recon: {recon}"
    )


# ---------------------------------------------------------------------------
# Contract: Exit codes
# ---------------------------------------------------------------------------

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_exit_code_0_on_success(mock_pub, mock_verify):
    """Successful publish exits 0 (saga contract step 3h success)."""
    mock_pub.return_value = _drafted_result()
    _, _, code = _run_publish(json.dumps(_make_payload()))
    assert code == 0


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_exit_code_4_all_rows_failed(mock_pub, mock_verify):
    """All rows failed → exit 4 (saga contract epilogue dispatch)."""
    mock_pub.side_effect = ExternalServiceError("timeout")
    payload = _make_payload()
    _, _, code = _run_publish(json.dumps(payload))
    assert code == 4, f"Expected exit 4 on all-failed, got {code}"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_exit_code_2_on_bad_args(mock_pub, mock_verify):
    """Invalid CLI args → exit 2 (saga contract step 3a failure)."""
    _, _, code = _run_publish("", ["--invalid-flag-xyz"])
    assert code == 2


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_contract_exit_code_3_on_auth_expired(mock_pub, mock_verify):
    """AuthExpiredError during dispatch → exit 3 (saga contract step 3c/3h)."""
    from backlink_publisher._util.errors import AuthExpiredError
    mock_pub.side_effect = AuthExpiredError(channel="medium")
    payload = _make_payload()
    _, _, code = _run_publish(json.dumps(payload))
    assert code == 3, f"Expected exit 3 on auth expired, got {code}"
