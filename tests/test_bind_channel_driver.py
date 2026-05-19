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
    """Each test gets a fresh channel-status.json + cleaned channel-side
    artifacts (storage_state.json, last-account files) next to the isolated
    config dir."""
    cfg = _config_dir()
    fresh = cfg / "channel-status.json"
    if fresh.exists():
        fresh.unlink()
    # Clean per-channel artifacts that previous tests may have left.
    for channel in ("velog", "medium", "blogger"):
        for suffix in (
            "storage-state.json",
            "last-account.txt",
            "last-account.tentative",
        ):
            artifact = cfg / f"{channel}-{suffix}"
            if artifact.exists():
                artifact.unlink()
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


class TestBrowserProfileDir:
    """``_browser_profile_dir`` resolves to a persistent Chromium profile path."""

    def test_default_is_config_dir_subdir(self, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_BROWSER_PROFILE_DIR", raising=False)
        assert drv._browser_profile_dir() == _config_dir() / "browser-profile"

    def test_env_override_takes_precedence(self, tmp_path, monkeypatch):
        custom = tmp_path / "alt-profile"
        monkeypatch.setenv("BACKLINK_PUBLISHER_BROWSER_PROFILE_DIR", str(custom))
        assert drv._browser_profile_dir() == custom


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
        identity_mismatch: tuple[str, str] | None = None,
    ) -> None:
        self.success = success
        self.launch_error = launch_error
        self.predicate_timeout = predicate_timeout
        # tuple of (old_account, new_account) → predicate raises IdentityMismatch
        self.identity_mismatch = identity_mismatch

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
        if self.identity_mismatch is not None:
            old, new = self.identity_mismatch
            raise drv.IdentityMismatch(old_account=old, new_account=new)
        on_login_detected()
        return _FakeStorageStateProvider()


# ─── Plan 2026-05-19-003 Unit 1 — IdentityMismatch driver arm ───


class TestIdentityMismatchClass:
    """IdentityMismatch carries old_account + new_account for the failed event."""

    def test_exception_records_accounts(self):
        exc = drv.IdentityMismatch(old_account="alice", new_account="bob")
        assert exc.old_account == "alice"
        assert exc.new_account == "bob"

    def test_is_runtime_error_subclass(self):
        # Subclass of RuntimeError so anything that catches generic Exception
        # still cleans up the browser context in launch_and_wait.
        assert issubclass(drv.IdentityMismatch, RuntimeError)


class TestRunBindIdentityMismatchArm:
    """run_bind catches IdentityMismatch and returns a BindResult that carries
    old_account / new_account in extras for the CLI to surface via JSONL."""

    def test_returns_identity_mismatch_error_code(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        result = drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(
                success=True, identity_mismatch=("alice", "bob")
            ),
        )
        assert result.success is False
        assert result.error_code == "identity_mismatch"
        assert result.storage_state_path is None

    def test_extras_carry_old_and_new_account(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        result = drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(
                success=True, identity_mismatch=("alice", "bob")
            ),
        )
        assert result.extras == {"old_account": "alice", "new_account": "bob"}

    def test_does_not_mark_bound(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(
                success=True, identity_mismatch=("alice", "bob")
            ),
        )
        status = get_status("medium")
        # mark_identity_mismatch is webui's responsibility (bind_job reads
        # JSONL and decides); driver only writes status if mark_bound runs.
        assert status["status"] == "unbound"

    def test_storage_state_file_not_written(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(
                success=True, identity_mismatch=("alice", "bob")
            ),
        )
        target = _config_dir() / "medium-storage-state.json"
        assert not target.exists()


