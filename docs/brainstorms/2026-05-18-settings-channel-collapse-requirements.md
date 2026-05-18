---
date: 2026-05-18
topic: settings-channel-collapse
---

# Settings 页面：渠道分组与折叠（瘦身版）

## Problem Frame

当前 `/settings` 页面把 Blogger OAuth、Blog ID 映射、Medium OAuth、Medium Integration Token、SEO 锚文本、排程发布等 6 张卡片平铺排列，模板已达约 675 行、滚动两屏才能看完。Blogger 卡片本身包含较长的 Google Cloud Console 教学；Medium 卡片同时提供 OAuth 与 Integration Token 两种路径，进一步拉长视线。

短期内还要接入 velog.io、telegra.ph 两个发布渠道（参见 `2026-05-15-velog-and-telegraph-adapters-requirements.md`），平铺会继续恶化。

本次目标是**只解决「页面太长、配置触达成本高」的 UX 问题**：把 settings 重构成「发布渠道」与「全局设置」两个分区，每个渠道一张可折叠卡片，并把现有渠道卡片体抽到独立 Jinja partial 文件。**不**引入 ChannelRegistry 抽象、**不**做自动注册、**不**为 velog / telegra.ph 添加 Coming soon 占位卡——这些将在第 3 个真实 adapter 落地时由真实需求驱动。

## User Flow

```
进入 /settings
    │
    ├── 顶部分区：发布渠道（独立可折叠卡片栈，非严格 accordion）
    │     ├── [▶] Blogger        [● 已授权 / 未授权]
    │     └── [▶] Medium         [● OAuth] [● Token]
    │            │
    │            └── 点开后展开完整配置面板
    │                 ├── 凭据 / Token 输入
    │                 ├── 平台专属资源映射（Blogger Blog ID）
    │                 └── 该渠道的操作按钮（授权 / 撤销 / 保存）
    │
    └── 底部分区：全局设置（常驻可见，不折叠）
          ├── SEO 锚文本关键词池（per-target 域名）
          ├── 排程发布设定（最小间隔 / 抖动）
          └── 配置文件路径（只读信息）
```

velog.io 与 telegra.ph 的渠道卡将在各自 adapter 实装的同一个 PR 中加入，不在本改版范围。

## Requirements

**信息架构**

- R1. 页面顶部新增「发布渠道」分区标题，下含 Blogger、Medium 两张可独立折叠的卡片，按字母顺序固定。
- R2. 页面底部新增「全局设置」分区，包含 SEO 锚文本关键词池、排程发布设定、配置文件路径三块内容；分区内不折叠，保持常驻展开。
- R3. 「Blogger Blog ID 映射」从独立卡片移入 Blogger 渠道卡内部，作为该卡片展开后的一个子 section。

**折叠卡组件（注意：不是 accordion，是独立可折叠卡片栈）**

- R4. 每张渠道卡片默认折叠；进入页面时所有渠道卡都是收起状态。
- R5. 折叠状态下，卡片头部必须显示：渠道图标 + 渠道名 + 状态徽章（≥1 个）+ 展开 chevron。
- R6. 状态徽章首期仅 2 态：`ok`（绿，已配置可用）/ `err`（红，未配置）。Medium 因有 OAuth + Token 两条独立凭据路径，可同时显示 2 个徽章（如 `[● OAuth] [● Token]`），各自独立着色。未来出现「token 过期 / 刷新中 / 速率受限」等中间态时再引入 `warn` 第三态。
- R7. 展开任一卡片不会自动折叠其它卡片（独立 Collapse，**不**使用 Bootstrap 5 的 `data-bs-parent` accordion 模式）；用户可同时展开多张卡片对照填写。
- R8. 卡片头部用 `<button type="button" data-bs-toggle="collapse">` 包裹（独立于卡片内部的 `<form>`），从结构上排除事件冒泡风险；任何 form submit 与 inline JS 句柄（copyUri / toggleSecret / toggleToken / Loading Overlay 全局 submit 监听器）行为不变。

**可访问性**

