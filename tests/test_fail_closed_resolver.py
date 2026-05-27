"""Layer 2 — test-only fail-closed resolver (Plan 2026-05-27-005 Unit 4).

``_config_dir()`` and ``_cache_dir()`` raise ``RuntimeError`` when the
test sentinel ``BACKLINK_PUBLISHER_TEST_SANDBOX`` is set but no override
env var is configured.  This catches subprocess spawns inside the test
suite that forgot to propagate the override (the "popped override" escape).

Production behaviour is entirely unchanged: the sentinel is never set
outside the test harness.
"""
from __future__ import annotations

import os

import pytest

from backlink_publisher.config.loader import _cache_dir, _config_dir

from conftest import SANDBOX_SENTINEL  # type: ignore[import]


# ── Happy paths ──────────────────────────────────────────────────────────────

def test_config_dir_returns_override_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override set + sentinel set → returns the override (no raise)."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", "/tmp/bp-test-config")
    monkeypatch.setenv(SANDBOX_SENTINEL, "1")
    result = _config_dir()
    assert str(result) == "/tmp/bp-test-config"


def test_cache_dir_returns_override_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override set + sentinel set → returns the override (no raise)."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", "/tmp/bp-test-cache")
    monkeypatch.setenv(SANDBOX_SENTINEL, "1")
    result = _cache_dir()
    assert str(result) == "/tmp/bp-test-cache"


# ── Fail-closed paths ────────────────────────────────────────────────────────

def test_config_dir_raises_when_override_absent_and_sentinel_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Override unset + sentinel set → raises (not real home)."""
    monkeypatch.delenv("BACKLINK_PUBLISHER_CONFIG_DIR", raising=False)
    monkeypatch.setenv(SANDBOX_SENTINEL, "1")
    with pytest.raises(RuntimeError, match="BACKLINK_PUBLISHER_CONFIG_DIR"):
        _config_dir()


def test_cache_dir_raises_when_override_absent_and_sentinel_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Override unset + sentinel set → raises (not real cache)."""
    monkeypatch.delenv("BACKLINK_PUBLISHER_CACHE_DIR", raising=False)
    monkeypatch.setenv(SANDBOX_SENTINEL, "1")
    with pytest.raises(RuntimeError, match="BACKLINK_PUBLISHER_CACHE_DIR"):
        _cache_dir()


def test_fail_closed_error_message_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The RuntimeError message must name the missing var and the sentinel."""
    monkeypatch.delenv("BACKLINK_PUBLISHER_CONFIG_DIR", raising=False)
    monkeypatch.setenv(SANDBOX_SENTINEL, "1")
    with pytest.raises(RuntimeError) as exc_info:
        _config_dir()
    msg = str(exc_info.value)
    assert SANDBOX_SENTINEL in msg, f"Sentinel name missing from message: {msg}"
    assert "BACKLINK_PUBLISHER_CONFIG_DIR" in msg, f"Override key missing from message: {msg}"


# ── Production (no sentinel) paths ───────────────────────────────────────────

def test_config_dir_returns_home_fallback_without_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentinel absent + override absent → falls through to platform default (no raise)."""
    monkeypatch.delenv("BACKLINK_PUBLISHER_CONFIG_DIR", raising=False)
    monkeypatch.delenv(SANDBOX_SENTINEL, raising=False)
    # Must not raise; return value is a Path (we don't check the exact path
    # because it depends on the OS and HOME, which may be the sandbox here).
    result = _config_dir()
    assert result is not None
    assert "backlink-publisher" in str(result)


def test_cache_dir_returns_home_fallback_without_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentinel absent + override absent → falls through to platform default (no raise)."""
    monkeypatch.delenv("BACKLINK_PUBLISHER_CACHE_DIR", raising=False)
    monkeypatch.delenv(SANDBOX_SENTINEL, raising=False)
    result = _cache_dir()
    assert result is not None
    assert "backlink-publisher" in str(result)


# ── resolve_config_dir propagation ───────────────────────────────────────────

def test_resolve_config_dir_propagates_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_resolve_config_dir()`` (the package-indirection hook) must also raise."""
    from backlink_publisher.config.loader import _resolve_config_dir

    monkeypatch.delenv("BACKLINK_PUBLISHER_CONFIG_DIR", raising=False)
    monkeypatch.setenv(SANDBOX_SENTINEL, "1")
    with pytest.raises(RuntimeError):
        _resolve_config_dir()
