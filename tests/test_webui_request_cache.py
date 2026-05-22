"""Plan E-perf — _g_cache per-request memoization tests."""

from __future__ import annotations

import pytest

from webui_app import create_app
from webui_app.helpers._request_cache import _g_cache


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    return app


# ── _g_cache unit tests ───────────────────────────────────────────────────────

def test_g_cache_returns_fn_result_outside_request_context():
    """Outside a request context, _g_cache calls fn() and returns its result."""
    calls = []
    def fn():
        calls.append(1)
        return 42
    result = _g_cache('test_key', fn)
    assert result == 42
    assert len(calls) == 1


def test_g_cache_calls_fn_each_time_outside_request_context():
    """Outside request context there is no g, so fn() is called every time."""
    calls = []
    def fn():
        calls.append(1)
        return 99
    _g_cache('k', fn)
    _g_cache('k', fn)
    assert len(calls) == 2  # no caching outside request context


def test_g_cache_returns_cached_value_within_request(app):
    """Within a request context, _g_cache returns the same value for the same key."""
    calls = []
    def fn():
        calls.append(1)
        return {'result': 'loaded'}

    with app.test_request_context('/'):
        first = _g_cache('my_key', fn)
        second = _g_cache('my_key', fn)

    assert first == second == {'result': 'loaded'}
    assert len(calls) == 1  # fn() called only once despite two cache hits


def test_g_cache_different_keys_call_fn_independently(app):
    """Different keys do not share cached values."""
    calls = {}
    def make_fn(name):
        def fn():
            calls[name] = calls.get(name, 0) + 1
            return name
        return fn

    with app.test_request_context('/'):
        r1 = _g_cache('key_a', make_fn('a'))
        r2 = _g_cache('key_b', make_fn('b'))
        r3 = _g_cache('key_a', make_fn('a'))  # cache hit

    assert r1 == 'a'
    assert r2 == 'b'
    assert r3 == 'a'
    assert calls == {'a': 1, 'b': 1}  # key_a fn called once, key_b fn called once


def test_g_cache_cleared_between_requests(app):
    """Each new request context gets a fresh cache — no cross-request pollution."""
    calls = []
    def fn():
        calls.append(1)
        return object()  # unique per call

    with app.test_request_context('/'):
        v1 = _g_cache('cfg', fn)

    with app.test_request_context('/'):
        v2 = _g_cache('cfg', fn)

    assert len(calls) == 2  # fn() called once per request, not once total
    assert v1 is not v2     # different objects — cache was cleared


# ── Integration: load_config() called once per /settings request ─────────────

def test_load_config_called_once_per_settings_request(app, monkeypatch):
    """_settings_context + channel_probes call load_config() but hit disk only once."""
    from backlink_publisher import config as _cfg_mod
    original = _cfg_mod.load_config
    calls = []

    def counting_load_config():
        calls.append(1)
        return original()

    monkeypatch.setattr(_cfg_mod, 'load_config', counting_load_config)
    # Also patch at the consumer references
    import webui_app.helpers.contexts as ctx_mod
    import webui_app.helpers.channel_probes as probe_mod
    monkeypatch.setattr(ctx_mod, 'load_config', counting_load_config)
    monkeypatch.setattr(probe_mod, 'load_config', counting_load_config)

    with app.test_client() as client:
        resp = client.get('/settings')
        assert resp.status_code == 200

    # With flask.g caching, load_config() should be called exactly once
    # despite _settings_context + _get_velog_status + _get_blogger_token_status
    # all needing the config.
    assert len(calls) == 1, (
        f"load_config() called {len(calls)} times in one /settings request; "
        f"expected 1 (flask.g cache should deduplicate)"
    )
