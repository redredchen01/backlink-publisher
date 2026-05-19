---
date: 2026-05-19
topic: telegraph-channel-end-to-end-wiring
---

# Telegraph 渠道端到端接线：从 spike 到"列表里看得见"

## Problem Frame

用户视角的痛点：**"为什么我的发布渠道列表里看不到 telegraph"** —— 即便 `scripts/telegraph_spike/publish_batch.py` 早已跑通、Phase 0 ship-seal 在用 API 路径批量发布。

源头不是单一原因，而是三条独立断链：

| # | 断链 | 现状 | 影响 |
|---|---|---|---|
| A | **Registry 未注册** | `adapters/__init__.py:39-40` 只 `register("blogger", …)`、`register("medium", …)`；telegraph 只活在脱离 dispatcher 的 spike 脚本里。`velog` 已 register（line 42）但**也**在 WebUI 不可见 —— 同一 bug 早于 telegraph 存在 | CLI 的 `--platform` choices、`schema.supported_platforms()` 都不含 telegraph |
| B | **WebUI 平台列表硬编码（远超 telegraph）** | `webui_app/templates/index.html:838-841` select、`:1253-1255` filter chip、`:1261` norm_platform tuple、`:1559/:1574` setVal、`:1654` JS 计数 dict、`settings.html:647/663` 默认值；`webui_app/helpers.py:336-345` `detect_platform()` 也只识别 medium/blogger/wordpress（未知域名 fallback 到 medium，**已导致 velog/telegraph URL 被误归类**）。此外 `wordpress` 是**无 adapter 的幽灵选项** | R9 解耦 PR（5-18-009）把 CLI/schema 修好了，但 WebUI 完全没改 —— 这是 telegraph 之前就存在的债务（velog 是活证据） |
| C | **技术路径未拍板** | 5-15 brainstorm 走 API-only；5-19 settings-browser-binding **绝口未提** telegraph（非显式排除，是省略 —— 可能是疏漏，也可能因为 telegra.ph 没传统 login event 而不适配 5-19 contract）；用户原话要 Playwright | 决定 adapter 是 1 个还是 2 个、Phase 0 spike 如何收编 |

只解决任一条，问题表象就还在。本 brainstorm 是 5-15 telegraph-adapter 与 5-19 settings-browser-binding 的**收尾接线**：把 telegraph 从"spike + 通用机制例外"升格为"端到端可见、可用、可降级"的生产渠道，**同时把 velog 这个早于 telegraph 就隐形的 WebUI 渠道一起救回来**。

## Architecture

```
Legend: register() 元组顺序 = primary → fallback 链

┌────────────────────────────────────────────────────────────────────┐
│ publishing/registry.py  (5-18-009 R9 解耦的单一事实源)             │
│   register("blogger",   BloggerAPIAdapter)                         │
│   register("medium",    MediumAPI → MediumBrave → MediumBrowser)   │
│   register("velog",     VelogGraphQLAdapter)         [已存在但 UI 不可见]
│   register("telegraph", TelegraphAPIAdapter → TelegraphBrowserAdapter) [新]
└──────────┬─────────────────────────────────────────────────────────┘
           │ registered_platforms()
   ┌───────┴───────┬─────────────────────────┐
   ▼               ▼                         ▼
 CLI argparse   schema.                   WebUI context processor [新]
 (已动态)       supported_platforms()      │  (默认方案，无需新 endpoint；
                (已动态)                   │   见 R6 决策)
                                           ├─→ index.html select/chip/norm/setVal/JS 计数 [全去硬编码]
                                           ├─→ settings.html 默认值                        [去硬编码]
                                           ├─→ helpers.py detect_platform                  [去硬编码]
                                           └─→ 幽灵选项 wordpress 清除

发布时 dispatcher 串行走 telegraph 链（按 register() 元组顺序）：
  TelegraphAPIAdapter.publish()  [primary]
    ├─ 成功 → AdapterResult(status="published", url=…)
    ├─ ExternalServiceError(429 / 5xx / 网络) → 直接传播，不 fall（与 Medium 链一致）
    └─ DependencyError (无 token / 模块缺失) ────┐
       OR 401 INVALID_TOKEN（先 createAccount 重试一次仍失败）─┴→ fall 到
                                                  TelegraphBrowserAdapter [fallback]
                                                  (Playwright headed Chromium,
                                                   复用 5-19 通用绑定基础设施)
```

