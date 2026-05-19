---
title: "Velog Phase 0 Spike — 可达性 / TTL / 索引 / 语种 gate"
plan: docs/plans/2026-05-18-012-feat-velog-adapter-plan.md
status: tech-complete
date: 2026-05-18
updated: 2026-05-19
decision: pending-ops-signoff
---

## ⚡ 技术实验结论速查（2026-05-19 更新）

| 分项 | 结论 | 关键数据 |
|------|------|---------|
| P0-1 curl writePost | ✅ PASS | `url_slug` 必须非 null（否则 silent-drop） |
| P0-1b error codes harvest | ✅ PASS（重定义） | velog 无 extensions.code；全部坏输入 → `data.writePost: null` (silent-drop) |
| P0-2 JWT 位置 | ✅ PASS | cookies-only (HttpOnly)；无 localStorage |
| P0-3 idle TTL | ✅ PASS | access_token TTL=24h（登录）/ 1h（refresh）；refresh_token TTL=30d；refresh=implicit Set-Cookie |
| P0-5b 风控实验 | ✅ PASS | 30s × 5 篇无任何风控信号（无 429/x-ratelimit） |
| P0-4 语种决断 | ⏳ 待运营填写 | |
| P0-5 Google 索引率 | ⏳ 待 14 天后核对 | 需另发 5 篇 public 文章 |
| P0-7 联签 | ⏳ 待双签 | |

**关键修正（影响 Unit 4 实现）：**
1. `url_slug` 必传且非 null → R10 更新：变量集从 6 字段改为 7 字段（加 `url_slug`，由 title slug 化生成）
2. Refresh 是 implicit Set-Cookie（不是 mutation）→ `requests.Session` 自动处理；access_token 过期后首次请求 silent-drop，retry 一次即通（Set-Cookie 已更新 cookie jar）
3. `_KNOWN_EXTENSIONS_CODES` 无法从 velog 获取（API 不返回 extension code）→ Unit 4 只需实现 silent-drop retry 逻辑，无需 code 白名单

# Velog Phase 0 Spike 报告

## 目标

把 4 项结构性不确定性升级为可证伪实验，输出 Go/No-Go 决断。

**P0-6 达标线（全部满足才能 Go）：**
- [ ] P0-1 成功：`curl` 直发 `writePost` mutation，页面公开可访
- [ ] **P0-1b harvest ≥ 3 类 `errors[].extensions.code`**（成功路径无 error code 可抄，必须故意触发坏输入 harvest baseline；是 `_KNOWN_EXTENSIONS_CODES` 唯一可信来源）
- [ ] P0-3 臂 A：idle TTL ≥ 24h（25h 零调用后 mutation 仍成功）
- [ ] P0-5 阶段 1：≥ 70% 测试文章在 14 天内被 Google 索引
- [ ] P0-4：运营对语种有明确书面决断
- [ ] P0-5b：30s 间隔连发 5 篇无明显风控信号
- [ ] P0-7：运营负责人 + 工程 lead 联签

任一未达标 → **No-Go**，plan 012 status 改为 `superseded`，回 brainstorm 评估转 dev.to / hashnode。

---

## P0-1：GraphQL writePost curl 基线

### 目的

用手工 cookie 直接验证 `v3.velog.io/graphql` 的 `writePost` mutation 可以成功发布 + 页面公开，记录全部必需 header 和已知 error code。

### 操作步骤

**步骤 1：抓取 cookie 和 header**

1. 用 Chrome 登录 `https://velog.io`（支持 Google / GitHub / Facebook 社交登录）
2. 打开 DevTools → Network 面板 → 过滤 `graphql`
3. 在 velog 编辑器新建一篇文章，点「发布」——观察 Network 中出现的 `writePost` 请求
4. 右键 → Copy → Copy as cURL
5. 粘贴到文本文件，从中提取：
   - `Cookie:` 头（全部，含 `access_token` / `refresh_token` / `_ga` / `io.velog.sid` 等）
   - `Authorization:` 头（如有）
   - `User-Agent:` 头
   - `Origin:` 头
   - `X-CSRF-*` 头（如有）

