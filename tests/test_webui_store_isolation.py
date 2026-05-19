"""Regression tests: webui_store singletons honour BACKLINK_PUBLISHER_CONFIG_DIR.

History: 2026-05-19 PR #87 verification + parallel `bp-cbu5-ui/` pytest run
both wiped the operator's real `~/.config/backlink-publisher/publish-history.json`
+ `draft-queue.json`. Root cause: `webui_store/__init__.py` captured paths from
`Path.home() / ".config" / "backlink-publisher"` at import time, ignoring the
session-autouse fixture's env override.

Fix: `_store_path()` delegates to canonical `_config_dir()` + `_refresh_paths()`
rebinds singletons after env changes. Conftest's `_isolate_user_dirs` fixture
calls `_refresh_paths()`.

These tests lock in the isolation contract so future refactors can't reintroduce
the env-blind regression.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_singleton_paths_resolve_to_isolated_dir():
    """After ``_refresh_paths()``, every singleton points inside the
    currently-active config dir — not the operator's real ``~/.config/``.

    Calls ``_refresh_paths()`` explicitly to defend against other tests
    that mutate env+singletons via function-scope monkeypatch without
    restoring state (their tmp dirs vanish on teardown, leaving singletons
    pointing at deleted paths). The conftest session fixture sets env at
    session start; this test asserts the contract holds after a fresh
    refresh against current env.
    """
    from webui_store import _refresh_paths
    import webui_store

    _refresh_paths()

    cfg_env = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR", "")
    assert cfg_env, "conftest fixture should have set this env"

    isolated = Path(cfg_env).resolve()
    real_prod = (Path.home() / ".config" / "backlink-publisher").resolve()

    for store_name in (
        "history_store",
        "drafts_store",
        "profiles_store",
        "schedule_store",
        "queue_store",
        "channel_status_store",
    ):
        store = getattr(webui_store, store_name)
        store_path = Path(store.path).resolve()
        # The store path must be inside the isolated tmp dir
        assert store_path.is_relative_to(isolated), (
            f"{store_name} path {store_path} not inside isolated dir {isolated}"
        )
        # And must NOT be inside the operator's real ~/.config/
        assert not store_path.is_relative_to(real_prod), (
            f"{store_name} path {store_path} leaked into prod {real_prod}"
        )


def test_writes_land_in_isolated_dir_not_prod():
    """Writing through history_store / drafts_store touches the isolated
    tmp file — never the operator's real publish-history.json / draft-queue.json."""
    from webui_store import _refresh_paths, drafts_store, history_store

    _refresh_paths()

    real_history = Path.home() / ".config" / "backlink-publisher" / "publish-history.json"
    real_drafts = Path.home() / ".config" / "backlink-publisher" / "draft-queue.json"

    # Capture pre-write mtimes if files exist (operator may or may not have them)
    pre_history_mtime = real_history.stat().st_mtime if real_history.exists() else None
    pre_drafts_mtime = real_drafts.stat().st_mtime if real_drafts.exists() else None

    history_store.update(lambda lst: lst + [{"id": "iso-test", "status": "published"}])
    drafts_store.update(lambda lst: lst + [{"id": "iso-draft", "status": "drafted"}])

    # Prod files must be untouched (mtime unchanged or still missing)
    post_history_mtime = real_history.stat().st_mtime if real_history.exists() else None
    post_drafts_mtime = real_drafts.stat().st_mtime if real_drafts.exists() else None
    assert pre_history_mtime == post_history_mtime, (
        "history_store write leaked into prod publish-history.json"
    )
    assert pre_drafts_mtime == post_drafts_mtime, (
        "drafts_store write leaked into prod draft-queue.json"
    )

    # The write must be visible via the singleton's own path
    isolated_history = Path(history_store.path)
    assert isolated_history.exists(), "isolated history file was not written"
    assert any(r.get("id") == "iso-test" for r in history_store.load())

    isolated_drafts = Path(drafts_store.path)
    assert isolated_drafts.exists(), "isolated drafts file was not written"
    assert any(r.get("id") == "iso-draft" for r in drafts_store.load())


def test_refresh_paths_picks_up_env_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """After mutating env mid-session + calling _refresh_paths(), singletons
    re-bind to the new config dir. Locks in the contract that test fixtures
    can re-isolate dynamically."""
    from webui_store import _refresh_paths, history_store

    new_dir = tmp_path / "fresh-config"
    new_dir.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(new_dir))

    _refresh_paths()

    expected = (new_dir / "publish-history.json").resolve()
    assert Path(history_store.path).resolve() == expected

    history_store.update(lambda lst: lst + [{"id": "refresh-test"}])
    assert expected.exists()
