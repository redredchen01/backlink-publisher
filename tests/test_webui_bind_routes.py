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


@pytest.fixture(autouse=True)
def _reset_channel_status_store():
    """The channel_status_store singleton's path is resolved at import
    time from the session-scope BACKLINK_PUBLISHER_CONFIG_DIR env var
    (conftest.py:_isolated_user_dirs) — so the store file is SHARED
    across every test in the session. Without this autouse reset, a
    test that leaves status=identity_mismatch will collide with PR #83
    Unit 4's new start_bind guard (409 on subsequent POST /bind for
    that channel) in any later test."""
    from webui_store import channel_status_store
    channel_status_store.update(lambda _: {})
    yield
    channel_status_store.update(lambda _: {})


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


def _bind_origin_headers() -> dict[str, str]:
    """Headers carrying the allowlisted Origin for the bind blueprint —
    required after Plan 003 Unit 3's _check_bind_origin_or_abort guard
    became active on /settings/channels/<channel>/bind."""
    from webui_app.helpers import _FLASK_PORT
    return {"Origin": f"http://127.0.0.1:{_FLASK_PORT}"}


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
            headers=_bind_origin_headers(),
        )
        assert resp.status_code == 200, resp.data[:200]
        body = resp.get_json()
        assert body["status"] == "running"
        assert body["channel"] == "medium"
        assert body["job_id"]

    def test_post_missing_csrf_returns_403(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl({"event": "channel.bind.start", "channel": "medium"}))
        resp = client.post(
            "/settings/channels/medium/bind",
            data={},
            headers=_bind_origin_headers(),
        )
        assert resp.status_code == 403

    def test_post_unknown_channel_returns_400(self, client, fake_subprocess):
        fake_subprocess(_events_jsonl())
        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/foobar/bind",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
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
            headers=_bind_origin_headers(),
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

    def test_post_rejects_when_channel_in_identity_mismatch(
        self, client, fake_subprocess
    ):
        # PR #83 adversarial review (P1 #3): if the channel is in
        # identity_mismatch, a fresh bind must NOT be allowed to run
        # in parallel with the keep/replace decision. The TOCTOU window
        # otherwise lets the bind subprocess complete between
        # get_status() and the resolution closure, silently accepting
        # the new account under "keep old".
        from backlink_publisher.config.loader import _config_dir
        from webui_store import channel_status_store
        from webui_store.channel_status import (
            mark_bound, mark_identity_mismatch,
        )
        cfg = _config_dir()
        cfg.mkdir(parents=True, exist_ok=True)
        storage = cfg / "medium-storage-state.json"
        storage.write_text('{"cookies": [], "origins": []}')
        mark_bound("medium", storage)
        mark_identity_mismatch(
            "medium", old_account="alice", new_account="bob"
        )

        try:
            fake_subprocess(_events_jsonl(
                {"event": "channel.bind.start", "channel": "medium"},
            ))
            token = _seed_csrf(client)
            resp = client.post(
                "/settings/channels/medium/bind",
                data={"csrf_token": token},
                headers=_bind_origin_headers(),
            )
            assert resp.status_code == 409, resp.data[:200]
            body = resp.get_json()
            assert body["error"] == "identity_mismatch_unresolved"
        finally:
            # Test leaves identity_mismatch state in the SESSION-shared
            # channel_status_store; wipe so following tests in this
            # module start clean (otherwise their /bind POSTs 409).
            channel_status_store.update(lambda _: {})


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
            headers=_bind_origin_headers(),
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
            headers=_bind_origin_headers(),
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
            headers=_bind_origin_headers(),
        )
        job_id = post.get_json()["job_id"]
        # request the SAME job_id but on a different channel URL
        resp = client.get(f"/settings/channels/velog/bind/{job_id}")
        assert resp.status_code == 404


# ─── Plan 2026-05-19-003 Unit 4 — identity-mismatch resolution routes ───


