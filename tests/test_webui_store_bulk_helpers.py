"""Tests for DraftsStore / HistoryStore bulk helpers — Plan 2026-05-19-006 Unit 2."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from webui_store import DraftsStore, HistoryStore


# ── DraftsStore bulk_delete / bulk_update ────────────────────────────────────


class TestDraftsStoreBulkDelete:
    def test_removes_only_matching_ids(self, tmp_path):
        store = DraftsStore(tmp_path / "drafts.json")
        store.save([{"id": "a"}, {"id": "b"}, {"id": "c"}])
        assert store.bulk_delete(["a", "c"]) == 2
        assert [it["id"] for it in store.load()] == ["b"]

    def test_empty_ids_is_noop(self, tmp_path):
        store = DraftsStore(tmp_path / "drafts.json")
        store.save([{"id": "a"}])
        assert store.bulk_delete([]) == 0
        assert store.load() == [{"id": "a"}]

    def test_missing_ids_returns_zero(self, tmp_path):
        store = DraftsStore(tmp_path / "drafts.json")
        store.save([{"id": "a"}])
        assert store.bulk_delete(["zzz", "yyy"]) == 0
        assert store.load() == [{"id": "a"}]

    def test_mixed_existing_and_missing(self, tmp_path):
        store = DraftsStore(tmp_path / "drafts.json")
        store.save([{"id": "a"}, {"id": "b"}])
        assert store.bulk_delete(["a", "zzz"]) == 1
        assert [it["id"] for it in store.load()] == ["b"]


class TestDraftsStoreBulkUpdate:
    def test_merges_fields_into_each_match(self, tmp_path):
        store = DraftsStore(tmp_path / "drafts.json")
        store.save([{"id": "a", "status": "pending"}, {"id": "b", "status": "pending"}])
        assert store.bulk_update(["a", "b"], status="scheduled") == 2
        assert all(it["status"] == "scheduled" for it in store.load())

    def test_empty_ids_or_fields_is_noop(self, tmp_path):
        store = DraftsStore(tmp_path / "drafts.json")
        store.save([{"id": "a", "status": "pending"}])
        assert store.bulk_update([], status="scheduled") == 0
        assert store.bulk_update(["a"]) == 0
        assert store.load()[0]["status"] == "pending"

    def test_partial_match(self, tmp_path):
        store = DraftsStore(tmp_path / "drafts.json")
        store.save([{"id": "a"}, {"id": "b"}])
        assert store.bulk_update(["a", "zzz"], status="scheduled") == 1
        loaded = {it["id"]: it for it in store.load()}
        assert loaded["a"]["status"] == "scheduled"
        assert "status" not in loaded["b"]


# ── HistoryStore item helpers ────────────────────────────────────────────────


class TestHistoryStoreItemHelpers:
    def test_get_item_returns_match(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a", "status": "published"}])
        assert store.get_item("a") == {"id": "a", "status": "published"}

    def test_get_item_returns_none_when_absent(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a"}])
        assert store.get_item("zzz") is None

    def test_update_item_merges_fields(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a", "status": "published"}])
        assert store.update_item("a", status="failed", verify_error="404") is True
        assert store.get_item("a") == {
            "id": "a", "status": "failed", "verify_error": "404"
        }

    def test_update_item_returns_false_when_missing(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a"}])
        assert store.update_item("zzz", status="failed") is False

    def test_delete_item_removes(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a"}, {"id": "b"}])
        assert store.delete_item("a") is True
        assert [it["id"] for it in store.load()] == ["b"]

    def test_delete_item_returns_false_when_missing(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a"}])
        assert store.delete_item("zzz") is False


# ── HistoryStore bulk helpers ────────────────────────────────────────────────


class TestHistoryStoreBulkDelete:
    def test_removes_matching(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a"}, {"id": "b"}, {"id": "c"}])
        assert store.bulk_delete(["a", "c"]) == 2
        assert [it["id"] for it in store.load()] == ["b"]

    def test_empty_ids_is_noop(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a"}])
        assert store.bulk_delete([]) == 0


class TestHistoryStoreBulkUpdate:
    def test_merges_fields(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([
            {"id": "a", "status": "published_unverified"},
            {"id": "b", "status": "published_unverified"},
        ])
        n = store.bulk_update(["a", "b"], status="failed", verify_error="404")
        assert n == 2
        loaded = {it["id"]: it for it in store.load()}
        assert all(it["status"] == "failed" for it in loaded.values())
        assert all(it["verify_error"] == "404" for it in loaded.values())


class TestHistoryStorePurgeByStatus:
    def test_removes_all_with_matching_status(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([
            {"id": "a", "status": "failed"},
            {"id": "b", "status": "published"},
            {"id": "c", "status": "failed"},
        ])
        assert store.purge_by_status("failed") == 2
        assert [it["id"] for it in store.load()] == ["b"]

    def test_no_match_returns_zero(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a", "status": "published"}])
        assert store.purge_by_status("failed") == 0
        assert store.load() == [{"id": "a", "status": "published"}]

    def test_empty_status_is_noop(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a", "status": "failed"}])
        assert store.purge_by_status("") == 0


# ── Concurrency: bulk operations under lock ─────────────────────────────────


class TestBulkConcurrency:
    def test_bulk_delete_during_concurrent_update_item_does_not_lose_writes(self, tmp_path):
        """Locks ensure either the bulk_delete sees the prior update_item
        or vice versa — but never a torn write."""
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": f"i{n}", "status": "pending"} for n in range(20)])

        barrier = threading.Barrier(2)
        results = []

        def t_delete():
            barrier.wait()
            results.append(("del", store.bulk_delete([f"i{n}" for n in range(10)])))

        def t_update():
            barrier.wait()
            # Updating an item that may or may not still exist
            results.append(("upd", store.update_item("i15", status="done")))

        threads = [threading.Thread(target=t_delete), threading.Thread(target=t_update)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        remaining = store.load()
        # 10 items deleted from indices 0-9; i15 should still exist
        assert {it["id"] for it in remaining} == {f"i{n}" for n in range(10, 20)}
        # i15 was updated successfully
        i15 = next(it for it in remaining if it["id"] == "i15")
        assert i15["status"] == "done"


# ── Backward compatibility: history_store still exposes Store API ────────────


class TestHistoryStoreIsJsonStore:
    def test_load_save_update_inherited(self, tmp_path):
        store = HistoryStore(tmp_path / "history.json")
        store.save([{"id": "a"}])
        assert store.load() == [{"id": "a"}]
        result = store.update(lambda items: [*items, {"id": "b"}])
        assert [it["id"] for it in result] == ["a", "b"]
        assert [it["id"] for it in store.load()] == ["a", "b"]
