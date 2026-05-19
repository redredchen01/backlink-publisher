"""WebUI state persistence — Plan 2026-05-18-001 Unit 2.

Four module-level singletons replace the legacy ``_load_*`` / ``_save_*``
helpers that were inlined in ``webui.py``:

  - ``history_store``       — publish history list
  - ``profiles_store``      — campaign profile list
  - ``drafts_store``        — draft queue list (specialized with
                              ``get_item`` / ``update_item`` / ``delete_item``)
  - ``schedule_store``      — schedule settings dict

Each store has identical load/save semantics:
  - File missing → returns ``default_factory()``
  - File present + valid JSON → returns parsed value
  - ``save(value)`` atomically writes via temp-file rename
  - ``update(fn)`` runs ``load → fn → save`` under a per-store lock

This package will move to ``webui/store/`` in Unit 3 when ``webui.py`` is
split into a ``webui/`` package. Path-only rename; no API change planned.
"""

from __future__ import annotations

from pathlib import Path

from backlink_publisher.config.loader import _config_dir

from .base import JsonStore, Store
from .channel_status import channel_status_store
from .drafts import DraftsStore
from .history import HistoryStore
from .queue_store import QueueStore


def _store_path(filename: str) -> Path:
    """Resolve a store file path under the current config dir.

    Delegates to ``backlink_publisher.config.loader._config_dir()`` so
    ``BACKLINK_PUBLISHER_CONFIG_DIR`` is honoured. Use ``_refresh_paths()``
    below if the env changes after this module is imported (e.g. test
    fixtures setting an isolated tmp dir).
    """
    return _config_dir() / filename


# Singleton bindings declared as the ``Store`` protocol (not the concrete
# ``JsonStore``) so a future SQLite implementation can drop in here
# without rippling type annotations across the route + service layers.
# Plan 2026-05-18-001 Unit 8 — see ``base.py`` for the protocol contract
# and a worked SqliteStore swap example.
history_store: HistoryStore = HistoryStore(_store_path("publish-history.json"))
profiles_store: Store = JsonStore(
    _store_path("campaign-profiles.json"), default_factory=list,
)
drafts_store: DraftsStore = DraftsStore(_store_path("draft-queue.json"))
schedule_store: Store = JsonStore(
    _store_path("schedule-settings.json"), default_factory=dict,
)
queue_store: QueueStore = QueueStore(
    _store_path("publish-queue.json"), default_factory=list,
)


def _refresh_paths() -> None:
    """Rebind every singleton's ``path`` from the current ``_config_dir()``.

    Module-level singletons capture their path at import time. Test fixtures
    that set ``BACKLINK_PUBLISHER_CONFIG_DIR`` after import (the common case
    for session-scope autouse fixtures) must call this to redirect writes
    away from the operator's real ``~/.config/backlink-publisher/``.

    History note: 2026-05-19 verification of PR #87 exposed this by wiping
    the operator's real ``publish-history.json`` + ``draft-queue.json`` —
    the autouse fixture sets env but the import-time singletons did not
    re-read it. See ``feedback_webui_store_config_dir_frozen.md``.
    """
    history_store.path = _store_path("publish-history.json")
    profiles_store.path = _store_path("campaign-profiles.json")
    drafts_store.path = _store_path("draft-queue.json")
    schedule_store.path = _store_path("schedule-settings.json")
    queue_store.path = _store_path("publish-queue.json")
    channel_status_store.path = _store_path("channel-status.json")


__all__ = [
    "Store",
    "JsonStore",
    "DraftsStore",
    "HistoryStore",
    "QueueStore",
    "history_store",
    "profiles_store",
    "drafts_store",
    "schedule_store",
    "queue_store",
    "channel_status_store",
    "_refresh_paths",
]
