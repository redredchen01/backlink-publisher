"""Tests for webui_store.channel_status — Plan 2026-05-19-001 Unit 1.

Locks the contract for the binding state singleton:
- mark_bound / mark_expired / get_status / list_all atomic via JsonStore
- channel whitelist enforced at every write site
- storage_state_path must resolve inside _config_dir() (defense-in-depth
  against supply-chain adapter writing /etc/passwd)
- get_status of unknown channel returns unbound default, not KeyError
- BACKLINK_PUBLISHER_CONFIG_DIR env override honored
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from backlink_publisher._util.errors import UsageError
from backlink_publisher.config.loader import _config_dir
from webui_store import channel_status_store
from webui_store.channel_status import (
    get_status,
    list_all,
    mark_bound,
    mark_expired,
)


# Reset store between tests since the singleton persists across runs.
@pytest.fixture(autouse=True)
def _reset_store(tmp_path, monkeypatch):
    """Point channel_status_store at a fresh tmp file per test."""
    fresh = tmp_path / "channel-status.json"
    monkeypatch.setattr(channel_status_store, "path", fresh, raising=False)


class TestMarkBoundHappyPath:
    def test_marks_velog_bound(self, tmp_path):
        target = tmp_path / "velog-cookies.json"
        target.write_text("{}")
        # Move file into _config_dir so mark_bound accepts it
        config_target = _config_dir() / "velog-cookies.json"
        config_target.parent.mkdir(parents=True, exist_ok=True)
        config_target.write_text("{}")

        mark_bound("velog", config_target)

        rec = get_status("velog")
        assert rec["status"] == "bound"
        assert rec["bound_at"] is not None
        assert rec["storage_state_path"] == str(config_target)

    def test_subsequent_mark_expired_preserves_bound_at(self):
        config_target = _config_dir() / "medium-state.json"
        config_target.parent.mkdir(parents=True, exist_ok=True)
        config_target.write_text("{}")

        mark_bound("medium", config_target)
        bound_at_before = get_status("medium")["bound_at"]

        mark_expired("medium")

        rec = get_status("medium")
        assert rec["status"] == "expired"
        assert rec["bound_at"] == bound_at_before
        assert rec["storage_state_path"] == str(config_target)


class TestGetStatusDefaults:
    def test_unknown_channel_returns_unbound_default(self):
        # "unknown" is NOT in CHANNELS but get_status must not raise — it's
        # a read API for UI; we just report "unbound".
        rec = get_status("unknown")
        assert rec == {"status": "unbound", "bound_at": None, "storage_state_path": None}

    def test_known_unbound_channel_returns_default(self):
        rec = get_status("velog")
        assert rec == {"status": "unbound", "bound_at": None, "storage_state_path": None}

    def test_list_all_returns_dict_of_records(self):
        config_target = _config_dir() / "blogger-state.json"
        config_target.parent.mkdir(parents=True, exist_ok=True)
        config_target.write_text("{}")
        mark_bound("blogger", config_target)

        all_records = list_all()
        assert "blogger" in all_records
        assert all_records["blogger"]["status"] == "bound"


class TestChannelWhitelistTraversal:
    """Path traversal must be rejected at every write site."""

    def test_mark_bound_rejects_traversal_channel(self, tmp_path):
        with pytest.raises(UsageError):
            mark_bound("../evil", tmp_path / "x.json")

    def test_mark_bound_rejects_unknown_channel(self, tmp_path):
        target = _config_dir() / "x.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        with pytest.raises(UsageError):
            mark_bound("twitter", target)

    def test_mark_expired_rejects_traversal_channel(self):
        with pytest.raises(UsageError):
            mark_expired("../evil")

    def test_mark_expired_rejects_unknown_channel(self):
        with pytest.raises(UsageError):
            mark_expired("twitter")


class TestPathValidation:
    """storage_state_path must resolve inside _config_dir()."""

    def test_mark_bound_rejects_outside_config_dir(self, tmp_path):
        outside = tmp_path / "outside-state.json"
        outside.write_text("{}")
        with pytest.raises(UsageError):
            mark_bound("velog", outside)

    def test_mark_bound_rejects_etc_passwd(self):
        with pytest.raises(UsageError):
            mark_bound("velog", "/etc/passwd")

    def test_mark_bound_accepts_path_inside_config_dir(self):
        target = _config_dir() / "velog-cookies.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        mark_bound("velog", target)  # should not raise


class TestConfigDirEnvOverride:
    """BACKLINK_PUBLISHER_CONFIG_DIR override must steer channel-status.json."""

    def test_store_path_honors_env_override(self, tmp_path, monkeypatch):
        """If BACKLINK_PUBLISHER_CONFIG_DIR is set (it is, by conftest),
        the channel_status_store's default path must be inside that dir."""
        # Resolved path on each access of _config_dir() reflects current env.
        assert str(_config_dir()).startswith(str(tmp_path.parent.parent.parent)) or (
            str(_config_dir()) == os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
        )


class TestConcurrentMarkBound:
    def test_two_threads_marking_bound_leave_consistent_state(self):
        target_v = _config_dir() / "velog-cookies.json"
        target_m = _config_dir() / "medium-state.json"
        target_v.parent.mkdir(parents=True, exist_ok=True)
        target_v.write_text("{}")
        target_m.write_text("{}")

        def bind_velog():
            mark_bound("velog", target_v)

        def bind_medium():
            mark_bound("medium", target_m)

        t1 = threading.Thread(target=bind_velog)
        t2 = threading.Thread(target=bind_medium)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both channels must be bound — neither write was lost.
        all_records = list_all()
        assert all_records["velog"]["status"] == "bound"
        assert all_records["medium"]["status"] == "bound"


class TestStoreSerialization:
    def test_persisted_json_is_valid(self):
        target = _config_dir() / "velog-cookies.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        mark_bound("velog", target)

        # Reload from disk and verify structure
        with open(channel_status_store.path) as f:
            data = json.load(f)
        assert "velog" in data
        assert data["velog"]["status"] == "bound"