class TestBindResultExtras:
    """BindResult.extras is optional (default None) and carries auxiliary
    payload for failure terminal events."""

    def test_extras_defaults_to_none(self):
        result = drv.BindResult(
            success=True,
            channel="velog",
            storage_state_path=None,
            error_code=None,
        )
        assert result.extras is None

    def test_happy_path_has_no_extras(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        result = drv.run_bind(
            channel="velog",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        assert result.extras is None


class TestTentativeLastAccountRename:
    """On successful bind, the driver atomically renames
    ``<config_dir>/<channel>-last-account.tentative`` to
    ``<channel>-last-account.txt`` (after _persist_storage_state succeeds,
    before mark_bound). Plan 003 Unit 1's predicate writes the tentative
    file."""

    def test_rename_happens_on_success(self, capsys):
        # Pre-write a tentative file as the recipe predicate would have
        tentative = _config_dir() / "medium-last-account.tentative"
        tentative.parent.mkdir(parents=True, exist_ok=True)
        tentative.write_text("alice\n")

        recipe = _make_fake_recipe(predicate_outcome="ok")
        result = drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        assert result.success is True

        final = _config_dir() / "medium-last-account.txt"
        assert final.exists()
        assert final.read_text() == "alice\n"
        assert not tentative.exists()

    def test_no_tentative_file_is_noop(self, capsys):
        # No tentative file: bind still succeeds; no last-account file
        # written by the driver.
        recipe = _make_fake_recipe(predicate_outcome="ok")
        result = drv.run_bind(
            channel="velog",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        assert result.success is True
        assert not (_config_dir() / "velog-last-account.txt").exists()

    def test_tentative_not_renamed_on_failure(self, capsys):
        # Pre-write a tentative as if a prior bind crashed mid-flow
        tentative = _config_dir() / "medium-last-account.tentative"
        tentative.parent.mkdir(parents=True, exist_ok=True)
        tentative.write_text("alice\n")

        recipe = _make_fake_recipe(predicate_outcome="ok")
        drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True, predicate_timeout=True),
        )
        # On predicate_timeout, no rename happens; tentative stays.
        assert tentative.exists()
        assert not (_config_dir() / "medium-last-account.txt").exists()


class TestPostPersistHook:
    """Plan 2026-05-19-005 Unit 1: optional recipe.post_persist hook.

    Contract:
    - When recipe.post_persist is None (velog/blogger default), driver skips
      it and records the original storage_state_path in channel_status_store.
    - When recipe.post_persist returns None, the driver still records the
      original storage_state_path.
    - When recipe.post_persist returns a Path, that Path becomes the canonical
      bound credential path recorded in channel_status_store.
    - Hook is called AFTER _persist_storage_state (file exists on disk) and
      BEFORE mark_bound (so a hook that unlinks storage_state.json + returns
      a new path leaves the store consistent).
    - Hook receives (config_dir, persisted_storage_state_path).
    """

    def test_no_hook_keeps_original_path(self, capsys):
        recipe = _make_fake_recipe(predicate_outcome="ok")
        # default ChannelRecipe has post_persist=None
        assert recipe.post_persist is None
        drv.run_bind(
            channel="velog",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        status = get_status("velog")
        assert status["status"] == "bound"
        assert status["storage_state_path"].endswith("velog-storage-state.json")

    def test_hook_receives_config_dir_and_persisted_path(self, capsys):
        from backlink_publisher.cli._bind.recipes import ChannelRecipe

        received: list[tuple[Path, Path]] = []

        def _capture(config_dir: Path, persisted: Path) -> None:
            received.append((config_dir, persisted))
            return None  # keep original path

        recipe = ChannelRecipe(
            login_url="https://example.test/login",
            bound_predicate=lambda page: None,
            cookie_host_filter=lambda host: True,
            post_persist=_capture,
        )

        drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )

        assert len(received) == 1
        cfg_arg, persisted_arg = received[0]
        assert cfg_arg == _config_dir()
        assert persisted_arg == _config_dir() / "medium-storage-state.json"
        assert persisted_arg.exists()  # file is on disk when hook fires

    def test_hook_return_replaces_canonical_path(self, capsys):
        from backlink_publisher.cli._bind.recipes import ChannelRecipe

        def _replace_with_cookies_only(config_dir: Path, persisted: Path) -> Path:
            cookies_path = config_dir / "medium-cookies.json"
            cookies_path.write_text('{"cookies": []}')
            import os
            os.chmod(cookies_path, 0o600)
            persisted.unlink()  # remove storage_state; cookies.json is canonical now
            return cookies_path

        recipe = ChannelRecipe(
            login_url="https://example.test/login",
            bound_predicate=lambda page: None,
            cookie_host_filter=lambda host: True,
            post_persist=_replace_with_cookies_only,
        )

        result = drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )

        assert result.success is True
        status = get_status("medium")
        assert status["status"] == "bound"
        # canonical path is the cookies.json the hook returned
        assert status["storage_state_path"].endswith("medium-cookies.json")
        # the original storage_state.json is gone
        assert not (_config_dir() / "medium-storage-state.json").exists()
        # BindResult also reflects the new canonical path
        assert result.storage_state_path.name == "medium-cookies.json"

    def test_hook_return_none_keeps_original_path(self, capsys):
        from backlink_publisher.cli._bind.recipes import ChannelRecipe

        def _none_hook(config_dir: Path, persisted: Path) -> None:
            return None

        recipe = ChannelRecipe(
            login_url="https://example.test/login",
            bound_predicate=lambda page: None,
            cookie_host_filter=lambda host: True,
            post_persist=_none_hook,
        )

        drv.run_bind(
            channel="medium",
            recipe=recipe,
            _browser_runner=_FakeBrowserRunner(success=True),
        )
        status = get_status("medium")
        assert status["storage_state_path"].endswith("medium-storage-state.json")
