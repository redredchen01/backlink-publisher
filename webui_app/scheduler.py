import json
import uuid
import random
from datetime import datetime, timedelta

from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from backlink_publisher._util.logger import plan_logger

from webui_store import drafts_store as _drafts_store
from webui_store import history_store as _history_store
from webui_store import queue_store as _queue_store

from .helpers.cli_runner import run_pipe, strip_cli_diagnostic_banner
from .helpers.history import (
    _parse_publish_results,
    _push_history_per_row,
    _push_history_single_failure,
)

_MAX_BACKOFF_SECONDS = 3600


def _exponential_backoff(retry_count: int) -> int:
    """Calculate exponential backoff time with jitter."""
    base = 5 * (2 ** retry_count)
    return min(base + random.randint(0, 10), _MAX_BACKOFF_SECONDS)


def _execute_publish_task(task: dict) -> dict:
    """Execute a single publish task pipeline (Plan -> Validate -> Publish)."""
    task_id = task['id']
    _queue_store.update_task(task_id, {'status': 'processing'})
    try:
        config = task['config']
        urls = task['urls']
        if not urls:
            raise ValueError("No URLs provided")

        platform = config.get('platform', 'medium')
        language = config.get('target_language', 'zh-CN')
        url_mode = config.get('url_mode', 'A')
        publish_mode = config.get('publish_mode', 'draft')

        # 1. Plan
        seed = {
            'target_url': urls[0],
            'platform': platform,
            'language': language,
            'url_mode': url_mode,
            'extra_urls': urls[1:],
            'custom_title': config.get('custom_title', ''),
            'custom_tags': config.get('custom_tags', ''),
        }
        res_plan = run_pipe(['plan-backlinks'], json.dumps([seed]))
        plans_jsonl = res_plan['stdout']

        # 2. Validate
        run_pipe(['validate-backlinks'], plans_jsonl)

        # 3. Publish
        cmd_pub = ['publish-backlinks', '--platform', platform, '--mode', publish_mode]
        res_pub = run_pipe(cmd_pub, plans_jsonl)

        return {'status': 'success', 'completed_at': datetime.now().isoformat()}
    except Exception as exc:
        retry_count = task.get('retry_count', 0)
        max_retries = task.get('max_retries', 3)

        stderr = str(exc)
        backoff = _exponential_backoff(retry_count)
        next_retry = datetime.now() + timedelta(seconds=backoff)

        if retry_count >= max_retries:
            return {'status': 'failed', 'error': '已达最大重试次数'}

        if "429" in stderr or "Too Many Requests" in stderr:
            error_msg = f'频率限制 (429)，将在 {next_retry.strftime("%H:%M")} 重试'
        else:
            error_msg = stderr

        return {
            'status': 'failed',
            'error': error_msg,
            'retry_count': retry_count + 1,
            'next_retry_at': next_retry.isoformat()
        }


_scheduler = BackgroundScheduler(
    executors={'default': APSThreadPoolExecutor(max_workers=1)},
    job_defaults={'misfire_grace_time': 3600},
)


def _process_queue_job() -> None:
    """轮询队列中的 pending 任务并执行发布，支持 429 自动退避。"""
    tasks = _queue_store.load()
    now = datetime.now()

    # 查找任务：PENDING 且 不在退避时间内
    pending = [t for t in tasks if t.get('status') in ('pending', 'failed')
               and (not t.get('next_retry_at') or datetime.fromisoformat(t['next_retry_at']) <= now)]

    for task in pending:
        task_id = task['id']
        try:
            result = _execute_publish_task(task)
            _queue_store.update_task(task_id, result)
        except Exception as exc:
            _queue_store.update_task(task_id, {'status': 'failed', 'error': str(exc)})


def _publish_draft_job(item_id: str) -> None:
    """APScheduler job: publish a draft item and update history."""
    item = _drafts_store.get_item(item_id)
    if not item or item.get('status') != 'scheduled':
        return

    platform = item.get('platform', 'medium')
    publish_mode = item.get('publish_mode', 'draft')
    plans_jsonl = item.get('plans_jsonl', '')

    try:
        cmd = ['publish-backlinks', '--platform', platform, '--mode', publish_mode]
        result = run_pipe(cmd, plans_jsonl)
        published = result['stdout']

        if not published.strip():
            raise RuntimeError(result.get('stderr') or '发布失败，无输出')

        publish_results = _parse_publish_results(published)
        article_urls = [
            u for r in publish_results
            for u in ((r.get('published_url'), r.get('draft_url')))
            if u
        ]

        # Reflect aggregate outcome on the draft row itself. If any row is
        # `*_unverified`, the draft is marked `published_unverified` so the
        # UI badge tells the truth even before recheck runs.
        draft_status = 'published'
        any_unverified = any(
            (r.get('status') or '').endswith('_unverified') for r in publish_results
        )
        if any_unverified:
            draft_status = 'published_unverified'
        _drafts_store.update_item(
            item_id, status=draft_status,
            article_urls=article_urls,
            published_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
        )

        # Plan 2026-05-19-006 Unit 1: per-row truth-propagation. The old
        # implementation hard-wrote `'drafted'` / `'published'` regardless
        # of per-row `status`, hiding `*_unverified` rows as solid ✓.
        _push_history_per_row(
            publish_results,
            target_url_fallback=item.get('target_url', 'unknown'),
            platform_fallback=platform,
            language_fallback=item.get('language', 'zh-CN'),
        )
    except Exception as exc:
        msg = strip_cli_diagnostic_banner(str(exc)) or str(exc)
        _drafts_store.update_item(item_id, status='failed', error=msg)
        _push_history_single_failure(
            target_url=item.get('target_url', 'unknown'),
            platform=platform,
            language=item.get('language', 'zh-CN'),
            error=msg,
        )


def _schedule_draft_job(item_id: str, run_date: datetime) -> None:
    _scheduler.add_job(
        _publish_draft_job, trigger='date', run_date=run_date,
        id=item_id, args=[item_id], replace_existing=True,
    )


def _restore_scheduled_jobs() -> None:
    """On startup, re-register any 'scheduled' draft items into APScheduler."""
    _scheduler.add_job(
        _process_queue_job,
        trigger='interval',
        minutes=1,
        id='queue_processor',
        replace_existing=True,
    )

    now = datetime.now()
    for item in _drafts_store.load():
        if item.get('status') != 'scheduled':
            continue
        item_id = item.get('id')
        ts = item.get('scheduled_at')
        if not item_id or not ts:
            continue
        try:
            run_date = datetime.fromisoformat(ts)
            if run_date < now:
                run_date = now + timedelta(seconds=5)
            _schedule_draft_job(item_id, run_date)
        except Exception:
            plan_logger.warn("restore_scheduled_job_failed", item_id=item_id, ts=ts)
