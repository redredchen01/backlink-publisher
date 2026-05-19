"""LLM settings route handlers."""
from flask import Blueprint, jsonify, request
from ..helpers import _load_llm_settings
import requests

bp = Blueprint("llm", __name__)

@bp.route('/settings/test-llm-connection', methods=['POST'])
def settings_test_llm():
    try:
        endpoint = request.form.get('endpoint', '').strip().rstrip('/')
        api_key = request.form.get('api_key', '').strip()
        model = request.form.get('model', '').strip()

        if not endpoint or not api_key:
            return jsonify({'status': 'error', 'message': '请填写 Endpoint 和 API Key'}), 200

        # Try to call v1/models
        test_url = f"{endpoint}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        models_list = []
        try:
            resp = requests.get(test_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                try:
                    m_data = resp.json()
                    if isinstance(m_data, dict) and 'data' in m_data:
                        models_list = [m['id'] for m in m_data['data'] if isinstance(m, dict) and 'id' in m]
                except Exception:
                    pass
                return jsonify({'status': 'ok', 'message': '连接成功！', 'models': models_list}), 200
            
            # Fallback
            test_url = f"{endpoint}/chat/completions"
            data = {"model": model or "gpt-3.5-turbo", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
            resp = requests.post(test_url, headers=headers, json=data, timeout=10)
            if resp.status_code == 200:
                return jsonify({'status': 'ok', 'message': '连接成功！', 'models': []}), 200
            
            return jsonify({'status': 'error', 'message': f'连接失败: HTTP {resp.status_code}'}), 200
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'请求异常: {str(e)}'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'发生错误: {str(e)}'}), 200

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
        test_content = request.form.get('test_content', '这是一个测试内容。')
        
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
