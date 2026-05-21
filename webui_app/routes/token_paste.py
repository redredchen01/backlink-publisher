"""Token-paste binding route — Plan 006 follow-up (2026-05-20).

Single POST endpoint that writes a `<channel>-token.json` (0600) for the
PAT-based publishing platforms whose binding is "paste a token", not OAuth
or browser-mediated. Currently allowlists ghpages only — writeas was
retired from the WebUI on 2026-05-20 because the channel is unusable in
practice. hashnode lives in the same code path but is excluded from the
allowlist until its dofollow status can be empirically verified — once
confirmed, add it here.

The route is deliberately narrow: it writes the token file only. Routing
config fields (repo / collection_alias / api_base / etc) are operator-edited
in config.toml directly — extending save_config to round-trip those
sections is a separate, larger change tracked as a follow-up.
"""

from __future__ import annotations

import os
import stat

from flask import Blueprint, redirect, request

from backlink_publisher.config import (
    save_ghpages_token,
)

bp = Blueprint("token_paste", __name__)


# Platform → (save_fn, token_file_basename, anchor hash for redirect).
# When extending to hashnode, add another entry once dofollow is verified.
_ALLOWED: dict[str, tuple] = {
    "ghpages": (save_ghpages_token, "ghpages-token.json"),
}


@bp.route('/settings/save-channel-token', methods=['POST'])
def save_channel_token():
    channel = (request.form.get('channel', '') or '').strip()
    if channel not in _ALLOWED:
        return redirect(
            f'/settings?flash_type=danger&flash_msg='
            f'unknown channel "{channel}" — allowed: {sorted(_ALLOWED)}'
        )

    save_fn, token_basename = _ALLOWED[channel]
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
            return redirect(
                f'/settings?flash_type=danger&flash_msg='
                f'清除 {channel} token 失败: {e}{anchor}'
            )

    # Save button — token can be empty (means "leave as-is").
    token = (request.form.get('token', '') or '').strip()
    if not token:
        return redirect(
            f'/settings?flash_type=info&flash_msg='
            f'未填入 token，{channel} 配置未变更{anchor}'
        )

    try:
        save_fn({"token": token})
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
        return redirect(
            f'/settings?flash_type=danger&flash_msg='
            f'保存 {channel} token 失败: {e}{anchor}'
        )
