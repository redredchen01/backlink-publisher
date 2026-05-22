"""Template context builders and render helper — Plan 2026-05-21-007 Unit 5."""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import render_template, session
from google.oauth2.credentials import Credentials

from backlink_publisher import checkpoint as _checkpoint_mod
from backlink_publisher.config import (
    _config_dir,
    load_blogger_token,
    load_config,
    merge_site_url_categories,
    save_config,
    upgrade_target_to_threeurl,
)
from backlink_publisher._util.logger import plan_logger

from webui_store import (
    drafts_store as _drafts_store,
    history_store as _history_store,
    profiles_store as _profiles_store,
    queue_store as _queue_store,
    schedule_store as _schedule_store,
)

from .security import _FLASK_PORT, _ensure_csrf_token, _oauth_callback_uri


def _llm_settings_file() -> Path:
    # Lazy so BACKLINK_PUBLISHER_CONFIG_DIR rebinds are honored per-call.
    return _config_dir() / 'llm-settings.json'


def _image_gen_status(cfg) -> dict:
    """Snapshot of image-gen state for the Settings template.

    Reads ``Config.image_gen`` (config.toml ``[image_gen]`` section) plus
    the on-disk presence + mtime of ``frw-token.json``.  The api_key
    itself is NEVER returned — even shape/length information leaks
    timing-attack surface.
    """
    import datetime as _dt
    cfg_dict: dict | None = None
    if cfg.image_gen is not None:
        cfg_dict = {
            "base_url": cfg.image_gen.base_url,
            "model": cfg.image_gen.model,
            "banner_size": cfg.image_gen.banner_size,
            "daily_cap": cfg.image_gen.daily_cap,
            "per_run_cap": cfg.image_gen.per_run_cap,
            "strict": cfg.image_gen.strict,
            "use_image_gen": cfg.image_gen.use_image_gen,
            "auto_disable_threshold": cfg.image_gen.auto_disable_threshold,
        }

    token_path = cfg.frw_token_path
    token_present = token_path.exists()
    token_mtime: str | None = None
    if token_present:
        try:
            token_mtime = _dt.datetime.fromtimestamp(
                token_path.stat().st_mtime, tz=_dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
        except OSError:
            token_mtime = None

    return {
        "configured": cfg_dict is not None,
        "config": cfg_dict,
        "token_path": str(token_path),
        "token_present": token_present,
        "token_mtime": token_mtime,
    }


def _load_llm_settings() -> dict:
    defaults = {
        'api_key': '',
        'endpoint': '',
        'model': '',
        'temperature': 0.7,
        'system_prompt': '',
        'use_article_gen': False,
        'article_system_prompt': '',
        'image_gen_api_key': '',
        'use_image_gen': False
    }
    path = _llm_settings_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            defaults.update(data)
        except Exception:
            plan_logger.warning("failed to parse llm-settings.json, using defaults")
    return defaults


def _get_blogger_token_status() -> dict:
    """Return token health status without making network calls."""
    try:
        cfg = load_config()
        token_data = load_blogger_token(cfg.blogger_token_path)
        if not token_data:
            return {'state': 'none', 'label': '未授权', 'days_left': None}
        if not cfg.blogger_oauth:
            return {'state': 'none', 'label': '未配置 OAuth', 'days_left': None}
        try:
            creds = Credentials.from_authorized_user_info(
                token_data, ['https://www.googleapis.com/auth/blogger']
            )
        except Exception:
            return {'state': 'expired', 'label': 'Token 无效', 'days_left': 0}
        if creds.expiry is None:
            return {'state': 'ok', 'label': 'Token 有效', 'days_left': None}
        now = datetime.now(timezone.utc)
        expiry = creds.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        days = (expiry - now).days
        if days < 0:
            if creds.refresh_token:
                return {'state': 'expiring', 'label': 'Token 已过期（将自动刷新）',
                        'days_left': days}
            return {'state': 'expired', 'label': 'Token 已过期，需重新授权',
                    'days_left': days}
        if days <= 3:
            return {'state': 'expiring', 'label': f'Token {days} 天后到期',
                    'days_left': days}
        return {'state': 'ok', 'label': f'Token 有效（{days} 天）', 'days_left': days}
    except Exception:
        return {'state': 'ok', 'label': 'Blogger 已连接', 'days_left': None}


def _get_velog_status() -> dict:
    """Return velog channel status for the WebUI badge (6 states)."""
    try:
        cfg = load_config()
        from backlink_publisher.publishing.adapters.velog_graphql import (
            _effective_cap,
            _read_count,
        )
        velog_cfg = cfg.velog
        cookies_path = (
            velog_cfg.cookies_path if velog_cfg else
            cfg.config_dir / "velog-cookies.json"
        )
        count_path = cfg.config_dir / "velog-rate-limit.json"
        cap = _effective_cap()

        if not cookies_path.exists():
            return {
                'state': 'err',
                'label': '未绑定',
                'guide': '运行: velog-login',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        try:
            mode = os.stat(cookies_path).st_mode & 0o777
            if mode != 0o600:
                return {
                    'state': 'permission_denied',
                    'label': f'权限错误 ({oct(mode)})',
                    'guide': f'chmod 600 {cookies_path}',
                    'cookies_path': str(cookies_path),
                    'count': 0,
                    'cap': cap,
                }
        except PermissionError:
            return {
                'state': 'permission_denied',
                'label': '无法读取 cookie 文件（uid 不匹配）',
                'guide': f'chmod 640 {cookies_path}  # 或确认 WebUI 与 CLI 使用同一 uid',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        try:
            raw = json.loads(cookies_path.read_text())
            cookie_list = raw.get('cookies', [])
            if not cookie_list:
                return {
                    'state': 'warn',
                    'label': 'Cookie 文件为空',
                    'guide': 'velog-login',
                    'cookies_path': str(cookies_path),
                    'count': 0,
                    'cap': cap,
                }
        except Exception:
            return {
                'state': 'warn',
                'label': 'Cookie 文件解析失败',
                'guide': 'velog-login',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        count, _ = _read_count(count_path)
        if count >= cap:
            return {
                'state': 'cap_reached',
                'label': f'今日上限已达 ({count}/{cap})',
                'guide': '重置时间：UTC 午夜',
                'cookies_path': str(cookies_path),
                'count': count,
                'cap': cap,
            }

        mtime = cookies_path.stat().st_mtime
        if (datetime.now().timestamp() - mtime) < 60:
            return {
                'state': 'fresh',
                'label': '刚刚绑定',
                'guide': '',
                'cookies_path': str(cookies_path),
                'count': count,
                'cap': cap,
            }

        return {
            'state': 'ok',
            'label': f'已绑定（今日 {count}/{cap}）',
            'guide': '',
            'cookies_path': str(cookies_path),
            'count': count,
            'cap': cap,
        }

    except Exception as exc:
        return {
            'state': 'err',
            'label': f'状态检查失败: {exc}',
            'guide': 'velog-login',
            'cookies_path': '',
            'count': 0,
            'cap': 5,
        }


def _persist_three_tier_config(
    main_url: str, category_url: str, work_url: str,
) -> None:
    """Persist the homepage form's three-tier URL data via ThreeUrlConfig."""
    cfg = load_config()
    upgraded = upgrade_target_to_threeurl(
        cfg,
        main_url=main_url,
        category_url=category_url or None,
        work_url=work_url or None,
    )
    domain_key = main_url.rstrip("/")
    merged = dict(cfg.target_three_url)
    merged[domain_key] = upgraded
    save_config(cfg, target_anchor_keywords=None, target_three_url=merged)

    site_additions: dict[str, str] = {"home": main_url}
    if category_url:
        site_additions["category"] = category_url
    merge_site_url_categories(main_url, site_additions)

    plan_logger.recon(
        "homepage_form_persisted",
        main=main_url,
        list_url=upgraded.list_url,
        work_count=len(upgraded.work_urls),
    )


def _load_schedule_settings() -> dict:
    defaults = {'min_interval_hours': 4, 'jitter_minutes': 30}
    loaded = _schedule_store.load()
    if isinstance(loaded, dict):
        defaults.update(loaded)
    return defaults


def _save_schedule_settings(data: dict) -> None:
    _schedule_store.save(data)


def _calc_next_available(requested_dt: datetime) -> datetime:
    """Return the earliest publish time that respects min-interval + jitter."""
    settings = _load_schedule_settings()
    min_hours = settings.get('min_interval_hours', 4)
    jitter_mins = settings.get('jitter_minutes', 30)

    last_published = None
    for item in _drafts_store.load():
        if item.get('status') in ('published', 'scheduled'):
            ts = item.get('published_at') or item.get('scheduled_at')
            if ts:
                try:
                    dt = datetime.fromisoformat(ts) if 'T' in ts else \
                         datetime.strptime(ts, '%Y-%m-%d %H:%M')
                    if last_published is None or dt > last_published:
                        last_published = dt
                except ValueError:
                    plan_logger.warn("_calc_next_available: bad date in drafts_store", ts=ts)

    for item in _history_store.load():
        ts = item.get('created_at')
        if ts and item.get('status') in ('drafted', 'published'):
            try:
                dt = datetime.strptime(ts, '%Y-%m-%d %H:%M')
                if last_published is None or dt > last_published:
                    last_published = dt
            except ValueError:
                plan_logger.warn("_calc_next_available: bad date in history_store", ts=ts)

    if last_published is None:
        return requested_dt
    earliest = last_published + timedelta(hours=min_hours)
    if jitter_mins > 0:
        earliest += timedelta(minutes=random.randint(0, jitter_mins))
    return max(requested_dt, earliest)


def _load_incomplete_run():
    """Return the most recent incomplete checkpoint run (with pending_count), or None."""
    try:
        runs = _checkpoint_mod.list_incomplete()
    except Exception:
        return None
    if not runs:
        return None
    run = runs[0]
    pending_count = sum(
        1 for i in run.get("items", []) if i.get("status") in ("pending", "failed")
    )
    return {**run, "pending_count": pending_count}


def _get_medium_browser_status(cfg, *, session=None) -> dict:
    """Return a dict describing the Medium browser fallback readiness.

    Reads only the filesystem and Python import state — no Playwright launch,
    no network call.  ``logged_in`` state is set only via flask.session after
    a successful probe_login_status() invocation.
    """
    import platform as _plat
    from datetime import datetime, timezone

    try:
        from backlink_publisher.publishing.adapters.medium_browser import (
            sync_playwright as _spw,
        )
        playwright_installed = _spw is not None
    except Exception:
        playwright_installed = False

    brave_macos = _plat.system() == "Darwin"
    user_data_dir = cfg.medium_user_data_dir or (cfg.config_dir / "chrome-profile-default")
    cookies_path = user_data_dir / "Default" / "Cookies"
    singleton_path = user_data_dir / "SingletonLock"

    profile_has_cookies = cookies_path.exists()
    singleton_lock_present = singleton_path.exists()
    cookies_mtime: str | None = None
    cookies_age_days: int | None = None

    if profile_has_cookies:
        try:
            mtime = cookies_path.stat().st_mtime
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            cookies_mtime = dt.isoformat()
            cookies_age_days = (datetime.now(timezone.utc) - dt).days
        except OSError:
            cookies_age_days = 0

    if not playwright_installed and not brave_macos:
        state = "not_installed"
    elif not profile_has_cookies:
        state = "no_profile"
    elif session is not None and session.get("medium_probe_logged_in"):
        state = "logged_in"
    else:
        state = "profile_exists_unverified"

    return dict(
        playwright_installed=playwright_installed,
        brave_macos=brave_macos,
        profile_dir=str(user_data_dir),
        profile_has_cookies=profile_has_cookies,
        cookies_mtime=cookies_mtime,
        cookies_age_days=cookies_age_days,
        singleton_lock_present=singleton_lock_present,
        state=state,
    )


def _token_paste_status(cfg, channel: str, load_fn, *, token_field: str = "token") -> dict:
    """Status dict consumed by _settings_channel_token_paste.html.

    Reads the platform's token file via the load function injected by
    the caller. Returns {bound, masked, dofollow}. Defensive against any
    load failure — broken token files surface as "unbound" rather than
    crashing the settings page render.

    ``token_field`` is the JSON key to read from the token file (default
    "token"). Dev.to uses "api_key" instead.
    """
    from backlink_publisher.publishing.registry import dofollow_status
    try:
        token_path_attr = f"{channel}_token_path"
        token_path = getattr(cfg, token_path_attr, None)
        data = load_fn(token_path) if token_path else load_fn()
    except Exception:
        data = None
    token = (data or {}).get(token_field, "") if isinstance(data, dict) else ""
    bound = bool(token)
    if bound and len(token) > 6:
        masked = token[:3] + "*" * (len(token) - 6) + token[-3:]
    elif bound:
        masked = "*" * len(token)
    else:
        masked = ""
    return {
        "bound": bound,
        "masked": masked,
        "dofollow": dofollow_status(channel),
    }


def _token_paste_status_notion(cfg, load_fn) -> dict:
    """Status dict for the Notion token-paste card.

    Notion's token file has two fields (integration_token + database_id)
    rather than the single 'token' field used by ghpages/hashnode/devto.
    """
    from backlink_publisher.publishing.registry import dofollow_status
    try:
        token_path = getattr(cfg, "notion_token_path", None)
        data = load_fn(token_path) if token_path else load_fn()
    except Exception:
        data = None
    integration_token = (data or {}).get("integration_token", "") if isinstance(data, dict) else ""
    database_id = (data or {}).get("database_id", "") if isinstance(data, dict) else ""
    bound = bool(integration_token and database_id)
    if bound and len(integration_token) > 6:
        masked = integration_token[:3] + "*" * (len(integration_token) - 6) + integration_token[-3:]
    elif bound:
        masked = "*" * len(integration_token)
    else:
        masked = ""
    return {
        "bound": bound,
        "masked": masked,
        "dofollow": dofollow_status("notion"),
        "database_id_set": bool(database_id),
    }


def _settings_context(flash=None):
    """Build template context for the settings page."""
    from flask import session as _flask_session

    from backlink_publisher.config import (
        load_devto_token,
        load_ghpages_token,
        load_hashnode_token,
        load_medium_token,
        load_notion_token,
    )
    from backlink_publisher.cli._bind.channels import CHANNELS
    from webui_store.channel_status import list_all as _channel_list_all
    from ..services.bind_job import BIND_ERROR_MESSAGES

    cfg = load_config()
    token_data = load_blogger_token(cfg.blogger_token_path)
    medium_token_data = load_medium_token()

    ghpages_status = _token_paste_status(cfg, "ghpages", load_ghpages_token)
    hashnode_status = _token_paste_status(cfg, "hashnode", load_hashnode_token)
    ghpages_config_summary = [
        ("repo", cfg.ghpages.repo if cfg.ghpages else ""),
        ("branch", cfg.ghpages.branch if cfg.ghpages else "gh-pages"),
        ("path_template", cfg.ghpages.path_template if cfg.ghpages else "_posts/{date}-{slug}.md"),
    ]
    hashnode_config_summary = [
        ("publication_id", cfg.hashnode.publication_id if cfg.hashnode else ""),
    ]

    notion_status = _token_paste_status_notion(cfg, load_notion_token)
    devto_status = _token_paste_status(cfg, "devto", load_devto_token, token_field="api_key")
    notion_config_summary: list[tuple[str, str]] = []
    devto_config_summary: list[tuple[str, str]] = []

    token = cfg.medium_integration_token or ""
    masked = ("*" * 8 + token[-4:]) if len(token) > 4 else ("*" * len(token))

    all_targets = sorted(
        set(cfg.blogger_blog_ids.keys()) | set(cfg.target_anchor_keywords.keys())
    )

    try:
        from ..medium_liveness import medium_liveness_check
        medium_liveness_check()
    except Exception:  # noqa: BLE001 — Settings render must not depend on probe
        pass

    try:
        channel_statuses = _channel_list_all()
    except Exception:
        channel_statuses = {}

    try:
        csrf_token = _ensure_csrf_token()
    except Exception:
        csrf_token = ""

    velog_status = _get_velog_status()

    try:
        from backlink_publisher.publishing.registry import registered_platforms
        from ..binding_status import get_channel_status, HIDDEN_FROM_UI
        dashboard_channels = [
            (name, get_channel_status(name, cfg))
            for name in registered_platforms()
            if name not in HIDDEN_FROM_UI
        ]
    except Exception:
        dashboard_channels = []

    return dict(
        flash=flash,
        csrf_token=csrf_token,
        dashboard_channels=dashboard_channels,
        medium_browser_status=_get_medium_browser_status(cfg, session=_flask_session),
        blogger_token=bool(token_data),
        blogger_client_id=cfg.blogger_oauth.client_id if cfg.blogger_oauth else "",
        blogger_client_secret_set=bool(cfg.blogger_oauth and cfg.blogger_oauth.client_secret),
        blog_ids=cfg.blogger_blog_ids,
        medium_token_set=bool(token),
        medium_token_masked=masked if token else "",
        medium_token_file_exists=bool(medium_token_data),
        medium_oauth_configured=bool(medium_token_data and cfg.medium_oauth),
        config_path=str(cfg.config_dir / "config.toml"),
        token_path=str(cfg.blogger_token_path),
        port=_FLASK_PORT,
        callback_uri=_oauth_callback_uri(),
        profiles=_profiles_store.load(),
        plans_list=[],
        schedule_settings=_load_schedule_settings(),
        llm_settings=_load_llm_settings(),
        image_gen_status=_image_gen_status(cfg),
        all_targets=all_targets,
        target_anchor_keywords=cfg.target_anchor_keywords,
        binding_channels=sorted(CHANNELS),
        channel_statuses=channel_statuses,
        bind_error_messages=BIND_ERROR_MESSAGES,
        velog_status=velog_status,
        velog_cookies_path=velog_status.get('cookies_path', ''),
        ghpages_status=ghpages_status,
        ghpages_config_summary=ghpages_config_summary,
        hashnode_status=hashnode_status,
        hashnode_config_summary=hashnode_config_summary,
        notion_status=notion_status,
        notion_config_summary=notion_config_summary,
        devto_status=devto_status,
        devto_config_summary=devto_config_summary,
    )


def _draft_tab_extra() -> dict:
    """Extra template context for the draft tab."""
    return {
        'schedule_settings': _load_schedule_settings(),
    }


def _group_history(items: list[dict]) -> list[dict]:
    """Group consecutive history items by ``run_id`` into run-groups.

    Items sharing a ``run_id`` (from one ``_push_history_per_row`` call) fold
    into one group so the UI renders them as a collapsible card instead of N
    flat rows. Items without ``run_id`` each form a group of size 1.

    Each group dict: run_id, rows, created_at, platform, language,
    n_published, n_drafted, n_failed, n_unverified, n_total, is_multi.
    """
    groups: list[dict] = []
    current: dict | None = None
    for item in items:
        rid = item.get("run_id")
        if rid and current and current["run_id"] == rid:
            current["rows"].append(item)
        else:
            current = {
                "run_id": rid,
                "rows": [item],
                "created_at": item.get("created_at", ""),
                "platform": item.get("platform", ""),
                "language": item.get("language", ""),
            }
            groups.append(current)
    for g in groups:
        statuses = [i.get("status", "") for i in g["rows"]]
        g["n_published"] = sum(1 for s in statuses if s in ("published", "success"))
        g["n_drafted"] = sum(1 for s in statuses if s == "drafted")
        g["n_failed"] = sum(1 for s in statuses if s == "failed")
        g["n_unverified"] = sum(1 for s in statuses if "unverified" in s)
        g["n_total"] = len(g["rows"])
        g["is_multi"] = g["n_total"] > 1
    return groups


def _render(template_name: str, **kwargs):
    """Render a Jinja2 template, auto-injecting common context.

    Auto-injected context (when not provided by caller):
      - history, blogger_token_status, profiles, draft_queue, tasks,
        now_iso, suggested_next, incomplete_run
    """
    if 'history' not in kwargs:
        kwargs['history'] = _history_store.load()
    if 'grouped_history' not in kwargs:
        kwargs['grouped_history'] = _group_history(kwargs['history'])
    if 'blogger_token_status' not in kwargs:
        kwargs['blogger_token_status'] = _get_blogger_token_status()
    if 'profiles' not in kwargs:
        kwargs['profiles'] = _profiles_store.load()
    if 'draft_queue' not in kwargs:
        kwargs['draft_queue'] = _drafts_store.load()
    if 'tasks' not in kwargs:
        try:
            kwargs['tasks'] = _queue_store.load()
        except Exception:
            kwargs['tasks'] = []
    if 'now_iso' not in kwargs:
        now = datetime.now()
        kwargs['now_iso'] = now.strftime('%Y-%m-%dT%H:%M')
        kwargs.setdefault(
            'suggested_next',
            _calc_next_available(now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
        )
    if 'incomplete_run' not in kwargs:
        kwargs['incomplete_run'] = _load_incomplete_run()
    return render_template(template_name, **kwargs)
