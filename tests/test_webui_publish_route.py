"""/ce:publish route — Plan 2026-05-21-004 regression coverage.

Locks the invariant that a false-success banner never reaches the operator
when ``publish-backlinks`` produced no usable URL. Mirrors the pattern of
``test_webui_token_paste.py`` (Flask test client + session_transaction +
``run_pipe`` mocked at the import boundary).
"""

from __future__ import annotations

import json

import pytest

from webui_app import create_app
from webui_store import history_store


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False  # bypass global CSRF guard for route logic test
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded_session(client):
    """Seed `plans` + `config` into the test session — exactly what /ce:generate
    would have written before the operator clicks 发布."""
    plans = json.dumps({"target_url": "https://example.com/work/1", "platform": "blogger"})
    with client.session_transaction() as sess:
        sess["plans"] = plans
        sess["config"] = {
            "target_url": "https://example.com",
            "platform": "blogger",
            "target_language": "zh-CN",
            "publish_mode": "publish",
        }
    return plans


def _capture_logger(monkeypatch):
    """Replace plan_logger.info/.warn with capture lambdas."""
    events: list[tuple[str, str, dict]] = []
    from webui_app.routes import pipeline as pipeline_mod

    def _info(msg, **extra):
        events.append(("info", msg, extra))

    def _warn(msg, **extra):
        events.append(("warn", msg, extra))

    monkeypatch.setattr(pipeline_mod.plan_logger, "info", _info)
    monkeypatch.setattr(pipeline_mod.plan_logger, "warn", _warn)
    return events


def _mock_run_pipe(monkeypatch, *, stdout="", stderr="", raise_exc=None):
    """Patch run_pipe at the route's import site."""
    from webui_app.routes import pipeline as pipeline_mod

    def _fake(cmd, stdin):
        if raise_exc is not None:
            raise raise_exc
        return {"stdout": stdout, "stderr": stderr}

    monkeypatch.setattr(pipeline_mod, "run_pipe", _fake)


