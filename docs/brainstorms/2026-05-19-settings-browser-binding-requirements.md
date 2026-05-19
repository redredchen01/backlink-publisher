---
date: 2026-05-19
topic: settings-browser-binding
---

# Settings 页浏览器驱动备援绑定机制

## Problem Frame

backlink-publisher 当前的渠道绑定路径割裂：

- **Medium / Blogger**：标准 OAuth（`webui_app/routes/oauth.py`），但用户必须先在 Medium / Google Cloud Console 注册 OAuth 应用拿 client_id/secret。这一步对运营用户门槛高，且 Medium OAuth 长期处于 deprecated/受限审批状态，部分账号申不到。
- **Velog**：无官方 API，鉴权必须经社交登录（Google/GitHub/Facebook）后由 Playwright 导出 storage_state。`_settings_channel_velog.html` 已预告 "服务端启 velog-login CLI 弹 Chromium" 的方案，被 Phase 0 spike gate 锁住未实做。
- **Telegraph**：匿名发布，无需绑定，不在本次范围。

需要一个**统一的浏览器驱动绑定机制**，让用户从 settings 页点一个按钮，本机弹出 Chromium，自己用真实浏览器完成登录，凭据落盘。Velog 用它当主路径，Medium/Blogger 用它当 OAuth 的备援并列方案，方便没有 OAuth 应用注册条件、或 OAuth 受限的用户。

## User Flow

```
┌─ User in /settings ─────────────────────────────────────────────┐
│  [Velog tab]                                                    │
│    └─ [🌐 浏览器登录 velog] ← 唯一按钮                          │
│  [Medium tab]                                                   │
│    ├─ OAuth 表单 (Recommended)                                  │
│    └─ [🌐 浏览器登录 Medium] ← 并列备援                         │
│  [Blogger tab]                                                  │
│    ├─ OAuth 表单 (Recommended)                                  │
│    └─ [🌐 浏览器登录 Blogger] ← 并列备援                        │
└──────────────────┬──────────────────────────────────────────────┘
                   │ POST /settings/<channel>/browser-bind
                   ▼
        ┌──────────────────────────────────┐
        │ Server subprocess 启 Playwright  │
        │ headed Chromium 导航至登录页      │
        └──────────────────┬───────────────┘
                           │ (window pops on user's screen)
                           ▼
        ┌──────────────────────────────────┐
        │ User 手动完成社交/账密登录        │
        │ + 2FA / captcha 全在真浏览器里走  │
        └──────────────────┬───────────────┘
                           │ (driver 检测已登录态)
                           ▼
        ┌──────────────────────────────────┐
        │ 导出 storage_state.json          │
        │ → ~/.config/backlink-publisher/  │
        │   <channel>-state.json (0600)    │
        │ + 关闭 Chromium                  │
        └──────────────────┬───────────────┘
                           ▼
            Settings 页刷新，显示已绑定状态
```

## Requirements

**Settings UI**

- R1. 每个支持的渠道 tab（velog / medium / blogger）展示「🌐 浏览器登录 <channel>」按钮。
- R2. Velog tab：浏览器登录是**唯一**绑定入口（无 OAuth 表单），保留现有预告文案逻辑但替换 disabled 按钮为可点击。
- R3. Medium / Blogger tab：浏览器登录与 OAuth 表单**并列**显示，OAuth 一侧标 "Recommended"。
- R4. 渠道绑定状态以 badge 形式展示三态：`未绑定` / `已绑定（最近一次绑定时间）` / `已失效（请重绑）`。
- R5. 点击按钮后，UI 进入"等待登录"态：显示提示文案"本机已打开 Chromium 窗口，请在窗口里完成登录；完成后此页面会自动刷新"，并提供「取消」按钮。

**绑定流程**

- R6. 服务端收到绑定请求后，subprocess 启动一个 headed Chromium，导航至该渠道的登录入口 URL。
- R7. 驱动等待用户完成登录（轮询页面状态判断已登录，例如出现用户菜单 / cookie 中有特定鉴权键），最长等待 5 分钟，超时则结束子进程并报 timeout 给 UI。
- R8. 登录成功后，导出 Playwright `storage_state` 到 `~/.config/backlink-publisher/<channel>-state.json`，文件权限 0600。
- R9. 旧凭据文件存在时，新绑定**覆盖**旧文件（单账号、后绑覆盖）。
- R10. 子进程退出后服务端更新 `webui_store` 内该渠道的 `bound_at` 时间戳，UI 刷新后看到新状态。

**失效检测与提示**

- R11. publish-backlinks 跑该渠道时，adapter 把 HTTP 401 / cookie expired / storage_state 缺失三种错误归一映射为 `channel_status=expired`，写入 `webui_store`。
- R12. Settings 页加载时若任一渠道 `status=expired`，页顶展示 banner："X 个渠道凭据已失效，请重新绑定" + 跳转到对应 tab 的 anchor link。
- R13. 不做后台主动健康探测；只在发布失败的真实事件里识别。

**CLI 入口（被 webui 调用）**

- R14. 新增 `bind-channel` CLI 子命令（或复用已规划的 `velog-login` 扩展为通用），签名约定为 `bind-channel --channel <name> --output <path>`，stderr 输出进度 JSON 行（`{"event":"window_opened"}` / `{"event":"login_detected"}` / `{"event":"saved","path":"..."}`），exit 0 成功，非 0 失败。
- R15. CLI 同时可以独立调用，不耦合 webui（保留 backlink-publisher CLI-first 输出契约）。