## Requirements

**Group 1 — Registry & Adapter 接线 (根因 A)**

- R1. 新增 `src/.../adapters/telegraph_api.py`，实现 `Publisher` ABC，复用 `telegraph_node.py` 转换器与 `scripts/telegraph_spike/publish_batch.py` 的 createAccount/createPage 核心逻辑。Spike 脚本**不改动**（保留为 Phase 0 routines 的历史依赖，CLI surface freeze）。Adapter 与 spike 共用相同 token 文件路径（见 R5）实现 token 复用。
- R2. ~~（已砍：TelegraphBrowserAdapter 不再实现 / P0-Q1 决策）~~
- R3. 在 `adapters/__init__.py` 增加 `register("telegraph", TelegraphAPIAdapter)`（**单 adapter，无 fallback 元组**）。Telegraph 不需要 dispatcher fallback 链（无 browser 备援）；401 INVALID_TOKEN 在 adapter 内部就地自愈（R13）。
- R4. `verify_adapter_setup("telegraph", config)`：检查 token 文件可读 OR createAccount 端点可达；否则抛 `DependencyError`。**不**对称于 Medium（无 Playwright 二选一），更接近 Blogger 的"单路径，缺凭据即拒"模式。
- R5. Telegraph access_token 持久化策略（**fixed factual: 与 spike 文件名/schema 对齐**）：
  - **文件名**：`~/.config/backlink-publisher/telegraph-phase0-token.json`（保留 `-phase0-` 中缀以匹配 spike `publish_batch.py:340` 的实际写入路径），权限 0600
  - **Schema**：仅 `{access_token, short_name}`（spike `publish_batch.py:290` 实际写入字段，author_name / page_count **不写入**）
  - **Phase 0 解耦事实**：Phase 0 routines (T+7/14/21) 实际依赖的是 `recheck.py` + `results-manifest.json`，**不直接依赖 `publish_batch.py` 二次调用**。Spike 历史性产出 manifest 一次后即不再写入；adapter 共用 token 文件不会推翻 routines。SC5 由此降级为"不破坏 manifest 与 recheck.py"，而非"不破坏 spike CLI"

**Group 2 — WebUI 从 Registry 反向驱动 (根因 B)**

> ⚠ 完整硬编码站点比 5-15/5-19 任一文档预想的都多；R7-R10 修订为穷举清单。

- R6. WebUI 平台列表的注入方式：**默认走 Flask context processor**（每次模板渲染注入 `platforms` 上下文，无需新 endpoint、无需前端 fetch、无 loading/empty state 风险）。仅当未来出现明确的 SPA-style 客户端按需刷新需求时再新增 `GET /api/platforms` JSON endpoint。
- R7. `index.html:838-841` 的 platform `<select>` 改为模板循环 `{% for p in platforms %}<option value="{{p.slug}}">{{p.display_name}}</option>{% endfor %}`。
- R7b. `index.html:1559` 和 `:1574` 的 `setVal('platform', p.platform || 'blogger')` 默认值改为读取 `platforms[0].slug`（若有），fallback 仍是 `blogger`。
- R7c. `index.html:1654` 的 JS 计数 dict `platform: { all: 0, blogger: 0, medium: 0, other: 0 }` 改为后端渲染时序列化注入：`platform: { all: 0, {% for p in platforms %}{{p.slug}}: 0,{% endfor %} other: 0 }`。
- R8. `index.html:1253-1255` 的 filter chip 行从 `platforms` 上下文循环渲染；保留 `other` 兜底 chip 处理 norm_platform fallback。
- R8b. `index.html:1261` 的 `norm_platform` 模板表达式（当前硬编码 `('blogger', 'medium')` tuple）改为 `platforms | map(attribute='slug') | list`。
- R9. `settings.html:647/663` 的 `setSelect('platform', p.platform || 'blogger')` 默认值同 R7b 处理。
- R10. **清除幽灵 `wordpress` 选项**（**两处**）：
  - R10a. `index.html:841` `<option value="wordpress">` 移除
  - R10b. `webui_app/helpers.py:336-345` `detect_platform()` 移除 `wordpress.com` 分支（registry 没注册，分类器不应回填）。**注意**：未知域名 fallback 现在是 `medium` —— 这本身是 bug（telegra.ph URL 被误判为 medium），应改为 `None` 或显式 unknown 并由调用方处理
