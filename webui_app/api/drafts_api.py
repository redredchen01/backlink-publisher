"""DraftAPI — structured CRUD wrapper around ``drafts_store`` + scheduler.

Centralises the draft lifecycle so routes never touch the store or scheduler
directly.  Every mutating method returns a dict with ``ok`` / ``error`` /
``flash_msg`` for the route's redirect response.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from webui_store import drafts_store as _drafts_store

from ..helpers.contexts import _calc_next_available
from ..scheduler import _publish_draft_job, _scheduler


# ── helpers ────────────────────────────────────────────────────────────────


def _remove_job_silent(job_id: str) -> None:
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass


# ── DraftAPI ───────────────────────────────────────────────────────────────


class DraftAPI:
    """Encapsulates draft item lifecycle.

    Usage::

        api = DraftAPI()
        result = api.create(plans_jsonl, config, platform="velog")
        # result == {"ok": True, "id": "ab12cd34", "flash_msg": "已加入草稿栏"}
    """

    # ── create ───────────────────────────────────────────────────────────

    def create(
        self,
        plans_jsonl: str,
        config: dict[str, Any],
        *,
        platform: str | None = None,
        publish_mode: str = "publish",
        target_url: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Save validated plans as a pending draft queue item."""
        if not plans_jsonl:
            return {"ok": False, "flash_msg": "没有可保存的内容"}

        platform = platform or config.get("platform", "blogger")
        target_url = target_url or config.get("target_url", "unknown")
        language = language or config.get("target_language", "zh-CN")

        item = {
            "id": str(uuid.uuid4())[:8],
            "target_url": target_url,
            "platform": platform,
            "language": language,
            "publish_mode": publish_mode,
            "plans_jsonl": plans_jsonl,
            "status": "pending",
            "scheduled_at": None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": [],
            "error": None,
        }
        _drafts_store.insert_first(item)
        return {
            "ok": True,
            "id": item["id"],
            "flash_msg": "已加入草稿栏",
        }

    # ── read helpers ──────────────────────────────────────────────────────

    def get(self, item_id: str) -> dict[str, Any] | None:
        """Fetch a single draft item by id."""
        return _drafts_store.get_item(item_id)

    def list_all(self) -> list[dict[str, Any]]:
        """Return all draft items."""
        return _drafts_store.load()

    # ── schedule ──────────────────────────────────────────────────────────

    def schedule(
        self,
        item_id: str,
        scheduled_at_str: str,
    ) -> dict[str, Any]:
        """Schedule a draft for publishing at an ISO-8601 datetime string.

        Returns ``{"ok": True, "flash_msg": ...}`` or error dict.
        """
        if not item_id or not scheduled_at_str:
            return {"ok": False, "flash_msg": "参数缺失"}

        try:
            requested_dt = datetime.fromisoformat(scheduled_at_str)
        except ValueError:
            return {"ok": False, "flash_msg": "时间格式错误"}

        final_dt = _calc_next_available(requested_dt)
        _drafts_store.update_item(
            item_id,
            status="scheduled",
            scheduled_at=final_dt.isoformat(),
        )

        from ..scheduler import _schedule_draft_job
        _schedule_draft_job(item_id, final_dt)

        adjusted = final_dt != requested_dt
        msg = f'已排程：{final_dt.strftime("%Y-%m-%d %H:%M")}'
        if adjusted:
            msg += "（已依间隔设定自动调整）"
        return {"ok": True, "flash_msg": msg}

    # ── publish-now ───────────────────────────────────────────────────────

    def publish_now(self, item_id: str) -> dict[str, Any]:
        """Immediately schedule a draft to publish in ~5 seconds."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}

        run_date = datetime.now() + timedelta(seconds=5)
        _drafts_store.update_item(
            item_id,
            status="scheduled",
            scheduled_at=run_date.isoformat(),
        )

        from ..scheduler import _schedule_draft_job
        _schedule_draft_job(item_id, run_date)

        return {"ok": True, "flash_msg": "正在发布，请稍候刷新页面"}

    # ── cancel ────────────────────────────────────────────────────────────

    def cancel(self, item_id: str) -> dict[str, Any]:
        """Cancel a scheduled draft."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}
        _remove_job_silent(item_id)
        _drafts_store.update_item(item_id, status="pending", scheduled_at=None)
        return {"ok": True, "flash_msg": "已取消排程"}

    # ── delete ────────────────────────────────────────────────────────────

    def delete(self, item_id: str) -> dict[str, Any]:
        """Delete a draft item (cancel job if scheduled)."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}
        _remove_job_silent(item_id)
        _drafts_store.delete_item(item_id)
        return {"ok": True, "flash_msg": "已删除"}

    # ── bulk operations ──────────────────────────────────────────────────

    def bulk_delete(self, ids: list[str]) -> dict[str, Any]:
        """Delete multiple drafts by id."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}
        for item_id in ids:
            _remove_job_silent(item_id)
        removed = _drafts_store.bulk_delete(ids)
        return {"ok": True, "flash_msg": f"已删除 {removed} 项"}

    def bulk_publish_now(self, ids: list[str]) -> dict[str, Any]:
        """Schedule multiple drafts for near-immediate publish, staggered."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}
        base = datetime.now()
        scheduled = 0
        for i, item_id in enumerate(ids):
            if not _drafts_store.get_item(item_id):
                continue
            run_date = base + timedelta(seconds=5 + i * 5)
            _drafts_store.update_item(
                item_id,
                status="scheduled",
                scheduled_at=run_date.isoformat(),
            )
            _scheduler.add_job(
                _publish_draft_job,
                trigger="date",
                run_date=run_date,
                id=item_id,
                args=[item_id],
                replace_existing=True,
            )
            scheduled += 1
        return {"ok": True, "flash_msg": f"正在批量发布 {scheduled} 项，请稍候刷新页面"}

    def bulk_cancel(self, ids: list[str]) -> dict[str, Any]:
        """Cancel scheduling for multiple drafts."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}
        cancelled = 0
        for item_id in ids:
            item = _drafts_store.get_item(item_id)
            if not item or item.get("status") != "scheduled":
                continue
            _remove_job_silent(item_id)
            _drafts_store.update_item(item_id, status="pending", scheduled_at=None)
            cancelled += 1
        return {"ok": True, "flash_msg": f"已取消 {cancelled} 项排程"}
