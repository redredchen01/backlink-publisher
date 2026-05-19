"""Plan 2026-05-19-006 Unit 3 — draft bulk-operation routes."""

from __future__ import annotations

import pytest
from werkzeug.datastructures import MultiDict

from webui_store import drafts_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts_store, "_path", tmp_path / "drafts.json")
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


@pytest.fixture
def isolated_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts_store, "_path", tmp_path / "drafts.json")
    return drafts_store


def _seed_drafts(items):
    drafts_store.save(items)


class TestDraftBulkDelete:
    def test_removes_selected(self, client, isolated_drafts):
        _seed_drafts([
            {"id": "a", "status": "pending"},
            {"id": "b", "status": "pending"},
            {"id": "c", "status": "pending"},
        ])
        resp = client.post(
            "/ce:draft/bulk-delete",
            data=MultiDict([("ids", "a"), ("ids", "c")]),
        )
        assert resp.status_code == 302
        # Flask URL-encodes the Chinese flash_msg
        from urllib.parse import unquote
        assert "已删除 2 项" in unquote(resp.location)
        assert [it["id"] for it in isolated_drafts.load()] == ["b"]

    def test_empty_ids_returns_warning(self, client, isolated_drafts):
        _seed_drafts([{"id": "a"}])
        resp = client.post("/ce:draft/bulk-delete", data={})
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.location
        assert len(isolated_drafts.load()) == 1

    def test_unknown_ids_are_silently_ignored(self, client, isolated_drafts):
        _seed_drafts([{"id": "a"}])
        resp = client.post(
            "/ce:draft/bulk-delete",
            data=MultiDict([("ids", "zzz"), ("ids", "yyy")]),
        )
        assert resp.status_code == 302
        assert len(isolated_drafts.load()) == 1  # 'a' still there

    def test_scheduled_drafts_also_get_job_removed(self, client, isolated_drafts):
        """bulk-delete must call remove_job for each id (catches JobLookupError silently)."""
        _seed_drafts([
            {"id": "a", "status": "scheduled"},
            {"id": "b", "status": "pending"},
        ])
        # No job is actually scheduled in the test scheduler — call should
        # silently succeed because remove_job raises and is caught.
        resp = client.post(
            "/ce:draft/bulk-delete",
            data=MultiDict([("ids", "a"), ("ids", "b")]),
        )
        assert resp.status_code == 302
        assert isolated_drafts.load() == []


class TestDraftBulkCancel:
    def test_only_scheduled_drafts_change_state(self, client, isolated_drafts):
        _seed_drafts([
            {"id": "a", "status": "scheduled", "scheduled_at": "2099-01-01T00:00:00"},
            {"id": "b", "status": "pending"},
            {"id": "c", "status": "published"},
        ])
        resp = client.post(
            "/ce:draft/bulk-cancel",
            data=MultiDict([("ids", "a"), ("ids", "b"), ("ids", "c")]),
        )
        assert resp.status_code == 302
        items = {it["id"]: it for it in isolated_drafts.load()}
        assert items["a"]["status"] == "pending"
        assert items["a"]["scheduled_at"] is None
        assert items["b"]["status"] == "pending"  # unchanged
        assert items["c"]["status"] == "published"  # unchanged

    def test_empty_ids(self, client, isolated_drafts):
        _seed_drafts([{"id": "a", "status": "scheduled"}])
        resp = client.post("/ce:draft/bulk-cancel", data={})
        assert "flash_type=warning" in resp.location


class TestDraftBulkPublishNow:
    def test_schedules_each_with_5s_stagger(self, client, isolated_drafts):
        _seed_drafts([
            {"id": "a", "status": "pending", "plans_jsonl": "{}", "platform": "medium"},
            {"id": "b", "status": "pending", "plans_jsonl": "{}", "platform": "medium"},
            {"id": "c", "status": "pending", "plans_jsonl": "{}", "platform": "medium"},
        ])
        resp = client.post(
            "/ce:draft/bulk-publish-now",
            data=MultiDict([("ids", "a"), ("ids", "b"), ("ids", "c")]),
        )
        assert resp.status_code == 302
        items = {it["id"]: it for it in isolated_drafts.load()}
        assert items["a"]["status"] == "scheduled"
        assert items["b"]["status"] == "scheduled"
        assert items["c"]["status"] == "scheduled"
        # The three scheduled_at values must be distinct (stagger applied)
        ts = {items[k]["scheduled_at"] for k in ("a", "b", "c")}
        assert len(ts) == 3

    def test_missing_ids_skipped(self, client, isolated_drafts):
        _seed_drafts([{"id": "a", "status": "pending", "plans_jsonl": "{}", "platform": "medium"}])
        resp = client.post(
            "/ce:draft/bulk-publish-now",
            data=MultiDict([("ids", "a"), ("ids", "zzz")]),
        )
        assert resp.status_code == 302
        assert isolated_drafts.load()[0]["status"] == "scheduled"
        # only 1 was actually scheduled
        assert "1" in resp.location

    def test_empty_ids(self, client, isolated_drafts):
        resp = client.post("/ce:draft/bulk-publish-now", data={})
        assert "flash_type=warning" in resp.location
