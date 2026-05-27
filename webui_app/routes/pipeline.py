"""/ce:plan, /ce:generate, /ce:validate, /ce:publish — Plan Unit 3.

Phase A refactoring: CLI invocations go through ``PipelineAPI`` instead of
raw ``run_pipe`` calls.  Session and template logic remain in the route.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from backlink_publisher._util.markdown import render_to_html
from backlink_publisher._util.logger import plan_logger

from flask import Blueprint, request, session

from ..api import PipelineAPI
from ..api.pipeline_api import publish_state_summary

from ..helpers.cli_runner import surface_cli_error
from ..helpers.contexts import _persist_three_tier_config, _render, _get_velog_status
from ..helpers.history import (
    _push_history_per_row,
    _push_history_single_failure,
)
from ..helpers.url_meta import (
    _normalize_url,
    _verify_urls_or_error,
    detect_language,
    detect_platform,
    fetch_full_tdk,
    fetch_url_metadata,
    get_main_domain,
)

bp = Blueprint("pipeline", __name__)
_api = PipelineAPI()


@bp.route('/ce:plan', methods=['POST'])
def ce_plan():
    main_url = _normalize_url(
        request.form.get('main_url') or request.form.get('target_url') or ''
    )
    category_url = _normalize_url(request.form.get('category_url') or '')
    work_url = _normalize_url(request.form.get('work_url') or '')

    extra_urls: list[str] = []
    for key in request.form.keys():
        if key in ('main_url', 'target_url', 'category_url', 'work_url'):
            continue
        if key.startswith('url_') or key == 'url_new':
            val = _normalize_url(request.form.get(key, ''))
            if val:
                extra_urls.append(val)

    if not main_url:
        return _render(
            'index.html', error="请输入主网域",
            category_url=category_url, work_url=work_url,
        )

    field_errors: list[str] = []
    if not main_url.startswith("https://"):
        field_errors.append("主网域必须 https")
    if category_url and not category_url.startswith("https://"):
        field_errors.append("分类页必须 https")
    if work_url and not work_url.startswith("https://"):
        field_errors.append("漫画页必须 https")
    if field_errors:
        return _render(
            'index.html', error="; ".join(field_errors),
            target_url=main_url, category_url=category_url, work_url=work_url,
        )

    tier_urls = [u for u in (main_url, category_url, work_url) if u]
    gate_urls = tier_urls + extra_urls
    _, gate_err = _verify_urls_or_error(gate_urls, "URL")
    if gate_err:
        return _render(
            'index.html', error=gate_err,
            target_url=main_url, category_url=category_url, work_url=work_url,
        )

    warning_msg = None
    if category_url or work_url:
        try:
            _persist_three_tier_config(main_url, category_url, work_url)
        except Exception as exc:
            warning_msg = f"漫画/分类页配置保存失败 ({type(exc).__name__})，但生成任务仍可继续。"
            plan_logger.warn(
                "homepage_form_persist_failed",
                main=main_url, reason=type(exc).__name__, detail=str(exc)[:120],
            )

    url_inputs = [main_url] + extra_urls

    preview_urls = [u for u in (main_url, category_url, work_url) if u][:5]
    with ThreadPoolExecutor(max_workers=3) as pool:
        meta_results = list(pool.map(fetch_url_metadata, preview_urls))
    meta_info = [m for m in meta_results if m.get('status') == 'success']

    urls_json = json.dumps(url_inputs)
    target_url = main_url
    target_language = request.form.get('target_language', detect_language(target_url))

    # Fetch TDK if enabled and add suggested anchors
    fetch_tdk = request.form.get('fetch_tdk', 'yes')
    suggested_anchors = []
    if fetch_tdk == 'yes':
        tdk_data = fetch_full_tdk(target_url)
        if tdk_data.get('status') == 'success':
            suggested_anchors = tdk_data.get('suggested_anchors', [])

    config = {
        'target_url': target_url,
        'main_domain': get_main_domain(target_url),
        'platform': detect_platform(target_url),
        'url_mode': 'C',
        'publish_mode': 'publish',
        'target_language': target_language,
        'custom_title': '',
        'custom_tags': '',
        'fetch_tdk': fetch_tdk,
        'suggested_anchors': suggested_anchors,
        'urls': url_inputs,
        'meta_info': meta_info,
    }
    session['config'] = config
    session['urls_json'] = urls_json

    extra_urls = url_inputs[1:] if len(url_inputs) > 1 else []
    return _render('index.html',
        target_url=target_url, config=config,
        urls_json=urls_json, extra_urls=extra_urls,
        meta_info=meta_info[:3], warning=warning_msg)


@bp.route('/ce:generate', methods=['POST'])
def ce_generate():
    stored_config = session.get('config', {})
    urls_json = request.form.get('urls_json', session.get('urls_json', '[]'))

    try:
        urls = json.loads(urls_json)
    except Exception as exc:
        # Distinguish "no input provided" (legitimate fallback to stored urls)
        # from "operator submitted a non-empty value that failed to parse"
        # (do NOT silently generate against stale urls — surface it).
        submitted = request.form.get('urls_json', '').strip()
        if submitted and submitted != '[]':
            plan_logger.warn("urls_json_parse_error", reason=type(exc).__name__)
            return _render('index.html', error="连结格式无效，未使用旧数据",
                           config=stored_config)
        urls = stored_config.get('urls', [])

    if not urls:
        return _render('index.html', error="没有有效的连结", config=stored_config)

    platform = request.form.get('platform', stored_config.get('platform', 'blogger'))
    url_mode = request.form.get('url_mode', stored_config.get('url_mode', 'C'))
    publish_mode = request.form.get('publish_mode',
                                    stored_config.get('publish_mode', 'publish'))
    target_language = request.form.get('target_language',
                                       stored_config.get('target_language', 'zh-CN'))
    custom_title = request.form.get('custom_title', '').strip()
    custom_tags = request.form.get('custom_tags', '').strip()
    fetch_tdk = request.form.get('fetch_tdk', stored_config.get('fetch_tdk', 'no'))

    main_url = urls[0]
    extra_urls = urls[1:] if len(urls) > 1 else []

    tdk_data = {}
    if fetch_tdk == 'yes':
        tdk_data = fetch_full_tdk(main_url)

    seed = {
        'target_url': main_url,
        'main_domain': get_main_domain(main_url),
        'platform': platform,
        'language': detect_language(main_url),
        'url_mode': url_mode,
        'publish_mode': publish_mode,
        'target_language': target_language,
    }
    if custom_title:
        seed['custom_title'] = custom_title
    if custom_tags:
        seed['custom_tags'] = custom_tags
    if extra_urls:
        seed['extra_urls'] = extra_urls
    if tdk_data and tdk_data.get('status') == 'success':
        suggested = tdk_data.get('suggested_anchors', [])
        if suggested:
            seed['suggested_anchors'] = suggested

    seed_json = json.dumps(seed, ensure_ascii=False)

    result = _api.plan(seed_json)
    if not result.success:
        return _render('index.html', target_url=main_url,
                       error=result.error or "生成失败，没有输出",
                       config=stored_config)

    plans = result.stdout
    if not plans.strip():
        return _render('index.html', target_url=main_url,
                       error=result.stderr_cleaned or "生成失败，没有输出",
                       config=stored_config)

    plans_list = result.rows
    if not plans_list:
        plan_logger.warn("json_parse_error", line=plans[:100])
        return _render('index.html', target_url=main_url,
                       error=f"解析生成结果失败。原始输出: {plans[:200]}",
                       config=stored_config)

    config = {
        'platform': platform, 'target_language': target_language,
        'urls': urls, 'fetch_tdk': fetch_tdk,
        'url_mode': url_mode, 'publish_mode': publish_mode,
        'custom_title': custom_title, 'custom_tags': custom_tags,
    }
    session['config'] = config
    session['plans'] = plans

    return _render('index.html', target_url=main_url, config=config,
        plans=plans, plans_list=plans_list,
        urls_json=urls_json, extra_urls=extra_urls)


@bp.route('/ce:validate', methods=['POST'])
def ce_validate():
    plans = session.get('plans', '') or request.form.get('plans', '')
    config = session.get('config', {})

    result = _api.validate(plans, no_check_urls=True)
    if not result.success:
        return _render('index.html', plans=plans,
                       error=result.error or "验证失败，请检查链接数量是否在 6-8 个之间",
                       config=config)

    validated = result.stdout
    if not validated.strip():
        return _render('index.html', plans=plans,
                       error=result.stderr_cleaned or "验证失败，请检查链接数量是否在 6-8 个之间",
                       config=config)

    session['validated'] = validated
    return _render('index.html', validated=validated, plans=plans, config=config)


@bp.route('/ce:publish', methods=['POST'])
def ce_publish():
    plans = session.get('plans', '') or request.form.get('plans', '')
    config = session.get('config', {})

    platform = request.form.get('platform', config.get('platform', 'blogger'))
    publish_mode = request.form.get('publish_mode', config.get('publish_mode', 'publish'))
    target_url = config.get('target_url', 'unknown')
    language = config.get('target_language', 'zh-CN')

    if platform == 'velog':
        velog_status = _get_velog_status()
        if velog_status.get('state') not in ('ok', 'fresh'):
            detail = velog_status.get('guide') or velog_status.get('label') or ''
            return _render('index.html',
                error=f"Velog 凭证无效，请先在设置页重新绑定。{detail}",
                config=config, history_active=True)

    result = _api.publish(plans, platform, publish_mode)

    if not result.success:
        # ``result.error`` is the full typed message (or banner-stripped stderr on
        # QUARANTINE) — no truncation. Tag the typed class for the operator.
        msg = result.error or "发布失败"
        display = (
            f"[{result.error_class}] {msg}"
            if result.error_class and result.error_class != "unrecognized"
            else msg
        )
        _push_history_single_failure(
            target_url=target_url, platform=platform, language=language, error=display,
        )
        plan_logger.warn(
            "webui_publish_result",
            state="all_failed", platform=platform, publish_mode=publish_mode,
            n_ok=0, n_failed=0, error_class=result.error_class, stderr_preview=msg,
        )
        return _render('index.html',
            publish_state='all_failed', publish_error=f"发布失败: {display}",
            config=config, history_active=True)

    published = result.stdout
    stderr = result.stderr
    publish_results = result.rows

    if not publish_results:
        diagnostic = surface_cli_error(result.stderr) or "publish-backlinks returned no parseable rows"
        _push_history_single_failure(
            target_url=target_url, platform=platform, language=language,
            error=diagnostic,
        )
        plan_logger.warn(
            "webui_publish_result",
            state="all_failed", platform=platform, publish_mode=publish_mode,
            n_ok=0, n_failed=0, stderr_preview=diagnostic,
        )
        return _render('index.html',
            publish_state='all_failed', publish_error=diagnostic,
            published=published, config=config, history_active=True)

    _push_history_per_row(
        publish_results,
        target_url_fallback=target_url,
        platform_fallback=platform,
        language_fallback=language,
    )

    summary = publish_state_summary(publish_results)
    n_ok = summary["n_ok"]
    n_failed = summary["n_failed"]
    publish_state = summary["state"]
    publish_error = summary["failure_detail"]

    log_fn = plan_logger.info if publish_state == 'all_success' else plan_logger.warn
    log_fn(
        "webui_publish_result",
        state=publish_state, platform=platform, publish_mode=publish_mode,
        n_ok=n_ok, n_failed=n_failed, stderr_preview=surface_cli_error(stderr),
    )

    return _render('index.html', published=published,
                   publish_results=publish_results,
                   publish_state=publish_state, publish_error=publish_error,
                   n_ok=n_ok, n_total=len(publish_results),
                   config=config, history_active=True)

@bp.route('/ce:preview', methods=['POST'])
def ce_preview():
    urls_json = request.form.get('urls_json', '[]')
    try:
        urls = json.loads(urls_json)
    except json.JSONDecodeError as exc:
        plan_logger.warn("preview_urls_parse_error", reason=type(exc).__name__)
        return "Invalid URLs"

    seed = {
        'target_url': urls[0],
        'main_domain': get_main_domain(urls[0]),
        'platform': request.form.get('platform', 'blogger'),
        'language': request.form.get('target_language', 'zh-CN'),
        'url_mode': request.form.get('url_mode', 'C'),
        'publish_mode': request.form.get('publish_mode', 'publish'),
        'custom_title': request.form.get('custom_title', ''),
        'custom_tags': request.form.get('custom_tags', ''),
        'extra_urls': urls[1:],
    }
    if request.form.get('fetch_tdk') == 'yes':
        seed['tdk'] = fetch_full_tdk(urls[0])

    result = _api.plan(json.dumps([seed]))
    content = result.stdout

    fmt = request.args.get('format', 'md')
    if fmt == 'html':
        return render_to_html(content)
    return content
