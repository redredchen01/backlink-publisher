"""Queue store — background publishing task persistence."""

from __future__ import annotations

from typing import Any

from .base import JsonStore


class QueueStore(JsonStore):
    """JsonStore specialised for task-queue semantics.

    Extends base load/save/update with task-level mutation helpers
    so callers don't have to spell out the read-modify-write pattern.
    """

    def update_task(self, task_id: str, updates: dict[str, Any]) -> None:
        def _apply(tasks: list[dict]) -> list[dict]:
            for t in tasks:
                if t.get("id") == task_id:
                    t.update(updates)
                    break
            return tasks

        self.update(_apply)