class TestIdentityMismatchKeep:
    """POST /settings/channels/<channel>/identity-mismatch/keep flips
    status back to bound and preserves storage_state.json + last_account."""

    def _seed_mismatch(self, client, channel="medium"):
        """Put the store + filesystem in a realistic identity_mismatch
        state: storage_state.json present, last_account.txt present
        (from the previously-bound account), channel_status_store has
        the identity_mismatch flag."""
        from backlink_publisher.config.loader import _config_dir
        from webui_store.channel_status import (
            mark_bound,
            mark_identity_mismatch,
        )
        cfg = _config_dir()
        cfg.mkdir(parents=True, exist_ok=True)
        storage = cfg / f"{channel}-storage-state.json"
        storage.write_text('{"cookies": [], "origins": []}')
        (cfg / f"{channel}-last-account.txt").write_text("alice\n")
        mark_bound(channel, storage)
        mark_identity_mismatch(channel, old_account="alice", new_account="bob")

    def test_keep_restores_bound_status(self, client):
        self._seed_mismatch(client)
        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/keep",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
        )
        # Redirect back to /settings
        assert resp.status_code in (302, 303)

        from webui_store.channel_status import get_status
        rec = get_status("medium")
        assert rec["status"] == "bound"

    def test_keep_preserves_storage_state_and_last_account(self, client):
        self._seed_mismatch(client)
        from backlink_publisher.config.loader import _config_dir
        cfg = _config_dir()

        token = _seed_csrf(client)
        client.post(
            "/settings/channels/medium/identity-mismatch/keep",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
        )

        # OLD artifacts must remain — the operator is keeping the old account
        assert (cfg / "medium-storage-state.json").exists()
        assert (cfg / "medium-last-account.txt").exists()
        assert (cfg / "medium-last-account.txt").read_text().strip() == "alice"

    def test_keep_rejects_missing_csrf(self, client):
        self._seed_mismatch(client)
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/keep",
            data={},
            headers=_bind_origin_headers(),
        )
        assert resp.status_code == 403

    def test_keep_rejects_missing_origin(self, client):
        self._seed_mismatch(client)
        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/keep",
            data={"csrf_token": token},
            # No Origin header
        )
        assert resp.status_code == 403

    def test_keep_rejects_unknown_channel(self, client):
        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/tiktok/identity-mismatch/keep",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
        )
        assert resp.status_code == 400

    def test_keep_with_missing_storage_state_demotes_to_expired(self, client):
        # PR #83 adversarial review (P1 #4): if the operator wiped
        # storage_state.json externally, "keep" cannot literally
        # preserve the old credential — but the previous code path
        # silently called _execute_replace, which ALSO deletes
        # last_account.txt + .tentative. The UI button says "keep";
        # silent escalation to destructive replace is the bug.
        # Correct behavior: demote to expired (semantically honest:
        # "you used to be bound, credential is gone, please rebind")
        # WITHOUT touching sibling files.
        from backlink_publisher.config.loader import _config_dir
        from webui_store.channel_status import (
            get_status, mark_bound, mark_identity_mismatch,
        )
        cfg = _config_dir()
        cfg.mkdir(parents=True, exist_ok=True)
        storage = cfg / "medium-storage-state.json"
        storage.write_text('{"cookies": []}')
        (cfg / "medium-last-account.txt").write_text("alice\n")
        mark_bound("medium", storage)
        mark_identity_mismatch("medium", old_account="alice", new_account="bob")
        storage.unlink()  # external wipe between mismatch + operator click

        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/keep",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
        )
        assert resp.status_code in (302, 303)

        rec = get_status("medium")
        assert rec["status"] == "expired"
        # Sibling artifact must NOT have been deleted — the UI said "keep".
        assert (cfg / "medium-last-account.txt").exists()

    def test_keep_noop_when_state_changed_under_us(self, client):
        # PR #83 adversarial review (P1 #3 second half): if a concurrent
        # bind subprocess landed status=bound between the operator's
        # render and click, "keep" must NOT silently rewrite that new
        # bound record. The atomic-closure check should observe the
        # changed status and no-op.
        from backlink_publisher.config.loader import _config_dir
        from webui_store.channel_status import get_status, mark_bound
        cfg = _config_dir()
        cfg.mkdir(parents=True, exist_ok=True)
        storage = cfg / "medium-storage-state.json"
        storage.write_text('{"cookies": []}')
        # Status is bound (not identity_mismatch) by the time keep fires.
        mark_bound("medium", storage)
        bound_at_before = get_status("medium")["bound_at"]

        token = _seed_csrf(client)
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/keep",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
        )
        assert resp.status_code in (302, 303)
        rec = get_status("medium")
        # Status unchanged; bound_at unchanged — closure no-op'd cleanly.
        assert rec["status"] == "bound"
        assert rec["bound_at"] == bound_at_before


class TestIdentityMismatchReplace:
    """POST /settings/channels/<channel>/identity-mismatch/replace wipes
    storage_state.json + last_account.txt and resets status to unbound."""

    def _seed_mismatch(self, channel="medium"):
        from backlink_publisher.config.loader import _config_dir
        from webui_store.channel_status import (
            mark_bound,
            mark_identity_mismatch,
        )
        cfg = _config_dir()
        cfg.mkdir(parents=True, exist_ok=True)
        storage = cfg / f"{channel}-storage-state.json"
        storage.write_text('{"cookies": [], "origins": []}')
        (cfg / f"{channel}-last-account.txt").write_text("alice\n")
        mark_bound(channel, storage)
        mark_identity_mismatch(channel, old_account="alice", new_account="bob")

    def test_replace_wipes_storage_state(self, client):
        self._seed_mismatch()
        from backlink_publisher.config.loader import _config_dir

        token = _seed_csrf(client)
        client.post(
            "/settings/channels/medium/identity-mismatch/replace",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
        )

        cfg = _config_dir()
        assert not (cfg / "medium-storage-state.json").exists()
        assert not (cfg / "medium-last-account.txt").exists()

    def test_replace_resets_to_unbound(self, client):
        self._seed_mismatch()
        token = _seed_csrf(client)
        client.post(
            "/settings/channels/medium/identity-mismatch/replace",
            data={"csrf_token": token},
            headers=_bind_origin_headers(),
        )

        from webui_store.channel_status import get_status
        # The wipe removes the channel record entirely; get_status returns
        # the unbound default.
        rec = get_status("medium")
        assert rec["status"] == "unbound"

    def test_replace_rejects_missing_csrf(self, client):
        self._seed_mismatch()
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/replace",
            data={},
            headers=_bind_origin_headers(),
        )
        assert resp.status_code == 403


class TestBlueprintRegistered:
    def test_bind_blueprint_is_registered(self, app):
        assert "bind" in app.blueprints