## Success Criteria

- 一个全新用户在没有 Medium OAuth 应用、没有 Blogger Google Cloud project 的前提下，能在 5 分钟内完成 velog + medium + blogger 三个渠道的绑定并成功发一篇草稿。
- velog 的"暂未开放"placeholder 被实际可用的按钮替换，`_settings_channel_velog.html` 的 P0-7 联签 gate 满足后这个 UI 直接进入可用态。
- publish 时遇到失效凭据不再静默挂掉，settings 页有明确的失效 → 重绑闭环。
- 不引入新的运行时依赖（除 playwright + chromium，已是 velog 计划既定）。

## Scope Boundaries

- **不支持远程部署**：假设 webui 与用户在同一台机器（与现有 `python webui.py` 本机使用模式一致）；VPS / Docker / SSH-only 场景明确不在 v1 范围。
- **不支持多账号**：每个渠道单账号，后绑覆盖前绑。多账号轮换防风控是未来增量。
- **不做主动健康探测**：不写定时轮询 / 后台 ping，避免触发渠道风控并节省运维成本。
- **不替换 Medium/Blogger 的 OAuth 路径**：浏览器绑定只是并列备援，OAuth 仍是 Recommended，已绑定的用户不被迁移。
- **不复用用户现有 Chrome**：不实现 attach-existing-Chrome / `--remote-debugging-port` 模式，避免跨平台路径检测和文档负担。
- **不集成 Claude-in-Chrome / Opencode CLI / 其他 agent 工具链**：这些是 dev 侧工具，不绑进产品功能。
- **Telegraph 不在范围**：匿名发布无需绑定。
- **不录制 session video / 不保留登录过程截图**：隐私 + 存储成本不值。

## Key Decisions

- **驱动选 Playwright headed + storage_state**：复用 velog 计划既定方向；`storage_state` 比纯 cookies 完整（含 localStorage，SPA token 友好）；社交登录跳转在真 Chromium 里天然支持；CI 安装路径已通。
- **本机假设**：保持现有 webui 单机模型，不引入 noVNC / Browserless / WebSocket 流式架构。
- **OAuth 与浏览器登录并列、不互斥**：避免误导用户、降低运营门槛、保留 OAuth 的稳定性优势给能用的人。
- **被动失效检测 + 重绑闭环**，不做主动 health probe：成本/收益最优，避免触发渠道风控。
- **单账号、后绑覆盖**：v1 简化数据模型与 UI；多账号是独立未来增量。
- **CLI 与 webui 解耦**：`bind-channel` 是 standalone CLI，stderr 输出 JSON 进度行，webui 只是它的一个调用者；与 backlink-publisher CLI-first 契约一致。

## Dependencies / Assumptions

- 用户机器能跑 headed Chromium（图形界面可用；macOS/Linux/Windows 桌面均可，纯 headless 服务器明确不支持，与 scope boundary 一致）。
- Playwright + Chromium 已经被 velog 计划纳入依赖；本特性不引入新的 runtime dep，仅复用。
- `webui_store` 已有 module-level 持久化能力（`webui_store/base.py`），新增 channel-status 字段对其扩展即可，不需要新建存储层。
- 渠道侧登录页 DOM / 鉴权 cookie 名称是已知量（velog 已 spike 出，medium/blogger 由 plan 阶段补登录态判定 selector / cookie 名）。

## Outstanding Questions

### Resolve Before Planning

无 — 产品决策都已闭合。

### Deferred to Planning

- [Affects R7][Technical] 每个渠道判断"用户已登录完毕"的具体信号是什么（cookie 名 / DOM selector / URL pattern）。Velog 由 Phase 0 spike 提供；Medium/Blogger 需要 plan 阶段在登录跳转完成后用同步代码段验证。
- [Affects R6][Technical] subprocess 启动的 Playwright 子进程在哪个 Python 进程上下文跑（webui Flask 进程的子进程 vs 独立 detach），以及 SIGCHLD / Ctrl-C 的清理策略。
- [Affects R11][Technical] adapter 侧 401 / cookie-expired 归一化的具体实现位置：是统一在 `publishing/adapters/base.py` 加 envelope，还是各 adapter 自己 raise 标准异常。
- [Affects R5][UX] "等待登录"态的进度反馈通道选 SSE / WebSocket / long-poll / 定时轮询；现有 webui 已有 SSE 基础设施吗。
- [Affects R14][Needs research] 把 `bind-channel` 做成新 CLI 还是把已规划的 `velog-login` 扩展为通用入口，取决于 R9 plan recovered 里 velog-login 的接口冻结程度。
- [Affects R8][Technical] storage_state JSON 落盘时是否需要在文件里附加 metadata（绑定时间、Playwright 版本、渠道名）方便迁移与诊断。
- [Affects 全局][Needs research] 浏览器绑定是否会触发渠道反爬虫指纹检测（user-agent / canvas fingerprint / WebGL），velog Phase 0 spike 的结论是否能外推到 medium/blogger。

### Architecture Alternative 备查

- 留 `BrowserBindDriver` 抽象（仅一个 `PlaywrightHeadedDriver` 实现），方便未来切换到 attach-existing-Chrome 反检测策略。**v1 不实现**，但 plan 阶段如果发现 driver 接口面只有 2-3 个方法，做 / 不做都成立 — 把决策标在 plan 里。

## Next Steps

→ `/ce:plan` for structured implementation planning
