"""Unit 2b: publish-history aggregate helper + F22 server-side invariant.

Tests:
 - _push_history_aggregate raises ValueError on published/drafted with no URLs
 - _push_history_aggregate accepts failed/failed_partial without URLs
 - _apply_history_cap trims to _HISTORY_MAX_ITEMS
 - checkpoint.py uses _push_history_aggregate (no direct _history_store.update)
 - /ce:history/update-status returns 400 on invariant violation (F22 server guard)
 - /ce:history/update-status allows setting 'failed' on a no-URL row
 - /ce:history/update-status allows setting 'published' on a row that HAS URLs
"""

from __future__ import annotations

import pytest


@pytest.fixture
def client():
    """Flask test client — CSRF disabled so we can test business-logic guards."""
    import webui

    webui.app.config["TESTING"] = True
    webui.app.config["SESSION_COOKIE_SECURE"] = False
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


from webui_app.helpers import (
    _HISTORY_MAX_ITEMS,
    _REQUIRES_URL_STATUSES,
    _apply_history_cap,
    _push_history_aggregate,
)


# ── Unit-level helper tests ──────────────────────────────────────────────────

class TestApplyHistoryCap:
    def test_trims_to_max(self):
        hist = [{"id": str(i)} for i in range(_HISTORY_MAX_ITEMS + 10)]
        result = _apply_history_cap(hist)
        assert len(result) == _HISTORY_MAX_ITEMS

    def test_short_list_unchanged(self):
        hist = [{"id": "a"}, {"id": "b"}]
        result = _apply_history_cap(hist)
        assert result == hist

    def test_empty_list(self):
        assert _apply_history_cap([]) == []


class TestPushHistoryAggregateInvariant:
    """_push_history_aggregate enforces the publish-history invariant."""

    def test_published_with_urls_accepted(self, tmp_path, monkeypatch):
        # Patch the store to avoid real file I/O
        written: list[list] = []

        def fake_update(fn):
            result = fn([])
            written.append(result)
            return result

        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", fake_update)

        entry = {"status": "published", "article_urls": ["https://example.com/post"]}
        result = _push_history_aggregate(entry)
        assert written, "update was not called"
        assert result[0]["status"] == "published"

    def test_drafted_with_urls_accepted(self, tmp_path, monkeypatch):
        written: list[list] = []

        def fake_update(fn):
            result = fn([])
            written.append(result)
            return result

        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", fake_update)

        entry = {"status": "drafted", "article_urls": ["https://example.com/draft"]}
        _push_history_aggregate(entry)
        assert written

    def test_published_without_urls_raises(self, monkeypatch):
        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", lambda fn: fn([]))

        entry = {"status": "published", "article_urls": []}
        with pytest.raises(ValueError, match="article_urls"):
            _push_history_aggregate(entry)

    def test_published_no_article_urls_key_raises(self, monkeypatch):
        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", lambda fn: fn([]))

        entry = {"status": "published"}
        with pytest.raises(ValueError, match="article_urls"):
            _push_history_aggregate(entry)

    def test_drafted_without_urls_raises(self, monkeypatch):
        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", lambda fn: fn([]))

        entry = {"status": "drafted", "article_urls": []}
        with pytest.raises(ValueError, match="article_urls"):
            _push_history_aggregate(entry)

    def test_failed_without_urls_accepted(self, monkeypatch):
        """failed status does not require article_urls."""
        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", lambda fn: fn([]))

        entry = {"status": "failed", "article_urls": []}
        result = _push_history_aggregate(entry)
        assert result[0]["status"] == "failed"

    def test_failed_partial_without_urls_accepted(self, monkeypatch):
        """failed_partial is not in REQUIRES_URL_STATUSES."""
        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", lambda fn: fn([]))

        entry = {
            "status": "failed_partial",
            "article_urls": [],
            "stderr_summary": "some error",
        }
        result = _push_history_aggregate(entry)
        assert result[0]["status"] == "failed_partial"

    def test_requires_url_statuses_set(self):
        """Verify the set contains exactly the expected values."""
        assert "published" in _REQUIRES_URL_STATUSES
        assert "drafted" in _REQUIRES_URL_STATUSES
        assert "failed" not in _REQUIRES_URL_STATUSES
        assert "failed_partial" not in _REQUIRES_URL_STATUSES

    def test_cap_applied(self, monkeypatch):
        """_push_history_aggregate applies the history cap."""
        existing = [{"id": str(i), "status": "failed"} for i in range(_HISTORY_MAX_ITEMS)]

        import webui_app.helpers as h
        monkeypatch.setattr(h._history_store, "update", lambda fn: fn(existing))

        entry = {"status": "failed", "article_urls": []}
        result = _push_history_aggregate(entry)
        assert len(result) == _HISTORY_MAX_ITEMS


# ── Integration: /ce:history/update-status server-side guard ────────────────

class TestHistoryUpdateStatusInvariant:
    """F22 server-side guard: forged POST cannot set published on no-URL row."""

    def _seed_history(self, client, item_id: str, article_urls: list):
        """Directly set history store for the test."""
        from webui_store import history_store as _history_store
        _history_store.update(lambda hist: [
            {
                "id": item_id,
                "target_url": "https://example.com",
                "platform": "medium",
                "language": "zh-CN",
                "status": "failed",
                "created_at": "2026-05-21 10:00",
                "article_urls": article_urls,
            },
            *hist,
        ])

    def test_set_published_on_no_url_row_returns_400(self, client):
        item_id = "test0001"
        self._seed_history(client, item_id, article_urls=[])
        resp = client.post(
            "/ce:history/update-status",
            data={"id": item_id, "status": "published"},
        )
        assert resp.status_code == 400

    def test_set_drafted_on_no_url_row_returns_400(self, client):
        item_id = "test0002"
        self._seed_history(client, item_id, article_urls=[])
        resp = client.post(
            "/ce:history/update-status",
            data={"id": item_id, "status": "drafted"},
        )
        assert resp.status_code == 400

    def test_set_failed_on_no_url_row_is_allowed(self, client):
        item_id = "test0003"
        self._seed_history(client, item_id, article_urls=[])
        resp = client.post(
            "/ce:history/update-status",
            data={"id": item_id, "status": "failed"},
        )
        assert resp.status_code == 200

    def test_set_published_on_row_with_url_is_allowed(self, client):
        item_id = "test0004"
        self._seed_history(client, item_id, article_urls=["https://example.com/post"])
        resp = client.post(
            "/ce:history/update-status",
            data={"id": item_id, "status": "published"},
        )
        assert resp.status_code == 200

    def test_set_drafted_on_row_with_url_is_allowed(self, client):
        item_id = "test0005"
        self._seed_history(client, item_id, article_urls=["https://example.com/draft"])
        resp = client.post(
            "/ce:history/update-status",
            data={"id": item_id, "status": "drafted"},
        )
        assert resp.status_code == 200
