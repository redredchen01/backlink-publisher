"""Tests for verifier integration in publish-backlinks (Unit 6).

Covers: per-row verifier invocation, JSONL additive fields populated from
VerificationOutcome, R16 per-row stderr lines, R17 run-end summary,
exit-code max() rule with DependencyError log-and-continue, and the
verifier_internal_error saturation guard.

Patches `verify_published` at the dispatcher import boundary so the test
matrix exercises dispatcher policy rather than verifier internals.
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.errors import DependencyError, ExternalServiceError
from backlink_publisher.verifier import VerificationOutcome


def _run_publish(
    input_data: str,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    """Clone of the helper in test_publish_backlinks.py — keeps these tests
    independent of that test module's internals.

    Throttle env vars default to 0 so multi-row Medium tests don't sleep
    60-300s between rows.
    """
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    old_env = dict(os.environ)
    try:
        os.environ.setdefault("MEDIUM_THROTTLE_MIN", "0")
        os.environ.setdefault("MEDIUM_THROTTLE_MAX", "0")
        if env:
            os.environ.update(env)
        sys.stdin = StringIO(input_data)
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        os.environ.clear()
        os.environ.update(old_env)


def _payload(id_: str = "row-1", platform: str = "medium") -> dict:
    return {
        "id": id_,
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1"],
        "content_markdown": "Body about https://example.com.",
        "links": [
            {"url": "https://example.com", "anchor": "Example",
             "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article",
             "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki",
             "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN",
             "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO",
             "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub",
             "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article | SEO",
            "description": "SEO description",
            "canonical_url": "https://example.com/article",
        },
    }


def _published(platform: str = "medium", url: str | None = None) -> AdapterResult:
    if url is None:
        url = "https://medium.com/@u/test-article" if platform == "medium" \
            else "https://myblog.blogspot.com/2026/05/test.html"
    adapter = "medium-api" if platform == "medium" else "blogger-api"
    return AdapterResult(
        status="published",
        adapter=adapter,
        platform=platform,
        published_url=url,
    )


def _outcome_true():
    return VerificationOutcome(
        verified=True, verified_at="2026-05-12T00:00:00+00:00",
        verification_error=None,
    )


def _outcome_false(error: str = "http_404"):
    return VerificationOutcome(
        verified=False, verified_at="2026-05-12T00:00:00+00:00",
        verification_error=error,
    )


def _outcome_null(error: str = "http_503"):
    return VerificationOutcome(
        verified=None, verified_at=None, verification_error=error,
    )


def _outcome_internal(error: str = "verifier_internal_error: RuntimeError: boom"):
    return VerificationOutcome(
        verified=None, verified_at=None, verification_error=error,
    )


# ---------- happy path ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_two_verified_rows_clean_run(mock_verify, mock_pub, mock_setup):
    """2 published rows, both verified=true → exit 0, summary shows 2 verified."""
    mock_pub.side_effect = [_published("medium"), _published("medium")]
    mock_verify.side_effect = [_outcome_true(), _outcome_true()]

    payloads = [_payload(f"row-{i}") for i in range(2)]
    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "publish"]
    )

    assert code == 0
    out_lines = [json.loads(l) for l in stdout.strip().split("\n") if l]
    assert len(out_lines) == 2
    assert all(r["verified"] is True for r in out_lines)
    assert all(r["verified_at"] is not None for r in out_lines)
    assert all(r["verification_error"] is None for r in out_lines)

    # R17 summary on stderr
    assert "verification: 2 verified" in stderr
    assert "0 unverified" in stderr
    assert "0 null" in stderr
    assert "internal-error" not in stderr  # internal-error suffix omitted when 0


# ---------- founding-incident defense ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_fake_url_404_marks_false_and_exits_4(mock_verify, mock_pub, mock_setup):
    """The original incident class: adapter returns a URL that doesn't exist."""
    mock_pub.return_value = _published("medium", url="https://medium.com/p/fake123")
    mock_verify.return_value = _outcome_false("http_404")

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )

    assert code == 4
    row = json.loads(stdout.strip())
    assert row["verified"] is False
    assert row["verification_error"] == "http_404"
    assert row["published_url"] == "https://medium.com/p/fake123"  # audit trail kept