- R9. 头部触发器必须可由 Tab 聚焦；Enter / Space 触发折叠切换；带 `aria-expanded={true|false}` 与 `aria-controls={panel id}`；chevron 与图标 `aria-hidden=true`；状态徽章对屏幕阅读器暴露文本（如 `aria-label="Blogger 已授权"`）。
- R10. 折叠/展开切换后焦点保留在触发器上，不跳到面板内部。

**操作按钮反馈**

- R11. 「保存」类按钮在 form submit 中沿用现有 Loading Overlay 全局机制（不新增组件）；成功后通过 flask flash 消息 inline 展示（保持现有 `{% if flash %}` 行为）。
- R12. 「撤销授权」按钮保留现有 `confirm()` JavaScript 二次确认（如现状），不新增 Modal 组件；按钮在 loading 状态下 disabled。
- R13. 错误反馈沿用 flash 消息机制；本次改版**不**新增 toast / Modal / inline error helper 等任何新反馈组件。

**模板拆分**

- R14. 各渠道卡片体抽离到独立 Jinja partial：`webui_app/templates/_settings_channel_blogger.html`、`_settings_channel_medium.html`；`settings.html` 主模板通过具名 `{% include %}` 引用。**不**引入 ChannelRegistry 抽象，**不**做循环遍历——主模板对每个渠道有一行显式 include。
- R15. 「全局设置」分区下的 SEO 锚文本块亦抽到 partial：`_settings_global_keywords.html`，与排程块 `_settings_global_schedule.html` 并列；目的是把 SEO 锚文本目前 ~40 行的 details 块从主模板里挪出去，让主模板回归信息架构骨架。

## Success Criteria

可验证、与 UX 痛点直接对齐的行为指标，**取代**原版的「首屏可见」和「主模板 LOC 不增加」这类 proxy metric：

- **任务时间**：修改任一渠道 OAuth 配置的鼠标交互 ≤ 2 次（点头部展开 + 点字段聚焦）；不再需要从顶部滚动到目标卡片。
- **信息架构**：进入页面时桌面端（≥1280×720）首屏内必须能看到「发布渠道」分区标题 + Blogger + Medium 两张折叠头部。移动端（375px）首屏可见至少 1 张完整折叠头部，第 2 张部分可见。
- **回归零容忍**：以下 9 个 POST 端点 + inline JS 句柄行为 100% 等价，作为 PR merge 前必过的回归清单：
  - `/settings/blogger/oauth-start`、`/settings/save-blogger-oauth`、`/settings/revoke-blogger`、`/settings/save-blog-ids`
  - `/settings/medium/oauth-start`、`/settings/save-medium-token`、`/settings/clear-medium-token`、`/settings/clear-medium-oauth`
  - `/settings/save-target-keywords`、`/settings/schedule`
  - copyUri / toggleSecret / toggleToken / addRow / removeRow inline JS 不变
  - Loading Overlay 在每个 OAuth/保存提交时仍触发
  - deep-link `#blogger-blog-ids` 仍能滚动到 Blog ID 映射子 section（即使它现在嵌在 Blogger 卡内部，锚点 id 保留）
- **可访问性**：键盘单独操作即可完成「定位 → 展开 → 编辑 → 提交」全流程；屏幕阅读器读出渠道名 + 状态徽章文本。

## Scope Boundaries

- **不**引入 ChannelRegistry / 自动注册机制（延后到第 3 个真实 adapter 落地时）。
- **不**为 velog.io、telegra.ph 添加 Coming soon 占位卡（这两个渠道在各自 adapter PR 中加入）。
- **不**修改任何后端发布行为、模型字段、数据库 schema。
- **不**引入前端框架（保持纯 Bootstrap 5 + 原生 JS）。
- **不**引入新反馈组件（toast / Modal / inline error helper）；继续使用 flash + Loading Overlay + 原生 `confirm()`。
- **不**重构 Blogger OAuth 内部的 Google Cloud Console 教学文案。
- **不**实现拖拽排序 / 状态过滤 / 搜索 / 折叠状态 localStorage 持久化。
- **不**统一 SEO 锚文本与 Blog ID 映射的「按目标域配置」总表（见 Outstanding Questions：本次保留两者分置的不一致，承担一次「同一目标域要在两处编辑」的 UX 摩擦，留待后续观察）。

