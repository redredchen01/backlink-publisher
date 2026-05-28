"""Tests for publish reliability event emission — Plan 2026-05-28-001 Unit 1."""

import pytest

from backlink_publisher.publishing.reliability.events import (
    Outcome,
    emit_attempt,
    now_ms,
)


def test_emit_attempt_success_does_not_raise():
    emit_attempt("medium", Outcome.SUCCESS, 123.4)


def test_emit_attempt_all_outcomes_do_not_raise():
    for outcome in Outcome:
        emit_attempt("velog", outcome, 50.0)


def test_emit_attempt_with_run_id_does_not_raise():
    emit_attempt("devto", Outcome.AUTH_EXPIRED, 200.0, run_id="run-abc123")


def test_emit_attempt_with_extra_kwargs_does_not_raise():
    emit_attempt("mastodon", Outcome.EXTERNAL_ERROR, 10.0, extra_key="extra_val")


def test_now_ms_returns_positive_float():
    t = now_ms()
    assert isinstance(t, float)
    assert t > 0


def test_emit_attempt_never_raises_on_bad_input(monkeypatch):
    """emit_attempt must never propagate internal errors."""
    import backlink_publisher.publishing.reliability.events as ev

    def boom(*a, **kw):
        raise RuntimeError("logger exploded")

    monkeypatch.setattr(ev._log, "info", boom)
    # Should NOT raise
    emit_attempt("medium", Outcome.SUCCESS, 1.0)


def test_outcome_values_are_strings():
    assert Outcome.SUCCESS.value == "success"
    assert Outcome.AUTH_BANNED.value == "auth_banned"
    assert Outcome.AUTH_EXPIRED.value == "auth_expired"
    assert Outcome.EXTERNAL_ERROR.value == "external_error"
    assert Outcome.TRANSIENT.value == "transient"
