"""Plan 2026-05-19-006 Unit 6 — index.html bulk UI rendering."""

from __future__ import annotations

import pytest

from webui_store import drafts_store, history_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts_store, "_path", tmp_path / "drafts.json")
    monkeypatch.setattr(history_store, "_path", tmp_path / "history.json")
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


class TestHistoryUnverifiedRendering:
    def test_unverified_status_chip_present(self, client):
        history_store.save([
            {"id": "a", "status": "published_unverified", "target_url": "https://t/",
             "platform": "medium", "article_urls": ["https://x/"], "created_at": "2026-05-19"},
        ])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        assert resp.status_code == 200
        # The unverified chip should appear in the status filter row
        assert 'data-filter-value="unverified"' in body
        # The item's data-status is normalized to 'unverified'
        assert 'data-status="unverified"' in body
        # The badge text should reflect the truth, not "已发布"
        assert "已发布·未核实" in body

    def test_drafted_unverified_renders_distinctly(self, client):
        history_store.save([
            {"id": "b", "status": "drafted_unverified", "target_url": "https://t/",
             "platform": "medium", "article_urls": ["https://x/"], "created_at": "2026-05-19"},
        ])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        assert "草稿·未核实" in body


class TestBulkActionBarRendering:
    def test_history_bulk_form_and_buttons_present(self, client):
        history_store.save([{"id": "a", "status": "published", "target_url": "https://t/",
                             "article_urls": ["https://x/"], "created_at": "2026-05-19"}])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        assert 'id="historyBulkForm"' in body
        assert 'formaction="/ce:history/bulk-delete"' in body
        assert 'formaction="/ce:history/bulk-recheck"' in body
        assert 'id="historyPurgeForm"' in body
        # Per-item checkbox associated with the bulk form
        assert 'class="form-check-input bulk-select history-bulk-select"' in body
        assert 'form="historyBulkForm"' in body

    def test_draft_bulk_form_and_buttons_present(self, client):
        drafts_store.save([{"id": "d1", "status": "pending", "target_url": "https://t/",
                            "created_at": "2026-05-19", "platform": "medium",
                            "publish_mode": "draft"}])
        resp = client.get("/")
        body = resp.data.decode("utf-8")
        assert 'id="draftBulkForm"' in body
        assert 'formaction="/ce:draft/bulk-delete"' in body
        assert 'formaction="/ce:draft/bulk-publish-now"' in body
        assert 'formaction="/ce:draft/bulk-cancel"' in body
        # Draft item carries its id in a checkbox
        assert 'form="draftBulkForm"' in body
        assert 'value="d1"' in body

    def test_purge_failed_count_in_button(self, client):
        history_store.save([
            {"id": "a", "status": "failed", "target_url": "https://t/", "created_at": "x"},
            {"id": "b", "status": "failed", "target_url": "https://t/", "created_at": "x"},
            {"id": "c", "status": "published", "target_url": "https://t/", "created_at": "x"},
        ])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        # Count rendered into the button text
        assert "一键清失败 (2)" in body

    def test_purge_failed_disabled_when_zero(self, client):
        history_store.save([
            {"id": "a", "status": "published", "target_url": "https://t/", "created_at": "x"},
        ])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        assert "一键清失败 (0)" in body
        # disabled attribute on the button
        assert 'form="historyPurgeForm" class="btn btn-sm btn-warning"\n                                    disabled' in body or \
               'form="historyPurgeForm" class="btn btn-sm btn-warning" disabled' in body


class TestRecheckButtonRendering:
    def test_per_item_recheck_button_present(self, client):
        history_store.save([{"id": "a", "status": "published_unverified",
                             "target_url": "https://t/", "article_urls": ["https://x/"],
                             "created_at": "2026-05-19", "platform": "medium"}])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        assert '/ce:history/recheck' in body
        assert '重新核实' in body

    def test_recheck_button_disabled_when_no_article_urls(self, client):
        history_store.save([{"id": "a", "status": "failed",
                             "target_url": "https://t/", "article_urls": [],
                             "created_at": "2026-05-19", "platform": "medium"}])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        # disabled attribute somewhere in the recheck form
        assert 'disabled title="此记录无文章 URL，无法核实"' in body


class TestVerifyErrorRendering:
    def test_verify_error_shown(self, client):
        history_store.save([{
            "id": "a", "status": "failed",
            "target_url": "https://t/",
            "article_urls": ["https://x/"],
            "verify_error": "HTTP 404",
            "created_at": "2026-05-19",
        }])
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        assert "核实失败：HTTP 404" in body
