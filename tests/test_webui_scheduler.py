"""Tests for webui_app/scheduler.py — queue processor and draft publisher.

Covers:
  - _exponential_backoff (pure function)
  - _execute_publish_task (full plan→validate→publish pipeline)
  - _process_queue_job (multi-worker dispatch)
  - _publish_draft_job (APScheduler draft publishing)
  - _restore_scheduled_jobs (startup restore)
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import ANY, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


class TestExponentialBackoff:
    def test_base_case(self):
        from webui_app.scheduler import _exponential_backoff
        assert _exponential_backoff(0) >= 5

    def test_increases_with_retries(self):
        from webui_app.scheduler import _exponential_backoff
        # Base: 5 * 2^retry, then + jitter(0-10), capped at _MAX_BACKOFF_SECONDS
        # With jitter, we verify the function returns valid values in expected ranges
        for n in [0, 1, 2, 3]:
            val = _exponential_backoff(n)
            base = 5 * (2 ** n)
            assert base <= val <= base + 10, f"retry {n}: expected [{base},{base+10}], got {val}"

    def test_capped(self):
        from webui_app.scheduler import _exponential_backoff, _MAX_BACKOFF_SECONDS
        assert _exponential_backoff(50) <= _MAX_BACKOFF_SECONDS * 1.25

    def test_jitter_variation(self):
        from webui_app.scheduler import _exponential_backoff
        results = {_exponential_backoff(3) for _ in range(10)}
        assert len(results) > 1


class TestExecutePublishTask:
    SAMPLE_PUBLISH = json.dumps({"title": "Test", "published_url": "https://x.com/post", "status": "published", "target_url": "https://example.com/"})

    @patch("webui_app.scheduler.run_pipe")
    @patch("webui_app.scheduler._push_history_per_row", return_value=[])
    @patch("webui_app.scheduler._queue_store")
    def test_happy_path_full_pipeline(self, mock_qs, mock_history, mock_run_pipe):
        from webui_app.scheduler import _execute_publish_task

        plan_out = json.dumps({"title": "A", "content_markdown": "# A", "links": [], "tags": [], "platform": "blogger", "language": "zh-CN"})
        mock_run_pipe.side_effect = [
            {"stdout": plan_out + "\n", "stderr": ""},
            {"stdout": plan_out + "\n", "stderr": ""},
            {"stdout": self.SAMPLE_PUBLISH + "\n", "stderr": ""},
        ]

        result = _execute_publish_task({
            "id": "t1", "urls": ["https://example.com/"],
            "config": {"platform": "blogger", "publish_mode": "draft", "target_language": "zh-CN", "url_mode": "A"},
        })

        assert result["status"] == "success"
        assert "completed_at" in result
        mock_qs.update_task.assert_any_call("t1", {"status": "processing"})

    @patch("webui_app.scheduler.run_pipe")
    @patch("webui_app.scheduler._push_history_per_row", return_value=[])
    @patch("webui_app.scheduler._queue_store")
    def test_plan_failure_triggers_retry(self, mock_qs, mock_history, mock_run_pipe):
        from webui_app.scheduler import _execute_publish_task

        mock_run_pipe.side_effect = Exception("plan-backlinks failed")

        result = _execute_publish_task({
            "id": "t2", "urls": ["https://example.com/"],
            "config": {"platform": "medium"},
            "retry_count": 0, "max_retries": 5,
        })

        assert result["status"] == "failed"
        assert result["retry_count"] == 1
        assert "next_retry_at" in result

    @patch("webui_app.scheduler.run_pipe")
    @patch("webui_app.scheduler._push_history_per_row", return_value=[])
    @patch("webui_app.scheduler._queue_store")
    def test_publish_failure_with_429(self, mock_qs, mock_history, mock_run_pipe):
        from webui_app.scheduler import _execute_publish_task

        mock_run_pipe.side_effect = Exception("429 Too Many Requests")

        result = _execute_publish_task({
            "id": "t3", "urls": ["https://example.com/"],
            "config": {"platform": "medium"},
            "retry_count": 0, "max_retries": 5,
        })

        assert result["status"] == "failed"
        assert "频率限制" in result["error"]

    @patch("webui_app.scheduler.run_pipe")
    @patch("webui_app.scheduler._push_history_per_row", return_value=[])
    @patch("webui_app.scheduler._queue_store")
    def test_max_retries_exceeded(self, mock_qs, mock_history, mock_run_pipe):
        from webui_app.scheduler import _execute_publish_task

        mock_run_pipe.side_effect = Exception("persistent failure")

        result = _execute_publish_task({
            "id": "t4", "urls": ["https://example.com/"],
            "config": {"platform": "medium"},
            "retry_count": 2, "max_retries": 2,
        })

        assert result["status"] == "failed"
        assert "已达最大重试次数" in result["error"]

    @patch("webui_app.scheduler.run_pipe")
    @patch("webui_app.scheduler._push_history_per_row", return_value=[])
    @patch("webui_app.scheduler._queue_store")
    def test_empty_urls_returns_error(self, mock_qs, mock_history, mock_run_pipe):
        from webui_app.scheduler import _execute_publish_task

        result = _execute_publish_task({
            "id": "t5", "urls": [], "config": {},
        })

        assert result["status"] == "failed"


class TestProcessQueueJob:
    @patch("webui_app.scheduler._execute_publish_task")
    @patch("webui_app.scheduler._queue_store")
    def test_empty_queue(self, mock_qs, mock_exec):
        from webui_app.scheduler import _process_queue_job

        mock_qs.load.return_value = []
        _process_queue_job()
        mock_qs.load.assert_called_once()
        mock_exec.assert_not_called()

    @patch("webui_app.scheduler._execute_publish_task")
    @patch("webui_app.scheduler._queue_store")
    def test_processes_multiple_tasks(self, mock_qs, mock_exec):
        from webui_app.scheduler import _process_queue_job

        mock_qs.load.return_value = [
            {"id": "t1", "urls": ["https://a.com/"], "config": {}, "status": "pending"},
            {"id": "t2", "urls": ["https://b.com/"], "config": {}, "status": "pending"},
        ]
        mock_exec.side_effect = [
            {"status": "success", "completed_at": "now"},
            {"status": "failed", "error": "boom"},
        ]

        _process_queue_job()

        assert mock_exec.call_count == 2
        mock_qs.update_task.assert_any_call("t1", {"status": "success", "completed_at": ANY})
        mock_qs.update_task.assert_any_call("t2", {"status": "failed", "error": ANY})

    @patch("webui_app.scheduler._execute_publish_task")
    @patch("webui_app.scheduler._queue_store")
    def test_worker_exception_handled(self, mock_qs, mock_exec):
        from webui_app.scheduler import _process_queue_job

        mock_qs.load.return_value = [
            {"id": "t1", "urls": ["https://a.com/"], "config": {}, "status": "pending"},
        ]
        mock_exec.return_value = {"status": "failed", "error": "worker crashed"}

        _process_queue_job()

        assert mock_exec.call_count == 1
        mock_qs.update_task.assert_called()


class TestPublishDraftJob:
    SAMPLE_PUBLISH = json.dumps({"title": "Test", "published_url": "https://b.com/p", "status": "published", "target_url": "https://example.com/"})

    @patch("webui_app.scheduler.run_pipe")
    @patch("webui_app.scheduler._push_history_per_row", return_value=[])
    @patch("webui_app.scheduler._drafts_store")
    def test_publishes_scheduled_draft(self, mock_drafts, mock_history, mock_run_pipe):
        from webui_app.scheduler import _publish_draft_job

        mock_drafts.get_item.return_value = {
            "id": "d1", "status": "scheduled", "platform": "blogger",
            "publish_mode": "publish", "plans_jsonl": '{"title":"T"}',
        }
        mock_run_pipe.return_value = {"stdout": self.SAMPLE_PUBLISH + "\n", "stderr": ""}

        _publish_draft_job("d1")

        mock_drafts.update_item.assert_called_once()
        mock_history.assert_called_once()

    @patch("webui_app.scheduler.run_pipe", side_effect=Exception("publish failed"))
    @patch("webui_app.scheduler._push_history_single_failure", return_value=[])
    @patch("webui_app.scheduler._drafts_store")
    def test_draft_failure_updates_status(self, mock_drafts, mock_history, mock_run_pipe):
        from webui_app.scheduler import _publish_draft_job

        mock_drafts.get_item.return_value = {
            "id": "d1", "status": "scheduled", "platform": "blogger",
        }

        _publish_draft_job("d1")

        mock_drafts.update_item.assert_called_once_with("d1", status="failed", error="publish failed")
        mock_history.assert_called_once()

    @patch("webui_app.scheduler.run_pipe")
    @patch("webui_app.scheduler._drafts_store")
    def test_skip_non_scheduled(self, mock_drafts, mock_run_pipe):
        from webui_app.scheduler import _publish_draft_job

        mock_drafts.get_item.return_value = {"id": "d1", "status": "published"}
        _publish_draft_job("d1")
        mock_run_pipe.assert_not_called()


class TestRestoreScheduledJobs:
    @patch("webui_app.scheduler._schedule_draft_job")
    @patch("webui_app.scheduler._scheduler")
    @patch("webui_app.scheduler._drafts_store")
    def test_restores_scheduled_drafts(self, mock_drafts, mock_sched, mock_schedule_job):
        from webui_app.scheduler import _restore_scheduled_jobs

        mock_drafts.load.return_value = [
            {"id": "d1", "status": "scheduled", "scheduled_at": "2027-01-01T12:00:00"},
            {"id": "d2", "status": "pending"},
            {"id": "d3", "status": "scheduled", "scheduled_at": "2020-01-01T00:00:00"},
        ]

        _restore_scheduled_jobs()

        mock_sched.add_job.assert_called_once()
        assert mock_schedule_job.call_count == 2