# ---------- host-spoofing defense ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_host_not_allowed_marks_false(mock_verify, mock_pub, mock_setup):
    mock_pub.return_value = _published("medium", url="https://attacker.example/x")
    mock_verify.return_value = _outcome_false("host_not_allowed: attacker.example")

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )
    assert code == 4
    row = json.loads(stdout.strip())
    assert row["verified"] is False
    assert "host_not_allowed" in row["verification_error"]


# ---------- transient null does NOT fail exit code ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_503_transient_null_does_not_fail_exit(mock_verify, mock_pub, mock_setup):
    """Medium 5xx → verified=null → exit 0 (transient, not a hard fail)."""
    mock_pub.return_value = _published("medium")
    mock_verify.return_value = _outcome_null("http_503")

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )
    assert code == 0
    row = json.loads(stdout.strip())
    assert row["verified"] is None
    assert row["verification_error"] == "http_503"
    assert "1 null" in stderr


# ---------- verifier internal error rolls up to exit 4 ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_verifier_internal_error_rolls_up_to_exit_4(mock_verify, mock_pub, mock_setup):
    """Saturation guard: verifier bug → verified=null on JSONL but exit 4."""
    mock_pub.return_value = _published("medium")
    mock_verify.return_value = _outcome_internal()

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )
    assert code == 4
    row = json.loads(stdout.strip())
    # JSONL keeps verified=null (the schema's "unknown" sentinel).
    assert row["verified"] is None
    assert row["verification_error"].startswith("verifier_internal_error:")
    # Summary line distinguishes internal-error from regular null.
    assert "1 internal-error" in stderr


# ---------- dry-run skips verification ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_dry_run_skips_verifier(mock_verify, mock_pub, mock_setup):
    """--dry-run never invokes the verifier; output carries verification_error='dry_run'."""
    dry_result = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        published_url="", draft_url="", _dry_run=True,
        _command="dry-run command",
    )
    mock_pub.return_value = dry_result

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--dry-run", "--mode", "draft"]
    )
    assert code == 0
    mock_verify.assert_not_called()
    row = json.loads(stdout.strip())
    assert row["verified"] is None
    assert row["verification_error"] == "dry_run"


# ---------- exit-code max(): DependencyError + verified=false ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_dependency_error_plus_verified_false_yields_exit_4(
    mock_verify, mock_pub, mock_setup,
):
    """Row 1: DependencyError (would be exit 3); row 2: verified=false (exit 4).

    Final code = max(3, 4) = 4. The DependencyError log-and-continue
    refactor is what makes this max() reachable — previously DependencyError
    would have aborted the loop with exit 3, masking the verification
    failure on row 2."""
    mock_pub.side_effect = [
        DependencyError("oauth missing"),
        _published("medium"),
    ]
    mock_verify.return_value = _outcome_false("http_404")

    payloads = [_payload(f"row-{i}") for i in range(2)]
    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "publish"]
    )

    assert code == 4  # max(3, 4)
    # DependencyError row went to stderr text-block; verified=false row on stdout
    assert "dependency error" in stderr
    assert "oauth missing" in stderr
    row = json.loads(stdout.strip())
    assert row["verified"] is False


# ---------- DependencyError alone → exit 3 (preserved) ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_dependency_error_alone_yields_exit_3(mock_verify, mock_pub, mock_setup):
    """All rows DependencyError → exit 3 (no verification failures to push to 4)."""
    mock_pub.side_effect = [DependencyError("oauth missing")] * 2

    payloads = [_payload(f"row-{i}") for i in range(2)]
    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "publish"]
    )
    assert code == 3
    mock_verify.assert_not_called()  # adapter never succeeded → verifier never called