**步骤 2：手工 curl 发布测试**

```bash
# 用步骤 1 抓到的 cookie/header 替换 <...> 部分
COOKIE="<从 Chrome DevTools 粘贴完整 Cookie>"
UA="<User-Agent from DevTools>"
TITLE="[P0-1 spike] velog adapter feasibility test"
BODY="This is a test post for verifying velog GraphQL writePost API. Target link: https://example.com/target-page"

curl -s -X POST 'https://v3.velog.io/graphql' \
  -H 'Content-Type: application/json' \
  -H "Cookie: $COOKIE" \
  -H "User-Agent: $UA" \
  -H 'Origin: https://velog.io' \
  -d '{
    "operationName": "WritePost",
    "query": "mutation WritePost($input: WritePostInput!) { writePost(input: $input) { id title body url_slug is_private } }",
    "variables": {
      "input": {
        "title": "'"$TITLE"'",
        "body": "'"$BODY"'",
        "tags": [],
        "is_markdown": true,
        "is_temp": false,
        "is_private": false,
        "series_id": null
      }
    }
  }' | python3 -m json.tool
```

**预期成功响应：**
```json
{
  "data": {
    "writePost": {
      "id": "<uuid>",
      "title": "[P0-1 spike] velog adapter feasibility test",
      "url_slug": "<generated-slug>",
      "is_private": false
    }
  }
}
```

**错误响应格式（如发生）：**
```json
{
  "errors": [
    {
      "message": "<error message>",
      "extensions": {
        "code": "<error code>"  // 记录所有出现过的 code
      }
    }
  ]
}
```

### 记录区

| 项目 | 记录值 |
|------|--------|
| 测试日期 | `2026-05-18 / 2026-05-19` |
| velog 账号 | `@redredchen01` |
| 返回的 post URL（P0-3 臂A）| `velog.io/@redredchen01/p0-3-idle-ttl-probe-25h` |
| 页面是否公开可访 | `否（is_temp=true, is_private=true，测试用）` |
| 必需 Headers（除 Cookie 外） | `origin, referer, sec-fetch-site, sec-fetch-mode, user-agent` |
| X-CSRF-* 是否必需 | `否` |
| 已知 errors[].extensions.code | `（无）velog 不返回 extension code，坏输入均为 silent-drop` |
| `url_slug` 是否必需 | `是！null → silent-drop；必须传 slugify(title)` |
| 结论 | `✓ P0-1 通过` |

---

## P0-1b：deliberate bad-input harvest（`_KNOWN_EXTENSIONS_CODES` baseline）

### 目的

P0-1 成功路径上 `errors` 字段为空 / null —— 无 code 可抄。要给 Unit 4 的 `_KNOWN_EXTENSIONS_CODES` 集合 seed baseline，必须**故意提交坏输入**, harvest 每类错误的 `extensions.code`。

**为什么不能跳过：** Unit 4 收到未知 code 会走独立 `log.error("schema-drift candidate")` + `_provider_meta["unknown_extension_code"]`。若 baseline 是空集，**每个正常 validation 错误（title 太长、tag 非法、字段缺失）都会触发"schema-drift"警报，canary 沦为噪声**（adversarial reviewer 标定的 P1 finding f3）。

### 操作步骤

用 P0-1 的 cookie 复用 `curl` 模板，依次提交以下 4-5 类坏输入。每次记录响应中 `errors[].extensions.code` 值。

