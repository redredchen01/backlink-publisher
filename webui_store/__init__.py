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

from .base import JsonStore, Store
from .channel_status import channel_status_store
from .drafts import DraftsStore
from .history import HistoryStore
from .queue_store import QueueStore

_CONFIG_DIR = Path.home() / ".config" / "backlink-publisher"

# Singleton bindings declared as the ``Store`` protocol (not the concrete
# ``JsonStore``) so a future SQLite implementation can drop in here
# without rippling type annotations across the route + service layers.
# Plan 2026-05-18-001 Unit 8 — see ``base.py`` for the protocol contract
# and a worked SqliteStore swap example.
history_store: HistoryStore = HistoryStore(
    _CONFIG_DIR / "publish-history.json",
)
profiles_store: Store = JsonStore(
    _CONFIG_DIR / "campaign-profiles.json", default_factory=list,
)
drafts_store: DraftsStore = DraftsStore(
    _CONFIG_DIR / "draft-queue.json",
)
schedule_store: Store = JsonStore(
    _CONFIG_DIR / "schedule-settings.json", default_factory=dict,
)
queue_store: QueueStore = QueueStore(
    _CONFIG_DIR / "publish-queue.json", default_factory=list,
)


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
]
