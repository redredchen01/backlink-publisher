"""HistoryStore — publish-history specialized JsonStore.

Plan 2026-05-19-006 Unit 2. Adds per-item + bulk helpers on top of the
plain ``JsonStore`` that backed ``history_store`` before. Existing code
that calls ``history_store.load()`` / ``.update(fn)`` keeps working because
``HistoryStore`` inherits ``JsonStore``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import JsonStore


class HistoryStore(JsonStore):
    """Publish-history list store with item + bulk helpers.

    All mutations go through ``update()`` / the internal lock so background
    scheduler writes and HTTP-handler writes stay race-free.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path, default_factory=list)

    # ── per-item helpers ──────────────────────────────────────────────

    def get_item(self, item_id: str) -> dict | None:
        for item in self.load():
            if item.get("id") == item_id:
                return item
        return None

    def update_item(self, item_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        with self._lock:
            items = self.load()
            for it in items:
                if it.get("id") == item_id:
                    it.update(fields)
                    self.save(items)
                    return True
            return False

    def delete_item(self, item_id: str) -> bool:
        with self._lock:
            items = self.load()
            new_items = [it for it in items if it.get("id") != item_id]
            if len(new_items) == len(items):
                return False
            self.save(new_items)
            return True

    # ── bulk helpers ──────────────────────────────────────────────────

    def bulk_delete(self, ids: list[str]) -> int:
        if not ids:
            return 0
        id_set = set(ids)
        with self._lock:
            items = self.load()
            kept = [it for it in items if it.get("id") not in id_set]
            removed = len(items) - len(kept)
            if removed:
                self.save(kept)
            return removed

    def bulk_update(self, ids: list[str], **fields: Any) -> int:
        if not ids or not fields:
            return 0
        id_set = set(ids)
        with self._lock:
            items = self.load()
            n = 0
            for it in items:
                if it.get("id") in id_set:
                    it.update(fields)
                    n += 1
            if n:
                self.save(items)
            return n

    def purge_by_status(self, status: str) -> int:
        """Remove every history item whose ``status`` field equals ``status``.
        Returns the count actually removed."""
        if not status:
            return 0
        with self._lock:
            items = self.load()
            kept = [it for it in items if it.get("status") != status]
            removed = len(items) - len(kept)
            if removed:
                self.save(kept)
            return removed