- R11. 加集成测试覆盖"反向驱动契约"：
  - R11a. **优先用 velog**（已 register，是真实存在的 invisible-in-UI 证据）作为 canary，断言 select / chip / norm tuple / JS 计数 dict 都自动出现 `velog` —— 这一项实际 fix 一个 pre-existing bug，不只是测试新机制
  - R11b. 同时用 mock `register("dummy", DummyAdapter)` 验证"加新 adapter 零 HTML 改动"。**注意**：`_REGISTRY` 是 module-level dict，测试必须用 fixture/context-manager snapshot+restore，否则会污染其他 test
- R11c. **检查 ROUTE_TIER_MATRIX 副作用**：`content_negotiation.py:_matrix_targets_registered_platforms` 用 set 差集监控未配置 tier 的平台。加 telegraph + velog（B 顺带救回）后必须为它们分配 tier 或显式声明 None tier，否则会触发"unconfigured platform"警告/失败

**Group 3 — API 自愈路径 (原"Playwright fallback"已砍 / P0-Q1 决策)**

> **P0-Q1 已决策（2026-05-19）：砍 TelegraphBrowserAdapter / R2 / R13 / R14 / R15**。理由：3 个 reviewer (product-lens, scope-guardian, adversarial) 独立指出 telegra.ph 无传统账号体系，401 INVALID_TOKEN 可通过就地 `createAccount` 重写 token 5 行代码自愈，无需独立 browser adapter。Playwright 路径未触发过实际失败，是 framework-ahead-of-need。
>
> 若未来真出现持续 401 / Telegram-bound 发布需求，开新 brainstorm 重新评估。

- R12. 默认走 `TelegraphAPIAdapter`：从 config 读取 access_token；无 token 时调一次 `createAccount` 写盘后继续 createPage。
- R13. **401 INVALID_TOKEN 就地自愈**：API 返回 401（确切 error code planning 阶段验证）时，**就地** 调一次 `createAccount` 重写 token 文件、重试 createPage 一次。仍失败才升级为 `ExternalServiceError` 抛出。此机制内化在 `TelegraphAPIAdapter.publish()` 内部，**不涉及 dispatcher fallback 链**。
- R14. **其他错误处理**（与 dispatcher 现有规则一致）：
  - `ExternalServiceError(429 / 5xx / network)` → 直接抛出，由 dispatcher / publish_backlinks CLI 的重试逻辑处理
  - 其他未预期异常 → 传播，不静默吞掉
  - `register("telegraph", TelegraphAPIAdapter)` 只注册一个 adapter（**不是元组**），dispatcher fallback 链对 telegraph 不适用

## Success Criteria

- **SC1 — 列表可见（telegraph + velog 一起）**：运行 `publish-backlinks --platform telegraph --help` 看到 telegraph 出现在 choices；打开 WebUI `/` 页 platform select 同时看到"Telegraph"和"Velog"（velog 是 B 修复的副作用证据）；wordpress 选项已消失；filter chip 行同步出现 telegraph + velog。
- **SC2 — API 路径端到端**：`publish-backlinks --platform telegraph --mode publish` 走真实 createPage，返回 `published_url`，落 JSONL 行 status=published。
- **SC3 — 401 就地自愈**：测试模拟 API 返回 401 INVALID_TOKEN 一次，adapter 自动 `createAccount` 重写 token、重试一次、成功落 status=published；token 文件被刷新；WARN 级日志记录 `telegraph_token_rotated` 计数（防止静默轮换掩盖入侵 — 见 Threat Model #3）。
- **SC4 — 反向驱动契约锁定**：R11 测试通过 —— 用 velog（已 register）作为真实 canary 证明 WebUI 自动渲染，再用 DummyAdapter mock 证明"加新 adapter 零 HTML 改动"。
- **SC5 — Phase 0 ship-seal 实质契约不被推翻**：`scripts/telegraph_spike/recheck.py` + `results-manifest.json` 行为保持兼容（这两个才是 Phase 0 routines T+7/14/21 实际调用的路径，非 publish_batch.py 二次调用）。Phase 0 deadlines (5/25, 6/01, 6/08) 不受影响。
- **SC6 — 后置价值校验**（来自 product-lens 增量）：merge 后 7 天内，至少 1 条经新 CLI/WebUI 路径发布的 telegra.ph URL 出现在 `site:telegra.ph <slug>` Google 索引中（或目标站 GSC 入链报告）。**若 Phase 0 ship-seal 已经测了等价指标，引用其结论并标 SC6 为 inherited**；否则这是真正的 value gate。

