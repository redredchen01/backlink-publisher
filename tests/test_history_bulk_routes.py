"""Plan 2026-05-19-006 Unit 4 — history bulk-delete + purge-failed routes."""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from werkzeug.datastructures import MultiDict

from webui_store import history_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_path", tmp_path / "history.json")
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_path", tmp_path / "history.json")
    return history_store


class TestHistoryBulkDelete:
    def test_removes_selected(self, client, isolated_history):
        isolated_history.save([
            {"id": "a", "status": "published"},
            {"id": "b", "status": "failed"},
            {"id": "c", "status": "published"},
        ])
        resp = client.post(
            "/ce:history/bulk-delete",
            data=MultiDict([("ids", "a"), ("ids", "c")]),
        )
        assert resp.status_code == 302
        assert "已删除 2 条" in unquote(resp.location)
        assert [it["id"] for it in isolated_history.load()] == ["b"]

    def test_empty_ids_returns_warning(self, client, isolated_history):
        isolated_history.save([{"id": "a"}])
        resp = client.post("/ce:history/bulk-delete", data={})
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.location
        assert len(isolated_history.load()) == 1

    def test_unknown_ids_silently_ignored(self, client, isolated_history):
        isolated_history.save([{"id": "a"}])
        resp = client.post(
            "/ce:history/bulk-delete",
            data=MultiDict([("ids", "zzz")]),
        )
        assert resp.status_code == 302
        assert "已删除 0 条" in unquote(resp.location)


class TestHistoryPurgeFailed:
    def test_removes_only_failed(self, client, isolated_history):
        isolated_history.save([
            {"id": "a", "status": "failed"},
            {"id": "b", "status": "published"},
            {"id": "c", "status": "failed"},
            {"id": "d", "status": "published_unverified"},
        ])
        resp = client.post("/ce:history/purge-failed", data={})
        assert resp.status_code == 302
        assert "已清除 2 条" in unquote(resp.location)
        remaining_ids = {it["id"] for it in isolated_history.load()}
        assert remaining_ids == {"b", "d"}

    def test_no_failures_returns_info(self, client, isolated_history):
        isolated_history.save([{"id": "a", "status": "published"}])
        resp = client.post("/ce:history/purge-failed", data={})
        assert resp.status_code == 302
        assert "flash_type=info" in resp.location
        assert "没有失败记录可清除" in unquote(resp.location)
        assert len(isolated_history.load()) == 1

    def test_does_not_touch_unverified(self, client, isolated_history):
        """purge-failed must not delete `published_unverified` — those need
        the user's recheck, not a silent drop."""
        isolated_history.save([
            {"id": "a", "status": "published_unverified"},
            {"id": "b", "status": "drafted_unverified"},
            {"id": "c", "status": "failed"},
        ])
        resp = client.post("/ce:history/purge-failed", data={})
        assert resp.status_code == 302
        assert "已清除 1 条" in unquote(resp.location)
        remaining = {it["id"] for it in isolated_history.load()}
        assert remaining == {"a", "b"}
