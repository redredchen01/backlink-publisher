---
date: 2026-05-26
topic: overview-inline-channel-binding
---

# 渠道綁定總覽 — 注册表驱动的统一绑定面

> 修订记录：初稿基于"总览 10 渠道 / 缺 3 卡"的过时快照。会话期间并发 session 把 phase1 PR #236 合入本地 main（HEAD `4852ee0`），`active_platforms()` 实际为 **27 个**。文档已据此重写为根治方案。

## Problem Frame

`/settings` 的渠道绑定有两个不同步的来源:

- **渠道綁定總覽**（`#section-dashboard`）由注册表 `active_platforms()` **动态驱动** → 当前列出**全部 27 个**渠道。
- **发布渠道**（`#section-channels`）由模板**写死的 6 张卡**组成（blogger/medium/velog/ghpages/devto/notion）。

后果（两个叠加的缺陷）:

1. **死锚点 / 无绑定面**：总览每张卡的 `Configure ↓` 指向 `#channel-<name>`,但只有 6 个有对应卡片。**约 21 个渠道**出现在总览里却无处绑定,`Configure↓` 是死链。
2. **bound 误报**：`webui_app/binding_status.get_channel_status()` 的 `bound` 由 offline `verify_adapter_setup()` 决定,而后者只对 `_SETUP_CHECKS` 里的 7 个渠道有判定;**其余 ~20 个已注册渠道**全部返回 `bound=False` + blocker `"No adapter configured for platform: X"` —— 即便它们都已 `register()`。总览大部分卡因此误显"未綁定"。

**根因**:渠道列表是注册表驱动（加渠道自动出现）、绑定 UI 是写死的 HTML（加渠道不自动出现）→ 结构性漂移。本次**转向根治**:让绑定面也由注册表驱动,按各渠道的 auth 类型自动渲染绑定 UI,一次覆盖全部 active 渠道;新增渠道零模板改动。

## Approach（已定方向）

把"渠道綁定總覽"升级为**唯一的、注册表驱动的绑定面**。每个渠道按其 **auth 类型**自动套用一个绑定 UI 模板,而非逐个写死卡片。`develop` 的 6 张工作卡按下表收敛或保留。

**auth 类型 → 绑定 UI 模板**（完整 27 渠道的逐个归类是规划期的代码审计任务,见 Outstanding）:

| auth 类型 | 绑定 UI | 代表渠道 | 本轮处理 |
|---|---|---|---|
| **ANON**（无凭证) | 「免绑定·就绪」徽章 + 无副作用连通性探测 | txtfyi、telegraph(auto) | ✅ 纳入 |
| **TOKEN**（单字段密钥) | token-paste 表单 | devto、ghpages | ✅ 纳入 |
| **TOKEN+FIELDS**（多字段) | 多字段表单 | notion(token+db_id) | ✅ 纳入 |
| **USERPASS**（账密,服务端存) | username+password 表单 | livejournal | ✅ 纳入 |
| **OAUTH**（重定向) | 复用现有 OAuth 流程 | blogger、medium(oauth) | ⏸ 保留现状,接入统一面但不重写流程 |
| **BROWSER_LOGIN**（Chrome/Playwright 登录) | 浏览器登录流程 | velog、medium(browser)、**mastodon** | ⏸ velog 保留现状;**mastodon 拆出单独评估** |

## Requirements

**注册表驱动绑定面（根治漂移）**
- R1. 绑定面必须由 `active_platforms()` 驱动:新增/退役渠道时绑定面自动同步,**不需要新增写死 HTML**。消除"注册表 vs 写死卡"的漂移根因。
- R2. 每个渠道按其 auth 类型套用对应绑定 UI 模板（见上表 ANON/TOKEN/TOKEN+FIELDS/USERPASS）。OAUTH 与 BROWSER_LOGIN 类渠道接入同一面板,但**复用各自现有流程**,本轮不重写。
- R3. 消除所有死锚点 `Configure ↓`:渠道的绑定/配置在面板内**内联完成**,不依赖跳转到已退役/不存在的卡片。
- R4. 现有 6 张工作卡（blogger/medium/velog/ghpages/devto/notion）的绑定/配置能力**零回归**;迁入统一面后行为与原先一致。

