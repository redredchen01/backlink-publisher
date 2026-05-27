"""P0-2 工具：velog 登录 + 凭证 dump。

运行方式：
    python3 docs/spikes/velog_login_dump.py

输出文件：
    velog_credentials_dump.json  — 供 P0-2 分析 + P0-3 臂 A TTL 测试

前置依赖：
    pip install playwright
    playwright install chromium
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DUMP_PATH = Path("velog_credentials_dump.json")
COOKIE_FILE = Path("velog_cookies_flat.txt")  # 用于 curl 命令的扁平格式


def _velog_host_allowed(host: str | None) -> bool:
    """Plan 012 R16 规范化白名单 primitive：精确匹配 velog.io。

    `host.lower().lstrip(".")` 后 == "velog.io"。排除：
    - `evilvelog.io`（前缀混淆）
    - `velog.io.attacker.tld`（后缀混淆）
    - `Velog.IO`（大小写归一为 True）
    - 空 / None（防御性 False）

    Unit 3 实现 cookies + storage_state.origins[] 双过滤时复用此 primitive。
    """
    if not host:
        return False
    return host.lower().lstrip(".") == "velog.io"


def _origin_host_allowed(origin: str | None) -> bool:
    """Storage_state.origins[] 用：从 origin URL 提取 host 后过 _velog_host_allowed。"""
    if not origin:
        return False
    try:
        return _velog_host_allowed(urlparse(origin).hostname)
    except Exception:
        return False


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("=" * 60)
        print("请在弹出的浏览器中完成 velog 登录（支持 Google / GitHub）")
        print("登录完成后脚本会自动继续...")
        print("=" * 60)
        await page.goto("https://velog.io")

        # 等待登录成功（出现用户头像 or access_token cookie）
        try:
            await page.wait_for_function(
                "document.cookie.includes('access_token') || "
                "!!document.querySelector('a[href^=\"/@\"]')",
                timeout=180_000,
            )
            print("✓ 检测到登录成功")
        except Exception:
            print("⚠ 等待超时，继续 dump 当前状态（可能未登录成功）")

        dump_time = datetime.now(timezone.utc).isoformat()

        # 1. Cookies（规范化白名单过滤，与 Unit 3 R16 行为一致）
        all_cookies = await context.cookies()
        velog_cookies = [
            c for c in all_cookies
            if _velog_host_allowed(c.get("domain"))
        ]
        other_cookies = [
            c for c in all_cookies
            if not _velog_host_allowed(c.get("domain"))
        ]

        # 2. localStorage / sessionStorage
        local_storage = dict(await page.evaluate("Object.entries(localStorage)"))
        session_storage = dict(await page.evaluate("Object.entries(sessionStorage)"))

        # 3. storage_state（origins 用同一 host primitive 过滤）
        storage_state = await context.storage_state()
        all_origins = storage_state.get("origins", [])
        velog_origins = [
            o for o in all_origins
            if _origin_host_allowed(o.get("origin"))
        ]
        dropped_origins = [
            o.get("origin") for o in all_origins
            if not _origin_host_allowed(o.get("origin"))
        ]
        storage_state["origins"] = velog_origins

        # 分析 token 位置
        token_locations: dict[str, list[str]] = {
            "access_token": [],
            "refresh_token": [],
        }
        for tok in ["access_token", "refresh_token"]:
            if any(c["name"] == tok for c in velog_cookies):
                token_locations[tok].append("cookie")
            if tok in local_storage:
                token_locations[tok].append("localStorage")
            if tok in session_storage:
                token_locations[tok].append("sessionStorage")

        dump = {
            "_dump_time": dump_time,
            "_filter_primitive": "_velog_host_allowed(host).lower().lstrip('.') == 'velog.io'",
            "_analysis": {
                "velog_cookie_names": [c["name"] for c in velog_cookies],
                "idp_cookie_domains": list({
                    c.get("domain", "") for c in other_cookies
                    if any(d in c.get("domain", "") for d in [
                        "google", "github", "facebook", "accounts"
                    ])
                }),
                "dropped_origin_urls": dropped_origins,
                "token_locations": token_locations,
                "recommended_persistence": (
                    "cookies-only"
                    if all(len(v) == 1 and "cookie" in v
                           for v in token_locations.values())
                    else "storage_state"
                ),
            },
            "velog_cookies": velog_cookies,
            "local_storage_keys": list(local_storage.keys()),
            "session_storage_keys": list(session_storage.keys()),
            "storage_state_velog_only": storage_state,
        }

        DUMP_PATH.write_text(json.dumps(dump, indent=2, ensure_ascii=False))

        # 生成扁平 cookie 字符串（用于 curl）
        flat_cookie = "; ".join(
            f"{c['name']}={c['value']}" for c in velog_cookies
        )
        COOKIE_FILE.write_text(flat_cookie)

        print()
        print("=" * 60)
        print(f"✓ 完整 dump → {DUMP_PATH}")
        print(f"✓ curl 用 cookie → {COOKIE_FILE}")
        print()
        print("P0-2 分析结果：")
        print(f"  velog cookies: {dump['_analysis']['velog_cookie_names']}")
        print(f"  token 位置: {token_locations}")
        print(f"  推荐持久化策略: {dump['_analysis']['recommended_persistence']}")
        if dropped_origins:
            print("  ⚠ 已过滤的非 velog.io origin（IdP storage 防泄漏）:")
            for o in dropped_origins:
                print(f"      - {o}")
        print()
        print("下一步：")
        print("  1. 检查 velog_credentials_dump.json 确认 access_token 位置")
        print("  2. 用 velog_cookies_flat.txt 执行 P0-1 curl 测试")
        print("  3. **P0-1b deliberate-bad-input harvest**：用同一 cookie 故意提交")
        print("     4-5 类坏输入，harvest errors[].extensions.code（≥ 3 类）")
        print("     这是 Unit 4 _KNOWN_EXTENSIONS_CODES baseline 的唯一来源。")
        print("  4. 保存 velog_cookies_flat.txt，25h 后执行 P0-3 臂 A 测试")
        print("=" * 60)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
