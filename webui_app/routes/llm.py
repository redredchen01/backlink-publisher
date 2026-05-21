"""LLM settings route handlers."""
import json
import os

from flask import Blueprint, jsonify, request
import requests

from backlink_publisher._util.llm_allowlist import is_allowlisted
from backlink_publisher._util.net_safety import _check_url_for_ssrf
from backlink_publisher.persistence.safe_write import atomic_write

from ..helpers.contexts import _llm_settings_file, _load_llm_settings
from ..helpers.security import _safe_flash_redirect

bp = Blueprint("llm", __name__)


# Plan 2026-05-21-006 Unit 3.1 — response-side caps for the test endpoint.
# Cap upstream-response size at 64 KB streamed read: a malicious endpoint
# could otherwise return a multi-GB body and exhaust memory.
_LLM_TEST_MAX_BYTES = 64 * 1024


def _guard_llm_endpoint(url: str) -> tuple[str | None, str | None]:
    """Return (rejection_reason, detail) or (None, None) if URL is acceptable.

    Layered gates:
      1. Scheme must be http(s).
      2. Host must be in the LLM allowlist (or operator opted out via
         BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1).
      3. SSRF gate (RFC1918, link-local, metadata IPs, etc.) unless the
         loopback exception is opted in via
         BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK=1, in which case loopback
         IPs and `localhost` are allowed.

    Env vars are read inline (not cached at import) so tests can flip them
    via monkeypatch without `importlib.reload` (ce:review maint-001).
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "scheme_rejected", f"only http/https allowed, got {parsed.scheme!r}"

    if not is_allowlisted(url):
        return (
            "host_not_allowlisted",
            f"host {parsed.hostname!r} is not in the LLM allowlist; "
            f"set BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST=1 to opt out",
        )

    # SSRF check — but skip loopback rejection when operator opted in.
    allow_loopback = os.environ.get(
        "BACKLINK_PUBLISHER_LLM_ALLOW_LOOPBACK", "0") == "1"
    ssrf_reason = _check_url_for_ssrf(url)
    if ssrf_reason is not None:
        if allow_loopback and _is_loopback_host(parsed.hostname):
            return None, None
        return "url_rejected", ssrf_reason

    return None, None


def _is_loopback_host(host: str | None) -> bool:
    """Hostname-level loopback check decoupled from net_safety's reason
    string format (ce:review maint-002). Catches 127.0.0.1, ::1, IPv4
    loopback aliases (0.0.0.0/8, 127.0.0.0/8), and the literal
    'localhost'.
    """
    import ipaddress
    if not host:
        return False
    h = host.strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _safe_get_json(url: str, headers: dict, timeout: int = 10):
    """Bounded GET with content-type + size guards. Returns parsed JSON or
    raises ValueError. Used by the LLM test-connection route only.

    `allow_redirects=False` (ce:review C1 / sec-001): the SSRF gate is
    one-shot at input. Following redirects would re-issue the request
    (including the Bearer api_key header) against an attacker-chosen
    target, defeating the gate.
    """
    resp = requests.get(url, headers=headers, timeout=timeout, stream=True,
                        allow_redirects=False)
    if 300 <= resp.status_code < 400:
        raise ValueError(
            f"redirect_not_allowed: upstream returned {resp.status_code}; "
            f"refusing to follow Location header")
    ctype = resp.headers.get("Content-Type", "")
    if "json" not in ctype.lower():
        raise ValueError(f"bad_content_type: {ctype!r}")
    body = b""
    for chunk in resp.iter_content(chunk_size=8192):
        body += chunk
        if len(body) > _LLM_TEST_MAX_BYTES:
            raise ValueError(
                f"response_too_large: exceeded {_LLM_TEST_MAX_BYTES} bytes")
    return resp.status_code, json.loads(body)


def _safe_post_json(url: str, headers: dict, payload: dict, timeout: int = 10):
    """Bounded POST counterpart of _safe_get_json."""
    resp = requests.post(url, headers=headers, json=payload,
                         timeout=timeout, stream=True,
                         allow_redirects=False)
    if 300 <= resp.status_code < 400:
        raise ValueError(
            f"redirect_not_allowed: upstream returned {resp.status_code}; "
            f"refusing to follow Location header")
    ctype = resp.headers.get("Content-Type", "")
    if "json" not in ctype.lower():
        raise ValueError(f"bad_content_type: {ctype!r}")
    body = b""
    for chunk in resp.iter_content(chunk_size=8192):
        body += chunk
        if len(body) > _LLM_TEST_MAX_BYTES:
            raise ValueError(
                f"response_too_large: exceeded {_LLM_TEST_MAX_BYTES} bytes")
    return resp.status_code, json.loads(body)


_LLM_DEFAULTS = {
    'api_key': '',
    'endpoint': '',
    'model': '',
    'temperature': 0.7,
    'system_prompt': '',
    'use_article_gen': False,
    'article_system_prompt': '',
    'image_gen_api_key': '',
    'use_image_gen': False,
}


def _write_llm_settings(payload: dict) -> None:
    # Delegates to the canonical credential-write helper so the file lands
    # 0o600 (api_key is a long-term secret). PR #139 hand-rolled this write
    # and forgot the chmod, leaving llm-settings.json world-readable.
    path = _llm_settings_file()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write(path, text)


@bp.route('/settings/save-llm-config', methods=['POST'])
def settings_save_llm_config():
    # P2: clearLlmSettings() flips a hidden action=clear marker to reset the whole file.
    if request.form.get('action') == 'clear':
        try:
            _write_llm_settings(dict(_LLM_DEFAULTS))
            return _safe_flash_redirect(
                '/settings', flash_type='success',
                msg='LLM 配置已清除', fragment='sect-ai')
        except Exception as e:
            return _safe_flash_redirect(
                '/settings', flash_type='danger',
                msg=f'清除失败: {e}', fragment='sect-ai')

    existing = _load_llm_settings()
    try:
        temperature = float(request.form.get('temperature', existing.get('temperature', 0.7)))
    except ValueError:
        temperature = existing.get('temperature', 0.7)

    # P3: blank secret inputs preserve the stored value so we don't wipe it on partial edits.
    new_api_key = request.form.get('api_key', '').strip()
    new_image_key = request.form.get('image_gen_api_key', '').strip()

    existing.update({
        'endpoint': request.form.get('endpoint', '').strip().rstrip('/'),
        'api_key': new_api_key or existing.get('api_key', ''),
        'model': request.form.get('model', '').strip(),
        'temperature': temperature,
        'system_prompt': request.form.get('system_prompt', ''),
        'use_article_gen': 'use_article_gen' in request.form,
        'image_gen_api_key': new_image_key or existing.get('image_gen_api_key', ''),
        'use_image_gen': 'use_image_gen' in request.form,
    })
    try:
        _write_llm_settings(existing)
        return _safe_flash_redirect(
            '/settings', flash_type='success',
            msg='LLM 设定已保存', fragment='sect-ai')
    except Exception as e:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'保存失败: {e}', fragment='sect-ai')


@bp.route('/settings/test-llm-connection', methods=['POST'])
def settings_test_llm():
    try:
        endpoint = request.form.get('endpoint', '').strip().rstrip('/')
        api_key = request.form.get('api_key', '').strip()
        model = request.form.get('model', '').strip()

        # P3 fallback: form sends blanks when secrets aren't re-typed; read stored values.
        if not api_key or not endpoint:
            stored = _load_llm_settings()
            api_key = api_key or stored.get('api_key', '')
            endpoint = endpoint or stored.get('endpoint', '').rstrip('/')
            model = model or stored.get('model', '')

        if not endpoint or not api_key:
            return jsonify({'status': 'error', 'message': '请填写 Endpoint 和 API Key'}), 200

        # Plan 2026-05-21-006 Unit 3.1 — guard endpoint URL BEFORE sending the
        # api_key. SSRF gate + host allowlist + scheme check.
        reason, detail = _guard_llm_endpoint(f"{endpoint}/models")
        if reason is not None:
            return jsonify({
                'status': 'failed',
                'reason': reason,
                'message': f'endpoint URL rejected ({reason}): {detail}',
            }), 400

        # Try to call v1/models
        test_url = f"{endpoint}/models"
        headers = {"Authorization": f"Bearer {api_key}"}

        models_list = []
        try:
            status, m_data = _safe_get_json(test_url, headers)
            if status == 200:
                if isinstance(m_data, dict) and 'data' in m_data:
                    models_list = [m['id'] for m in m_data['data']
                                   if isinstance(m, dict) and 'id' in m]
                return jsonify({'status': 'ok', 'message': '连接成功！',
                                'models': models_list}), 200

            # Fallback to /chat/completions with the same guards.
            fb_url = f"{endpoint}/chat/completions"
            reason, detail = _guard_llm_endpoint(fb_url)
            if reason is not None:
                return jsonify({
                    'status': 'failed',
                    'reason': reason,
                    'message': f'endpoint URL rejected ({reason}): {detail}',
                }), 400
            data = {
                "model": model or "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            }
            status, _ = _safe_post_json(fb_url, headers, data)
            if status == 200:
                return jsonify({'status': 'ok', 'message': '连接成功！',
                                'models': []}), 200

            return jsonify({'status': 'error',
                            'message': f'连接失败: HTTP {status}'}), 200
        except ValueError as ve:
            # Raised by _safe_get_json/_safe_post_json for size/content-type
            # violations. Surface the structured reason but don't expose
            # raw bytes.
            return jsonify({
                'status': 'failed',
                'reason': 'response_invalid',
                'message': f'响应不合规: {ve}',
            }), 400
        except Exception as e:
            return jsonify({'status': 'error',
                            'message': f'请求异常: {str(e)}'}), 200
    except Exception as e:
        return jsonify({'status': 'error',
                        'message': f'发生错误: {str(e)}'}), 200

@bp.route('/settings/test-llm-generation', methods=['POST'])
def settings_preview_llm():
    try:
        from backlink_publisher.publishing.adapters.llm_anchor_provider import OpenAICompatibleProvider
        settings = _load_llm_settings()
        
        provider = OpenAICompatibleProvider(
            base_url=settings['endpoint'],
            api_key=settings['api_key'],
            model=settings['model'],
            temperature=settings['temperature'],
            system_prompt=settings['system_prompt'],
            article_system_prompt=settings['article_system_prompt']
        )
        
        test_title = request.form.get('test_title', '测试文章')

        if settings.get('use_article_gen'):
            result = provider.generate_article_body(
                domain_label='example.com',
                main_domain='https://example.com',
                anchors=['示例锚点', '更多资源'],
                topic=test_title
            )
            return jsonify({'status': 'ok', 'result': result}), 200
        else:
            # Fallback to anchor candidate generation
            from backlink_publisher.publishing.adapters.llm_anchor_provider import LLMAnchorRequest
            req = LLMAnchorRequest(keyword=test_title, domain="example.com", target_url="https://example.com")
            result = provider.generate_candidates(req)
            return jsonify({'status': 'ok', 'result': f"生成的锚点候选: {', '.join(result)}"}), 200
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'生成预览失败: {str(e)}'}), 200
