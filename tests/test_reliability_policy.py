"""Tests for publish_with_policy — Plan 2026-05-28-001 Units 2–3."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backlink_publisher._util.errors import AuthExpiredError, ExternalServiceError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.reliability.events import Outcome
from backlink_publisher.publishing.reliability.policy import (
    _is_browser_tier,
    publish_with_policy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CIRCUIT_COOLDOWN_S", "300")

    class _Cfg:
        config_dir = tmp_path

    return _Cfg()


def _result(platform="medium", status="published"):
    return AdapterResult(status=status, adapter="medium-api", platform=platform)


# get_status is lazily imported inside policy.py, so we patch at its source.
_GET_STATUS = "webui_store.channel_status.get_status"
_ADAPTER_PUB = "backlink_publisher.publishing.reliability.policy.adapter_publish"
_EMIT = "backlink_publisher.publishing.reliability.policy.emit_attempt"


# ---------------------------------------------------------------------------
# Unit 2: _is_browser_tier
# ---------------------------------------------------------------------------


def test_browser_tier_platforms():
    for p in ("medium", "velog", "devto", "mastodon"):
        assert _is_browser_tier(p) is True


def test_non_browser_tier_platforms():
    for p in ("blogger", "wordpress", "telegraph", "notion", "hashnode"):
        assert _is_browser_tier(p) is False


# ---------------------------------------------------------------------------
# Unit 2: Non-browser-tier passthrough
# ---------------------------------------------------------------------------


def test_non_browser_tier_delegates_directly(cfg):
    """Non-browser-tier platforms bypass health gate and circuit breaker."""
    result = _result(platform="blogger")
    with patch(_ADAPTER_PUB, return_value=result) as mock_pub:
        out = publish_with_policy("blogger", payload={"id": "1"}, config=cfg)

    mock_pub.assert_called_once()
    assert out.status == "published"


# ---------------------------------------------------------------------------
# Unit 2: Health gate
# ---------------------------------------------------------------------------


def test_health_gate_blocks_unbound(cfg):
    with patch(_GET_STATUS, return_value={"status": "unbound"}):
        out = publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    assert out.status == "skipped_policy"
    assert "unbound" in (out.error or "")


def test_health_gate_blocks_expired(cfg):
    with patch(_GET_STATUS, return_value={"status": "expired"}):
        out = publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    assert out.status == "skipped_policy"


def test_health_gate_allows_bound(cfg):
    result = _result()
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, return_value=result):
        out = publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    assert out.status == "published"


def test_health_gate_fails_closed_on_exception(cfg):
    """If get_status raises (import or runtime), channel is treated as unbound."""
    with patch(_GET_STATUS, side_effect=RuntimeError("store unavailable")):
        out = publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    assert out.status == "skipped_policy"


# ---------------------------------------------------------------------------
# Unit 2: Observability events
# ---------------------------------------------------------------------------


def test_event_emitted_on_success(cfg):
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, return_value=_result()), \
         patch(_EMIT) as mock_emit:
        publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][0] == "medium"
    assert mock_emit.call_args[0][1] == Outcome.SUCCESS


def test_event_emitted_on_auth_expired(cfg):
    exc = AuthExpiredError(channel="medium", reason="Session expired")
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, side_effect=exc), \
         patch(_EMIT) as mock_emit:
        with pytest.raises(AuthExpiredError):
            publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == Outcome.AUTH_EXPIRED


def test_event_emitted_on_auth_banned(cfg):
    exc = AuthExpiredError(channel="medium", reason="Account banned")
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, side_effect=exc), \
         patch(_EMIT) as mock_emit:
        with pytest.raises(AuthExpiredError):
            publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == Outcome.AUTH_BANNED


def test_event_emitted_on_external_error(cfg):
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, side_effect=ExternalServiceError("Browser crashed")), \
         patch(_EMIT) as mock_emit:
        with pytest.raises(ExternalServiceError):
            publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][1] == Outcome.EXTERNAL_ERROR


# ---------------------------------------------------------------------------
# Unit 3: Circuit breaker integration
# ---------------------------------------------------------------------------


def test_circuit_open_skips_dispatch(cfg):
    """Tripped circuit → skipped_circuit_open without calling adapter_publish."""
    from backlink_publisher.publishing.reliability.circuit import trip
    trip("medium", cfg)

    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB) as mock_pub:
        out = publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    mock_pub.assert_not_called()
    assert out.status == "skipped_circuit_open"


def test_ban_signal_trips_circuit(cfg):
    """AuthExpiredError with ban reason trips the circuit."""
    from backlink_publisher.publishing.reliability.circuit import is_tripped

    exc = AuthExpiredError(channel="medium", reason="Account banned")
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, side_effect=exc), \
         patch(_EMIT):
        with pytest.raises(AuthExpiredError):
            publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    assert is_tripped("medium", cfg) is True


def test_plain_auth_expiry_does_not_trip_circuit(cfg):
    """AuthExpiredError without ban reason does NOT trip the circuit."""
    from backlink_publisher.publishing.reliability.circuit import is_tripped

    exc = AuthExpiredError(channel="medium", reason="Session expired")
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, side_effect=exc), \
         patch(_EMIT):
        with pytest.raises(AuthExpiredError):
            publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    assert is_tripped("medium", cfg) is False


def test_ban_on_medium_does_not_trip_velog(cfg):
    """Circuit breaker is per-platform."""
    from backlink_publisher.publishing.reliability.circuit import is_tripped

    exc = AuthExpiredError(channel="medium", reason="Account suspended")
    with patch(_GET_STATUS, return_value={"status": "bound"}), \
         patch(_ADAPTER_PUB, side_effect=exc), \
         patch(_EMIT):
        with pytest.raises(AuthExpiredError):
            publish_with_policy("medium", payload={"id": "1"}, config=cfg)

    assert is_tripped("velog", cfg) is False