## Key Decisions

- **Cut 投机性架构**：4 位 reviewer 独立指出，ChannelRegistry + 自动注册 + Coming soon 占位卡对当前 2 真实渠道 + 4 可见渠道是 framework-ahead-of-need。Rule of three——抽象应由第 3 个具体 case 驱动，不是预制好等需求适配它。本次只解决「页面太长」UX 痛点。
- **命名修正**：避免称作 accordion——本设计允许多开，是「独立可折叠卡片栈」（Bootstrap 5 Collapse without `data-bs-parent`），命名错误会让实现者套用 mutual-exclusion 然后再拆掉。
- **状态徽章简化为 2 态**：现有代码路径只产生 binary 状态（`{% if blogger_token %}` / `{% if medium_token_set %}`），三态 ok/warn/err 是过度规范。warn 等真实中间态出现时再引入。
- **事件冒泡用结构隔离而非 JS stopPropagation**：头部触发器用独立 `<button>` 包裹（不嵌套在 form 内），从 DOM 结构上根除冒泡风险，比依赖运行时 `stopPropagation()` 更可靠。
- **保留现有反馈机制**：不引入新 UI 组件来「补足」按钮状态——继续用 flask flash + Loading Overlay + 原生 `confirm()`。这与「不引入前端框架」边界一致。
- **R3 vs SEO 锚文本不一致**：明知 Blog ID 映射（per-domain）放进 Blogger 卡、SEO 锚文本（per-domain）放在全局是分类不一致；本次接受这种不一致，承担「同一目标域两处编辑」的小摩擦。统一为「按目标域配置」总表需要更大重构，等数据驱动证明值得做时再做。
- **删除 LOC ceiling 与首屏可见 proxy 指标**：用「任务交互次数 + 桌面/移动两个 viewport 上的具体可见性 + 回归清单」替代。LOC 可以通过 include 移动来 gaming，无法测量真实复杂度变化。

## Dependencies / Assumptions

- 假设现有所有 settings 路由 URL 保持不变；前端只是把 form 包进折叠卡。
- 假设 Bootstrap 5 Collapse 在不带 `data-bs-parent` 的情况下，多张卡片独立工作（标准行为）。
- 假设 `webui_app/helpers.py` 的 `_settings_context()` 现有上下文键（`blogger_token`、`blogger_client_id`、`blog_ids`、`medium_token_set`、`medium_oauth_configured`、`callback_uri` 等）保持不变；模板拆分后各 partial 通过 `with context` 继承。

## Outstanding Questions

### Resolve Before Planning

（无）

### Deferred to Planning

- [Affects R8][Technical] Bootstrap 5 Collapse + 内部 `<form>` + 全局 submit 监听器（Loading Overlay）的具体兼容性验证：实装时跑一遍点击「确认绑定」「使用 Google 账号登录」「保存 Token」三个流程，确认 Loading Overlay 仍触发、Collapse 不拦截 submit。
- [Affects R14][Technical] partial 文件命名：用前缀 `_settings_channel_*` 还是放到子目录 `_settings/channels/blogger.html`。本次只有 2 个渠道，前缀法更直接；如果 planning 时发现现有模板已用子目录约定，对齐之。
- [Affects R6][Needs research] 检查 Medium 当前 `medium_oauth_configured` 与 `medium_token_set` 两个 flag 是否真的相互独立（即用户能否两个都设、或都不设、或只设一个）；徽章设计基于「两个独立路径，各自显示一个 ok/err 徽章」，需在 planning 时验证。
- [Affects 整体] 跨平台「按目标域配置」总表（SEO 锚文本 + Blog ID + 未来 velog/telegra.ph 的 per-domain 字段）的可行性，等接入第 3 个渠道时重新评估。本次明确保留 inconsistency。

## Next Steps

→ `/ce:plan` for structured implementation planning