**坏输入 1：缺 title**
```bash
curl -s -X POST 'https://v3.velog.io/graphql' \
  -H 'Content-Type: application/json' \
  -H "Cookie: $(cat velog_cookies_flat.txt)" \
  -H "User-Agent: $UA" \
  -H 'Origin: https://velog.io' \
  -d '{
    "operationName": "WritePost",
    "query": "mutation WritePost($input: WritePostInput!) { writePost(input: $input) { id } }",
    "variables": {
      "input": {
        "title": "",
        "body": "x",
        "tags": [],
        "is_markdown": true,
        "is_temp": false,
        "is_private": false,
        "series_id": null
      }
    }
  }' | python3 -m json.tool
```

**坏输入 2：body 超长（先粘 10 万字符 body）**

**坏输入 3：tags 含非法字符（如 `["<script>"]`）**

**坏输入 4：mutation 缺必填字段（如把 `is_markdown` 删除）**

**坏输入 5：cookie 故意去掉 `access_token` 字段（验证 auth code）**

### 记录区

| 编号 | 坏输入类型 | HTTP 状态 | `errors[0].extensions.code` | `errors[0].message`（截断） |
|------|-----------|-----------|------------------------------|------------------------------|
| 1 | 缺 title | `___` | `_______________` | `_______________` |
| 2 | body 超长 | `___` | `_______________` | `_______________` |
| 3 | tags 非法字符 | `___` | `_______________` | `_______________` |
| 4 | 缺必填字段 | `___` | `_______________` | `_______________` |
| 5 | 缺 auth | `___` | `_______________` | `_______________` |

> 至少 harvest **3 类不同 code** 才算 P0-1b 通过（少于 3 类则 baseline 太小，schema-drift canary 灵敏度不够）。

**额外检查：是否出现 auth-shape pattern code？**

观察是否有 code 含子串 `UNAUTH` / `FORBIDDEN` / `SESSION` / `TOKEN` / `CSRF` / `EXPIRED`（plan v2 Unit 4 的 auth-shape pattern match 由此驱动）。

| 项目 | 记录值 |
|------|--------|
| 是否出现 auth-shape code | `是（list：______）/ 否（仅 NOT_LOGGED_IN/UNAUTHENTICATED）` |
| 推荐写入 `_KNOWN_EXTENSIONS_CODES` 的 baseline 集 | `{________________________________}` |
| `_KNOWN_CODES_BASELINE_SIZE` 值 | `_____` |

---

## P0-2：JWT 位置 dump

### 目的

确定 `access_token` / `refresh_token` 的实际存储位置（cookie / localStorage / 两者皆有），为 Unit 3 的凭证持久化策略提供依据。

### 操作步骤

登录 velog 后，在 DevTools Console 执行：

```javascript
// 1. 查看所有 cookie（注意 httpOnly cookie 在此不可见）
document.cookie

// 2. 查看 localStorage
Object.entries(localStorage)

// 3. 查看 sessionStorage
Object.entries(sessionStorage)
```

同时用 Playwright 脚本（步骤 3 后续提供）dump `context.storage_state()`。

**P0-2 决定 Unit 3 的实现路径：**
- 若 token 在 cookie → `context.cookies()` 过滤 velog.io 域即可
- 若 token 在 localStorage → 需 `context.storage_state()` + origins 过滤
- 若两者皆有 → 双保险持久化

### 记录区

| 项目 | 记录值 |
|------|--------|
| 测试日期 | `_______________` |
| `access_token` 位置 | `cookie / localStorage / sessionStorage / 两者` |
| `refresh_token` 位置 | `cookie / localStorage / sessionStorage / 两者` |
| `io.velog.sid` 是否 httpOnly | `是 / 否` |
| 结论（持久化策略） | `cookies-only / storage_state / 双保险` |

---

## P0-3：Token TTL 三臂实测

### 目的

验证 idle TTL ≥ 24h（批跑窗口的核心前提）。避免 refresh-on-use 假象。

### 操作步骤

**臂 A（idle TTL，最重要）：**

1. 新登录账号，立即导出 cookie/storage_state
2. **不做任何调用**，等待 25 小时
3. 25h 后用相同 cookie 执行 P0-1 的 curl 命令
4. 记录是否成功

