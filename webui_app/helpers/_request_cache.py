"""Per-request in-process cache via flask.g.

Provides _g_cache(key, fn) for contexts.py and channel_probes.py to
eliminate duplicate disk reads within a single HTTP request.
"""

from __future__ import annotations

from typing import Any, Callable


def _g_cache(key: str, fn: Callable[[], Any]) -> Any:
    """Return per-request cached result of fn().

    Uses flask.g within an active request context so repeated calls in the
    same request (e.g. load_config() from _settings_context AND from
    _get_velog_status) hit disk only once.  Falls back to a direct fn() call
    outside a request context (CLI, background jobs, tests without a client).
    """
    try:
        from flask import g as _g
        cache = getattr(_g, '_ctx_cache', None)
        if cache is None:
            _g._ctx_cache = cache = {}
        if key not in cache:
            cache[key] = fn()
        return cache[key]
    except RuntimeError:
        # No active request context (CLI, background jobs, tests) — call directly.
        return fn()