# ---------- R16 per-row stderr line ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_per_row_stderr_line_format(mock_verify, mock_pub, mock_setup):
    """Each published row produces a stderr line: 'verified=<label> <url> [<reason>]'."""
    mock_pub.return_value = _published("medium")
    mock_verify.return_value = _outcome_true()

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )
    assert code == 0
    assert "verified=true" in stderr
    assert "https://medium.com/@u/test-article" in stderr


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_per_row_stderr_line_strips_control_chars(mock_verify, mock_pub, mock_setup):
    """Log-injection defence: CR/LF in verification_error doesn't break parsing."""
    mock_pub.return_value = _published("medium")
    mock_verify.return_value = VerificationOutcome(
        verified=False, verified_at="t",
        verification_error="evil\r\n\tpublish failed: bogus\nhttp_200",
    )

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )
    # Find the per-row line
    lines = stderr.strip().split("\n")
    per_row = [l for l in lines if l.startswith("verified=")]
    assert len(per_row) == 1
    assert "\r" not in per_row[0]
    # Tabs are stripped by _safe_for_log
    assert "\t" not in per_row[0]


# ---------- Blogger service plumbed through ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
@patch("backlink_publisher.adapters.blogger_api._get_service")
def test_blogger_row_passes_service_to_verifier(
    mock_get_service, mock_verify, mock_pub, mock_setup,
):
    """Blogger rows: dispatcher builds service via _get_service and passes to verifier."""
    mock_get_service.return_value = MagicMock(name="blogger_service")
    mock_pub.return_value = _published("blogger")
    mock_verify.return_value = _outcome_true()

    payload = _payload(platform="blogger")
    # Override main_domain to a Blogger-shaped host AND make it appear in
    # content_markdown so payload validation passes.
    payload["main_domain"] = "https://myblog.blogspot.com"
    payload["content_markdown"] = (
        "Body referencing https://myblog.blogspot.com and "
        "https://example.com/article."
    )
    payload["links"][0]["url"] = "https://myblog.blogspot.com"
    stdout, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "blogger", "--mode", "publish"]
    )

    assert code == 0
    # Verifier received the service object kwarg
    _, kwargs = mock_verify.call_args
    assert kwargs.get("service") is mock_get_service.return_value


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_medium_row_does_not_build_blogger_service(mock_verify, mock_pub, mock_setup):
    """Medium rows pass service=None — no Blogger auth happens on Medium-only batches."""
    mock_pub.return_value = _published("medium")
    mock_verify.return_value = _outcome_true()

    stdout, stderr, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )

    _, kwargs = mock_verify.call_args
    assert kwargs.get("service") is None


# ---------- R17 summary completeness ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_summary_line_counts_all_three_classes(mock_verify, mock_pub, mock_setup):
    """Mixed batch: 1 true, 1 false, 1 null — summary shows 1/1/1."""
    mock_pub.side_effect = [_published("medium")] * 3
    mock_verify.side_effect = [
        _outcome_true(),
        _outcome_false("title_missing"),
        _outcome_null("http_503"),
    ]

    payloads = [_payload(f"row-{i}") for i in range(3)]
    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "publish"]
    )

    assert code == 4  # the verified=false row drives this
    assert "1 verified" in stderr
    assert "1 unverified (verified=false)" in stderr
    assert "1 null (verified=null)" in stderr


# ---------- Output schema: verification fields ALWAYS present ----------


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_jsonl_output_always_includes_verification_keys(
    mock_verify, mock_pub, mock_setup,
):
    """Every successful row's JSONL carries verified/verified_at/verification_error."""
    mock_pub.return_value = _published("medium")
    mock_verify.return_value = _outcome_true()

    stdout, _, code = _run_publish(
        json.dumps(_payload()), ["--mode", "publish"]
    )
    row = json.loads(stdout.strip())
    assert "verified" in row
    assert "verified_at" in row
    assert "verification_error" in row