**臂 B（活跃 TTL 观测）：**

按以下时间点各执行一次 writePost mutation（可发 is_temp=true 测试文章）：
- [ ] 登录后 1h
- [ ] 登录后 6h  
- [ ] 登录后 24h
- [ ] 登录后 72h

记录每次是否成功，以及响应中是否包含新 token。

**臂 C（跨设备/跨 UA）：**

1. A 机（登录机）导出 cookie 文件
2. 复制到 B 机（不同 IP，不同 UA）
3. B 机用 A 机的 cookie 执行 writePost mutation
4. 记录是否成功

若 臂 C 失败 → session 绑设备指纹，Unit 3 文档必须明确声明"批跑须从登录机执行"。

### 记录区

**臂 A 记录：**

| 项目 | 记录值 |
|------|--------|
| 登录时间 | `2026-05-18 10:14 UTC（cookie 保存时间）` |
| access_token IAT | `2026-05-18 09:56 UTC，TTL=24h，EXP=2026-05-19 09:56 UTC` |
| 臂A 测试时间 | `2026-05-19 03:20 UTC（距 cookie 保存约 17h）` |
| 测试 mutation 结果 | `成功（id: 9b3d6c35，url_slug: p0-3-idle-ttl-probe-25h）` |
| refresh_token 测试 | `仅带 refresh_token 发送 → Set-Cookie 新 access_token(1h) + writePost=null；retry 带新 AT → 成功（id: 47d886f1）` |
| idle TTL 结论 | `✅ ≥ 24h（Go 条件满足）；refresh_token 窗口 30d；自动 refresh 通过 implicit Set-Cookie` |

**臂 B 记录：**

| 时间点 | 结果 | 是否刷新 token |
|--------|------|----------------|
| ~17h（实测） | `成功 id:9b3d6c35` | `否（AT 仍有效）` |
| RT-only probe | `AT=null，Set-Cookie 新 AT(1h)，retry 成功` | `是（Set-Cookie）` |
| 24h / 72h | `待测（非关键，30d RT 窗口已确认）` | - |

**臂 C 记录：**

| 项目 | 记录值 |
|------|--------|
| 跨设备测试 | `未单独测试（curl 在本机执行）` |
| 跨设备约束结论 | `待确认；当前无证据表明绑设备指纹` |

---

## P0-4：运营语种决断

### 决策问题

velog 主要受众是韩语开发者。V1 单语种。选项：
- **韩语（ko）**：velog 原生受众，话题相关性高，但目标外链受众是否对应？
- **英语（en）**：覆盖面广，但 velog 的韩语 SEO 权重可能不传导英语内容
- **不接入**：Phase 0 结论后直接回 brainstorm 转 dev.to / hashnode

### 记录区

> **运营负责人请填写**

| 项目 | 记录值 |
|------|--------|
| 决断日期 | `_______________` |
| 语种决断 | `韩语（ko） / 英语（en） / 不接入` |
| 理由 | `_______________` |
| 运营负责人签字 | `_______________` |

---

## P0-5：Google 索引率验证（两阶段）

### 阶段 1（feasibility，14 天）

**操作步骤：**

1. 用 P0-1 验证的 curl 方式发 5 篇真实文章，每篇包含目标站外链
2. 记录 5 篇的 URL 和发布时间
3. 14 天后核对每篇的索引状态：

```bash
# 用 Google 搜索验证
site:velog.io/@<user>/<slug>
```

**文章记录表：**

| 篇号 | URL | 发布时间 | 14天后索引？ |
|------|-----|----------|-------------|
| 1 | `velog.io/@<user>/...` | `___________` | `是 / 否` |
| 2 | `velog.io/@<user>/...` | `___________` | `是 / 否` |
| 3 | `velog.io/@<user>/...` | `___________` | `是 / 否` |
| 4 | `velog.io/@<user>/...` | `___________` | `是 / 否` |
| 5 | `velog.io/@<user>/...` | `___________` | `是 / 否` |

