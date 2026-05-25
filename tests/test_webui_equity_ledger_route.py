"""Unit 5 — equity-ledger WebUI page (GET, read-only)."""

import json

import pytest

from backlink_publisher.events import EventStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


def _seed():
    EventStore().add_article({
        "target_urls_json": json.dumps(["https://site.com/p"]),
        "live_url": "https://medium.com/post1",
    })
    from webui_store import history_store
    history_store.save([{
        "id": "h1", "platform": "medium", "target_url": "https://site.com/p",
        "article_urls": ["https://medium.com/post1"], "status": "published",
    }])


def test_page_renders_with_row(client):
    _seed()
    resp = client.get("/ce:equity-ledger")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Backlink Equity Ledger" in html
    # The row reaches the embedded JSON the table renders from.
    assert "https://site.com/p" in html


def test_empty_state_embeds_no_rows(client):
    resp = client.get("/ce:equity-ledger")
    assert resp.status_code == 200
    assert "const ROWS = []" in resp.get_data(as_text=True)


def test_stale_days_query_param_reflected(client):
    _seed()
    resp = client.get("/ce:equity-ledger?stale_days=7")
    assert resp.status_code == 200
    assert "stale &gt; 7d" in resp.get_data(as_text=True)


def test_matches_cli_engine_for_same_fixture(client, monkeypatch):
    # Engine parity: the row count/target the page renders equals build_ledger().
    _seed()
    from backlink_publisher.ledger import build_ledger
    engine_rows = build_ledger()
    resp = client.get("/ce:equity-ledger")
    html = resp.get_data(as_text=True)
    assert len(engine_rows) == 1
    assert engine_rows[0].target_url in html
