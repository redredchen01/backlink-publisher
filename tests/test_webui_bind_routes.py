"""WebUI bind blueprint contract — Plan 2026-05-19-001 Unit 4.

Covers:
  - POST /settings/channels/<channel>/bind  (CSRF + loopback + channel allow-list)
  - GET  /settings/channels/<channel>/bind/<job_id>  (poll lifecycle)
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from unittest.mock import patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    fake_config_dir = tmp_path / "config"
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ):
        yield fake_config_dir


@pytest.fixture
def app():
    from webui_app import create_app
    a = create_app(start_scheduler=False)
    a.config["TESTING"] = True
    a.config["SESSION_COOKIE_SECURE"] = False
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_csrf(client) -> str:
    """Round-trip a GET to set a session csrf_token, then return it."""
    with client.session_transaction() as sess:
        sess["csrf_token"] = "test-csrf-token-fixture"
    return "test-csrf-token-fixture"


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _FakeProc:
    def __init__(self, lines: list[str], returncode: int = 0):
        self.stdout = io.StringIO("".join(lines))
        self.stderr = io.StringIO("")
        self._returncode = returncode

    def wait(self, timeout=None):  # noqa: ARG002
        return self._returncode

    def kill(self):
        pass


def _events_jsonl(*events) -> list[str]:
    return [json.dumps(ev) + "\n" for ev in events]


@pytest.fixture
def fake_subprocess():
    """Replace the registry's Popen factory with a controllable fake."""
    from webui_app.services.bind_job import registry as r

    def _install(lines, returncode=0):
        r.reset_for_tests()
        r._popen = lambda *a, **kw: _FakeProc(lines, returncode=returncode)
        return r

    yield _install
    r.reset_for_tests()


class TestStartBindRoute:
    def test_post_happy_path_returns_job_id(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl(
            {"event": "channel.bind.start", "channel": "medium"},
            {"event": "channel.bind.persisted", "channel": "medium"},
        ))
        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/medium/bind",
            data={"csrf_token": token},
        )
        assert resp.status_code == 200, resp.data[:200]
        body = resp.get_json()
        assert body["status"] == "running"
        assert body["channel"] == "medium"
        assert body["job_id"]

    def test_post_missing_csrf_returns_403(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl({"event": "channel.bind.start", "channel": "medium"}))
        resp = client.post("/settings/channels/medium/bind", data={})
        assert resp.status_code == 403

    def test_post_unknown_channel_returns_400(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl())
        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/foobar/bind",
            data={"csrf_token": token},
        )
        assert resp.status_code == 400

    def test_post_path_traversal_channel_returns_400(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl())
        token = _seed_csrf(client)
        # Flask routes treat slashes as separators, so this just fails to match
        # — but a single-segment traversal string must still be rejected as 400.
        resp = client.post(
            "/settings/channels/..%2Fetc%2Fpasswd/bind",
            data={"csrf_token": token},
        )
        assert resp.status_code in {400, 404}

    def test_post_non_loopback_remote_returns_403(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl({"event": "channel.bind.start", "channel": "medium"}))
        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/medium/bind",
            data={"csrf_token": token},
            environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
        )
        assert resp.status_code == 403


class TestPollBindRoute:
    def test_poll_unknown_job_returns_404(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl())
        resp = client.get("/settings/channels/medium/bind/deadbeef")
        assert resp.status_code == 404

    def test_poll_unknown_channel_returns_400(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl())
        resp = client.get("/settings/channels/foobar/bind/anything")
        assert resp.status_code == 400

    def test_poll_lifecycle_reaches_done(self, client, fake_subprocess):
        from webui_app.services.bind_job import registry
        fake_subprocess(_events_jsonl(
            {"event": "channel.bind.start", "channel": "medium"},
            {"event": "channel.bind.browser_ready", "channel": "medium"},
            {"event": "channel.bind.login_detected", "channel": "medium"},
            {"event": "channel.bind.persisted", "channel": "medium"},
        ))
        token = _seed_csrf(client)
        post = client.post(
            "/settings/channels/medium/bind",
            data={"csrf_token": token},
        )
        job_id = post.get_json()["job_id"]

        assert _wait_until(
            lambda: registry.poll(job_id)["status"] in {"done", "failed"}
        )
        resp = client.get(f"/settings/channels/medium/bind/{job_id}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "done"
        assert len(body["events"]) == 4
        event_names = [e["event"] for e in body["events"]]
        assert event_names == [
            "channel.bind.start",
            "channel.bind.browser_ready",
            "channel.bind.login_detected",
            "channel.bind.persisted",
        ]

    def test_poll_returns_failed_with_chinese_message(self, client, fake_subprocess):
        from webui_app.services.bind_job import registry
        fake_subprocess(
            _events_jsonl(
                {"event": "channel.bind.start", "channel": "medium"},
                {"event": "channel.bind.failed", "channel": "medium",
                 "error_code": "bound_predicate_timeout"},
            ),
            returncode=3,
        )
        token = _seed_csrf(client)
        post = client.post(
            "/settings/channels/medium/bind",
            data={"csrf_token": token},
        )
        job_id = post.get_json()["job_id"]
        assert _wait_until(
            lambda: registry.poll(job_id)["status"] == "failed"
        )
        resp = client.get(f"/settings/channels/medium/bind/{job_id}")
        body = resp.get_json()
        assert body["status"] == "failed"
        assert body["error_code"] == "bound_predicate_timeout"
        assert "登录超时" in body["error_message"]

    def test_poll_with_mismatched_channel_returns_404(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl(
            {"event": "channel.bind.persisted", "channel": "medium"},
        ))
        token = _seed_csrf(client)
        post = client.post(
            "/settings/channels/medium/bind",
            data={"csrf_token": token},
        )
        job_id = post.get_json()["job_id"]
        # request the SAME job_id but on a different channel URL
        resp = client.get(f"/settings/channels/velog/bind/{job_id}")
        assert resp.status_code == 404


class TestBlueprintRegistered:
    def test_bind_blueprint_is_registered(self, app):
        assert "bind" in app.blueprints
