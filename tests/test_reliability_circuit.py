"""Tests for the publish circuit breaker — Plan 2026-05-28-001 Unit 3."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backlink_publisher._util.errors import AuthExpiredError
from backlink_publisher.publishing.reliability.circuit import (
    _DEFAULT_COOLDOWN_S,
    _LOCK_FILE,
    _STATE_FILE,
    is_ban_signal,
    is_tripped,
    reset_circuit,
    trip,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """Minimal config-like object pointing at a fresh tmp dir."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))

    class _Cfg:
        config_dir = tmp_path

    return _Cfg()


# ---------------------------------------------------------------------------
# Unit 3: is_ban_signal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [
    "Account banned",
    "user is banned from platform",
    "Your account has been suspended",
    "Ban detected",
])
def test_ban_signal_detected(reason):
    exc = AuthExpiredError(channel="medium", reason=reason)
    assert is_ban_signal(exc) is True


@pytest.mark.parametrize("reason", [
    "Session expired",
    "Token expired",
    "Access token invalid",
    "401 Unauthorized",
])
def test_ban_signal_not_present(reason):
    exc = AuthExpiredError(channel="medium", reason=reason)
    assert is_ban_signal(exc) is False


# ---------------------------------------------------------------------------
# Unit 3: circuit state
# ---------------------------------------------------------------------------


def test_not_tripped_by_default(cfg):
    assert is_tripped("medium", cfg) is False


def test_trip_sets_tripped(cfg):
    trip("medium", cfg)
    assert is_tripped("medium", cfg) is True


def test_reset_clears_trip(cfg):
    trip("medium", cfg)
    reset_circuit("medium", cfg)
    assert is_tripped("medium", cfg) is False


def test_trip_only_affects_targeted_platform(cfg):
    trip("medium", cfg)
    assert is_tripped("velog", cfg) is False


def test_cooldown_auto_reset_after_expiry(cfg):
    trip("medium", cfg)
    # Mock time so cooldown has elapsed
    far_future = time.time() + _DEFAULT_COOLDOWN_S + 1
    with patch("backlink_publisher.publishing.reliability.circuit.time") as mock_time:
        mock_time.monotonic.return_value = time.monotonic()
        mock_time.time.return_value = far_future
        mock_time.sleep = time.sleep
        assert is_tripped("medium", cfg) is False


def test_fail_closed_on_corrupt_state(cfg):
    """Corrupt state file → fail-CLOSED → is_tripped returns True."""
    import os
    state_path = cfg.config_dir / _STATE_FILE
    state_path.write_text("{{invalid json{{", encoding="utf-8")
    os.chmod(state_path, 0o600)
    assert is_tripped("medium", cfg) is True


def test_fail_closed_on_missing_state_file(cfg):
    """Missing state file → not tripped (first run, no ban)."""
    assert is_tripped("medium", cfg) is False


def test_state_file_created_with_0600_perms(cfg):
    trip("medium", cfg)
    state_path = cfg.config_dir / _STATE_FILE
    assert state_path.exists()
    import stat
    mode = state_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_state_file_is_valid_json(cfg):
    trip("medium", cfg)
    state_path = cfg.config_dir / _STATE_FILE
    data = json.loads(state_path.read_text())
    assert "medium" in data
    assert data["medium"]["tripped"] is True
    assert data["medium"]["tripped_at_iso"] is not None


def test_trip_preserves_other_platforms(cfg):
    trip("medium", cfg)
    trip("velog", cfg)
    assert is_tripped("medium", cfg) is True
    assert is_tripped("velog", cfg) is True
    reset_circuit("medium", cfg)
    assert is_tripped("medium", cfg) is False
    assert is_tripped("velog", cfg) is True


# ---------------------------------------------------------------------------
# Unit 3: concurrent trip safety
# ---------------------------------------------------------------------------


def test_concurrent_trip_barrier(cfg):
    """Two threads racing to trip the same platform both succeed without error."""
    errors = []
    barrier = threading.Barrier(2)

    def do_trip():
        try:
            barrier.wait()
            trip("medium", cfg)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=do_trip)
    t2 = threading.Thread(target=do_trip)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert errors == [], f"Unexpected errors: {errors}"
    assert is_tripped("medium", cfg) is True