def _rows_jsonl(*rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


# ──────────────────────────────────────────────────────────────────────────────
# Happy paths
# ──────────────────────────────────────────────────────────────────────────────


def test_all_published_renders_green_banner(client, seeded_session, monkeypatch):
    rows = _rows_jsonl(
        {"target_url": "https://example.com/a", "title": "A", "status": "published",
         "published_url": "https://blogger.example/a"},
        {"target_url": "https://example.com/b", "title": "B", "status": "published",
         "published_url": "https://blogger.example/b"},
    )
    _mock_run_pipe(monkeypatch, stdout=rows)
    events = _capture_logger(monkeypatch)

    resp = client.post("/ce:publish", data={
        "platform": "blogger", "publish_mode": "publish",
    })
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "发布成功！" in body
    assert "发布失败" not in body
    assert "部分发布成功" not in body

    hist = history_store.load()
    published_hist = [h for h in hist if h.get("status") == "published"]
    assert len(published_hist) >= 2
    assert all(h.get("article_urls") for h in published_hist[:2])

    assert any(name == "webui_publish_result"
               and extra.get("state") == "all_success"
               and extra.get("n_ok") == 2 and extra.get("n_failed") == 0
               for level, name, extra in events if level == "info")


def test_all_drafted_renders_green_banner(client, seeded_session, monkeypatch):
    rows = _rows_jsonl(
        {"target_url": "https://example.com/a", "title": "A", "status": "drafted",
         "draft_url": "https://blogger.example/a?draft=1"},
    )
    _mock_run_pipe(monkeypatch, stdout=rows)
    _capture_logger(monkeypatch)

    resp = client.post("/ce:publish", data={
        "platform": "blogger", "publish_mode": "draft",
    })
    body = resp.data.decode("utf-8")
    assert "发布成功！" in body
    assert "发布失败" not in body

    hist = history_store.load()
    assert any(h.get("status") == "drafted" and h.get("article_urls")
               for h in hist)


# ──────────────────────────────────────────────────────────────────────────────
# Failure paths — the bug this plan fixes
# ──────────────────────────────────────────────────────────────────────────────


def test_subprocess_exception_renders_red_banner(client, seeded_session, monkeypatch):
    _mock_run_pipe(monkeypatch, raise_exc=Exception("subprocess died: boom"))
    events = _capture_logger(monkeypatch)

    resp = client.post("/ce:publish", data={
        "platform": "blogger", "publish_mode": "publish",
    })
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "发布失败" in body
    assert "subprocess died: boom" in body
    assert "发布成功！" not in body

    hist = history_store.load()
    failed = [h for h in hist if h.get("status") == "failed"]
    assert any("subprocess died: boom" in (h.get("error") or "") for h in failed)

    warn_events = [(name, extra) for level, name, extra in events if level == "warn"]
    assert any(name == "webui_publish_result" and extra.get("state") == "all_failed"
               for name, extra in warn_events)


def test_all_failed_rows_no_url_renders_red_banner(client, seeded_session, monkeypatch):
    rows = _rows_jsonl(
        {"target_url": "https://example.com/a", "title": "A", "status": "failed",
         "error": "auth expired"},
        {"target_url": "https://example.com/b", "title": "B", "status": "failed",
         "error": "auth expired"},
    )
    _mock_run_pipe(monkeypatch, stdout=rows)
    _capture_logger(monkeypatch)

    resp = client.post("/ce:publish", data={
        "platform": "blogger", "publish_mode": "publish",
    })
    body = resp.data.decode("utf-8")
    assert "发布失败" in body
    assert "auth expired" in body
    assert "发布成功！" not in body
    assert "部分发布成功" not in body

    hist = history_store.load()
    failed = [h for h in hist if h.get("status") == "failed"]
    assert len(failed) >= 2


def test_mixed_rows_render_partial_banner(client, seeded_session, monkeypatch):
    rows = _rows_jsonl(
        {"target_url": "https://example.com/a", "title": "A", "status": "published",
         "published_url": "https://blogger.example/a"},
        {"target_url": "https://example.com/b", "title": "B", "status": "failed",
         "error": "auth expired"},
    )
    _mock_run_pipe(monkeypatch, stdout=rows)
    _capture_logger(monkeypatch)

    resp = client.post("/ce:publish", data={
        "platform": "blogger", "publish_mode": "publish",
    })
    body = resp.data.decode("utf-8")
    assert "部分发布成功 (1/2)" in body
    assert "auth expired" in body
    assert "发布成功！" not in body
    assert "发布失败" not in body  # the red bare banner — only present in all_failed

    hist = history_store.load()
    statuses = {h.get("status") for h in hist}
    assert "published" in statuses
    assert "failed" in statuses


def test_no_parseable_rows_renders_red_banner(client, seeded_session, monkeypatch):
    # stdout is non-empty (passes run_pipe silent-failure guard) but every
    # line is unparseable.
    _mock_run_pipe(monkeypatch, stdout="not-json-at-all\n",
                   stderr="hint from adapter")
    _capture_logger(monkeypatch)

    resp = client.post("/ce:publish", data={
        "platform": "blogger", "publish_mode": "publish",
    })
    body = resp.data.decode("utf-8")
    assert "发布失败" in body
    assert "hint from adapter" in body
    assert "发布成功！" not in body


# ──────────────────────────────────────────────────────────────────────────────
# Defensive defaults
# ──────────────────────────────────────────────────────────────────────────────


def test_legacy_caller_without_publish_state_renders_green(app):
    """Template default: when neither publish_state nor publish_error is
    passed but `published` is truthy, the template falls back to all_success.

    Protects any unmigrated caller (or test fixture) that still passes only
    `published=...`."""
    with app.test_request_context("/"):
        from webui_app.helpers.contexts import _render
        resp = _render("index.html", published="legacy-output", config={},
                       history_active=True)
    body = resp if isinstance(resp, str) else resp.decode("utf-8") if isinstance(resp, bytes) else str(resp)
    assert "发布成功！" in body
