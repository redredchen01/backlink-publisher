"""Shared helpers extracted from legacy webui.py — Plan 2026-05-18-001 Unit 3."""

from __future__ import annotations

import json
import os
import random
import re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import abort, render_template, request, session
from google.oauth2.credentials import Credentials

from backlink_publisher import checkpoint as _checkpoint_mod
from backlink_publisher import content_fetch
from backlink_publisher.config import (
    _domain_label,
    load_blogger_token,
    load_config,
    load_medium_token,
    merge_site_url_categories,
    save_config,
    upgrade_target_to_threeurl,
)
from backlink_publisher.logger import plan_logger

from webui_store import (
    drafts_store as _drafts_store,
    history_store as _history_store,
    profiles_store as _profiles_store,
    schedule_store as _schedule_store,
)

_LLM_SETTINGS_FILE = Path.home() / '.config' / 'backlink-publisher' / 'llm-settings.json'
_FLASK_PORT = int(os.environ.get('PORT', 8888))
_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_TRUTHY_BYPASS = {"1", "true", "yes"}


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
    if _LLM_SETTINGS_FILE.exists():
        try:
            data = json.loads(_LLM_SETTINGS_FILE.read_text(encoding='utf-8'))
            defaults.update(data)
        except Exception:
            plan_logger.warning("failed to parse llm-settings.json, using defaults")
    return defaults


def _is_fetch_verify_disabled() -> bool:
    return os.environ.get("BACKLINK_NO_FETCH_VERIFY", "").strip().lower() in _TRUTHY_BYPASS


# ─────────────────────────────────────────────────────────────────────────────
# Content-fetch gate (plan 2026-05-14-007)
# ─────────────────────────────────────────────────────────────────────────────


def _content_gate_enabled() -> bool:
    return not _is_fetch_verify_disabled()


def _verify_urls_or_error(
    urls: list[str], field_label: str
) -> tuple[list[str], str | None]:
    if not urls:
        return [], None
    if not _content_gate_enabled():
        return list(urls), None
    results = content_fetch.verify_urls_batch(urls)
    survivors: list[str] = []
    failures: list[str] = []
    for u in urls:
        ok, reason, _title = results.get(u, (False, "missing_result", None))
        if ok:
            survivors.append(u)
        else:
            failures.append(f"{u} ({reason})")
    if failures:
        joined = ", ".join(failures)
        return survivors, f"{field_label} 无可访问内容: {joined}"
    return survivors, None