**bound 状态正确性（修共用缺陷）**
- R5. 修正 `bound` 误报:所有已注册渠道在 offline 路径下不得因缺 `_SETUP_CHECKS` 条目而错报 `"No adapter configured"`。为各 auth 类型补 offline setup-check（ANON 恒就绪;TOKEN/USERPASS 检凭证文件存在;等）。
- R6. ANON 类（如 txtfyi/telegraph-auto）呈现「免绑定·可直接发布」绿色就绪态,不显示"未綁定 + 绑定按钮"。
- R7. 渠道的 dofollow/nofollow 标注沿用注册表 `dofollow_status()`,nofollow 渠道必须显式标注（与现有 nofollow 警示一致）。

**连通性验证（非破坏性）**
- R8. 沿用通用 `/api/<channel>/verify` 做绑定结果验证;R5 的 offline check 修好后,verify 对全部渠道可用。
- R9. ANON 类的连通性探测**必须无副作用**——绝不触发真实公开发布（txtfyi form-post 按一下会留真 paste）。用无副作用探测（如表单页 GET / `dry_run_intercept` 拦截),并以"绝不产生真实 paste"为验收条件。〔取代初稿 R6 的"测试发布"按钮——多角色共识其有公网足迹风险且 verify 可能已够。〕

**交互状态（每个异步动作都要定义）**
- R10. 每个异步绑定动作（保存凭证 / 连通性探测 / verify / 浏览器登录）触发后必须:禁用按钮 + 切换 in-progress 文案、禁止并发重复触发,直到结果返回。
- R11. 失败态必须 inline 回显在卡片内:用户向文案(非原始 stack trace / 非被截断的 stderr)、失败后输入保留、可直接重试。
- R12. 已绑定态卡片显示摘要(各 auth 类型字段不同:USERPASS 显 username;TOKEN 显 masked;ANON 显"就绪")+ `last_verified_at`;绑定/状态变更后**局部即时刷新**,无需整页刷新。
- R13. 破坏性"清除凭证"动作需二次确认,并说明后果与清除后回落的状态。

**安全（新绑定面与新路由的硬约束,非待规划项)**
- R14. 任何用户可控的 URL 输入（如未来 BROWSER_LOGIN 的实例地址)在保存与使用前必须过 `_util/net_safety` 的 SSRF 校验 + 强制 https + 拒绝私有/回环/链路本地/元数据网段。
- R15. 所有新凭证保存路由必须挂在受 `_global_csrf_guard` 覆盖的 blueprint 下并通过 CSRF 校验,前端带 `X-CSRFToken`(注意 medium_login 的 csrf_client ≠ 全局守卫的陷阱,见 [[reference_webui_csrf_architecture]])。
- R16. 所有以 `channel` 为键的路由/文件路径/profile 目录构造前,必须用注册表白名单校验 `channel`,拒绝未注册值与含 `/`、`..`、空字节的输入(防路径穿越)。
- R17. password / hpassword / token / cookie / storage-state **绝不得**出现在日志、stderr 诊断、异常消息或前端错误回显中;凭证文件经 `safe_write.atomic_write` 写入 `0o600`。

**livejournal（USERPASS 实例）**
- R18. 内联 username + password 表单,提交走新保存路由,后端调既有 `store_credentials(config, username, password)`(密码即时派生 hpassword,明文不落盘);旁附醒目警告:**仅用一次性小号**(凭证 password-equivalent、不可吊销)。已绑定支持重新绑定(rotation 走同一路径)与清除。

## Success Criteria
- 在 `/settings` 总览面板内,可对全部 active 渠道(除 mastodon 等 BROWSER_LOGIN 拆出项)完成与其 auth 类型相符的绑定/确认;**无死锚点**。
- 新增一个 TOKEN/USERPASS/ANON 类渠道到注册表后,绑定面**自动出现**对应 UI,无需改模板 HTML(根因消除的验收)。
- 总览不再有渠道因 `_SETUP_CHECKS` 缺项而误报"未綁定 / No adapter configured"。
- 现有 6 个工作渠道绑定流程零回归(含 OAuth/velog browser-login)。

