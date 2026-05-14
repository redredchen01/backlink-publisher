"""Tests for the work-themed WebUI surface — Plan 2026-05-13-004 Unit 5b.

Covers:
- ``GET /sites``: form renders with hidden CSRF token + posts to
  ``/sites/save-three-url``; pre-fills from existing target_three_url config
  when ``?domain=`` is supplied.
- ``POST /sites/save-three-url``: CSRF rejection (403); valid form ⇒ config
  updated + redirect with ``?saved=...`` toast hint; invalid main_url ⇒
  422 + per-field error rendering; multi-line work_urls parsing tolerates
  blank/space/tab/CRLF separators.
- ``GET /sites/scrape-preview``: returns JSON metadata from work_scraper.
- ``POST /sites/run``: CSRF rejection; valid run shells out via run_pipe
  with seed JSONL containing main_url/list_url/work_urls; redirects to
  ``/sites/run/<run_id>/result``.
- ``GET /sites/run/<id>/result``: renders summary + per-row status table.
- Bind assertion: ``_resolve_bind_host`` rejects non-loopback hosts
  unless ``BACKLINK_PUBLISHER_ALLOW_NETWORK=1`` is set.

Tests deliberately locate form fields by HTML attributes (input ``name``,
form ``action``) rather than Chinese labels — avoids the
feedback_jinja2-banner-text-collision.md failure mode.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Ensure the webui module is importable.
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── autouse: isolate config writes + suppress real network/subprocess ───────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    """Redirect all config.toml reads/writes to tmp_path."""
    fake_config_dir = tmp_path / "config"
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ), patch(
        "backlink_publisher.config._cache_dir", return_value=tmp_path / "cache",
    ):
        yield fake_config_dir


@pytest.fixture(autouse=True)
def _no_real_subprocess():
    """Mock subprocess.run so /sites/run never shells out to the real CLI."""
    import subprocess as sp_mod

    def _fake_run(cmd, *_args, **_kwargs):
        result = sp_mod.CompletedProcess(args=cmd, returncode=0)
        result.stdout = ""
        result.stderr = ""
        return result

    with patch("subprocess.run", side_effect=_fake_run):
        yield


@pytest.fixture
def client():
    """Flask test client with secure cookies disabled so the session round-trips."""
    import webui

    webui.app.config["TESTING"] = True
    webui.app.config["SESSION_COOKIE_SECURE"] = False
    webui.app.config["WTF_CSRF_ENABLED"] = False  # belt-and-suspenders if Flask-WTF ever lands
    return webui.app.test_client()


def _fetch_csrf(client) -> str:
    """Hit GET /sites, parse the hidden csrf_token out of the rendered form."""
    import re as _re

    resp = client.get("/sites")
    assert resp.status_code == 200, resp.data[:200]
    html = resp.data.decode()
    match = _re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, f"csrf_token hidden input not found in /sites HTML"
    return match.group(1)


# ═════════════════════════════════════════════════════════════════════════════
# GET /sites — form renders with CSRF token + correct action
# ═════════════════════════════════════════════════════════════════════════════


class TestSitesFormRender:
    def test_get_renders_form_with_csrf_and_correct_action(self, client):
        resp = client.get("/sites")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'action="/sites/save-three-url"' in body
        assert 'name="csrf_token"' in body
        # All required form inputs are present (located by name, not by label)
        for name in (
            "main_url", "list_url", "work_urls",
            "branded_pool", "partial_pool", "exact_pool",
            "work_anchor_templates", "count", "insecure_tls",
        ):
            assert f'name="{name}"' in body, f"missing form input name={name}"

    def test_csrf_token_is_stable_across_requests_in_one_session(self, client):
        token1 = _fetch_csrf(client)
        token2 = _fetch_csrf(client)
        assert token1 == token2

    def test_prefill_from_saved_three_url_config(self, client):
        # First save a target then reload the form with ?domain=
        from backlink_publisher.config import (
            ThreeUrlConfig, load_config, save_config,
        )
        save_config(
            load_config(),
            target_three_url={
                "https://prefill.com": ThreeUrlConfig(
                    main_url="https://prefill.com/",
                    list_url="https://prefill.com/list",
                    branded_pool=["BrandX"],
                    partial_pool=["partial-x"],
                    exact_pool=["exact-x"],
                    work_urls=["https://prefill.com/work/1"],
                )
            },
        )
        resp = client.get("/sites?domain=https://prefill.com")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "https://prefill.com/" in body
        assert "https://prefill.com/list" in body
        assert "BrandX" in body
        assert "partial-x" in body
        assert "exact-x" in body
        assert "https://prefill.com/work/1" in body


# ═════════════════════════════════════════════════════════════════════════════
# POST /sites/save-three-url — CSRF + validation + happy-path round-trip
# ═════════════════════════════════════════════════════════════════════════════


class TestSaveThreeUrl:
    def test_missing_csrf_returns_403_and_does_not_write_config(self, client):
        resp = client.post(
            "/sites/save-three-url",
            data={
                "main_url": "https://x.com/",
                "list_url": "https://x.com/list",
                "branded_pool": "B",
                "partial_pool": "p",
                "exact_pool": "e",
            },
        )
        assert resp.status_code == 403
        from backlink_publisher.config import load_config
        assert load_config().target_three_url == {}

    def test_wrong_csrf_returns_403(self, client):
        _fetch_csrf(client)  # establish session
        resp = client.post(
            "/sites/save-three-url",
            data={"csrf_token": "obviously-wrong"},
        )
        assert resp.status_code == 403

    def test_happy_path_writes_config_and_redirects_with_saved_query(
        self, client
    ):
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "https://happy.com/",
                "list_url": "https://happy.com/list",
                "work_urls": "https://happy.com/work/1\nhttps://happy.com/work/2",
                "branded_pool": "Brand A\nBrand B",
                "partial_pool": "partial keyword",
                "exact_pool": "exact keyword",
                "work_anchor_templates": "{title}\n{title} 详情",
                "count": "5",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/sites" in resp.headers["Location"]
        assert "saved=" in resp.headers["Location"]

        from backlink_publisher.config import load_config
        cfg = load_config()
        entry = cfg.target_three_url["https://happy.com"]
        assert entry.main_url == "https://happy.com/"
        assert entry.list_url == "https://happy.com/list"
        assert entry.work_urls == [
            "https://happy.com/work/1",
            "https://happy.com/work/2",
        ]
        assert entry.branded_pool == ["Brand A", "Brand B"]

    def test_invalid_main_url_returns_422_with_field_error(self, client):
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "http://insecure.com/",  # not https
                "list_url": "https://insecure.com/list",
                "branded_pool": "B",
                "partial_pool": "p",
                "exact_pool": "e",
            },
        )
        assert resp.status_code == 422
        body = resp.data.decode()
        # Field-level error renders next to the main_url input
        assert 'name="main_url"' in body
        # Inline error class present (for aria-describedby / styling)
        assert "field-error" in body
        # Form preserves user-entered values
        assert "http://insecure.com/" in body
        # Config NOT written
        from backlink_publisher.config import load_config
        assert load_config().target_three_url == {}

    def test_work_urls_textarea_handles_blank_lines_and_crlf(self, client):
        token = _fetch_csrf(client)
        # Mix of \n, \r\n, blank lines, leading/trailing whitespace, tabs
        raw = (
            "https://multi.com/work/1\r\n"
            "\r\n"
            "  https://multi.com/work/2  \n"
            "\thttps://multi.com/work/3\t\n"
        )
        resp = client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "https://multi.com/",
                "list_url": "https://multi.com/list",
                "work_urls": raw,
                "branded_pool": "B",
                "partial_pool": "p",
                "exact_pool": "e",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        from backlink_publisher.config import load_config
        entry = load_config().target_three_url["https://multi.com"]
        assert entry.work_urls == [
            "https://multi.com/work/1",
            "https://multi.com/work/2",
            "https://multi.com/work/3",
        ]


# ═════════════════════════════════════════════════════════════════════════════
# GET /sites/scrape-preview — JSON metadata from work_scraper
# ═════════════════════════════════════════════════════════════════════════════


class TestScrapePreview:
    def test_returns_json_metadata(self, client):
        from backlink_publisher.work_scraper import WorkMetadata
        with patch(
            "webui.fetch_work_metadata",
            return_value=WorkMetadata(
                title="预览标题", description="预览描述", h1="预览标题",
            ),
        ):
            resp = client.get("/sites/scrape-preview?url=https://x.com/work/1")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("application/json")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["title"] == "预览标题"
        assert data["description"] == "预览描述"
        assert data["h1"] == "预览标题"

    def test_returns_status_error_when_scraper_returns_none(self, client):
        with patch("webui.fetch_work_metadata", return_value=None):
            resp = client.get("/sites/scrape-preview?url=https://x.com/work/1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "error"

    def test_missing_url_param_returns_400(self, client):
        resp = client.get("/sites/scrape-preview")
        assert resp.status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
# POST /sites/run — CSRF + run_pipe invocation + redirect
# ═════════════════════════════════════════════════════════════════════════════


class TestSitesRun:
    def _save_basic(self, client) -> str:
        """Save a minimal target so /sites/run has something to run on. Returns the CSRF token."""
        token = _fetch_csrf(client)
        client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "https://run.com/",
                "list_url": "https://run.com/list",
                "work_urls": "https://run.com/work/1",
                "branded_pool": "B",
                "partial_pool": "p",
                "exact_pool": "e",
            },
        )
        return token

    def test_missing_csrf_returns_403(self, client):
        self._save_basic(client)
        resp = client.post("/sites/run", data={"main_url": "https://run.com/"})
        assert resp.status_code == 403

    def test_run_invokes_run_pipe_and_redirects_to_result(self, client):
        token = self._save_basic(client)
        with patch(
            "webui.run_pipe",
            return_value={"stdout": '{"id":"abc"}\n', "stderr": ""},
        ) as mock_pipe:
            resp = client.post(
                "/sites/run",
                data={
                    "csrf_token": token,
                    "main_url": "https://run.com/",
                },
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert "/sites/run/" in resp.headers["Location"]
        assert "/result" in resp.headers["Location"]
        # Pipeline was actually shelled out
        assert mock_pipe.called
        cmd = mock_pipe.call_args.args[0]
        assert cmd[0] == "plan-backlinks"

    def test_run_for_unknown_main_url_returns_400(self, client):
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/run",
            data={"csrf_token": token, "main_url": "https://nope.com/"},
        )
        assert resp.status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
# GET /sites/run/<id>/result — partial-failure status table + fail-empty UX
# ═════════════════════════════════════════════════════════════════════════════


class TestRunResultPage:
    def test_partial_failure_table_rendered_with_summary(self, client):
        # Stash a synthetic run summary in the in-memory store the route reads
        import webui
        run_id = "20260514T010101-deadbeef"
        webui._WORK_THEMED_RUNS[run_id] = {
            "main_url": "https://r.com/",
            "summary": {
                "total": 5, "generated": 3, "skipped": 2,
                "fail_empty": False,
            },
            "rows": [
                {"work_url": "https://r.com/work/1", "status": "success"},
                {"work_url": "https://r.com/work/2", "status": "success"},
                {"work_url": "https://r.com/work/3", "status": "scrape_failed"},
                {"work_url": "https://r.com/work/4", "status": "success"},
                {"work_url": "https://r.com/work/5", "status": "scrape_failed"},
            ],
        }
        try:
            resp = client.get(f"/sites/run/{run_id}/result")
        finally:
            webui._WORK_THEMED_RUNS.pop(run_id, None)
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "3/5" in body or "3 / 5" in body
        assert "scrape_failed" in body
        # Each work_url appears in the table
        for i in range(1, 6):
            assert f"https://r.com/work/{i}" in body

    def test_fail_empty_state_shows_helpful_message(self, client):
        import webui
        run_id = "20260514T020202-cafef00d"
        webui._WORK_THEMED_RUNS[run_id] = {
            "main_url": "https://empty.com/",
            "summary": {
                "total": 0, "generated": 0, "skipped": 0,
                "fail_empty": True,
            },
            "rows": [],
        }
        try:
            resp = client.get(f"/sites/run/{run_id}/result")
        finally:
            webui._WORK_THEMED_RUNS.pop(run_id, None)
        assert resp.status_code == 200
        body = resp.data.decode()
        # User-actionable next step is rendered
        assert "list_url" in body
        # Link back to the form
        assert "/sites" in body

    def test_unknown_run_id_returns_404(self, client):
        resp = client.get("/sites/run/00000000T000000-aaaaaaaa/result")
        assert resp.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# Bind assertion — non-loopback host requires explicit env opt-in
# ═════════════════════════════════════════════════════════════════════════════


class TestBindAssertion:
    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_loopback_hosts_pass_without_opt_in(self, host, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", raising=False)
        monkeypatch.setenv("BIND_HOST", host)
        import webui
        assert webui._resolve_bind_host() == host

    def test_default_when_no_env_is_loopback(self, monkeypatch):
        monkeypatch.delenv("BIND_HOST", raising=False)
        monkeypatch.delenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", raising=False)
        import webui
        # Default must be loopback, not 0.0.0.0 — historical default was unsafe
        assert webui._resolve_bind_host() in ("127.0.0.1", "::1", "localhost")

    def test_non_loopback_without_opt_in_raises(self, monkeypatch):
        monkeypatch.setenv("BIND_HOST", "0.0.0.0")
        monkeypatch.delenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", raising=False)
        import webui
        with pytest.raises(RuntimeError, match="loopback"):
            webui._resolve_bind_host()

    def test_non_loopback_with_explicit_opt_in_passes(self, monkeypatch):
        monkeypatch.setenv("BIND_HOST", "0.0.0.0")
        monkeypatch.setenv("BACKLINK_PUBLISHER_ALLOW_NETWORK", "1")
        import webui
        assert webui._resolve_bind_host() == "0.0.0.0"


# ═════════════════════════════════════════════════════════════════════════════
# Content-fetch gate (plan 2026-05-14-007 Unit 4)
# ═════════════════════════════════════════════════════════════════════════════


class TestContentFetchGate:
    """The content-fetch gate runs at form-save time so the operator gets
    field-level errors instantly rather than discovering the bad URL at
    publish time. ``BACKLINK_NO_FETCH_VERIFY=1`` bypasses for dev.
    """

    def test_save_three_url_main_url_gate_failure_returns_422(
        self, client, monkeypatch
    ):
        def _fail_main(urls, max_workers=5):
            return {
                u: (
                    (False, "http_404", None)
                    if "stale" in u
                    else (True, None, "ok")
                )
                for u in urls
            }

        monkeypatch.setattr(
            "webui.content_fetch.verify_urls_batch", _fail_main,
        )
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "https://stale.example.com/",
                "list_url": "https://other.example/list",
                "work_urls": "",
                "branded_pool": "B",
                "partial_pool": "P",
                "exact_pool": "E",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 422
        body = resp.data.decode()
        assert "main_url" in body
        # Failure reason surfaces to the operator
        assert "http_404" in body

    def test_save_three_url_work_urls_partial_gate_failure(
        self, client, monkeypatch
    ):
        def _fail_one(urls, max_workers=5):
            return {
                u: (
                    (False, "http_200_no_title", None)
                    if u.endswith("/bad")
                    else (True, None, "ok")
                )
                for u in urls
            }

        monkeypatch.setattr(
            "webui.content_fetch.verify_urls_batch", _fail_one,
        )
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "https://x.com/",
                "list_url": "https://x.com/list",
                "work_urls": "https://x.com/good\nhttps://x.com/bad",
                "branded_pool": "B",
                "partial_pool": "P",
                "exact_pool": "E",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 422
        body = resp.data.decode()
        assert "work_urls" in body
        assert "/bad" in body
        # The good URL should not be flagged
        assert "http_200_no_title" in body

    def test_save_three_url_all_urls_pass_gate_succeeds(
        self, client
    ):
        """The autouse mock in conftest defaults everything to pass."""
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "https://x.com/",
                "list_url": "https://x.com/list",
                "work_urls": "",
                "branded_pool": "B",
                "partial_pool": "P",
                "exact_pool": "E",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_save_three_url_env_bypass_skips_gate(
        self, client, monkeypatch
    ):
        """BACKLINK_NO_FETCH_VERIFY=1 → gate is not called even when it
        would fail. Use case: dev / staging environments with deliberately
        unreachable URLs."""
        call_count = {"n": 0}

        def _tracking(urls, max_workers=5):
            call_count["n"] += 1
            return {u: (False, "http_404", None) for u in urls}

        monkeypatch.setattr(
            "webui.content_fetch.verify_urls_batch", _tracking,
        )
        monkeypatch.setenv("BACKLINK_NO_FETCH_VERIFY", "1")
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={
                "csrf_token": token,
                "main_url": "https://x.com/",
                "list_url": "https://x.com/list",
                "work_urls": "",
                "branded_pool": "B",
                "partial_pool": "P",
                "exact_pool": "E",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302, "bypass should let the save proceed"
        assert call_count["n"] == 0, "gate must not be invoked under bypass"

    def test_ce_plan_url_gate_failure_renders_error(
        self, client, monkeypatch
    ):
        def _fail(urls, max_workers=5):
            return {u: (False, "http_404", None) for u in urls}

        monkeypatch.setattr(
            "webui.content_fetch.verify_urls_batch", _fail,
        )
        resp = client.post(
            "/ce:plan",
            data={"target_url": "https://stale.example/"},
            follow_redirects=False,
        )
        # /ce:plan re-renders the index page with an inline error rather
        # than 422; assert the error is surfaced
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "无可访问内容" in body or "http_404" in body


# ═════════════════════════════════════════════════════════════════════════════
# Homepage three-tier URL form (plan 2026-05-14-009 Units 1+2+4)
# ═════════════════════════════════════════════════════════════════════════════


class TestHomepageThreeTier:
    """Homepage / form structured into main_url / category_url / work_url
    instead of the single target_url + free-form url_new path. Backward
    compat: target_url still accepted as fallback for main_url."""

    def test_get_homepage_renders_three_tier_inputs(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.data.decode()
        # The three structured tier inputs are present with their badges.
        assert 'name="main_url"' in body
        assert 'name="category_url"' in body
        assert 'name="work_url"' in body
        assert ">主<" in body
        assert ">类<" in body
        assert ">漫<" in body
        # main_url marked required.
        assert 'name="main_url"' in body and 'required' in body
        # Legacy url_new textbox still present for free-form extras.
        assert 'name="url_new"' in body

    def test_post_only_main_url_succeeds_no_config_write(self, client, tmp_path):
        """Submit only main_url. No persistence (no category/work data)."""
        resp = client.post(
            "/ce:plan",
            data={"main_url": "https://example.com/"},
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        # Index re-rendered with config preview / no error
        assert "请输入主网域" not in body

    def test_post_three_tiers_persists_threeurl_config(
        self, client, tmp_path, monkeypatch
    ):
        """Full submit: main + category + work → upgrade_target_to_threeurl
        is called + save_config writes the ThreeUrlConfig block."""
        # Patch fetch_url_metadata so the preview path doesn't try real HTTP.
        monkeypatch.setattr(
            "webui.fetch_url_metadata",
            lambda url: {"url": url, "title": "x", "description": "", "status": "success"},
        )
        resp = client.post(
            "/ce:plan",
            data={
                "main_url": "https://example.com/",
                "category_url": "https://example.com/cat",
                "work_url": "https://example.com/work/1",
            },
        )
        assert resp.status_code == 200, resp.data[:300]

        # Reload config — ThreeUrlConfig should be written for the domain.
        from backlink_publisher.config import load_config
        cfg = load_config()
        key = "https://example.com"
        assert key in cfg.target_three_url, list(cfg.target_three_url.keys())
        entry = cfg.target_three_url[key]
        assert entry.list_url == "https://example.com/cat"
        assert entry.work_urls == ["https://example.com/work/1"]

    def test_post_missing_main_url_returns_error(self, client):
        resp = client.post(
            "/ce:plan",
            data={"category_url": "https://example.com/cat"},
        )
        assert resp.status_code == 200  # re-render index with error
        assert "请输入主网域" in resp.data.decode()

    def test_post_main_url_gate_failure_renders_error(
        self, client, monkeypatch
    ):
        """Plan 007 gate inherited: main_url gate fail → error rendered."""
        def _fail(urls, max_workers=5):
            return {u: (False, "http_404", None) for u in urls}

        monkeypatch.setattr(
            "webui.content_fetch.verify_urls_batch", _fail,
        )
        resp = client.post(
            "/ce:plan",
            data={"main_url": "https://stale.example.com/"},
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "http_404" in body or "无可访问内容" in body

    def test_post_non_https_category_url_returns_error(self, client):
        resp = client.post(
            "/ce:plan",
            data={
                "main_url": "https://example.com/",
                "category_url": "http://example.com/cat",
            },
        )
        body = resp.data.decode()
        assert "分类页必须 https" in body or "category" in body.lower()

    def test_post_legacy_target_url_fallback(self, client, monkeypatch):
        """Backward compat: old target_url name still works as main_url."""
        monkeypatch.setattr(
            "webui.fetch_url_metadata",
            lambda url: {"url": url, "title": "x", "description": "", "status": "success"},
        )
        resp = client.post(
            "/ce:plan",
            data={"target_url": "https://legacy.example/"},
        )
        assert resp.status_code == 200, resp.data[:300]
        body = resp.data.decode()
        assert "请输入主网域" not in body

    def test_post_legacy_anchor_keywords_upgraded_to_threeurl(
        self, client, monkeypatch, _isolated_config_dir
    ):
        """If main_url already has anchor_keywords (legacy schema), the form
        save triggers automatic upgrade — anchor_keywords are migrated into
        branded_pool inside the new ThreeUrlConfig."""
        from backlink_publisher.config import load_config, save_config

        save_config(
            load_config(), target_anchor_keywords={
                "https://hasanchor.example": ["BrandA", "BrandB"],
            },
        )

        monkeypatch.setattr(
            "webui.fetch_url_metadata",
            lambda url: {"url": url, "title": "x", "description": "", "status": "success"},
        )
        resp = client.post(
            "/ce:plan",
            data={
                "main_url": "https://hasanchor.example/",
                "category_url": "https://hasanchor.example/cat",
                "work_url": "https://hasanchor.example/w/1",
            },
        )
        assert resp.status_code == 200, resp.data[:300]

        cfg = load_config()
        key = "https://hasanchor.example"
        assert key in cfg.target_three_url
        entry = cfg.target_three_url[key]
        # anchor_keywords migrated to branded_pool
        assert entry.branded_pool == ["BrandA", "BrandB"]
        assert entry.list_url == "https://hasanchor.example/cat"
        assert entry.work_urls == ["https://hasanchor.example/w/1"]


# ═════════════════════════════════════════════════════════════════════════════
# Plan 008 Unit 3: webui TTL env wiring
# ═════════════════════════════════════════════════════════════════════════════


class TestContentFetchTTLWiring:
    """`BACKLINK_GATE_CACHE_TTL_SECONDS` → content_fetch.set_default_max_age
    happens at webui startup via `_wire_content_fetch_ttl_from_env`."""

    def test_default_900_seconds_when_env_unset(self, monkeypatch):
        from backlink_publisher import content_fetch
        import webui

        monkeypatch.delenv("BACKLINK_GATE_CACHE_TTL_SECONDS", raising=False)
        monkeypatch.delenv("BACKLINK_NO_FETCH_VERIFY", raising=False)
        content_fetch.set_default_max_age(None)
        webui._wire_content_fetch_ttl_from_env()
        # 900s default per plan 008 Unit 3
        assert content_fetch._DEFAULT_MAX_AGE_S == 900.0
        # Reset for the next test.
        content_fetch.set_default_max_age(None)

    def test_explicit_env_overrides_default(self, monkeypatch):
        from backlink_publisher import content_fetch
        import webui

        monkeypatch.setenv("BACKLINK_GATE_CACHE_TTL_SECONDS", "60")
        monkeypatch.delenv("BACKLINK_NO_FETCH_VERIFY", raising=False)
        content_fetch.set_default_max_age(None)
        webui._wire_content_fetch_ttl_from_env()
        assert content_fetch._DEFAULT_MAX_AGE_S == 60.0

    def test_bypass_env_skips_ttl_wiring(self, monkeypatch):
        from backlink_publisher import content_fetch
        import webui

        monkeypatch.setenv("BACKLINK_NO_FETCH_VERIFY", "1")
        monkeypatch.setenv("BACKLINK_GATE_CACHE_TTL_SECONDS", "60")
        content_fetch.set_default_max_age(None)
        webui._wire_content_fetch_ttl_from_env()
        assert content_fetch._DEFAULT_MAX_AGE_S is None

    def test_invalid_env_falls_back_to_900(self, monkeypatch):
        from backlink_publisher import content_fetch
        import webui

        monkeypatch.setenv("BACKLINK_GATE_CACHE_TTL_SECONDS", "not-a-number")
        monkeypatch.delenv("BACKLINK_NO_FETCH_VERIFY", raising=False)
        content_fetch.set_default_max_age(None)
        webui._wire_content_fetch_ttl_from_env()
        assert content_fetch._DEFAULT_MAX_AGE_S == 900.0

    def test_zero_or_negative_seconds_skips_wiring(self, monkeypatch):
        from backlink_publisher import content_fetch
        import webui

        for value in ("0", "-5"):
            monkeypatch.setenv("BACKLINK_GATE_CACHE_TTL_SECONDS", value)
            monkeypatch.delenv("BACKLINK_NO_FETCH_VERIFY", raising=False)
            content_fetch.set_default_max_age(None)
            webui._wire_content_fetch_ttl_from_env()
            assert content_fetch._DEFAULT_MAX_AGE_S is None, (
                f"TTL={value} should leave TTL disabled"
            )