# ─────────────────────────────────────────────────────────────────────────────
# Token status (Blogger)
# ─────────────────────────────────────────────────────────────────────────────


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
    """Return velog channel status for the WebUI badge (6 states).

    States:
      fresh            — file just written (mtime < 60 s)
      ok               — file exists, 0600, parseable, cap not reached
      warn             — file exists but JSON broken / cookies empty
      err              — file missing (needs velog-login)
      cap_reached      — daily cap exhausted
      permission_denied — file exists but WebUI uid cannot read (not 0600 or EPERM)
    """
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

        # file absent → err
        if not cookies_path.exists():
            return {
                'state': 'err',
                'label': '未绑定',
                'guide': f'运行: backlink-publisher velog-login',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        # permission check
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

        # parse cookies
        try:
            raw = json.loads(cookies_path.read_text())
            cookie_list = raw.get('cookies', [])
            if not cookie_list:
                return {
                    'state': 'warn',
                    'label': 'Cookie 文件为空',
                    'guide': 'backlink-publisher velog-login',
                    'cookies_path': str(cookies_path),
                    'count': 0,
                    'cap': cap,
                }
        except Exception:
            return {
                'state': 'warn',
                'label': 'Cookie 文件解析失败',
                'guide': 'backlink-publisher velog-login',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        # daily count
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

        # fresh: mtime < 60s
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

        # ok
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
            'guide': 'backlink-publisher velog-login',
            'cookies_path': '',
            'count': 0,
            'cap': 5,
        }


# ─────────────────────────────────────────────────────────────────────────────
# URL metadata fetchers
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_page(url, timeout=10):
    headers = {'User-Agent':
               'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    resp = requests.get(url, headers=headers, timeout=timeout, verify=False)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')


def _extract_title(soup):
    og = soup.find('meta', property='og:title')
    if og:
        return (og.get('content', '') or '').strip()
    tag = soup.find('title')
    return tag.text.strip() if tag else ''


def _extract_description(soup):
    og = soup.find('meta', property='og:description')
    if og:
        return (og.get('content', '') or '').strip()
    meta = soup.find('meta', attrs={'name': 'description'})
    return (meta.get('content', '') or '').strip() if meta else ''


def fetch_url_metadata(url):
    try:
        soup = _fetch_page(url, timeout=10)
        title = _extract_title(soup)
        desc = _extract_description(soup)
        return {'url': url, 'title': title, 'description': desc, 'status': 'success'}
    except Exception as e:
        return {'url': url, 'title': '', 'description': '',
                'status': 'error', 'error': str(e)}


def fetch_full_tdk(url):
    try:
        soup = _fetch_page(url, timeout=15)
        title = _extract_title(soup)
        description = _extract_description(soup)
        keywords = ''
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            keywords = (meta_keywords.get('content', '') or '').strip()

        suggested_anchors = []
        if keywords:
            suggested_anchors = [k.strip() for k in keywords.split(',') if k.strip()]
        if not suggested_anchors and title:
            suggested_anchors = [t for t in title.replace('|', '-').replace('_', '-').split('-') if len(t.strip()) > 3][:3]

        return {
            'title': title, 'description': description,
            'keywords': keywords, 'suggested_anchors': suggested_anchors,
            'status': 'success'
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def detect_platform(url):
    # Plan 2026-05-19-002 U2 / R10b SPLIT: wordpress branch removed (no
    # backing adapter ever existed) but unknown-domain fallback stays at
    # 'medium' — flipping that to None is a separate decision deferred
    # to a follow-up (scope-guardian F1 / adversarial F8).
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if 'medium.com' in domain:
        return 'medium'
    if 'blogspot.com' in domain or 'blogger.com' in domain:
        return 'blogger'
    return 'medium'


def detect_language(url):
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    if '.cn' in domain or 'cn' in path:
        return 'zh-CN'
    if '.tw' in domain or 'tw' in path or 'hk' in path:
        return 'zh-TW'
    if '.jp' in domain or 'jp' in path or 'ja' in path:
        return 'ja'
    if '.ru' in domain or 'ru' in path:
        return 'ru'
    if '.es' in domain or 'es' in path:
        return 'es'
    if '.de' in domain or 'de' in path:
        return 'de'
    if '.fr' in domain or 'fr' in path:
        return 'fr'
    return 'en'


def get_main_domain(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_publish_results(jsonl_str):
    results = []
    for line in (jsonl_str or '').strip().split('\n'):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


def _normalize_url(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val
    return val


# ─────────────────────────────────────────────────────────────────────────────
# Three-URL persistence
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# Schedule
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint helpers
# ─────────────────────────────────────────────────────────────────────────────


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


def _check_localhost():
    if request.remote_addr not in _LOOPBACK_HOSTS:
        abort(403)


def _validate_webui_run_id(run_id):
    if not run_id or not _RUN_ID_RE.match(run_id):
        abort(400)


# ─────────────────────────────────────────────────────────────────────────────
# OAuth / bind helpers
# ─────────────────────────────────────────────────────────────────────────────


def _oauth_callback_uri():
    return f'http://localhost:{_FLASK_PORT}/settings/blogger/oauth-callback'


def _resolve_bind_host() -> str:
    host = os.environ.get("BIND_HOST", "127.0.0.1")
    if host in _LOOPBACK_HOSTS:
        return host
    if os.environ.get("BACKLINK_PUBLISHER_ALLOW_NETWORK") == "1":
        return host
    raise RuntimeError(
        f"refusing to bind to non-loopback host {host!r}: this WebUI has "
        "minimal auth. Set BACKLINK_PUBLISHER_ALLOW_NETWORK=1 to opt in to "
        "network exposure (only do this on a trusted network)."
    )


def _ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _check_csrf_or_abort() -> None:
    token = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not token or not expected or not secrets.compare_digest(token, expected):
        abort(403)


def _parse_lines(raw: str) -> list[str]:
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _wire_content_fetch_ttl_from_env() -> None:
    if _is_fetch_verify_disabled():
        return
    raw = os.environ.get("BACKLINK_GATE_CACHE_TTL_SECONDS", "900").strip()
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 900.0
    if seconds <= 0:
        return
    content_fetch.set_default_max_age(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Derived pools (plan 006)
# ─────────────────────────────────────────────────────────────────────────────


_DERIVED_BRANDED_MAX: int = 30
_DERIVED_PARTIAL_MAX: int = 60
_DERIVED_PARTIAL_KEEP: int = 3
_DERIVED_PARTIAL_SPLIT_RE = re.compile(r"[。.；;，,、]+")


def _derive_branded_pool(main_url: str, tdk: dict | None) -> list[str]:
    if tdk and tdk.get("title"):
        title = str(tdk["title"]).strip()
        if title:
            return [title[:_DERIVED_BRANDED_MAX]]
    return [_domain_label(main_url)]


def _derive_partial_pool(main_url: str, tdk: dict | None) -> list[str]:
    if tdk and tdk.get("description"):
        desc = str(tdk["description"]).strip()
        if desc:
            phrases = [
                p.strip()[:_DERIVED_PARTIAL_MAX]
                for p in _DERIVED_PARTIAL_SPLIT_RE.split(desc)
                if p and p.strip()
            ]
            if phrases:
                return phrases[:_DERIVED_PARTIAL_KEEP]
    return [_domain_label(main_url)]


def _derive_exact_pool(main_url: str) -> list[str]:
    return [_domain_label(main_url)]


# ─────────────────────────────────────────────────────────────────────────────
# Settings context
# ─────────────────────────────────────────────────────────────────────────────


def _settings_context(flash=None):
    """Build template context for the settings page."""

    cfg = load_config()
    token_data = load_blogger_token(cfg.blogger_token_path)
    medium_token_data = load_medium_token()

    token = cfg.medium_integration_token or ""
    masked = ("*" * 8 + token[-4:]) if len(token) > 4 else ("*" * len(token))

    all_targets = sorted(
        set(cfg.blogger_blog_ids.keys()) | set(cfg.target_anchor_keywords.keys())
    )

    velog_status = _get_velog_status()

    return dict(
        flash=flash,
        blogger_token=bool(token_data),
        blogger_client_id=cfg.blogger_oauth.client_id if cfg.blogger_oauth else "",
        blogger_client_secret=cfg.blogger_oauth.client_secret if cfg.blogger_oauth else "",
        blog_ids=cfg.blogger_blog_ids,
        medium_token_set=bool(token),
        medium_token_masked=masked if token else "",
        medium_oauth_configured=bool(medium_token_data and cfg.medium_oauth),
        config_path=str(cfg.config_dir / "config.toml"),
        token_path=str(cfg.blogger_token_path),
        port=_FLASK_PORT,
        callback_uri=_oauth_callback_uri(),
        profiles=_profiles_store.load(),
        plans_list=[],
        schedule_settings=_load_schedule_settings(),
        llm_settings=_load_llm_settings(),
        all_targets=all_targets,
        target_anchor_keywords=cfg.target_anchor_keywords,
        velog_status=velog_status,
        velog_cookies_path=velog_status.get('cookies_path', ''),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Draft tab extra context
# ─────────────────────────────────────────────────────────────────────────────


def _draft_tab_extra() -> dict:
    """Extra template context for the draft tab."""
    return {
        'schedule_settings': _load_schedule_settings(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# run_pipe — subprocess wrapper for plan/validate/publish
# ─────────────────────────────────────────────────────────────────────────────


_CLI_MODULES = {
    'publish-backlinks': 'backlink_publisher.cli.publish_backlinks',
    'plan-backlinks': 'backlink_publisher.cli.plan_backlinks',
    'validate-backlinks': 'backlink_publisher.cli.validate_backlinks',
    'footprint': 'backlink_publisher.cli.footprint',
    'report-anchors': 'backlink_publisher.cli.report_anchors',
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_REPO_ROOT, 'src')


def _rewrite_cli_cmd(cmd):
    """Rewrite bare CLI command (publish-backlinks, plan-backlinks, ...) to
    ``sys.executable -m <module>`` and inject ``PYTHONPATH=./src``.

    Why: the installed entry-point shims (pyenv shim, .venv/bin/*) can point
    at a stale editable-install path that no longer exists. Running via the
    current interpreter + repo src/ bypasses that and is hermetic.
    """
    if not cmd:
        return cmd, None
    module = _CLI_MODULES.get(cmd[0])
    if module is None:
        return cmd, None
    new_cmd = [sys.executable, '-m', module, *cmd[1:]]
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC_DIR + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else ''
    )
    return new_cmd, env


def run_pipe(cmd, stdin):
    """Run a pipeline command."""
    new_cmd, env = _rewrite_cli_cmd(cmd)
    result = subprocess.run(
        new_cmd,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT or os.getcwd(),
        env=env,
    )
    if result.returncode != 0:
        raise Exception(result.stderr or f"Exit code: {result.returncode}")
    return {'stdout': result.stdout, 'stderr': result.stderr}


# ─────────────────────────────────────────────────────────────────────────────
# Render shim — replaces legacy render_template_string(HTML, ...) calls
# ─────────────────────────────────────────────────────────────────────────────


def _render(template_name: str, **kwargs):
    """Render a Jinja2 template, auto-injecting common context.

    Unit 4: replaces the legacy ``_render(HTML, ...)`` which passed the
    HTML string directly to ``render_template_string``. Now takes a
    template *file* name (e.g., ``"index.html"``) and Flask's
    ``render_template`` finds it under ``webui_app/templates/``.

    Auto-injected context (when not provided by caller):
      - history, blogger_token_status, profiles, draft_queue,
        now_iso, suggested_next, incomplete_run
    """
    if 'history' not in kwargs:
        kwargs['history'] = _history_store.load()
    if 'blogger_token_status' not in kwargs:
        kwargs['blogger_token_status'] = _get_blogger_token_status()
    if 'profiles' not in kwargs:
        kwargs['profiles'] = _profiles_store.load()
    if 'draft_queue' not in kwargs:
        kwargs['draft_queue'] = _drafts_store.load()
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


# ─────────────────────────────────────────────────────────────────────────────
# In-memory work-themed run store (shared between /sites routes)
# ─────────────────────────────────────────────────────────────────────────────


_WORK_THEMED_RUNS: dict[str, dict] = {}
_WORK_THEMED_RUNS_MAX = 50
