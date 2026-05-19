"""Tests for channel_status.reconcile_on_load — Plan 2026-05-19-001 Unit 1.

Locks the contract:
- On startup-time call (from webui_app.create_app), every record with
  status=bound is checked against its storage_state_path; missing files
  demote status=expired while preserving bound_at + path (UX hint).
- Idempotent: second call sees already-expired records and does not
  re-modify them.
- Records with status != bound are untouched.
"""

from __future__ import annotations

import pytest

from backlink_publisher.config.loader import _config_dir
from webui_store import channel_status_store
from webui_store.channel_status import (
    get_status,
    mark_bound,
    mark_expired,
    reconcile_on_load,
)


@pytest.fixture(autouse=True)
def _reset_store(tmp_path, monkeypatch):
    fresh = tmp_path / "channel-status.json"
    monkeypatch.setattr(channel_status_store, "path", fresh, raising=False)


class TestReconcileMissingFile:
    def test_bound_record_with_missing_file_demotes_to_expired(self):
        target = _config_dir() / "velog-cookies.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        mark_bound("velog", target)
        bound_at_before = get_status("velog")["bound_at"]

        # Simulate file disappearing
        target.unlink()
        reconcile_on_load()

        rec = get_status("velog")
        assert rec["status"] == "expired"
        # bound_at + path preserved for UX ("last bound at YYYY-MM-DD")
        assert rec["bound_at"] == bound_at_before
        assert rec["storage_state_path"] == str(target)


class TestReconcileFileExists:
    def test_bound_record_with_existing_file_unchanged(self):
        target = _config_dir() / "medium-state.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        mark_bound("medium", target)
        bound_at_before = get_status("medium")["bound_at"]

        reconcile_on_load()

        rec = get_status("medium")
        assert rec["status"] == "bound"
        assert rec["bound_at"] == bound_at_before


class TestReconcileNonBoundRecords:
    def test_expired_record_untouched(self):
        target = _config_dir() / "blogger-state.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        mark_bound("blogger", target)
        mark_expired("blogger")

        # Now also remove the file; reconcile should still leave it expired
        # (no transition needed, idempotent)
        target.unlink()
        reconcile_on_load()

        rec = get_status("blogger")
        assert rec["status"] == "expired"


class TestReconcileEmptyStore:
    def test_empty_store_no_op(self):
        # Just verify no crash on empty store
        reconcile_on_load()


class TestReconcileIdempotent:
    def test_double_call_no_extra_state_change(self):
        target = _config_dir() / "velog-cookies.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        mark_bound("velog", target)
        target.unlink()

        reconcile_on_load()  # demotes to expired
        rec_after_first = get_status("velog")

        reconcile_on_load()  # idempotent
        rec_after_second = get_status("velog")

        assert rec_after_first == rec_after_second
