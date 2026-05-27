"""Unit tests for the pure validate engine (thin-WebUI Phase 2 Unit 6).

Tests :func:`backlink_publisher.validate.engine.validate_rows` DIRECTLY — no
stdin/stdout, no SystemExit, no banner. The engine is the shared kernel behind
both the CLI shell (``cli/validate_backlinks.py``) and the in-process
``PipelineAPI.validate``. The CLI-behavior guards live in
``tests/test_validate_backlinks.py``; these pin the pure contract.
"""

from __future__ import annotations

import pytest

from backlink_publisher._util import errors
from backlink_publisher.validate.engine import ValidateOutcome, validate_rows


def _make_valid_payload(url_mode: str = "A", platform: str = "medium") -> dict:
    """Full valid payload — mirrors tests/test_validate_backlinks.py."""
    return {
        "id": "abc123",
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": url_mode,
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": (
            "This is a test article about https://example.com and some content here."
        ),
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


def test_happy_path_emits_output_no_errors():
    """Good payload → one enhanced output, no errors, no drops."""
    outcome = validate_rows([_make_valid_payload()], None, check_urls=False)
    assert isinstance(outcome, ValidateOutcome)
    assert len(outcome.outputs) == 1
    assert outcome.outputs[0]["id"] == "abc123"
    assert outcome.outputs[0]["validation"]["status"] == "passed"
    assert outcome.errors == []
    assert outcome.platform_drops == []
    assert outcome.validation_drops == []
    assert outcome.input_count == 1
    assert outcome.failed_count == 0


def test_malformed_row_collects_errors_not_raise():
    """Unregistered platform → collected into errors + platform_drops, no raise."""
    outcome = validate_rows(
        [{"id": "r0", "platform": "xyznonexistent"}], None, check_urls=False
    )
    assert outcome.outputs == []
    assert outcome.errors  # non-empty
    assert any("row 1" in e for e in outcome.errors)
    assert outcome.platform_drops == [1]
    assert outcome.input_count == 1
    assert outcome.failed_count == 1


def test_schema_failure_lands_in_validation_drops():
    """A registered-platform row missing required fields drops at the schema gate."""
    outcome = validate_rows(
        [{"id": "r1", "platform": "medium"}], None, check_urls=False
    )
    assert outcome.outputs == []
    assert outcome.errors
    # Not a platform drop (medium is registered) — a schema/validation drop.
    assert outcome.platform_drops == []
    assert outcome.validation_drops == [1]


def test_empty_input_empty_outcome():
    """No rows → empty outcome, no errors, no raise."""
    outcome = validate_rows([], None, check_urls=False)
    assert outcome.outputs == []
    assert outcome.errors == []
    assert outcome.platform_drops == []
    assert outcome.validation_drops == []
    assert outcome.input_count == 0
    assert outcome.failed_count == 0


def test_partial_success_passing_rows_still_stream():
    """One good + one bad row → good row in outputs, bad in errors."""
    good = _make_valid_payload()
    good["id"] = "good"
    bad = {"id": "bad", "platform": "linkedin"}
    outcome = validate_rows([good, bad], None, check_urls=False)
    assert len(outcome.outputs) == 1
    assert outcome.outputs[0]["id"] == "good"
    assert outcome.errors
    assert outcome.input_count == 2
    assert outcome.failed_count == 1


def test_url_check_failure_raises_external_service_error(monkeypatch):
    """check_urls=True + a reachability failure → engine RAISES (no emit-exit).

    Network is socket-blocked in tests, so patch the engine's check_urls_strict
    to raise. The contract: the engine propagates ExternalServiceError; the
    caller (shell / PipelineAPI) owns the exit-4 / PipeResult mapping.
    """
    def _boom(_urls):
        raise errors.ExternalServiceError("https://example.com unreachable")

    monkeypatch.setattr(
        "backlink_publisher.validate.engine.check_urls_strict", _boom
    )
    with pytest.raises(errors.ExternalServiceError):
        validate_rows([_make_valid_payload()], None, check_urls=True)


@pytest.mark.real_ssrf_check
def test_url_check_real_bad_url_raises_external_service_error():
    """Opt-in: check_urls=True with a real unreachable/blocked URL raises.

    Bypasses the autouse-fixture happy path (this test does NOT conflate with
    the default socket-blocked bypass — it is marked real_ssrf_check so it only
    runs under ``-m real_ssrf_check``). Uses an RFC 5737 TEST-NET host that is
    guaranteed unroutable, so check_urls_strict's reachability probe fails.
    """
    payload = _make_valid_payload()
    payload["target_url"] = "http://192.0.2.1/never"
    payload["main_domain"] = "http://192.0.2.1"
    payload["links"] = [
        {"url": "http://192.0.2.1", "anchor": "Example",
         "kind": "main_domain", "required": True},
        {"url": "http://192.0.2.1/never", "anchor": "Article",
         "kind": "target", "required": True},
    ]
    with pytest.raises(errors.ExternalServiceError):
        validate_rows([payload], None, check_urls=True)