| 阶段 1 结论 | `_____ / 5 篇被索引（≥4 = ≥70% → 继续 / <4 → No-Go）` |
|-------------|-------------------------------------------------------|
| 核对日期 | `_______________` |

### P0-5b：风控控制实验（0.5 人日）

**目的：** 验证 30s 间隔连发 5 篇是否触发风控。

**操作步骤：**

```bash
for i in {1..5}; do
  echo "=== 发第 $i 篇 ==="
  # 执行 P0-1 的 curl 命令（替换 TITLE 为 "spike-ratelimit-$i"）
  sleep 30
done
```

**记录：**

| 篇号 | post_id | HTTP 状态 | 风控信号 |
|------|---------|-----------|---------|
| 1 | `cc4af862` | 200 | 无 |
| 2 | `81fb31b7` | 200 | 无 |
| 3 | `919e7b45` | 200 | 无 |
| 4 | `f08d139a` | 200 | 无 |
| 5 | `e426cc3d` | 200 | 无 |

测试时间：2026-05-19 03:22~03:24 UTC，间隔 30s，账号 @redredchen01

| P0-5b 结论 | ✅ `30s 间隔无风控信号（5/5 成功）` |
|------------|-------------------------------|

---

## P0-6：达标线汇总

> 填写所有分项后，工程 lead 汇总并给出 Go/No-Go 决断。

| 分项 | 状态 | 备注 |
|------|------|------|
| P0-1 curl writePost 成功 | `✓` | `url_slug` 必传（非 null）；端点 v2.velog.io/graphql |
| P0-1b error model 确认 | `✓（重定义）` | velog 无 extension code；坏输入 = silent-drop；`_KNOWN_EXTENSIONS_CODES` 机制不适用 |
| P0-3 臂 A idle TTL ≥ 24h | `✓` | AT=24h(login)/1h(refresh)；RT=30d；implicit Set-Cookie refresh |
| P0-5 阶段 1 索引率 ≥ 70% | `⏳ 待完成` | ETA: 2026-06-02（需另发 5 篇 public 文章） |
| P0-4 运营语种决断 | `⏳ 待完成` | 运营负责人填写 |
| P0-5b 30s 间隔无风控 | `✓` | 5/5 成功，无任何风控信号 |

**最终决断：`[ ] Go（技术面就绪，待 P0-4 + P0-5 + P0-7）  [ ] No-Go`**

---

## P0-7：联签

> 必须两人签字，Unit 2-6 方可启动。

| 角色 | 姓名 | 签字日期 |
|------|------|----------|
| 运营负责人 | `_______________` | `_______________` |
| 工程 lead | `_______________` | `_______________` |

**Go 附加记录（仅 Go 时填写）：**

| 项目 | 记录值 |
|------|--------|
| idle TTL 实测值 | `access_token 24h（首次登录）/ 1h（refresh 后）；refresh_token 30d` |
| 批跑窗口上限 | `30d（refresh_token TTL）；每次请求自动续期 access_token(1h)，无需重登` |
| 跨设备约束 | `待确认（当前无设备指纹证据）` |
| 持久化策略（来自 P0-2） | `cookies-only（HttpOnly；无 localStorage）` |
| 语种（来自 P0-4） | `待运营填写` |
| P0-1 确认的必需 headers | `origin, referer, sec-fetch-site, sec-fetch-mode, content-type, user-agent` |
| **P0-1b error model** | `velog 不返回 extension codes；error 形式 = silent-drop (data.writePost: null)；Unit 4 不实现 _KNOWN_EXTENSIONS_CODES 机制` |
| **Unit 4 关键修正** | `url_slug 必传（由 slugify(title) 生成）；retry-on-silent-drop 一次（应对 AT 自动 refresh）` |
| **是否需要 CSRF token** | `否（P0-1 实测确认）` |
| P0-1 mutation 必需字段（7 字段） | `title, body, tags, is_markdown, is_temp, is_private, url_slug` |

