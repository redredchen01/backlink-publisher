"""Tests for the canary health store + ``[canary.<platform>]`` config reader.

Plan: docs/plans/2026-05-27-001-feat-adapter-contract-canary-plan.md (Unit 1).

Covers the v1-minimal health record round-trip, drift/link-alive debounce
counters, ``BACKLINK_PUBLISHER_CONFIG_DIR`` re-resolution, 0o600 atomic
writes, and the ``[canary.<platform>]`` config parse round-trip.

The session-autouse ``_isolate_user_dirs`` fixture (tests/conftest.py)
already points ``BACKLINK_PUBLISHER_CONFIG_DIR`` at a tmp dir; per-test
overrides use ``monkeypatch.setenv`` (never ``del os.environ`` — that
poisons later tests; see feedback_del_os_environ_poisons_later_tests).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from backlink_publisher.canary import store


@pytest.fixture(autouse=True)
def _isolated_canary_dir(tmp_path, monkeypatch):
    """Point each test at its OWN config dir so health-file writes from one
    test don't leak onto disk into the next (the session-autouse
    ``_isolate_user_dirs`` fixture shares a single dir across the suite).
    Uses ``monkeypatch.setenv`` — never ``del os.environ``. Resets the
    cached _LazyStore so the path re-resolves."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store.canary_health_store.reset()
    yield
    store.canary_health_store.reset()


def _health_path() -> Path:
    return Path(store.canary_health_store.path)


# ── Happy: first write round-trips ───────────────────────────────────────


def test_first_write_link_alive_round_trips():
    rec = store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    assert rec["status"] == store.STATUS_LINK_ALIVE
    assert rec["consecutive_failures"] == 0
    assert rec["last_ok_at"] is not None
    assert rec["last_drift_at"] is None

    reloaded = store.get_health("blogger")
    assert reloaded == rec
    # Disk truly persists identical content.
    on_disk = json.loads(_health_path().read_text(encoding="utf-8"))
    assert on_disk["blogger"] == rec


def test_get_health_unknown_returns_minimal_default():
    rec = store.get_health("never-seen")
    assert rec == {
        "status": store.STATUS_NOT_CONFIGURED,
        "consecutive_failures": 0,
        "last_ok_at": None,
        "last_drift_at": None,
    }
    # Read default must not have written a file.
    assert not _health_path().exists()


def test_health_record_only_has_v1_minimal_fields():
    rec = store.record_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    # Unit 1 must NOT introduce quarantined / consecutive_oks (Unit 4).
    assert set(rec) == {
        "status",
        "consecutive_failures",
        "last_ok_at",
        "last_drift_at",
    }


# ── Edge: debounce counters ──────────────────────────────────────────────


def test_consecutive_drift_increments_then_link_alive_resets():
    r1 = store.record_verdict("telegraph", store.STATUS_DRIFT_CONFIRMED)
    r2 = store.record_verdict("telegraph", store.STATUS_DRIFT_CONFIRMED)
    assert r1["consecutive_failures"] == 1
    assert r2["consecutive_failures"] == 2
    assert r2["last_drift_at"] is not None

    r3 = store.record_verdict("telegraph", store.STATUS_LINK_ALIVE)
    assert r3["consecutive_failures"] == 0
    assert r3["last_ok_at"] is not None
    # Prior drift timestamp is preserved (link-alive only touches last_ok_at).
    assert r3["last_drift_at"] == r2["last_drift_at"]


def test_advisory_preserves_counters_and_timestamps():
    store.record_verdict("ghpages", store.STATUS_DRIFT_CONFIRMED)
    before = store.get_health("ghpages")
    after = store.record_verdict("ghpages", store.STATUS_ADVISORY)
    # advisory is neither OK nor confirmed drift → counters untouched.
    assert after["consecutive_failures"] == before["consecutive_failures"]
    assert after["last_ok_at"] == before["last_ok_at"]
    assert after["last_drift_at"] == before["last_drift_at"]
    assert after["status"] == store.STATUS_ADVISORY


def test_multiple_platforms_keyed_independently():
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    store.record_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    allrecs = store.list_all()
    assert allrecs["blogger"]["status"] == store.STATUS_LINK_ALIVE
    assert allrecs["velog"]["status"] == store.STATUS_DRIFT_CONFIRMED
    assert allrecs["velog"]["consecutive_failures"] == 1


