"""Tests for ``EventStore.acquire_lease`` / ``release_lease``.

Covers the takeover ladder: fresh insert, same-owner refresh, contention
reject when a live PID owns the lease, expired-lease takeover, and the
stale-PID takeover path that recovers from crashed publish processes
which bypassed ``atexit`` cleanup.
"""

from __future__ import annotations

import os

import pytest

from backlink_publisher.events import store as store_module
from backlink_publisher.events._store_sqlite import _pid_alive
from backlink_publisher.events.store import EventStore


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def test_acquire_fresh_lease_inserts_row():
    store = EventStore()
    assert store.acquire_lease("medium", os.getpid()) is True

    lease = store.get_lease("medium")
    assert lease is not None
    assert lease["owner_pid"] == os.getpid()


def test_same_owner_can_refresh_lease():
    store = EventStore()
    pid = os.getpid()
    assert store.acquire_lease("medium", pid, ttl_seconds=10) is True
    first_expire = store.get_lease("medium")["expire_at"]

    assert store.acquire_lease("medium", pid, ttl_seconds=3600) is True
    second_expire = store.get_lease("medium")["expire_at"]
    assert second_expire >= first_expire


def test_live_owner_blocks_contender(monkeypatch):
    store = EventStore()
    monkeypatch.setattr(store_module, "_pid_alive", lambda pid: True)
    assert store.acquire_lease("medium", 11111) is True
    assert store.acquire_lease("medium", 22222) is False

    lease = store.get_lease("medium")
    assert lease["owner_pid"] == 11111


def test_expired_lease_is_taken_over():
    store = EventStore()
    # Insert directly with an already-elapsed expire_at.
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO publish_leases "
            "(target_host, owner_pid, started_at, expire_at) "
            "VALUES (?, ?, ?, ?)",
            ("medium", 99999, "2000-01-01T00:00:00+00:00",
             "2000-01-01T00:00:01+00:00"),
        )

    assert store.acquire_lease("medium", os.getpid()) is True
    assert store.get_lease("medium")["owner_pid"] == os.getpid()


def test_dead_owner_is_taken_over(monkeypatch):
    """The original bug: crashed publish leaves a stale lease until TTL."""
    store = EventStore()
    monkeypatch.setattr(store_module, "_pid_alive", lambda pid: True)
    assert store.acquire_lease("medium", 80164, ttl_seconds=3600) is True

    monkeypatch.setattr(
        store_module,
        "_pid_alive",
        lambda pid: False if pid == 80164 else True,
    )
    assert store.acquire_lease("medium", os.getpid()) is True
    assert store.get_lease("medium")["owner_pid"] == os.getpid()


def test_release_lease_clears_row():
    store = EventStore()
    pid = os.getpid()
    store.acquire_lease("medium", pid)
    store.release_lease("medium", pid)
    assert store.get_lease("medium") is None


def test_release_lease_no_op_for_wrong_owner():
    store = EventStore()
    pid = os.getpid()
    store.acquire_lease("medium", pid)
    store.release_lease("medium", pid + 1)
    assert store.get_lease("medium") is not None


def test_pid_alive_for_current_process():
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_false_for_unused_pid():
    # PID 0 is the sentinel/idle PID — our helper treats it as not alive.
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


def test_pid_alive_false_for_dead_process():
    # Spawn a child, reap it, then probe the now-defunct PID.
    pid = os.fork()
    if pid == 0:  # pragma: no cover — child path
        os._exit(0)
    os.waitpid(pid, 0)
    assert _pid_alive(pid) is False
