"""Token-paste binding route — Plan 006 follow-up (2026-05-20).

Single POST endpoint that writes a `<channel>-token.json` (0600) for the
PAT-based publishing platforms whose binding is "paste a token", not OAuth
or browser-mediated. The route is deliberately narrow: it writes the token
file only. Routing config fields are operator-edited in config.toml directly
— extending save_config to round-trip those sections is a separate, larger
change tracked as a follow-up.
"""

from __future__ import annotations

import os
import stat

from flask import Blueprint, redirect, request

from backlink_publisher.config import (
    save_devto_token,
    save_ghpages_token,
    save_notion_token,
)

from ..helpers.security import _safe_flash_redirect

bp = Blueprint("token_paste", __name__)


# Platform → (save_fn, token_file_basename, token_field_key).
# Single-token channels: save_fn is called with {token_field_key: value}.
# Multi-field channels (notion) use the separate /save-notion-token route.
_ALLOWED: dict[str, tuple] = {
    "ghpages": (save_ghpages_token, "ghpages-token.json", "token"),
    "devto": (save_devto_token, "devto-token.json", "api_key"),
}


@bp.route('/settings/save-channel-token', methods=['POST'])
def save_channel_token():
    channel = (request.form.get('channel', '') or '').strip()
    if channel not in _ALLOWED:
        return redirect(
            f'/settings?flash_type=danger&flash_msg='
            f'unknown channel "{channel}" — allowed: {sorted(_ALLOWED)}'
        )

    save_fn, token_basename, token_field_key = _ALLOWED[channel]
    anchor = f"#channel-{channel}"

    # Clear button (named "clear") → delete the file + report.
    if request.form.get('clear'):
        from backlink_publisher.config import load_config
        cfg = load_config()
        token_path = cfg.config_dir / token_basename
        try:
            if token_path.exists():
                token_path.unlink()
                return redirect(
                    f'/settings?flash_type=success&flash_msg='
                    f'{channel} token 已清除{anchor}'
                )
            return redirect(
                f'/settings?flash_type=info&flash_msg='
                f'{channel} token 文件不存在，无需清除{anchor}'
            )
        except OSError as e:
            return _safe_flash_redirect(
                '/settings', flash_type='danger',
                msg=f'清除 {channel} token 失败: {e}',
                fragment=f'channel-{channel}')

    # Save button — token can be empty (means "leave as-is").
    token = (request.form.get('token', '') or '').strip()
    if not token:
        return redirect(
            f'/settings?flash_type=info&flash_msg='
            f'未填入 token，{channel} 配置未变更{anchor}'
        )

    try:
        save_fn({token_field_key: token})
        # Defensive re-check: file should now exist + be 0600.
        from backlink_publisher.config import load_config
        cfg = load_config()
        token_path = cfg.config_dir / token_basename
        if not token_path.exists():
            return redirect(
                f'/settings?flash_type=danger&flash_msg='
                f'保存 {channel} token 失败（文件未创建）{anchor}'
            )
        mode = stat.S_IMODE(token_path.stat().st_mode)
        if os.name != "nt" and mode != 0o600:
            # save_fn should have set 0600; if it didn't, fix it now.
            os.chmod(token_path, 0o600)
        return redirect(
            f'/settings?flash_type=success&flash_msg='
            f'{channel} token 已绑定 ✓{anchor}'
        )
    except Exception as e:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'保存 {channel} token 失败: {e}',
            fragment=f'channel-{channel}')


@bp.route('/settings/save-notion-token', methods=['POST'])
def save_notion_channel_token():
    """Notion-specific save route: two required fields (integration_token + database_id).

    Notion's token file has a different shape from single-token platforms
    (integration_token + database_id), so it uses a dedicated route rather
    than the generic /save-channel-token handler.
    """
    anchor = "#channel-notion"
    integration_token = (request.form.get('integration_token', '') or '').strip()
    database_id = (request.form.get('database_id', '') or '').strip()

    # Clear button — delete notion-token.json.
    if request.form.get('clear'):
        from backlink_publisher.config import load_config
        cfg = load_config()
        token_path = cfg.notion_token_path
        try:
            if token_path.exists():
                token_path.unlink()
                return redirect(
                    f'/settings?flash_type=success&flash_msg='
                    f'notion token 已清除{anchor}'
                )
            return redirect(
                f'/settings?flash_type=info&flash_msg='
                f'notion token 文件不存在，无需清除{anchor}'
            )
        except OSError as e:
            return redirect(
                f'/settings?flash_type=danger&flash_msg='
                f'清除 notion token 失败: {e}{anchor}'
            )

    if not integration_token and not database_id:
        return redirect(
            f'/settings?flash_type=info&flash_msg='
            f'未填入 Notion 凭据，配置未变更{anchor}'
        )
    if not integration_token:
        return redirect(
            f'/settings?flash_type=danger&flash_msg='
            f'Integration Token 不能为空{anchor}'
        )
    if not database_id:
        return redirect(
            f'/settings?flash_type=danger&flash_msg='
            f'Database ID 不能为空{anchor}'
        )

    try:
        save_notion_token({
            "integration_token": integration_token,
            "database_id": database_id,
        })
        from backlink_publisher.config import load_config
        cfg = load_config()
        token_path = cfg.notion_token_path
        if not token_path.exists():
            return redirect(
                f'/settings?flash_type=danger&flash_msg='
                f'保存 notion token 失败（文件未创建）{anchor}'
            )
        mode = stat.S_IMODE(token_path.stat().st_mode)
        if os.name != "nt" and mode != 0o600:
            os.chmod(token_path, 0o600)
        return redirect(
            f'/settings?flash_type=success&flash_msg='
            f'notion token 已绑定 ✓{anchor}'
        )
    except Exception as e:
        return redirect(
            f'/settings?flash_type=danger&flash_msg='
            f'保存 notion token 失败: {e}{anchor}'
        )