# ── Edge: env re-resolution ──────────────────────────────────────────────


def test_config_dir_change_reresolves_store_path(tmp_path, monkeypatch):
    first = tmp_path / "cfg-a"
    second = tmp_path / "cfg-b"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(first))
    store.canary_health_store.reset()
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    assert (first / "canary-health.json").exists()

    # Flip env (monkeypatch.setenv, NOT del) → path must follow.
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(second))
    store.record_verdict("velog", store.STATUS_DRIFT_CONFIRMED)
    assert (second / "canary-health.json").exists()
    # The blogger write stayed in the first dir; second dir only has velog.
    second_data = json.loads(
        (second / "canary-health.json").read_text(encoding="utf-8")
    )
    assert set(second_data) == {"velog"}


# ── Error: permissions + atomicity ───────────────────────────────────────


def test_health_file_is_0600():
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    mode = stat.S_IMODE(_health_path().stat().st_mode)
    assert mode == 0o600


def test_failed_write_leaves_no_half_file(monkeypatch):
    # Seed a valid record first.
    store.record_verdict("blogger", store.STATUS_LINK_ALIVE)
    good = _health_path().read_text(encoding="utf-8")

    # Simulate an interruption mid-write inside atomic_write's fdopen body.
    import backlink_publisher.persistence.safe_write as sw

    real_fdopen = sw.os.fdopen

    class _Boom:
        def __enter__(self):
            raise OSError("simulated interruption")

        def __exit__(self, *a):
            return False

    def _boom_fdopen(*a, **k):
        # Close the real fd to avoid a leak, then blow up on context enter.
        fd = a[0]
        try:
            import os as _os

            _os.close(fd)
        except OSError:
            pass
        return _Boom()

    monkeypatch.setattr(sw.os, "fdopen", _boom_fdopen)
    with pytest.raises(OSError):
        store.record_verdict("blogger", store.STATUS_DRIFT_CONFIRMED)
    monkeypatch.setattr(sw.os, "fdopen", real_fdopen)

    # Original file is intact; no leftover temp sibling.
    assert _health_path().read_text(encoding="utf-8") == good
    leftovers = [
        p
        for p in _health_path().parent.iterdir()
        if p.name.startswith("canary-health.json.") and p != _health_path()
    ]
    assert leftovers == []


# ── Edge: [canary.<platform>] config round-trip ──────────────────────────


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_read_canary_config_round_trips(tmp_path):
    cfg = _write_config(
        tmp_path,
        "\n".join(
            [
                '[canary.blogger]',
                'post_url = "https://canary.blogspot.com/p.html"',
                'expected_target = "https://example.com/"',
                'hard_skip = true',
            ]
        )
        + "\n",
    )
    entry = store.read_canary_config("blogger", config_path=cfg)
    assert entry == {
        "post_url": "https://canary.blogspot.com/p.html",
        "expected_target": "https://example.com/",
        "hard_skip": True,
    }


def test_read_canary_config_hard_skip_defaults_false(tmp_path):
    cfg = _write_config(
        tmp_path,
        "\n".join(
            [
                '[canary.velog]',
                'post_url = "https://velog.io/@x/p"',
                'expected_target = "https://example.com/"',
            ]
        )
        + "\n",
    )
    entry = store.read_canary_config("velog", config_path=cfg)
    assert entry is not None
    assert entry["hard_skip"] is False


def test_read_canary_config_missing_platform_returns_none(tmp_path):
    cfg = _write_config(
        tmp_path,
        '[canary.blogger]\npost_url = "https://x"\nexpected_target = "https://y"\n',
    )
    assert store.read_canary_config("telegraph", config_path=cfg) is None


def test_read_canary_config_no_file_returns_none(tmp_path):
    assert (
        store.read_canary_config("blogger", config_path=tmp_path / "nope.toml")
        is None
    )


def test_read_canary_config_honors_env_config_dir(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    _write_config(
        cfg_dir,
        '[canary.ghpages]\npost_url = "https://gh.io/p"\n'
        'expected_target = "https://example.com/"\n',
    )
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg_dir))
    entry = store.read_canary_config("ghpages")
    assert entry is not None
    assert entry["post_url"] == "https://gh.io/p"
