"""BindJobRegistry semantics — Plan 2026-05-19-001 Unit 4.

Tests the registry's spawn/transition/cleanup behavior with a fake
``subprocess.Popen`` so no real ``bind-channel`` ever launches.
"""

from __future__ import annotations

import io
import json
import time
from typing import Any

import pytest

from backlink_publisher._util.errors import UsageError


class _FakeProc:
    """Minimal Popen stand-in: provides stdout iterator + wait() + returncode."""

    def __init__(self, lines: list[str], returncode: int = 0):
        self.stdout = io.StringIO("".join(lines))
        self.stderr = io.StringIO("")
        self._returncode = returncode
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self._returncode

    def kill(self) -> None:
        self.killed = True


def _make_popen(lines: list[str], returncode: int = 0):
    def _factory(*_args: Any, **_kwargs: Any) -> _FakeProc:
        return _FakeProc(lines, returncode=returncode)
    return _factory


def _events_jsonl(*events: dict[str, Any]) -> list[str]:
    return [json.dumps(ev) + "\n" for ev in events]


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def registry():
    from webui_app.services.bind_job import BindJobRegistry
    return BindJobRegistry()


class TestRegistryStart:
    def test_rejects_unknown_channel(self, registry):
        with pytest.raises(UsageError):
            registry.start("../etc/passwd")

    def test_happy_path_returns_running_job(self, registry):
        registry._popen = _make_popen(_events_jsonl(
            {"event": "channel.bind.start", "channel": "medium"},
            {"event": "channel.bind.browser_ready", "channel": "medium"},
            {"event": "channel.bind.login_detected", "channel": "medium"},
            {"event": "channel.bind.persisted", "channel": "medium",
             "storage_state_path": "/tmp/medium-storage-state.json"},
        ))
        job = registry.start("medium")
        assert job.channel == "medium"
        assert job.id and len(job.id) >= 8
        assert job.status in {"running", "done"}

    def test_concurrent_bind_same_channel_rejected(self, registry):
        # Use a slow proc whose stdout never terminates within the test window
        # to keep status="running" while we attempt the second start.
        class _BlockingProc:
            def __init__(self):
                self._read_lock = __import__("threading").Event()
                # never set → readline blocks forever; we'll close in teardown
                self.stdout = self  # iterable proxy
                self.stderr = io.StringIO("")
                self._returncode = 0

            def __iter__(self):
                # block until the test ends — simulates a long-running proc
                self._read_lock.wait(timeout=5.0)
                return iter([])

            def wait(self, timeout=None):  # noqa: ARG002
                return 0

            def kill(self):
                self._read_lock.set()

        first = _BlockingProc()
        registry._popen = lambda *a, **kw: first
        job = registry.start("velog")
        assert job.status == "running"
        with pytest.raises(UsageError):
            registry.start("velog")
        # cleanup
        first.kill()


class TestRegistryDrain:
    def test_terminal_persisted_transitions_to_done(self, registry):
        registry._popen = _make_popen(_events_jsonl(
            {"event": "channel.bind.start", "channel": "medium"},
            {"event": "channel.bind.persisted", "channel": "medium"},
        ))
        job = registry.start("medium")
        assert _wait_until(lambda: registry.poll(job.id)["status"] == "done")
        snap = registry.poll(job.id)
        assert snap["error_code"] is None
        assert len(snap["events"]) == 2

    def test_terminal_failed_transitions_to_failed_with_error_code(self, registry):
        registry._popen = _make_popen(_events_jsonl(
            {"event": "channel.bind.start", "channel": "medium"},
            {"event": "channel.bind.failed", "channel": "medium",
             "error_code": "bound_predicate_timeout"},
        ), returncode=3)
        job = registry.start("medium")
        assert _wait_until(lambda: registry.poll(job.id)["status"] == "failed")
        snap = registry.poll(job.id)
        assert snap["error_code"] == "bound_predicate_timeout"
        assert "登录超时" in snap["error_message"]

    def test_stream_closed_without_terminal_event_marks_failed(self, registry):
        registry._popen = _make_popen(_events_jsonl(
            {"event": "channel.bind.start", "channel": "medium"},
        ), returncode=1)
        job = registry.start("medium")
        assert _wait_until(lambda: registry.poll(job.id)["status"] == "failed")
        snap = registry.poll(job.id)
        assert snap["error_code"] == "stream_closed_no_terminal_event"

    def test_invalid_json_lines_skipped(self, registry):
        registry._popen = _make_popen([
            "not json\n",
            json.dumps({"event": "channel.bind.start", "channel": "medium"}) + "\n",
            json.dumps({"event": "channel.bind.persisted", "channel": "medium"}) + "\n",
        ])
        job = registry.start("medium")
        assert _wait_until(lambda: registry.poll(job.id)["status"] == "done")
        snap = registry.poll(job.id)
        # only 2 valid lines, the garbage line is skipped
        assert len(snap["events"]) == 2


class TestRegistryPoll:
    def test_unknown_job_returns_none(self, registry):
        assert registry.poll("does-not-exist") is None

    def test_known_error_code_renders_chinese_message(self, registry):
        from webui_app.services.bind_job import BIND_ERROR_MESSAGES
        for code, msg in BIND_ERROR_MESSAGES.items():
            assert isinstance(msg, str) and msg
            assert any("一" <= ch <= "鿿" for ch in msg), (
                f"BIND_ERROR_MESSAGES[{code!r}] should contain Chinese chars"
            )

    def test_error_messages_cover_all_known_failed_codes(self):
        """Every error_code Unit 2's driver can emit on a failed event maps to
        a Chinese message (no English fallback for known codes)."""
        from webui_app.services.bind_job import BIND_ERROR_MESSAGES
        known_codes = {
            "bound_predicate_timeout",
            "playwright_launch_failed",
            "storage_path_traversal",
            "persist_io_error",
            "stream_closed_no_terminal_event",
        }
        missing = known_codes - set(BIND_ERROR_MESSAGES.keys())
        assert not missing, f"missing Chinese mappings: {missing}"


class TestReapOrphans:
    def test_v1_noop_does_not_raise(self):
        from webui_app.services.bind_job import reap_orphans
        # v1 contract: in-memory registry has no persistent state — function
        # is a documented no-op. Calling it twice in succession must not raise.
        reap_orphans()
        reap_orphans()
