"""Tests for cli.velog_login alias — Plan 2026-05-19-001 Unit 3.

Locks the contract:
- ``velog_login.main(args)`` is a transparent alias for
  ``bind_channel.main(["--channel", "velog", *args])``.
- An informational banner is printed to stderr so plan-012 readers who
  invoke ``velog-login`` know the redirect happened.
- Extra flags pass through verbatim.
- Passing an explicit ``--channel`` flag is rejected with UsageError
  (exit_code=1) — alias implies channel, no override path.
- ``--help`` shows the bind-channel help with the alias banner.
"""

from __future__ import annotations

import pytest

from backlink_publisher.cli import velog_login


class _RecordingBrowserRunner:
    """Fake runner shared with bind_channel tests."""

    def __init__(self, *, success: bool = True) -> None:
        self.success = success
        self.calls: list[dict] = []

    def launch_and_wait(self, *, recipe, on_browser_ready, on_login_detected):
        from backlink_publisher.cli._bind import driver as drv
        if not self.success:
            raise drv.PlaywrightLaunchError("playwright_launch_failed")
        on_browser_ready()
        on_login_detected()

        def _provider(*, path):
            from pathlib import Path
            Path(path).write_text('{"cookies": [], "origins": []}')
        return _provider


class TestBannerOnStderr:
    def test_banner_appears_on_stderr_before_run(self, capsys):
        with pytest.raises(SystemExit):
            velog_login.main(
                ["--help"],
            )
        captured = capsys.readouterr()
        assert "alias" in captured.err.lower()
        assert "bind-channel" in captured.err
        assert "velog" in captured.err

    def test_help_passthrough_to_bind_channel(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            velog_login.main(["--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        # bind-channel's own help body lands on stdout
        assert "Drive a headed Playwright session" in captured.out


class TestExplicitChannelArgRejected:
    """Alias implies channel — operator may not override."""

    def test_explicit_channel_velog_rejected(self, capsys):
        # Even setting the channel to "velog" (the implicit value) is rejected
        # for symmetry — there's exactly one supported invocation.
        with pytest.raises(SystemExit) as excinfo:
            velog_login.main(["--channel", "velog"])
        assert excinfo.value.code == 1

    def test_explicit_channel_medium_rejected(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            velog_login.main(["--channel", "medium"])
        assert excinfo.value.code == 1


class TestDelegationToBindChannel:
    def test_no_args_delegates_to_bind_channel_velog(self, capsys):
        fake = _RecordingBrowserRunner(success=True)
        with pytest.raises(SystemExit) as excinfo:
            velog_login.main([], _browser_runner=fake)
        assert excinfo.value.code == 0

        out = capsys.readouterr().out
        # 4 events end-to-end through bind_channel.main: start + 3 driver events
        import json
        events = [json.loads(line) for line in out.strip().splitlines() if line.strip()]
        assert [e["event"] for e in events] == [
            "channel.bind.start",
            "channel.bind.browser_ready",
            "channel.bind.login_detected",
            "channel.bind.persisted",
        ]
        assert all(e["channel"] == "velog" for e in events)

    def test_extra_flags_pass_through(self, capsys):
        # If bind-channel grows new flags later, the alias must pass them
        # through unchanged. Today there are none — we exercise with an
        # unknown flag and expect argparse to reject it the same way
        # bind-channel would.
        with pytest.raises(SystemExit) as excinfo:
            velog_login.main(["--no-such-flag"])
        # argparse rejects with exit code 2 (argparse internal convention)
        assert excinfo.value.code == 2
