"""Tests for the verifier dispatch entry point and outcome model.

Unit 1 of the real-publish-verification plan: VerificationOutcome dataclass,
per-channel retry constants, module-level adapter metadata, and the
verify_published dispatch entry that handles status/dry-run skip paths.
Channel-specific verifiers are stubs returning not_implemented outcomes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.verifier import (
    _ADAPTER_METADATA,
    _API_RETRY_WAITS_S,
    _ERR_INTERNAL_PREFIX,
    _ERR_TRANSIENT_EXHAUSTED,
    _HTML_RETRY_WAITS_S,
    _MAX_BODY_BYTES,
    _MAX_FETCH_WALL_CLOCK_S,
    _MAX_REDIRECT_HOPS,
    VerificationOutcome,
    _resolve_adapter_metadata,
    verify_published,
)


# ---------- Module-level constants ----------

def test_html_retry_waits_has_four_attempts():
    """R9: HTML channel does 1 initial + 3 retries = 4 total attempts, ≤30s."""
    assert len(_HTML_RETRY_WAITS_S) == 4
    assert _HTML_RETRY_WAITS_S[0] == 0
    assert sum(_HTML_RETRY_WAITS_S) <= 30


def test_api_retry_waits_single_attempt():
    """R10: Blogger API is single-attempt (read-after-write consistency)."""
    assert _API_RETRY_WAITS_S == (0,)


def test_transient_exhausted_template_uses_attempt_count():
    """Cross-unit string coupling: the constant must format correctly so Unit 6
    can detect transient-exhausted rows by prefix, not by literal string."""
    n = len(_HTML_RETRY_WAITS_S)
    formatted = _ERR_TRANSIENT_EXHAUSTED.format(n=n)
    assert formatted == "transient_exhausted: 4/4 attempts"
    assert formatted.startswith("transient_exhausted:")


def test_internal_error_prefix_constant():
    """Unit 6 uses this prefix to roll up verifier_internal_error into verified_false."""
    assert _ERR_INTERNAL_PREFIX == "verifier_internal_error: "


def test_security_constants_present():
    """SSRF + slow-drip DoS defenses depend on these being defined."""
    assert _MAX_BODY_BYTES == 2_000_000
    assert _MAX_FETCH_WALL_CLOCK_S == 15
    assert _MAX_REDIRECT_HOPS == 5


# ---------- Adapter metadata ----------

def test_blogger_metadata_channel_and_allowlist():
    md = _resolve_adapter_metadata("blogger-api")
    assert md["channel"] == "api"
    assert "*.blogspot.com" in md["allowed_hosts"]
    assert "blogger.com" in md["allowed_hosts"]
    assert any("html" in p for p in md["allowed_path_patterns"])


def test_all_medium_adapters_use_html_channel_with_same_allowlist():
    for name in ("medium-api", "medium-browser", "medium-brave"):
        md = _resolve_adapter_metadata(name)
        assert md["channel"] == "html", f"{name} should use html channel"
        assert "medium.com" in md["allowed_hosts"]
        assert "*.medium.com" in md["allowed_hosts"]


def test_blogger_args_lambda_reads_provider_meta():
    md = _resolve_adapter_metadata("blogger-api")
    result = AdapterResult(
        status="published",
        adapter="blogger-api",
        platform="blogger",
        published_url="https://example.blogspot.com/2026/05/post.html",
    )
    result._provider_meta = {"blog_id": "BLOG_X", "post_id": "POST_Y"}
    row = {"id": "i", "title": "t", "links": []}
    args = md["args"](row, result)
    assert args == {"blog_id": "BLOG_X", "post_id": "POST_Y"}


def test_blogger_args_lambda_raises_keyerror_when_provider_meta_empty():
    """Unit 6 will catch this KeyError and map to verification_error=missing_provider_meta."""
    md = _resolve_adapter_metadata("blogger-api")
    result = AdapterResult(
        status="published", adapter="blogger-api", platform="blogger",
        published_url="https://example.blogspot.com/2026/05/post.html",
    )
    row = {"id": "i", "title": "t", "links": []}
    with pytest.raises(KeyError):
        md["args"](row, result)


def test_medium_args_lambda_returns_published_url():
    md = _resolve_adapter_metadata("medium-api")
    result = AdapterResult(
        status="published", adapter="medium-api", platform="medium",
        published_url="https://medium.com/@u/slug-abc",
    )
    args = md["args"]({}, result)
    assert args == {"url": "https://medium.com/@u/slug-abc"}


def test_resolve_unknown_adapter_raises_keyerror_listing_supported():
    with pytest.raises(KeyError) as exc:
        _resolve_adapter_metadata("substack-api")
    msg = str(exc.value)
    assert "substack-api" in msg
    assert "blogger-api" in msg  # the error message should list the known adapters
    assert "medium-api" in msg


# ---------- VerificationOutcome dataclass ----------

def test_outcome_is_frozen_dataclass():
    """Frozen so that callers can't accidentally mutate a returned outcome."""
    o = VerificationOutcome(verified=True, verified_at="2026-05-12T00:00:00+00:00",
                            verification_error=None)
    with pytest.raises(Exception):
        o.verified = False  # frozen dataclass should raise FrozenInstanceError