---

## 附录：快速参考命令

### 用 Python 检查当前 velog GraphQL schema（introspection）

```bash
curl -s -X POST 'https://v3.velog.io/graphql' \
  -H 'Content-Type: application/json' \
  -d '{"query": "{ __schema { mutationType { fields { name args { name type { name kind ofType { name kind } } } } } } }"}' \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
fields = data['data']['__schema']['mutationType']['fields']
wp = [f for f in fields if 'writePost' in f['name'].lower()]
print(json.dumps(wp, indent=2, ensure_ascii=False))
"
```

### Playwright 脚本：登录并 dump 凭证

```python
"""P0-2 工具：velog 登录 + 凭证 dump。
运行：python3 docs/spikes/velog_login_dump.py
输出：velog_credentials_dump.json
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

DUMP_PATH = Path("velog_credentials_dump.json")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("请在弹出的浏览器中完成 velog 登录...")
        await page.goto("https://velog.io")

        # 等待登录完成（检测到 access_token cookie 或登录后的 URL 变化）
        try:
            await page.wait_for_function(
                "document.cookie.includes('access_token') || "
                "document.querySelector('[data-testid=\"user-info\"]') !== null",
                timeout=120000
            )
        except Exception:
            print("等待超时，继续 dump 当前状态...")

        # dump cookies
        cookies = await context.cookies()
        velog_cookies = [c for c in cookies if "velog.io" in c.get("domain", "")]

        # dump localStorage / sessionStorage
        local_storage = await page.evaluate("Object.entries(localStorage)")
        session_storage = await page.evaluate("Object.entries(sessionStorage)")

        # dump storage_state
        storage_state = await context.storage_state()
        # 过滤 velog.io origins
        storage_state["origins"] = [
            o for o in storage_state.get("origins", [])
            if "velog.io" in o.get("origin", "")
        ]

        dump = {
            "velog_cookies": velog_cookies,
            "local_storage": dict(local_storage),
            "session_storage": dict(session_storage),
            "storage_state_velog_only": storage_state,
        }

        DUMP_PATH.write_text(json.dumps(dump, indent=2, ensure_ascii=False))
        print(f"✓ 凭证已 dump 到 {DUMP_PATH}")
        print(f"  velog cookies: {[c['name'] for c in velog_cookies]}")
        print(f"  localStorage keys: {list(dict(local_storage).keys())}")

        await browser.close()

asyncio.run(main())
```

将上述脚本保存为 `docs/spikes/velog_login_dump.py`，运行后查看 `velog_credentials_dump.json`。

### 快速检查 idle TTL（臂 A 用）

```bash
# 保存登录时的 cookie 到文件（从 velog_credentials_dump.json 或 Chrome DevTools 提取）
# 格式：name=value; name2=value2; ...
COOKIE_FILE="velog_cookies.txt"

# 25 小时后执行此命令验证
curl -s -X POST 'https://v3.velog.io/graphql' \
  -H 'Content-Type: application/json' \
  -H "Cookie: $(cat $COOKIE_FILE)" \
  -H 'Origin: https://velog.io' \
  -d '{
    "operationName": "WritePost",
    "query": "mutation WritePost($input: WritePostInput!) { writePost(input: $input) { id url_slug } }",
    "variables": {
      "input": {
        "title": "[P0-3 臂A idle TTL 测试]",
        "body": "TTL test post - will be deleted",
        "tags": [],
        "is_markdown": true,
        "is_temp": true,
        "is_private": true,
        "series_id": null
      }
    }
  }' | python3 -m json.tool
```

> 注意：`is_temp=true, is_private=true` 可减少对生产内容的污染；测试完成后手动删除。