## Scope Boundaries
- **mastodon 拆出**:本轮不做。其 BROWSER_LOGIN 有未解技术前提——bind 路径存 Playwright `storage_state` JSON,而发布走 `real-chrome-profile/<channel>`,**两者不互通**;且登录 URL 随 `instance_url` 变,静态 recipe 表达不了。需单独一轮做 cost/benefit(referral_value="high" 但 nofollow)+ 解 storage_state↔profile 兼容。
- **OAUTH / 其余 BROWSER_LOGIN 不重写**:blogger OAuth、medium 三模式、velog 登录保留现有流程,只接入统一面板的呈现/状态层,避免回归。
- **不**改发布流程、不改 publish 适配器本身;只动 WebUI 绑定面 + binding_status + 新增凭证保存路由。

## Key Decisions
- 转向**注册表驱动统一绑定面**而非逐个内联/逐个补卡:逐个方案随 27→N 渠道线性堆定制 UI,正是要消除的漂移;注册表驱动一次根治。
- **按 auth 类型模板化**:ANON/TOKEN/TOKEN+FIELDS/USERPASS 走自动模板;OAUTH/BROWSER_LOGIN 复用现有流程(回归风险高、且 mastodon 类有未解前提)。
- **mastodon 拆出单独评估**:三渠道里成本最高且 nofollow,捆进本轮会拖慢根治主线。
- **砍掉 txtfyi"测试发布"按钮**,改为无副作用连通性探测:避免公网真 paste 足迹。

## Dependencies / Assumptions
- 复用:`store_credentials()`(livejournal)、`/api/<channel>/verify`、`_util/net_safety` SSRF 防御、`safe_write.atomic_write`、注册表 `active_platforms()`/`dofollow_status()`/registered_platforms 白名单、`_global_csrf_guard`、`channel-binding.js` 的 `renderResult` 局部刷新。
- `_SETUP_CHECKS` 当前仅含 blogger/devto/ghpages/medium/notion/telegraph/velog 7 项(已代码证实)。

## Outstanding Questions

### Resolve Before Planning
（无 — 方向性产品决策已定:根治/统一面、mastodon 拆出、砍 dry-run、安全为硬需求。）

### Deferred to Planning
- [Affects R1/R2][Technical] 27 个 adapter 的逐个 auth 类型归类审计(谁是 ANON/TOKEN/TOKEN+FIELDS/USERPASS/OAUTH/BROWSER_LOGIN),据此确定每个模板覆盖哪些渠道。
- [Affects R1/R4][Technical] 统一面如何承载 OAUTH(blogger)与 velog browser-login 的现有流程:嵌入 vs 链接到保留的局部卡;以及 `#section-channels` 写死区块是退役还是收敛为"复杂 auth 专区"。
- [Affects R5][Technical] `_SETUP_CHECKS` offline-check 的扩展形态:逐 auth 类型一个通用判定 vs 逐渠道;及其对 drift-check / `test_no_monolith_regrowth` / R9 extension-readiness 断言的影响。
- [Affects R5/R6][Needs research] txtfyi 等 ANON 渠道改为"就绪态"后,`bound` 字段取值(True 还是保持 False+ready 标志)对 active_platforms 一致性断言与下游消费者的影响。
- [Affects R9][Technical] txtfyi 无副作用探测的具体手段:`dry_run_intercept`(requests-backed,Session.send 拦截早于 edit.php) vs 仅 GET 表单页;确认 `/api/<channel>/dry-run` 路由当前**不存在**(需建或避免)。
- [Affects R18][Technical] livejournal 保存路由的 blueprint 归属(token_paste vs bind vs settings_basic)与错误回显格式。
- [Affects 整体] mastodon 拆出轮的独立计划:storage_state↔real-chrome-profile 兼容、instance-aware 登录 recipe、扩 bind `CHANNELS` frozenset 的安全影响、SSRF 校验。

## Next Steps
→ `/ce:plan` for structured implementation planning