## Scope Boundaries

- ✗ **不重做 5-15 的 Phase 0 前置门**（dofollow/索引可达性已在 ship-seal 通道验证中，本 brainstorm 不重启该 gate）
- ✗ **不实现 wordpress adapter**（只是清掉 UI 幽灵选项；wordpress 接入是独立 brainstorm）
- ✗ **不动 Medium 的三件套适配链**（仅作为对照模板）
- ✗ **不加 Telegraph 编辑/删除/列表能力**（只 createPage；编辑能力可由 access_token + editPage 后续扩展，本期不做）
- ✗ **不引入 Telegram bot 绑定路径**（401 通过 createAccount 重写自愈即可）
- ✗ **不实现 TelegraphBrowserAdapter / Playwright 备援**（P0-Q1 决策：telegra.ph 无传统账号体系，浏览器路径解决的是不存在的问题；待真实失败模式出现再 revisit）
- ✗ **不与 5-19 settings-browser-binding 在本期发生交互**（Group 3 砍后，telegraph 不进入 5-19 CHANNELS 白名单，无机制差异需协调）
- ✗ **不改 dispatcher 自身**（沿用 5-18-009 R9 解耦后的 dispatch 规则，本期只加注册项）
- ✗ **不改 spike 脚本** `scripts/telegraph_spike/publish_batch.py`（保留为 Phase 0 历史依赖，CLI surface freeze；adapter 重写核心逻辑而非"提炼共享"，接受一次性 ~150 行复制成本换 routines 零风险）

## Key Decisions

- **API-only，无 browser fallback** — P0-Q1 决策。telegra.ph 无传统账号 → 401 自愈 = 重调 createAccount → 不需要独立 adapter。
- **WebUI 全面从 registry 反向驱动 + 清幽灵选项**（不是只加 telegraph option）— 用户在第二轮选定。理由：只加 telegraph 等于打补丁，velog 已是 invisible 活证据，下次加新 adapter 还得改 HTML，不是"从根本解决"。
- **WebUI 注入走 Flask context processor，不新增 `/api/platforms` JSON endpoint** — 简化方案：无 loading/empty state 风险、无前端 fetch、单用户本地部署下 auth 不是 blocker（P0-Q4 决策）。
- **R10 直接砍 wordpress 选项（UI + helpers.py 两处）**，无需 migration — P0-Q3 grep 验证：全仓零 wordpress 历史数据（jsonl/toml/json/yaml/fixtures 全空）。
- **Spike 脚本不动** — 不再提炼为薄壳。Adapter 与 spike 并存（共用 token 文件实现复用），spike CLI surface freeze 防止 routines 回归。

## Dependencies / Assumptions

- **5-18-009 R9 解耦只覆盖 CLI/schema 层**：本期 R6-R11 是 R9 解耦在 WebUI 层的首次延伸（非"R9 已完整 land"），整个 WebUI 模板/JS/helpers 都需扫一遍。
- **WebUI 部署模型 = 单用户本地 workstation，默认 bind 127.0.0.1**（P0-Q4 grep 验证：`webui.py:8-10, 85-88` 默认 loopback only，需 env var `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` 才能开非 loopback；session 仅用于 CSRF/OAuth state，不是 auth gate）。Context processor 在默认部署下安全；若用户启用 `BACKLINK_PUBLISHER_ALLOW_NETWORK`，需自负 auth 责任 —— 本期不增加 auth 中间件。
- **Phase 0 ship-seal 实际契约 = `recheck.py` + `results-manifest.json`**（已 grep 验证）。Spike 历史性产出 manifest 后，Phase 0 routines 只读 manifest，不再调 publish_batch.py。Adapter 与 spike 共用 token 不会推翻 routines。
- **ROUTE_TIER_MATRIX 必须为 telegraph + velog 各分配 tier 或 None**（见 R11c）；否则 `content_negotiation.py:_matrix_targets_registered_platforms` 的 set 差集会报"unconfigured platform"。

