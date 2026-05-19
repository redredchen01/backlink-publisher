"""Tests for cli._bind.driver — Plan 2026-05-19-001 Unit 2.

Locks the contract:
- ``run_bind`` writes ``<config_dir>/<channel>-storage-state.json`` with mode 0600
- Atomic write via tmp + ``os.replace`` (no partial file on failure)
- ``storage_state_path`` is rejected if it resolves outside ``_config_dir()``
- ``mark_bound`` is called only after the file lands on disk
- ``_emit`` validates ``event_name in EVENTS`` at emit time (typos fail loud)
- Event ordering on happy path: start → browser_ready → login_detected → persisted
- Failure path emits ``channel.bind.failed`` with ``error_code`` payload

Tests use a fake ``page-like`` object and a fake ``storage_state`` provider —
no real Playwright. Playwright is lazy-imported inside the driver so it isn't
required to import the module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind import driver as drv
from backlink_publisher.cli._bind.channels import EVENTS
from backlink_publisher.config.loader import _config_dir
from webui_store import channel_status_store
from webui_store.channel_status import get_status


@pytest.fixture(autouse=True)
def _reset_status_store(tmp_path, monkeypatch):
    """Each test gets a fresh channel-status.json next to the isolated config dir."""
    fresh = _config_dir() / "channel-status.json"
    if fresh.exists():
        fresh.unlink()
    monkeypatch.setattr(channel_status_store, "path", fresh, raising=False)


class _FakeStorageStateProvider:
    """Stand-in for Playwright's context.storage_state(); writes JSON to path."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"cookies": [], "origins": []}

    def __call__(self, *, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.payload))


class TestEmitValidatesEventName:
    """``_emit`` is the only writer of stdout JSONL — typos must fail loud."""

    def test_unknown_event_raises_assertion(self, capsys):
        with pytest.raises(AssertionError):
            drv._emit("channel.bind.persistent", channel="velog")  # typo

    def test_known_event_writes_jsonl_line(self, capsys):
        drv._emit("channel.bind.start", channel="velog")
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        rec = json.loads(out[0])
        assert rec["event"] == "channel.bind.start"
        assert rec["channel"] == "velog"
        assert "ts" in rec  # ISO timestamp

    @pytest.mark.parametrize("event", sorted(EVENTS))
    def test_every_member_of_EVENTS_is_acceptable(self, event: str, capsys):
        drv._emit(event, channel="velog")
        out = capsys.readouterr().out
        assert event in out


class TestValidateStoragePath:
    def test_accepts_path_inside_config_dir(self, tmp_path, monkeypatch):
        target = _config_dir() / "velog-storage-state.json"
        # Should not raise
        resolved = drv._validate_storage_state_path(target)
        assert resolved == target.resolve()

    def test_rejects_traversal_outside_config_dir(self, tmp_path):
        # /tmp/<something else> is outside _config_dir() (the isolated session dir)
        outsider = tmp_path / "elsewhere" / "velog-storage-state.json"
        outsider.parent.mkdir(parents=True, exist_ok=True)
        with pytest.raises(UsageError):
            drv._validate_storage_state_path(outsider)

    def test_rejects_absolute_traversal(self):
        with pytest.raises(UsageError):
            drv._validate_storage_state_path("/etc/passwd")


class TestPersistStorageState:
    """``_persist_storage_state`` writes the file atomically with mode 0600."""

    def test_writes_target_path(self, monkeypatch):
        target = _config_dir() / "velog-storage-state.json"
        provider = _FakeStorageStateProvider({"cookies": [{"name": "x"}]})

        result_path = drv._persist_storage_state(
            channel="velog",
            target_path=target,
            storage_state_provider=provider,
        )

        assert result_path == target
        assert target.exists()
        loaded = json.loads(target.read_text())
        assert loaded == {"cookies": [{"name": "x"}], "origins": []} or \
               loaded == {"cookies": [{"name": "x"}]}

    def test_file_mode_is_0600(self):
        target = _config_dir() / "medium-storage-state.json"
        drv._persist_storage_state(
            channel="medium",
            target_path=target,
            storage_state_provider=_FakeStorageStateProvider(),
        )
        mode = target.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    def test_uses_atomic_replace_not_direct_write(self, monkeypatch):
        """Verify the tmp + replace pattern by failing the provider AFTER tmp write
        is not directly observable here, but we can assert no .tmp residue exists
        after a successful write."""
        target = _config_dir() / "blogger-storage-state.json"
        drv._persist_storage_state(
            channel="blogger",
            target_path=target,
            storage_state_provider=_FakeStorageStateProvider(),
        )
        # No .tmp residue from atomic rename
        residue = list(_config_dir().glob("blogger-storage-state.json.tmp*"))
        assert residue == [], f"tmp residue left behind: {residue}"

    def test_traversal_target_raises(self, tmp_path):
        with pytest.raises(UsageError):
            drv._persist_storage_state(
                channel="velog",
                target_path=tmp_path / "outside" / "x.json",
                storage_state_provider=_FakeStorageStateProvider(),
            )