def test_outcome_field_combinations_valid_shape():
    """Three legal shapes: verified=True/False with a timestamp, or None for skip/transient."""
    # success
    o1 = VerificationOutcome(verified=True, verified_at="2026-05-12T00:00:00+00:00",
                             verification_error=None)
    assert o1.verified is True
    assert o1.verification_error is None
    # definitive failure
    o2 = VerificationOutcome(verified=False, verified_at="2026-05-12T00:00:00+00:00",
                             verification_error="http_404")
    assert o2.verified is False
    assert o2.verification_error == "http_404"
    # transient / skip
    o3 = VerificationOutcome(verified=None, verified_at=None,
                             verification_error="http_503")
    assert o3.verified is None


# ---------- verify_published skip rules ----------

def _result(status: str, adapter: str = "blogger-api", _dry_run: bool = False) -> AdapterResult:
    return AdapterResult(
        status=status,
        adapter=adapter,
        platform="blogger",
        published_url="https://example.blogspot.com/2026/05/post.html",
        _dry_run=_dry_run,
    )


def test_dry_run_skips_with_explicit_reason():
    """R4: dry-run skips verification entirely; outcome carries verification_error=dry_run."""
    out = verify_published({}, _result("published", _dry_run=True))
    assert out.verified is None
    assert out.verified_at is None
    assert out.verification_error == "dry_run"


def test_drafted_status_skips_without_reason():
    """R3: drafted rows skip verification; verified=null, no error string."""
    out = verify_published({}, _result("drafted"))
    assert out.verified is None
    assert out.verified_at is None
    assert out.verification_error is None


def test_failed_status_skips_without_reason():
    """R3: failed rows skip verification too."""
    out = verify_published({}, _result("failed"))
    assert out.verified is None
    assert out.verification_error is None


def test_published_dispatches_to_html_channel():
    """published+real → HTML channel handler fires.

    Patched at the dispatch boundary so this stays a pure routing test —
    HTML-channel internals are covered in test_verifier_html_channel.py.
    """
    sentinel = VerificationOutcome(verified=True, verified_at="t", verification_error=None)
    with patch("backlink_publisher.verifier._verify_html_channel", return_value=sentinel) as m:
        out = verify_published({"links": []}, _result("published", adapter="medium-api"))
    assert out is sentinel
    m.assert_called_once()


def test_dispatches_html_channel_for_medium_adapter():
    """All medium-* adapters route to the html channel handler."""
    sentinel = VerificationOutcome(verified=True, verified_at="t", verification_error=None)
    for adapter_name in ("medium-api", "medium-browser", "medium-brave"):
        result = AdapterResult(
            status="published", adapter=adapter_name, platform="medium",
            published_url="https://medium.com/@u/slug",
        )
        with patch("backlink_publisher.verifier._verify_html_channel", return_value=sentinel) as m:
            out = verify_published({"links": []}, result)
        assert out is sentinel, f"{adapter_name} did not dispatch to html channel"
        m.assert_called_once()


def test_unknown_adapter_becomes_verifier_internal_error():
    """Unknown adapter → verifier wraps the KeyError as verifier_internal_error.

    The defensive wrap means a verifier bug (or new adapter without metadata)
    never aborts the batch; instead it surfaces in the internal-error count
    and rolls into verified_false for exit-code purposes (Unit 6's
    saturation guard)."""
    result = AdapterResult(
        status="published", adapter="substack-api", platform="substack",
        published_url="https://substack.com/p/x",
    )
    out = verify_published({"links": []}, result)
    assert out.verified is None
    assert out.verification_error.startswith("verifier_internal_error:")
    assert "substack-api" in out.verification_error or "KeyError" in out.verification_error


def test_verify_published_signature_keyword_only_service():
    """Calling verify_published without service= must work (HTML adapters)."""
    sentinel = VerificationOutcome(verified=True, verified_at="t", verification_error=None)
    with patch("backlink_publisher.verifier._verify_html_channel", return_value=sentinel):
        out = verify_published({"links": []}, AdapterResult(
            status="published", adapter="medium-api", platform="medium",
            published_url="https://medium.com/x"))
    assert out is sentinel


# ---------- _sanitize_exception ----------


def test_sanitize_strips_bearer_token():
    from backlink_publisher.verifier import _sanitize_exception
    exc = RuntimeError("auth header was Bearer ya29.abc123secret")
    sanitized = _sanitize_exception(exc)
    assert "Bearer" not in sanitized
    assert "<redacted>" in sanitized
    assert sanitized.startswith("RuntimeError:")


def test_sanitize_strips_refresh_token_string():
    from backlink_publisher.verifier import _sanitize_exception
    exc = ValueError("refresh_token=1//abc was rejected")
    sanitized = _sanitize_exception(exc)
    assert "refresh_token" not in sanitized


def test_sanitize_strips_crlf():
    from backlink_publisher.verifier import _sanitize_exception
    exc = RuntimeError("a\r\nb\tc")
    sanitized = _sanitize_exception(exc)
    assert "\r" not in sanitized
    assert "\n" not in sanitized
    assert "\t" not in sanitized


def test_sanitize_caps_length():
    from backlink_publisher.verifier import _sanitize_exception
    exc = RuntimeError("x" * 1000)
    sanitized = _sanitize_exception(exc, max_len=50)
    assert len(sanitized) <= 50 + len("RuntimeError: ")  # cls prefix added after cap


def test_module_importable_without_side_effects():
    """No singletons, no I/O at import — Unit 1 promise."""
    # If we got here the import already succeeded; check that calling
    # the entry on a non-published row doesn't touch any external state.
    out = verify_published({}, _result("drafted"))
    assert out.verification_error is None
