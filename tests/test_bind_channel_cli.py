"""Tests for cli.bind_channel — Plan 2026-05-19-001 Unit 2.

Locks the CLI contract:
- ``--channel <name>`` is required; argparse rejects missing
- Unknown ``<name>`` raises UsageError → exit_code=1 BEFORE any browser launch
- ``--channel ../etc/passwd`` payloads are rejected as unknown channel (defense-in-depth)
- Happy path emits the four EVENTS in order and exits 0
- Failure path emits ``channel.bind.failed`` and exits 3
- ``--help`` lists the CHANNELS members verbatim

No real Playwright; a fake browser runner is injected via the ``_browser_runner``
keyword on ``main()`` (production main() defaults to the real Playwright runner).
"""

from __future__ import annotations

import json
import sys

import pytest

from backlink_publisher.cli import bind_channel as bc
from backlink_publisher.cli._bind.channels import CHANNELS


def _collect_events(out: str):
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


class TestArgparse:
    def test_help_lists_channels(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            bc.main(["--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr().out
        for ch in CHANNELS:
            assert ch in captured

    def test_missing_channel_arg_exits_nonzero(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            bc.main([])
        assert excinfo.value.code != 0


class TestChannelValidation:
    def test_unknown_channel_exits_1_no_events(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            bc.main(["--channel", "tiktok"])
        # UsageError → exit_code=1
        assert excinfo.value.code == 1
        # No JSONL events should have been emitted past parse failure
        out = capsys.readouterr().out
        assert "channel.bind.start" not in out

    def test_traversal_payload_rejected(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            bc.main(["--channel", "../etc/passwd"])
        assert excinfo.value.code == 1


class TestHappyPath:
    def test_emits_four_events_in_order(self, monkeypatch, capsys):
        from backlink_publisher.cli._bind import driver as drv

        fake = _RecordingBrowserRunner(success=True)
        with pytest.raises(SystemExit) as excinfo:
            bc.main(["--channel", "velog"], _browser_runner=fake)
        assert excinfo.value.code == 0

        events = _collect_events(capsys.readouterr().out)
        assert [e["event"] for e in events] == [
            "channel.bind.start",
            "channel.bind.browser_ready",
            "channel.bind.login_detected",
            "channel.bind.persisted",
        ]
        # Every event carries channel name
        assert all(e["channel"] == "velog" for e in events)


class TestFailurePath:
    def test_predicate_timeout_exits_3_with_failed_event(self, capsys):
        fake = _RecordingBrowserRunner(success=True, predicate_timeout=True)
        with pytest.raises(SystemExit) as excinfo:
            bc.main(["--channel", "medium"], _browser_runner=fake)
        assert excinfo.value.code == 3

        events = _collect_events(capsys.readouterr().out)
        assert events[-1]["event"] == "channel.bind.failed"
        assert events[-1]["error_code"] == "bound_predicate_timeout"

    def test_playwright_launch_failure_exits_3(self, capsys):
        fake = _RecordingBrowserRunner(success=False, launch_error="playwright_launch_failed")
        with pytest.raises(SystemExit) as excinfo:
            bc.main(["--channel", "blogger"], _browser_runner=fake)
        assert excinfo.value.code == 3

        events = _collect_events(capsys.readouterr().out)
        assert events[-1]["event"] == "channel.bind.failed"


# ───────── fake runner shared with driver tests ─────────


class _RecordingBrowserRunner:
    def __init__(
        self,
        *,
        success: bool = True,
        launch_error: str | None = None,
        predicate_timeout: bool = False,
    ) -> None:
        self.success = success
        self.launch_error = launch_error
        self.predicate_timeout = predicate_timeout

    def launch_and_wait(self, *, recipe, on_browser_ready, on_login_detected):
        from backlink_publisher.cli._bind import driver as drv
        if not self.success:
            raise drv.PlaywrightLaunchError(self.launch_error or "playwright_launch_failed")
        on_browser_ready()
        if self.predicate_timeout:
            raise drv.BoundPredicateTimeout()
        on_login_detected()
        return _FakeStorageStateProvider()


class _FakeStorageStateProvider:
    def __call__(self, *, path):
        from pathlib import Path
        Path(path).write_text('{"cookies": [], "origins": []}')