class TestRunBindHappyPath:
    """End-to-end driver.run_bind with a fake recipe — emits 3 events.

    The CLI's ``main()`` wraps this with ``channel.bind.start`` (before) and
    ``channel.bind.failed`` (on non-success); see test_bind_channel_cli.py.
    """

    def test_happy_path_emits_three_events_in_order(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        result = drv.run_bind(
            channel="velog",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        assert result.success is True
        assert result.error_code is None

        events = _collect_events(capsys)
        assert [e["event"] for e in events] == [
            "channel.bind.browser_ready",
            "channel.bind.login_detected",
            "channel.bind.persisted",
        ]

    def test_happy_path_marks_bound(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        status = get_status("medium")
        assert status["status"] == "bound"
        assert status["storage_state_path"].endswith("medium-storage-state.json")

    def test_happy_path_storage_state_file_lands(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        drv.run_bind(
            channel="blogger",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        target = _config_dir() / "blogger-storage-state.json"
        assert target.exists()
        assert (target.stat().st_mode & 0o777) == 0o600


class TestRunBindFailurePaths:
    """run_bind returns BindResult on failure; terminal channel.bind.failed
    event is emitted by the CLI's main() (see test_bind_channel_cli.py)."""

    def test_predicate_timeout_returns_failed_result(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="timeout")
        result = drv.run_bind(
            channel="velog",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True, predicate_timeout=True),
        )
        assert result.success is False
        assert result.error_code == "bound_predicate_timeout"
        assert result.storage_state_path is None

    def test_predicate_timeout_does_not_mark_bound(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="timeout")
        drv.run_bind(
            channel="velog",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True, predicate_timeout=True),
        )
        status = get_status("velog")
        assert status["status"] == "unbound"

    def test_playwright_launch_failure_returns_failed_result(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        result = drv.run_bind(
            channel="velog",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(
                success=False, launch_error="playwright_launch_failed"
            ),
        )
        assert result.success is False
        assert result.error_code == "playwright_launch_failed"
        # No status flip
        assert get_status("velog")["status"] == "unbound"


# ───────── helpers ─────────


def _collect_events(capsys: pytest.CaptureFixture) -> list[dict[str, Any]]:
    out = capsys.readouterr().out.strip()
    if not out:
        return []
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _make_fake_recipe(*, predicate_outcome: str):
    from backlink_publisher.cli._bind.recipes import ChannelRecipe

    def _ok_predicate(page) -> None:
        return None

    return ChannelRecipe(
        login_url="https://example.test/login",
        bound_predicate=_ok_predicate,
        cookie_host_filter=lambda host: True,
    )


class _FakeBrowserRunner:
    """Substitute for the real Playwright launch flow inside driver.run_bind.

    Production code path: ``driver.run_bind`` invokes ``_browser_runner.launch_and_wait(
        recipe, on_browser_ready, on_login_detected) -> storage_state_provider``.
    Tests inject this fake to skip Playwright entirely.
    """

    def __init__(
        self,
        *,
        success: bool,
        launch_error: str | None = None,
        predicate_timeout: bool = False,
    ) -> None:
        self.success = success
        self.launch_error = launch_error
        self.predicate_timeout = predicate_timeout

    def launch_and_wait(
        self,
        *,
        recipe,
        on_browser_ready,
        on_login_detected,
    ):
        if not self.success:
            raise drv.PlaywrightLaunchError(self.launch_error or "playwright_launch_failed")
        on_browser_ready()
        if self.predicate_timeout:
            raise drv.BoundPredicateTimeout()
        on_login_detected()
        return _FakeStorageStateProvider()
