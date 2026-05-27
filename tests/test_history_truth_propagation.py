"""Plan 2026-05-19-006 Unit 1 — history truth propagation.

Lock the contract: ``_push_history_per_row`` writes one history entry per
CLI publish-result row, transparently carrying the per-row ``status``
(including ``*_unverified`` suffixes) and synthesising ``failed`` when an
adapter returns no URL.
"""

from __future__ import annotations


import pytest

from webui_store import history_store


@pytest.fixture
def isolated_history_store(tmp_path, monkeypatch):
    """Rebind history_store.path to a tmp file so tests don't touch the
    user's ~/.config/backlink-publisher/publish-history.json."""
    test_path = tmp_path / "history.json"
    monkeypatch.setattr(history_store, "_path", test_path)
    # Also rebind webui_app.helpers._history_store reference if needed
    yield history_store


# ── _push_history_per_row happy paths ────────────────────────────────────────


class TestPushHistoryPerRow:
    def test_writes_one_entry_per_row(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row
        rows = [
            {
                "status": "published",
                "target_url": "https://a.example/",
                "platform": "medium",
                "title": "T1",
                "published_url": "https://medium.com/p/abc",
                "draft_url": "",
                "error": None,
            },
            {
                "status": "drafted_unverified",
                "target_url": "https://b.example/",
                "platform": "medium",
                "title": "T2",
                "published_url": "",
                "draft_url": "https://medium.com/p/def-draft",
                "error": None,
            },
        ]
        _push_history_per_row(rows, target_url_fallback="x", platform_fallback="y", language_fallback="zh-CN")
        history = isolated_history_store.load()
        assert len(history) == 2
        # Insertion order is preserved (matches CLI stdout order — both rows
        # belong to one publish batch, no notion of "newer within the batch")
        assert history[0]["status"] == "published"
        assert history[0]["title"] == "T1"
        assert history[0]["article_urls"] == ["https://medium.com/p/abc"]
        assert history[1]["status"] == "drafted_unverified"
        assert history[1]["title"] == "T2"
        assert history[1]["article_urls"] == ["https://medium.com/p/def-draft"]

    def test_preserves_unverified_suffix(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row
        rows = [{
            "status": "published_unverified",
            "target_url": "https://x.example/",
            "title": "T",
            "published_url": "https://medium.com/p/zzz",
            "draft_url": "",
            "error": None,
        }]
        _push_history_per_row(rows)
        assert isolated_history_store.load()[0]["status"] == "published_unverified"

    def test_empty_urls_with_no_error_coerces_to_failed(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row
        rows = [{
            "status": "drafted",
            "target_url": "https://x.example/",
            "title": "T",
            "published_url": "",
            "draft_url": "",
            "error": None,
        }]
        _push_history_per_row(rows)
        item = isolated_history_store.load()[0]
        assert item["status"] == "failed"
        assert item["error"] == "no URL returned by adapter"
        assert item["article_urls"] == []

    def test_unverified_with_empty_urls_stays_unverified(self, isolated_history_store):
        # An `_unverified` row with empty URLs is still informative — the
        # adapter at least *tried*. Don't downgrade to failed.
        from webui_app.helpers.history import _push_history_per_row
        rows = [{
            "status": "published_unverified",
            "target_url": "https://x.example/",
            "title": "T",
            "published_url": "",
            "draft_url": "",
            "error": None,
        }]
        _push_history_per_row(rows)
        assert isolated_history_store.load()[0]["status"] == "published_unverified"

    def test_falls_back_to_provided_target_url(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row
        rows = [{
            "status": "published",
            # no target_url in the row
            "title": "T",
            "published_url": "https://medium.com/p/x",
            "error": None,
        }]
        _push_history_per_row(rows, target_url_fallback="https://fallback.example/")
        assert isolated_history_store.load()[0]["target_url"] == "https://fallback.example/"

    def test_empty_rows_is_noop(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row
        isolated_history_store.save([{"id": "preexisting"}])
        result = _push_history_per_row([])
        # Existing items untouched, no new items prepended
        assert [it.get("id") for it in result] == ["preexisting"]

    def test_carries_adapter_field(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row
        rows = [{
            "status": "published",
            "title": "T",
            "adapter": "medium-api",
            "published_url": "https://medium.com/p/x",
            "error": None,
        }]
        _push_history_per_row(rows)
        assert isolated_history_store.load()[0]["adapter"] == "medium-api"

    def test_error_row_preserved(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row
        # publish-backlinks only sends successful rows to stdout, but
        # defensively a row with error should be carried as-is.
        rows = [{
            "status": "failed",
            "title": "T",
            "published_url": "",
            "error": "service error: 503 from medium",
        }]
        _push_history_per_row(rows)
        item = isolated_history_store.load()[0]
        assert item["status"] == "failed"
        assert item["error"] == "service error: 503 from medium"

    def test_truncates_to_max_items(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_per_row, _HISTORY_MAX_ITEMS
        # Pre-populate at limit
        isolated_history_store.save([
            {"id": f"old{n}"} for n in range(_HISTORY_MAX_ITEMS)
        ])
        new_rows = [{
            "status": "published",
            "title": "T",
            "published_url": f"https://x/{n}",
            "error": None,
        } for n in range(3)]
        result = _push_history_per_row(new_rows)
        assert len(result) == _HISTORY_MAX_ITEMS
        # New rows at the front
        assert all("status" in result[i] and result[i]["status"] == "published" for i in range(3))


class TestPushHistorySingleFailure:
    def test_writes_one_failed_entry(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_single_failure
        _push_history_single_failure(
            target_url="https://x.example/",
            platform="medium",
            language="zh-CN",
            error="boom",
        )
        items = isolated_history_store.load()
        assert len(items) == 1
        assert items[0]["status"] == "failed"
        assert items[0]["error"] == "boom"
        assert items[0]["article_urls"] == []

    def test_uses_default_error_when_blank(self, isolated_history_store):
        from webui_app.helpers.history import _push_history_single_failure
        _push_history_single_failure(
            target_url="https://x.example/", platform="", language="", error="",
        )
        assert isolated_history_store.load()[0]["error"] == "publish failed"
