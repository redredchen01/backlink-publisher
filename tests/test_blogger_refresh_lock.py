"""Regression: concurrent blogger token refreshes must serialize via flock.

Without the lock, two simultaneous publish processes both see an expiring
token, both call creds.refresh(), the second rotation invalidates the
first's access token, and half the batch rows raise AuthExpiredError.

These tests verify:
1. _refresh_lock serializes concurrent callers.
2. The lock is released after the context exits.
3. _build_credentials short-circuits (no refresh) when _near_expiry returns
   False after the re-read-inside-lock (peer already refreshed for us).
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from unittest.mock import MagicMock, patch


from backlink_publisher.publishing.adapters.blogger_api import _refresh_lock


# ── _refresh_lock unit tests ────────────────────────────────────────────────


def test_refresh_lock_creates_lock_file(tmp_path):
    token_path = tmp_path / "blogger-token.json"
    with _refresh_lock(token_path):
        lock_path = token_path.with_suffix(token_path.suffix + ".lock")
        assert lock_path.exists()


def test_refresh_lock_serializes_two_threads(tmp_path):
    """Second thread must wait until first releases the lock."""
    token_path = tmp_path / "blogger-token.json"
    order = []
    barrier = threading.Barrier(2)

    def first():
        with _refresh_lock(token_path):
            barrier.wait()       # signal second thread to try
            time.sleep(0.05)     # hold the lock for 50ms
            order.append("first-done")

    def second():
        barrier.wait()           # wait until first thread holds the lock
        with _refresh_lock(token_path):
            order.append("second-done")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert order == ["first-done", "second-done"]


def test_refresh_lock_releases_on_exit(tmp_path):
    token_path = tmp_path / "blogger-token.json"
    with _refresh_lock(token_path):
        pass
    lock_path = token_path.with_suffix(token_path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not raise
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_refresh_lock_releases_on_exception(tmp_path):
    token_path = tmp_path / "blogger-token.json"
    try:
        with _refresh_lock(token_path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # Lock must still be released
    lock_path = token_path.with_suffix(token_path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_build_credentials_skips_refresh_when_peer_already_refreshed(tmp_path):
    """After acquiring the lock, if _near_expiry returns False (because a peer
    updated the token file while we waited), refresh must not be called."""
    import json
    from backlink_publisher.publishing.adapters.blogger_api import _build_credentials

    token_path = tmp_path / "blogger-token.json"
    token_data = {
        "token": "access_tok",
        "refresh_token": "fake-rt-for-test",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csec",
        "scopes": ["https://www.googleapis.com/auth/blogger"],
    }
    token_path.write_text(json.dumps(token_data))

    config = MagicMock()
    config.blogger_token_path = str(token_path)
    config.blogger_oauth = None

    call_count = {"n": 0}

    def near_expiry_first_only(creds, window):
        call_count["n"] += 1
        # First call: near expiry (triggers the lock path)
        # Second call inside the lock after re-read: not near expiry (peer refreshed)
        return call_count["n"] == 1

    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds.valid = True
    mock_creds.expiry = None

    with patch("backlink_publisher.publishing.adapters.blogger_api._near_expiry",
               side_effect=near_expiry_first_only), \
         patch("backlink_publisher.publishing.adapters.blogger_api.load_blogger_token",
               return_value=token_data), \
         patch("google.oauth2.credentials.Credentials.from_authorized_user_info",
               return_value=mock_creds):
        result = _build_credentials(config)

    # refresh must not have been called — peer already did it
    mock_creds.refresh.assert_not_called()