## Outstanding Questions

### Resolve Before Planning

（无 —— 4 条 P0 全部 2026-05-19 解决，详见 Key Decisions 段）

### Deferred to Planning

- [Affects R13][Needs research] Telegraph API token 失效的确切 error code（`INVALID_TOKEN` vs `INVALID_ACCESS_TOKEN` vs 其他）— planning 阶段实测验证；决定 R13 self-heal 触发条件的精确匹配
- [Affects R11][Testing] 反向驱动测试的 `_REGISTRY` snapshot+restore fixture 实现细节（context manager / pytest fixture / monkeypatch）
- [Affects R6/R7][Design] platform 的 display name 源头（adapter class attribute? 单独 i18n 表？）与 i18n 行为（项目已支持 ko/zh-TW/ja）—— 影响 select/chip/history/settings 各处显示
- [Affects R5][Security] 是否把敏感凭据从明文 JSON 改用 OS keychain（macOS Keychain / Secret Service）？本期接受明文 + 0600 + Threat Model 文档化为 known risk，planning 阶段可选
- [Affects R1][Implementation] Spike `publish_batch.py` CLI surface 是否加 snapshot test 锁住 routines 兼容性？（即便本期不改 spike，加测试可防未来误改）
- [Affects R13][Telegraph API] Telegraph 是否支持 `revokeAccessToken`？支持 → 401 自愈时显式 revoke 旧 token；不支持 → Threat Model #3 的"旧 token 仍有效"风险无解，只能依赖 WARN 日志+计数器告警

## Threat Model

来自 security-lens 评审；列为一等公民。Group 3 砍后，storage_state 相关威胁自动消失。

1. **最可能 — 本地文件泄露（备份/同步）**：`~/.config/backlink-publisher/` 被 Dropbox/iCloud/dotfiles repo 卷入。下游读到 telegraph token → editPage 重写已发布外链。**缓解**：在 README/AGENTS.md 明示此目录不应纳入备份；planning 阶段考虑 OS keychain 迁移（Deferred）。
2. **中影响 — WebUI 远程访问下的平台枚举**：默认 bind 127.0.0.1 安全；用户启用 `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` 后 context processor 渲染的 `platforms` 列表（含 available 状态）暴露给任意访问者。**缓解**：Dependencies 段已明示"远程模式下用户自负 auth 责任"；可选在 README 提示远程模式需自行加反向代理 auth。
3. **最隐蔽 — 静默 token 轮换掩盖入侵**：攻击者窃 token → editPage 慢速污染；本端 401 → R13 就地自愈 createAccount → 用户无感，攻击者旧 token 在 Telegraph 端仍可能有效。**缓解**：R13 自愈时强制 WARN 级日志 + 累加 `telegraph_token_rotated` 计数（包含在 SC3 中）；planning 验证 Telegraph 是否支持 `revokeAccessToken`。

## Next Steps

→ `/ce:plan` for structured implementation planning

Recommended planning split (2 units, Group 3 已砍):

1. **Unit 1 — TelegraphAPIAdapter + registry + 401 自愈** (R1, R3, R4, R5, R12, R13, R14 简化, SC1 CLI 部分 + SC2 + SC3 + SC5 + SC6)
2. **Unit 2 — WebUI registry-driven + 清幽灵选项 + ROUTE_TIER_MATRIX 协调** (R6-R11c, SC1 WebUI 部分 + SC4)

依赖关系：U1 和 U2 **可并行**。U2 的反向驱动测试用 velog 作 canary（velog 已在 registry，不依赖 U1）；这也意味着 U2 会**顺带救回 velog 在 WebUI 的可见性**。

Phase 0 ship-seal 三个 routines (T+7/14/21, deadlines 5/25, 6/01, 6/08) 与本期 2 units 完全解耦，可并行进行。
