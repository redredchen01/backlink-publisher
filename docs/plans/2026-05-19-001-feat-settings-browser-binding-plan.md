---
title: "feat: Settings 页浏览器驱动备援绑定机制"
type: feat
status: active
date: 2026-05-19
deepened: 2026-05-19
origin: docs/brainstorms/2026-05-19-settings-browser-binding-requirements.md
---

# feat: Settings 页浏览器驱动备援绑定机制

## Overview

为 backlink-publisher webui 的设置页加入"浏览器登录"按钮，覆盖 velog（唯一路径）/ medium / blogger（与 OAuth 并列）三个渠道。点击后服务端 subprocess 启 Playwright headed Chromium，用户在本机真实浏览器完成登录（含社交 OAuth、2FA、captcha），驱动检测到登录态后导出 storage_state 到 `~/.config/backlink-publisher/<channel>-state.json`（0600）。publish-backlinks 在发布失败时识别 401/cookie-expired 并把渠道状态翻成 `expired`，settings 页 banner 提示重绑。

## Problem Frame

当前绑定路径割裂：

- **Medium / Blogger**：标准 OAuth（`webui_app/routes/oauth.py`），用户先在 Medium / Google Cloud Console 注册 OAuth 应用拿 client_id/secret。门槛高 + Medium OAuth 长期 deprecated/受限审批
- **Velog**：无官方 API，社交登录后必须导出 storage_state。`_settings_channel_velog.html` 已预告该路径但 disabled
- **凭据失效**：publish 时 401 / cookie-expired 当下只能从日志看到，settings 页无任何提示

本次统一一套"浏览器登录"机制覆盖三个渠道，并把失效检测闭环到 settings UI（见 origin: `docs/brainstorms/2026-05-19-settings-browser-binding-requirements.md`）。

## Requirements Trace

- R1-R5（settings UI）— Unit 5
- R6-R10（绑定流程：subprocess + Playwright + storage_state + 状态写入）— Unit 2, 3, 4
- R11-R13（失效检测与 banner）— Unit 1, 5, 6
- R14-R15（CLI 入口契约 + 解耦）— Unit 2, 3

## Scope Boundaries

- 不支持远程 / VPS / Docker / 无头部署（本机假设，与 `python webui.py` 现状一致）
- **不支持多 worker WSGI 部署**（gunicorn `-w >1`、uwsgi 多 worker、mod_wsgi 多 process）：in-memory job dict 不跨进程一致，binding 流程会破。**仅文档化警告**，不做运行时启发式检测（GUNICORN_CMD_ARGS 大多数 ops 不 set，false-negative 高；scope-guardian + feasibility 一致意见删除运行时检测）
- 不实现多账号；单渠道单账号、后绑覆盖
- 不写后台 health probe / 定时 ping
- 不替换 Medium/Blogger 的 OAuth 路径，浏览器登录是并列备援
- 不集成 Claude-in-Chrome / Opencode CLI / 外部 agent 工具
- Telegraph 匿名发布，不在范围
- 不录制 session video、不截图登录过程

### Accepted v1 Security Risks (Documented, Not Mitigated)

- **同 UID 进程可读 storage_state**：0600 只挡跨 UID。同 UID 恶意进程（用户运行的其他程序）可直接读凭据文件。OS keychain 集成（macOS Keychain / Linux Secret Service）推迟到 v2
- **同 UID 进程可驱动 webui binding**：CSRF + Host + Origin 挡跨源，**不**挡同 UID 进程（可 GET 拉 CSRF + POST 启 binding 流程）。webui 信任 OS 用户为唯一操作者；同 UID 隐含可信。与"同 UID 可读凭据"取一致。不加 startup bearer token 是有意决策
- **localhost HTTP cookie 明文**：webui 跑在 `http://127.0.0.1:8888`，session cookie `Secure=False`；同 UID packet-capture 可读 session cookie。属同 UID 风险族，与其他几条同档接受
- **备份工具复制 storage_state**：Time Machine / iCloud / rsync 默认会拷 `~/.config/`。Ops 文档强警告，但不主动加 `tmutil addexclusion`（避免改用户系统状态）
- **本机 webui 端口被同机其他进程探测**：CSRF + Host header + Origin/Referer + loopback bind 四层防 DNS-rebind / 跨源攻击。绑定 binding 流程为防御重点
- **Playwright + Chromium 供应链信任**：Playwright pin 在 pyproject.toml（具体版本 Unit 2 实施时定）；Chromium 二进制由 `playwright install chromium` 从 Microsoft CDN 拉，依赖 Playwright 内置 SHA 校验；CVE 出来时需手动 pause binding 流程升级

## Context & Research

### Relevant Code and Patterns

| 关注点 | 文件 / 函数 |
|---|---|
| CLI 模块结构 | `src/backlink_publisher/cli/<name>.py`，`main()`，argparse；`cli/phase0_seal.py:_build_parser` 是 subcommand 模板 |
| CLI 错误家族 | `_util/errors.py:PipelineError` + `UsageError(1)/InputValidationError(2)/DependencyError(3)/ExternalServiceError(4)/InternalError(5)`，`emit_error` / `handle_error` |
| CLI RECON 日志 | `_util/logger.py:plan_logger / validate_logger / opencli_logger`；`logger.recon(...)` 已是 always-on stderr 结构化 JSON；`_SENSITIVE_KEYS` 需扩展 |
| Webui blueprint 工厂 | `webui_app/__init__.py:create_app`；`webui_app/routes/__init__.py:register_blueprints` |
| Webui subprocess 复用 | `webui_app/helpers.py:run_pipe + _rewrite_cli_cmd`（注入 PYTHONPATH 与 `sys.executable -m`） |
| OAuth 模式参考 | `webui_app/routes/oauth.py:settings_medium_oauth_start/callback`（state in Flask session、redirect 到 `/settings?...#channel-<name>`） |
| 状态 store | `webui_store/base.py:JsonStore`（atomic rename + 锁 + `~/.config/.../<name>.json`），`webui_store/__init__.py` 暴露 singleton |
| Settings 模板 | `webui_app/templates/settings.html` + `_settings_channel_<name>.html`；context 由 `webui_app/helpers.py:_settings_context` 聚合 |
| Adapter 当前错误模式 | `medium_api.py` / `blogger_api.py` / `medium_browser.py` 都 raise `ExternalServiceError("…")` 字符串 |
| Adapter 错误收口 | `cli/publish_backlinks.py` 内 `except ExternalServiceError`（~L305 段）→ `AdapterResult(status="failed")` |
| Playwright 用法先例 | `publishing/adapters/medium_browser.py` 内 import-guard 与 `launch_persistent_context`；velog 不沿用 persistent context |
| 配置目录解析 | `backlink_publisher.config.loader._config_dir()`（honors `BACKLINK_PUBLISHER_CONFIG_DIR`）— **必须**复用，否则违反 tests-coupled-to-operator-config-state 学习 |
| Token 写盘惯例 | `config/tokens.py:save_blogger_token / save_medium_token`（0600 + atomic 临时文件 + chmod） |
| Subprocess CLI 测试 | `tests/test_cli_footprint.py:_run_regen_subprocess`（subprocess.run + 检 returncode/stderr） |
| Playwright mock | `tests/test_adapter_medium_browser.py:@patch("...sync_playwright")` 模块属性级 |

### Institutional Learnings

- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — Flask 路由不能 `subprocess.run().wait()`，>5s 冻 UI。结论：**Popen + 立即返回 job_id + polling status endpoint**，前端 JS 提交时 disable 按钮 + overlay
- `docs/solutions/best-practices/recon-log-level-for-always-on-signals-2026-05-15.md` — RECON-level structured JSON-on-stderr 是 always-on 信号，stdout 留给数据。本 CLI 进度上报复用 `logger.recon(...)`，**不发明新 progress helper**
- `docs/solutions/best-practices/standalone-page-vs-retrofit-webui-2026-05-15.md` — 大模板加新功能要审慎。妥协：绑定**路由**独立成 `routes/channel_binding.py`，**UI**仍嵌入 settings tab 内（按钮 + status badge）
- `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md` — Medium 适配器链是 `API → Brave (macOS) → Browser`，binding 失效信号要同时考虑这三条路径
- `docs/solutions/best-practices/tests-coupled-to-operator-config-state-2026-05-18.md`（最关键）— 新路径必须经 `_config_dir()` honor env override，否则会引入 operator-config-state-bleed 测试污染
- velog spike `docs/spikes/2026-05-18-velog-phase0.md` — velog 社交登录 Google/GitHub/Facebook；cookie TTL 实测 ≥24h；rebind 是常规操作非边缘 case
- Memory `project_backlink_publisher_overview.md` — velog 仍 paused 等 telegra.ph 6/8 判决；若 fail 可能砍掉。设计 channel registry 让 velog 干净退场

### External References

未引外部研究 — 仓库内 Playwright 用法与 velog plan 已是充分先例；OAuth 既有路径不动；本机 subprocess 模式是常规 Flask 套路。

## Key Technical Decisions

- **CLI 收敛为 `bind-channel`，`velog-login` 作 alias**：用户已决定（见上方对话）。`bind-channel --channel <name> --output <path>` 是规范入口；`velog-login` 在 pyproject.toml 保留，实现上调用 `bind_channel.main` 默认 `--channel velog --output ~/.config/backlink-publisher/velog-cookies.json` 以兼容 PR #66 已 lock 的路径
- **Playwright headed + `storage_state` 导出**：复用 velog plan 既定方向；不沿用 `launch_persistent_context`（velog plan 已禁，便于显式 host filter）；`storage_state` 比裸 cookies 完整（带 localStorage）
- **Playwright context 加固契约**：`browser.new_context(accept_downloads=False, ignore_https_errors=False, bypass_csp=False)`。理由：headed Chromium 在 5min 等待窗口暴露给"用户可能误点"的攻击面，禁下载/禁忽略证书/禁 CSP 绕过收紧域。**不**禁 JavaScript（社交登录 SPA 必需）
- **进度上报：RECON JSON-on-stderr + polling 而非 SSE**：复用既有 `logger.recon(...)` 模式。Webui 端 `subprocess.Popen(stderr=PIPE, bufsize=1, text=True, start_new_session=True)` + reader thread 拉行 → 内存 job dict；前端 2s 间隔轮询 `/settings/<channel>/browser-bind/status/<job_id>`。**不引入 SSE** 因 webui 无先例，新失败模式不值
- **`start_new_session=True` 进程组语义**：让 cancel/timeout 通过 `os.killpg(pgid, SIGTERM)` 杀整棵子树（Chromium subprocess + python wrapper），而非只杀 python wrapper留 Chromium 僵尸；与 Windows 行为差异在 ops 文档备注
- **共享 CLI ↔ Webui 事件常量集（精简）**：在 `cli/_bind/channels/__init__.py` 内（与 `CHANNELS` 同位置）定义 `EVENTS: frozenset[str] = frozenset({"launching","awaiting_login","login_detected","saved","timeout","internal_error"})`。CLI driver 与 webui reader 都 import 此 frozenset 比对。**不**做 schema version + TypedDict + 三向 contract test —— 单 SHA 生产/消费方，YAGNI（scope-guardian + adversarial 一致意见）
- **Reader thread 单一 `wait()` 拥有者**：reader thread 是唯一调 `proc.wait()` 的代码路径；cancel/timeout 路径只设 `cancel_requested` flag + 发 signal；reader thread 观察 returncode 后合成终态（cancel_requested 仅在 returncode != 0 时生效）。避免 cancel 命中已自然退出的子进程时 `ProcessLookupError`
- **状态存储：新 singleton `channel_status_store: JsonStore`**：放 `~/.config/backlink-publisher/channel-status.json`，schema `{<channel>: {status: "unbound"|"bound"|"expired", bound_at: iso, storage_state_path: str}}`。结构性满足 `Store` 协议，无需子类。**首次访问触发 `reconcile_on_load()`**：每个 status=bound 的渠道 stat 其 `storage_state_path`，文件缺则降级为 expired（保 `bound_at` 用作 UX 提示）
- **`CHANNELS = frozenset({"velog","medium","blogger"})` 单一权威源**：定义在 `cli/_bind/channels/__init__.py`。所有 entry point（webui POST 路由、cancel 路由、status 路由、`mark_bound`、`mark_expired`、`bind_job.start`、CLI argparse）都必须先做 `channel in CHANNELS` 校验后才动 path / argv。防 `../traversal` 与未授权渠道
- **失效检测错误类：`AuthExpiredError(DependencyError)`**：继承 `DependencyError`（exit_code=3）。**主要理由是 coordination 不是 semantics**：plan-012 §Unit 4 已 lock `DependencyError("velog cookie expired")`，AuthExpiredError 若选 ExternalServiceError(exit_code=4) 会让 velog 与 medium/blogger 同语义不同 exit code，下游脚本/CI grep 无所适从。语义上"credentials rejected by service"本应更近 ExternalServiceError，但代价是与 plan-012 互不兼容；本 plan 选 coordination 优先。`channel` 属性附加在子类。规则记入 `_util/errors.py` docstring：DependencyError 家族 = "user must take action"；ExternalServiceError 家族 = "retry may succeed"
- **凭据落盘：0600 + atomic + `_config_dir()`**：复用 `config/tokens.py` 已验证的写盘惯例 + `finally:` 块兜底 unlink 临时文件。**不写 metadata 进 storage_state.json 自身**（保持 Playwright 原生 schema），metadata 落 channel_status_store
- **`recipe.resolve_output_path(config_dir) -> Path` 方法**：把 channel 特殊路径决策封进 recipe 自身（velog → `velog-cookies.json` 兼容 PR #66；medium/blogger → `<channel>-state.json`）。bind_job 和 velog-login alias 都调同一函数，不再在 webui 侧硬编码 channel-name 条件分支
- **每渠道 host filter primitive（严格 dotted-suffix）**：`host == base or host.endswith("." + base)`，**禁止**裸 `endswith` 或 `in` 子串匹配（会被 `evil-accounts.google.com` 吃掉）。velog 用 `velog.io` 严格相等（沿用 plan-012 §R16）；medium 用 `medium.com`；blogger 用 `blogger.com` + `blogspot.com` + `google.com`（Google SSO 必需）+ `accounts.google.com` 显式。Blogger 因覆盖 Google SID/HSID/SSID **blast radius 最大**，发布顺序排最后
- **绑定窗口期 host 离开监听**：driver `page.on("framenavigated")` 中如果 URL 落到 `host_allowed` 集合外的域且非预期 OAuth 跳转域，立即中止 + RECON `{event: internal_error, reason: "navigated_off_allowlist"}`。防止用户被诱导到攻击者站点
- **CSRF + Host allowlist + Origin/Referer + loopback bind 四层防御（Unit 4 hard precondition）**：
  - (a) **Flask-WTF `CSRFProtect(app)` 启用后立即对所有既有 blueprint 调 `@csrf.exempt`**，**仅 channel_binding blueprint 受 CSRF 保护**。其他 36 条 POST 路由 0 行为变化。系统级 CSRF 迁移是另一 plan 的 scope
  - (b) `Host:` header 校验 `127.0.0.1:8888` / `localhost:8888`（normalize 后缀点、大小写）
  - (c) `Origin`/`Referer` 如存在 host 须在白名单；缺省（无 Origin 无 Referer）**拒**绝 mutating POST
  - (d) **`create_app` 启动断言 Flask bind 是 `127.0.0.1`**：检测 `webui.py` argv / Flask `app.run(host=...)` / 环境变量；若非 loopback → loud abort + 错误信息让用户改回 127.0.0.1
  - (e) session cookie `SameSite=Strict`、`HttpOnly=True`；`Secure=False` 是 localhost HTTP 的接受决策（同 UID packet-capture 是已接受的 v1 风险，文档化）
  - 任一失败一律 403。**这是 Unit 4 必交付内容，不是 nice-to-have**
- **Job ID 不可枚举 + per-session ownership**：`job_id = secrets.token_urlsafe(32)`；每个 `BindJob` 持 `owner_session_id`（Flask `session.get("id")`，session id 用 `secrets.token_urlsafe(16)` 在首次访问 webui 时落地）。`GET /status/<job_id>` + `POST /cancel/<job_id>` 验证 `request.session.id == job.owner_session_id` 否则**返 404**（不是 403，避免泄漏 job 存在）。绑定成功后 rotate session id 防 pre-bind cookie 泄漏后利用
- **Popen env 显式 allowlist**：`Popen(..., env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": "src", "HOME": os.environ["HOME"], "DISPLAY": os.environ.get("DISPLAY",""), "BACKLINK_PUBLISHER_CONFIG_DIR": os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR","")})` — 父进程其他 env（AWS_*、GITHUB_TOKEN、OPENAI_API_KEY 等）**绝不**继承进 Chromium 子进程。Unit 4 显式测：在 parent 设 sentinel env var 后启子进程 → 子进程 env 不含 sentinel
- **Chromium 临时 user-data-dir**：driver 用 `tempfile.mkdtemp(prefix="bp-bind-")` 作 ephemeral profile dir 传 `chromium.launch(args=["--user-data-dir=..."])`；finally 块 `shutil.rmtree`。避免误用真实 Chrome profile 灌入其他网站 cookies
- **RECON redactor 强化契约**：`_SENSITIVE_KEYS` 扩展只挡 JSON key，**还要**加 free-text 模式匹配（`Set-Cookie:` 前缀、长 base64-like blob 紧邻 cookie 关键字、Authorization 头）。Webui 端：**非 JSON 行直接丢弃**，绝不进 `progress_events` 也不打到 Flask logger；error_msg 只承载枚举化错误码，不承载 raw stderr 字符串
- **Channel registry 模式**：所有 channel-specific 信号（`host_allowed`、`login_url`、`is_logged_in`、`resolve_output_path`）集中到 `cli/_bind/channels/<name>.py`，CLI 入口只做 dispatch。velog 若 6/8 被砍，需要协同清除 6 处触点（见 §Risks 表），non-trivial 但有 checklist
- **本机假设保持**：不引入 noVNC / Browserless / WebSocket；与 webui 单机模型一致

## Open Questions

### Resolved During Planning

- **CLI 命名冲突**：`bind-channel` 统一入口，`velog-login` 作 alias 兼容 PR #66（用户决定）
- **失效检测错误类**：`AuthExpiredError(DependencyError)`，exit_code=3。**主要理由是 coordination 不是 semantics**（与 plan-012 收敛同 exit code，否则下游 grep 无所适从）
- **进度传输**：polling（2s 间隔）；不上 SSE（webui 无先例，新失败模式不值）
- **storage_state metadata 落点**：channel_status_store，不污染 storage_state.json
- **配置路径**：经 `_config_dir()` 强制 honor `BACKLINK_PUBLISHER_CONFIG_DIR`（institutional learning 警告）
- **CSRF 范围**：Flask-WTF CSRFProtect 启用 + 立即 `@csrf.exempt` 所有既有 blueprint；**仅 channel_binding blueprint 受 CSRF 保护**。系统级 CSRF 迁移是另一 plan 的 scope（review-doc P0）
- **DNS-rebind 防御层数**：CSRF + Host allowlist + Origin/Referer + **Flask bind loopback only 启动断言**（review-doc P0）
- **`<channel>` URL 段注入防御**：`CHANNELS = frozenset(...)` 单一权威源 + 每 entry point 校验 + `mark_bound` 二次校 path（review-doc P0）
- **Job ID 与 ownership**：`secrets.token_urlsafe(32)` 不可猜 + per-session owner check；跨 session 返 404 防存在性泄漏（review-doc P0）
- **Popen env**：显式 allowlist（PATH/PYTHONPATH/HOME/DISPLAY/BACKLINK_PUBLISHER_CONFIG_DIR），父 env 不继承（review-doc P0）
- **Chromium 隔离 profile**：`tempfile.mkdtemp("bp-bind-")` ephemeral user-data-dir + finally rmtree（review-doc P1）
- **无用户级 auth**：接受为 v1 风险，文档化"同 UID 进程隐含可信"（review-doc P0，明确决策不缓解）
- **进程组与 cancel 拥有权**：`start_new_session=True` + reader thread 唯一 wait() 调用者（review-reliability P-2）
- **多 worker WSGI 支持**：明确不支持；**ops 文档警告，不做运行时检测**（scope-guardian + feasibility 一致）
- **Windows 支持**：v1 明确不支持（POSIX 进程组语义 / `taskkill /T` 需要单独工作量）
- **store 与文件一致性**：`reconcile_on_load()` 由 `create_app` 启动末尾显式调一次（避免 lazy init 线程竞态）
- **Unit 6 范围**：瘦身为仅 OAuth 401 路径（medium_api / blogger_api）；**不**重写 medium_browser，**不**创建 `_auth_state.load_storage_state` 中心化 helper（review-feasibility P0：现有 adapter 不读 storage_state 文件，中心化前提不成立）
- **共享 event 常量**：单个 `EVENTS = frozenset(...)` 常量在 `cli/_bind/channels/__init__.py`；不另起 `_bind/protocol.py` 模块（scope-guardian + adversarial：YAGNI）
- **ceremony 文档**：不写 SECURITY-NOTE.md 同目录文件 + 不预写 velog-retirement-checklist.md（scope-guardian P1）
- **`AuthExpiredError` 与 velog adapter (plan-012 §Unit 4) 收敛策略**：plan-012 当前 raise `DependencyError("velog cookie expired")`，本 plan 不动 velog adapter；plan-012 owner 后续可独立升级到 `AuthExpiredError(channel="velog")` 接入自动 store 翻 expired（纯加法）
- **Modal vs Inline 进度 UI**：inline progress region 替换按钮+badge 区域（不用 modal — 避免 dialog focus trap + 更好 a11y）（review-design P0）
- **Reopen-tab mid-bind**：sessionStorage 存活跃 job_id；settings 页加载 JS 检 sessionStorage 中已有 job_id → 恢复 polling；返 404 → 显示"上次绑定已结束，当前状态：<store_value>"

### Deferred to Implementation

- **每 channel 的 `is_logged_in` 具体 selector / URL 模式** — velog 由 plan-012 §R-detection 给出；medium/blogger 需在 Unit 2 spike 出（建议：medium 检测 URL 离开 `/m/signin` + cookie `sid` / `uid` 出现；blogger 检测 cookie `SID` + 跳转到 `blogger.com/u/` 路径）。Spike 用本机 Chromium 手动观察足矣，不需要新基础设施
- **Medium Brave adapter 失效信号**：`medium_brave.py` 是 macOS-only AppleScript 路径，是否也升级为 `AuthExpiredError` — 留到 Unit 6 实施时按现场判定
- **前端 JS 进度反馈具体形态**：是 button 旁 inline spinner 还是模态对话框 — Unit 5 实施时 1h 内可决定
- **CLI 退出时清理子进程的 SIGCHLD 处理细节**：`Popen(start_new_session=True)` + `os.killpg` 已是核心契约；Windows 因无进程组语义需用 `CREATE_NEW_PROCESS_GROUP` 区分 — 实现时按平台分支
- **velog cookies.json 的 schema 是否完全等价 storage_state schema**：velog plan §R16 lock 了 `{"cookies":[...], "origins":[...]}` 形态；bind-channel alias 调用要确保 storage_state 输出形态匹配。若发现细差，实现时在 alias 层做 shape 适配
- **失效 banner 的"X 个渠道凭据已失效"copy 国际化**：当前 webui 全简中，无 i18n 基础设施 — 直接硬编码中文，与现有 oauth.py 的简中错误一致
- **首次启动 `playwright install chromium` 检测**：Unit 5 设置页加载时探测 `from playwright.sync_api import sync_playwright; sync_playwright().start().chromium.executable_path` 或 `which playwright`；若未装，按钮 disabled + 提示文案 + 链接 ops 文档。实施时决定探测频率（每次加载 vs 启动一次缓存）
- **Job TTL 行为**：lazy-on-read 简化；TTL 后 polling endpoint 返回 `404` 还是 `200 {status: "expired_history"}` — 实现时看前端方便
- **2FA 超时延展（heartbeat extension）**：用户在 2FA 邮件验证可能 >5min。v1 接受 hard 300s 上限；future 增量是 RECON `awaiting_login` 检测到用户交互信号后 reset timer。文档化为 known limitation
- **跨进程 JsonStore 写入冲突**：当前只 Flask 写 channel_status_store；publish-backlinks CLI 直接跑（非 webui 触发）时也会写 → 两进程并发。JsonStore atomic rename 防 partial write 但不防 last-writer-wins。v1 接受；future 看是否要 fcntl flock

## High-Level Technical Design

> *以下示意展示组件如何衔接，是审阅方向性指引，不是实现规范。实现者把它当上下文，不要照抄。*

```
                ┌──── Browser (user) ────┐
                │  /settings 页面          │
                │  [浏览器登录 <chan>]按钮│
                └─────────┬───────────────┘
                          │ POST /settings/<chan>/browser-bind
                          ▼
            ┌─────────────────────────────────────┐
            │ webui_app/routes/channel_binding.py │
            │  POST  /browser-bind                │
            │  GET   /browser-bind/status/<jid>   │
            │  POST  /browser-bind/cancel/<jid>   │
            └─────────┬───────────────────────────┘
                      │ delegate
                      ▼
       ┌──────────────────────────────────────────┐
       │ webui_app/services/bind_job.py           │
       │  • Popen(bind-channel ...) stderr=PIPE   │
       │  • reader Thread → parses RECON JSON     │
       │  • in-memory job dict: status/progress   │
       │  • 5min timeout → SIGTERM → SIGKILL      │
       └─────────┬────────────────────────────────┘
                 │ spawns
                 ▼
       ┌────────────────────────────────────────────┐
       │ CLI: bind-channel --channel <c> --output  │
       │   src/backlink_publisher/cli/bind_channel.py│
       │   ├─ load channel recipe (registry)        │
       │   │     cli/_bind/channels/{velog,medium,  │
       │   │                         blogger}.py    │
       │   ├─ playwright headed chromium            │
       │   ├─ goto recipe.login_url                 │
       │   ├─ wait_for(recipe.login_detected_*)     │
       │   ├─ context.storage_state()               │
       │   ├─ filter via recipe._host_allowed       │
       │   ├─ atomic write 0600 to --output         │
       │   └─ logger.recon({"event": ..., ...})     │
       └─────────┬──────────────────────────────────┘
                 │ on exit 0
                 ▼
       channel_status_store["<chan>"] = {
         status: "bound", bound_at: ..., storage_state_path: ...
       }

   ──── 失效闭环 ────
   publish-backlinks runtime:
     adapter 401/cookie_expired
         raise AuthExpiredError(channel="<c>")
              ↓ caught at cli/publish_backlinks.py
     channel_status_store["<c>"].status = "expired"
              ↓ next page load
     settings.html 顶端 banner 渲染
```

## Implementation Units

- [ ] **Unit 1: 错误类 + 状态 store 基础 + channel 白名单权威源**

**Goal:** 引入 `AuthExpiredError(DependencyError)` 子类、`channel_status_store` 单例、`CHANNELS` frozenset 权威源、store 的 `reconcile_on_load()` 自检，为后续 unit 提供契约、持久化、校验层。

**Requirements:** R10, R11, R12

**Dependencies:** 无

**Files:**
- Modify: `src/backlink_publisher/_util/errors.py`（加 `AuthExpiredError`）
- Create: `src/backlink_publisher/cli/_bind/__init__.py`（占位 + import 转发 CHANNELS）
- Create: `src/backlink_publisher/cli/_bind/channels/__init__.py`（`CHANNELS = frozenset({"velog","medium","blogger"})` 权威源）
- Modify: `webui_store/__init__.py`（暴露新 singleton）
- Create: `webui_store/channel_status.py`（薄包装层 + 校验 + reconcile）
- Test: `tests/test_auth_expired_error.py`
- Test: `tests/webui_store/test_channel_status.py`
- Test: `tests/webui_store/test_channel_status_reconcile.py`

**Approach:**
- **`AuthExpiredError(DependencyError)`**：构造参数 `channel: str`，`__init__` 校验 `channel in CHANNELS` 否则 raise `UsageError`；message 含 channel；继承 DependencyError `exit_code=3`（与 velog plan-012 收敛）
- **`CHANNELS = frozenset({"velog","medium","blogger"})`** 在 `cli/_bind/channels/__init__.py`，是**唯一权威源**。所有 entry point（route、mark_bound、mark_expired、AuthExpiredError 构造）都 import 此 frozenset 做白名单校验。新增 channel 只在此处加，其他位置全 import
- **`channel_status_store: Store = JsonStore(_config_dir() / "channel-status.json", default_factory=dict)`**
- `webui_store/channel_status.py` 暴露 `mark_bound(channel, storage_state_path) / mark_expired(channel) / get_status(channel) / list_all() / reconcile_on_load()`；每个写函数先 `if channel not in CHANNELS: raise UsageError(f"unknown channel: {channel}")`；**`mark_bound` 再校 `Path(storage_state_path).resolve().is_relative_to(_config_dir())`** 否则 `UsageError`（防 supply-chain adapter 写恶意路径）；内部走 `store.update(fn)` 拿锁
- **`reconcile_on_load()`** **由 `webui_app/__init__.py:create_app` 在启动末尾显式调一次**（与 Unit 4 的 `bind_job.reap_orphans()` 同位置；单线程路径避免 lazy init + 多请求竞态）。遍历 status=bound 的 record，对其 `storage_state_path` 做 `os.path.exists`；文件缺 → 改 status=expired（保留 `bound_at` 用作"上次曾绑过 YYYY-MM-DD"UX 提示）；同时 RECON 一行 warn。设计目的：webui 启动后看到的渠道状态总是与磁盘一致。CLI 不触发 reconcile（v1 medium_api/blogger_api 走 OAuth 路径不读 storage_state 文件；velog 由 plan-012 自有路径管理）
- schema：`{<channel>: {"status": Literal["bound","expired","unbound"], "bound_at": iso str | None, "storage_state_path": str | None}}`；缺渠道返回 `{"status": "unbound", "bound_at": None, "storage_state_path": None}`
- 路径 helper 必须经 `config.loader._config_dir()`，禁止裸 `Path.home() / ".config"`

**Patterns to follow:**
- `webui_store/drafts.py`、`webui_store/queue_store.py` 的 singleton + 薄 API 风格
- `_util/errors.py:DependencyError` 的子类样板
- `config.loader._config_dir` 的路径解析强契约

**Test scenarios:**
- Happy path: `mark_bound("velog", "/tmp/x.json")` → `get_status("velog")` 拿到 status=bound + 非空 bound_at + path
- Happy path: `mark_expired("medium")` → status=expired，bound_at / storage_state_path 不被擦
- Happy path: `reconcile_on_load()` 在 store 含 `{velog: bound, /tmp/missing.json}` 时把它降为 expired，bound_at 保留
- Happy path: `reconcile_on_load()` 在文件存在时不动 record
- Edge case: 未知 channel `get_status("unknown")` → 返回 unbound 默认而非 KeyError
- Edge case: `mark_bound("../evil", ...)` → raise `UsageError`，store 文件不被写
- Edge case: `mark_bound("velog", "/etc/passwd")` → raise `UsageError`（path 不在 _config_dir 内），store 文件不被写
- Edge case: `mark_expired("../evil")` → raise `UsageError`
- Edge case: `AuthExpiredError(channel="../evil")` → raise `UsageError`，错误对象不被构造（防 supply-chain 通过恶意 adapter 抛错绕过白名单）
- Edge case: `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/iso pytest` → channel-status.json 落 `/tmp/iso/`，不污染 `~/.config/`
- Edge case: 并发两次 `mark_bound` → JsonStore 锁确保最终态一致（用线程或 `update(fn)` 单元测试模拟）
- Error path: `AuthExpiredError(channel="velog")` 的 `str(e)` 含 channel + `exit_code == 3` + `isinstance(exc, DependencyError) == True`
- Integration: `AuthExpiredError` 被 `except DependencyError` 捕到（验证继承关系）
- Integration: store 首次 import 触发 reconcile，二次 import 不再扫（lazy idempotent）

**Verification:**
- `pytest tests/test_auth_expired_error.py tests/webui_store/test_channel_status.py tests/webui_store/test_channel_status_reconcile.py` 全过
- `grep -r "Path.home() / .config" src/backlink_publisher/` 在新增代码内 0 命中
- `grep -rn '"velog"\|"medium"\|"blogger"' src/backlink_publisher/cli/_bind/ src/backlink_publisher/_util/errors.py webui_store/channel_status.py` 显示 channel 名 literal 集中在 `CHANNELS` 定义点，其他位置全 import

---

- [ ] **Unit 2: `bind-channel` CLI + Playwright headed driver + channel recipes + 共享协议**

**Goal:** 实做通用绑定 CLI，含加固版 Playwright headed 驱动、共享 RECON 协议模块、三个 channel recipe（严格 host filter + 文件路径解析方法）、storage_state 落盘 0600。

**Requirements:** R6, R7, R8, R9, R14, R15

**Dependencies:** Unit 1（`CHANNELS` 权威源已定义；CLI 不直接 import webui_store，保持解耦）

**Files:**
- Create: `src/backlink_publisher/cli/bind_channel.py`（`main()`、argparse、orchestration、subprocess 退出码）
- Create: `src/backlink_publisher/cli/_bind/driver.py`（`PlaywrightHeadedDriver`：launch + 加固 new_context + ephemeral user-data-dir + navigation listener + wait predicate + storage_state + finally cleanup）
- Modify: `src/backlink_publisher/cli/_bind/channels/__init__.py`（Unit 1 已建 CHANNELS；本 unit 加 `RECIPES: dict[str, ChannelRecipe]` registry + `EVENTS: frozenset[str] = frozenset({"launching","awaiting_login","login_detected","saved","timeout","internal_error"})` — CLI driver 与 Unit 4 webui reader 都 import 此 frozenset，不另起 `_bind/protocol.py` 模块）
- Create: `src/backlink_publisher/cli/_bind/channels/velog.py`
- Create: `src/backlink_publisher/cli/_bind/channels/medium.py`
- Create: `src/backlink_publisher/cli/_bind/channels/blogger.py`
- Modify: `src/backlink_publisher/_util/logger.py`（`_SENSITIVE_KEYS` 加 `storage_state`, `cookies`, `origins`, `Authorization`, `Set-Cookie`；加 free-text pattern 匹配长 base64-like + cookie 关键字邻近）
- Modify: `pyproject.toml`（`[project.scripts]` 加 `bind-channel = "backlink_publisher.cli.bind_channel:main"`）
- Test: `tests/test_cli_bind_channel.py`（subprocess 调用 + Playwright mock）
- Test: `tests/test_bind_recipes.py`（pure-function 测每 recipe 的 host_allowed + resolve_output_path）
- Test: `tests/test_bind_driver.py`（mock Playwright 测 driver 编排 + 加固 flags + navigation abort）
- Test: `tests/test_bind_events.py`（`EVENTS` frozenset 与 CLI emit 集合一致；webui reader 用同一 frozenset 解析 — 跨 Unit 2 + 4 contract test 由 Unit 4 测试拥有）
- Test: `tests/test_recon_redactor_freetext.py`（free-text cookie 脱敏 — 双正则 pattern A + B）

**Approach:**
- CLI 接口：`bind-channel --channel <name> --output <path> [--timeout-seconds 300]`；`channel` 入参立即校验 `in CHANNELS` 否则 `UsageError` exit 1；exit 0 成功，非 0 走 `_util/errors.py` 既有 exit code 体系
- **Channel recipe（dataclass + 方法）**：
  - `name: str`
  - `login_url: str`
  - `host_allowed(host: str) -> bool` — 实现为**严格 dotted-suffix**：`h = host.lower().lstrip("."); return h == base or h.endswith("." + base)` 中 base 是渠道允许列表。**禁止**用裸 `endswith` 或 `in` 子串
  - `is_logged_in(page) -> bool` — URL 模式 + cookie 名 + DOM probe 任两通过判已登录
  - `resolve_output_path(config_dir: Path) -> Path` — **velog 返回 `config_dir / "velog-cookies.json"`** 兼容 PR #66；medium/blogger 返回 `config_dir / f"{name}-state.json"`。webui 和 CLI alias 都调此方法，不再硬编码路径分支
- **Driver 流程（加固版）**：
  1. import-guard `sync_playwright`（沿用 medium_browser.py 形态）；missing → `DependencyError("Run: playwright install chromium")`
  2. `user_data_dir = tempfile.mkdtemp(prefix="bp-bind-")`（**ephemeral profile**，与用户真实 Chrome profile 完全隔离）
  3. `logger.recon({event: "launching", channel})`
  4. `pw.chromium.launch(headless=False, args=[f"--user-data-dir={user_data_dir}"])` + `browser.new_context(accept_downloads=False, ignore_https_errors=False, bypass_csp=False)`（**3 个加固 flag 必传**）
  5. `page.on("framenavigated")` 注册：navigation 后 host 不在 `recipe.host_allowed` 的允许集合（含 OAuth 跳转域如 `accounts.google.com` for blogger，`github.com` / `google.com` / `facebook.com` for velog 由 recipe.allowed_navigation_hosts 提供）→ 立即设置 abort flag + RECON `{event: "internal_error", reason: "navigated_off_allowlist"}`
  6. `page.goto(recipe.login_url)`
  7. `logger.recon({event: "awaiting_login"})`
  8. 循环：每 1s 检 `recipe.is_logged_in(page)` 与 abort flag；超时（默认 300s）→ RECON `{event: "timeout"}` + `DependencyError`
  9. `logger.recon({event: "login_detected"})`
  10. `context.storage_state()` → dict（**不**记入 RECON，避免敏感值进日志）
  11. 双 filter（`cookies[]` 与 `origins[]`）经 `recipe.host_allowed`
  12. atomic write 0600 to `--output`（参考 `config/tokens.py:save_blogger_token` 模式）；写后 stat-check verify mode == 0o600
  13. `logger.recon({event: "saved", path_masked: <user-segment-masked>, cookie_count: n})`
  14. `finally:` 块兜底 unlink 临时文件 + `context.close()` + `browser.close()` + `shutil.rmtree(user_data_dir, ignore_errors=True)`，无论何种退出路径
- **每渠道 host filter（严格 dotted-suffix）**：
  - velog: `velog.io` 严格 + plan-012 §R16 lstrip("."); allowed_navigation_hosts = `{google.com, accounts.google.com, github.com, facebook.com}`
  - medium: `medium.com` 严格 dotted-suffix；allowed_navigation_hosts = `{google.com, accounts.google.com, facebook.com, twitter.com}`；登录态判断**仅在 medium domain**（即 `is_logged_in` 检查 `page.url` 必须在 medium.com 域）
  - blogger: `blogger.com`, `blogspot.com`, `google.com`, `accounts.google.com` 严格 dotted-suffix；**blast radius 最大**（Google SID/HSID/SSID），ops 文档要警告
- **RECON redactor 强化**：
  - 既有 `_SENSITIVE_KEYS` JSON key 黑名单扩展为 `{storage_state, cookies, origins, Authorization, Set-Cookie, sid, uid, SID, HSID, SSID, access_token, refresh_token}`
  - 新增 free-text pattern 双正则：
    - 模式 A（关键字邻近）：`(?i)(set-cookie|authorization|bearer|x-csrf-token)[\s:=]+\S+` → 整 value 替换为 `[REDACTED-FREETEXT-COOKIE]`
    - 模式 B（长 base64-like blob 在 cookie/token 键值内）：JSON 字段名 `(?i)(cookie|token|state|session|sid|access|refresh)` 对应的 value 若是 string 且长度 ≥ 20 且匹配 `^[A-Za-z0-9+/=_\-]+$` → 替换为 `[REDACTED-BLOB-len=N]`
  - Path 在 RECON event 里始终 masked（用户名段替换为 `<user>`）
- **路径校验**：`--output` 路径必须在 `_config_dir()` 之内或显式白名单（防 `--output ../../etc/passwd`）；resolve absolute + `is_relative_to(_config_dir())` 检查
- velog-login alias 在 Unit 3 实现，本 unit 提供 `bind_channel.main_argv(argv: list[str])` 入口供 alias 注入参数
- 不直接 import `webui_store`，CLI 完全独立（R15 解耦契约）

**Execution note:** Test-first 先建 recipes 的纯函数测试（host_allowed + resolve_output_path）与 driver 的 mocked-playwright 测试；headed Chromium 部分手动验一次即可，CI 全走 mock。

**Patterns to follow:**
- `publishing/adapters/medium_browser.py` 的 Playwright import-guard
- `cli/phase0_seal.py` 的 argparse 模板（本 CLI 用 `--channel` 而非 subcommand）
- `config/tokens.py:save_blogger_token` 的 atomic 0600 写盘
- `_util/logger.py:_SENSITIVE_KEYS` 既有 JSON key 黑名单结构

**Test scenarios:**
- Happy path（mock）: `--channel velog --output <config_dir>/velog-cookies.json` → driver 调到 `storage_state()` → 文件存在 0600 + 内容含 `cookies` 键
- Happy path: 每 recipe `host_allowed` 表驱动测严格 dotted-suffix：
  - velog: `velog.io` ✓ / `.velog.io` ✓（前缀点 lstrip 后等价）/ `evil-velog.io` ✗ / `s3.velog.io` ✗（s3 不是 velog.io 的 dotted-suffix）/ `velog.io.evil.com` ✗
  - medium: `medium.com` ✓ / `cdn.medium.com` ✓ / `evil-medium.com` ✗ / `medium.com.evil.com` ✗
  - blogger: `accounts.google.com` ✓ / `evil-accounts.google.com` ✗ / `accounts.google.com.evil.com` ✗ / `blogspot.com` ✓ / `myblog.blogspot.com` ✓
- Happy path: `recipe.resolve_output_path(_config_dir())` velog → `.../velog-cookies.json`；medium → `.../medium-state.json`；blogger → `.../blogger-state.json`
- Edge case: Playwright 不在 `sys.modules`（patch `bind_channel.sync_playwright = None`）→ exit `DependencyError`(3) + stderr 含 "playwright install chromium"
- Edge case: storage_state 含 host 命中 + host 不命中两类 cookies → filter 后只剩命中类
- Edge case: `--output /etc/passwd` → `UsageError`，文件不写
- Edge case: storage_state 写盘后 mode != 0o600（mock `os.stat`）→ `InternalError` + unlink + 不发 EVENT_SAVED
- Error path: `is_logged_in` 永远 False + timeout 用 1s → 1s 后 RECON `EVENT_TIMEOUT` + exit `DependencyError`(3)
- Error path: `--output` 路径不可写 → exit `InternalError`(5)，临时文件清理（finally unlink 触发）
- Error path: 用户在 Chromium 关窗（mock `page.is_closed() = True`）→ exit `DependencyError` + 提示信息
- Error path: 未知 `--channel foo` → exit `UsageError`(1) + stderr 列出 `CHANNELS` 内容
- Error path: 用户在等待窗口被诱导到 `evil.com`（navigation listener 触发）→ 立即 abort + RECON `EVENT_INTERNAL_ERROR reason=navigated_off_allowlist` + exit `DependencyError`
- Integration: RECON 日志 `_SENSITIVE_KEYS` redactor 把 `storage_state` / `cookies` / `Authorization` / `Set-Cookie` 完整脱敏（构造含敏感值的 dict + free-text 字段测试）
- Integration: 注入 stderr free-text `"got Set-Cookie: sessionid=abc123def..."` → redactor 把整段替换为 `[REDACTED-FREETEXT-COOKIE]`
- Integration: 进程被外部 SIGTERM → finally 块 cleanup + 临时文件被删（mock signal handler 路径）
- Integration: CLI driver 发出的 event 名集合 ⊆ `cli/_bind/channels/__init__.py:EVENTS` frozenset（Unit 4 webui reader 用同一 frozenset 做白名单解析）

**Verification:**
- `pytest tests/test_cli_bind_channel.py tests/test_bind_recipes.py tests/test_bind_driver.py tests/test_bind_events.py tests/test_recon_redactor_freetext.py` 全过
- `bind-channel --help` 列出 `--channel` choices = `CHANNELS`
- `grep -rn 'endswith(' src/backlink_publisher/cli/_bind/channels/ | grep -v '"."'` 0 命中（确认全用带点前缀）
- CI 无新增 `playwright install` 步骤即可通过（全部 mock）

---

- [ ] **Unit 3: `velog-login` alias 兼容 PR #66**

**Goal:** 保留 `velog-login` CLI entry，实现上 thin wrapper exec `bind-channel` 默认 velog 渠道与既有 cookie 文件路径，让 plan-012 §verification 的 `velog-login --help` 检查继续通过、PR #66 现有调用方不破。

**Requirements:** R14 兼容性子项 — alias 必须保持 velog plan-012 的 `velog-login` CLI 调用方式（含 --help / --output / --timeout-seconds 透传）

**Dependencies:** Unit 2

**Files:**
- Modify: `src/backlink_publisher/cli/velog_login.py`（如已存在则改造为 wrapper；不存在则创建）
- Modify: `pyproject.toml`（保留 `velog-login = "backlink_publisher.cli.velog_login:main"`）
- Test: `tests/test_cli_velog_login_alias.py`

**Approach:**
- `velog_login.main()` 内部组装 argv：注入 `--channel velog`，把 `--output` **通过 `recipes["velog"].resolve_output_path(_config_dir())` 解析**（保 DRY，路径策略只在 recipe 一处），再调 `bind_channel.main_argv(...)` 或 `runpy` exec
- 透传剩余参数（如 `--timeout-seconds`）
- 不在 alias 里加新逻辑；只做参数注入与默认值
- 输出 shape：velog-cookies.json 形态 `{"cookies":[...], "origins":[...]}` 与 storage_state 兼容 — 由 Unit 2 driver 直接 dump `context.storage_state()` 自然达成
- 留两行 trailing CLI-first / WebUI-first 提示（plan-012 §6.5 已 lock 的契约）

**Patterns to follow:**
- 内部子模块复用 + 公开 entry 不变的 Python 模式（参考标准库 `argparse.Namespace` 包一层）

**Test scenarios:**
- Happy path: `velog-login --help` 输出含 `velog` 字样（plan-012 §verification line 442 兼容）
- Happy path: subprocess `velog-login` 调用（mock playwright）→ 文件落在 `velog-cookies.json` 路径
- Edge case: 显式 `velog-login --output /tmp/x.json` 覆盖默认路径
- Edge case: `--channel` 参数若用户误传不同值 → 强制 `velog`（或报 `UsageError`）
- Integration: 既有 PR #66 内引用 `velog-login` 的脚本/test 不破

**Verification:**
- `pytest tests/test_cli_velog_login_alias.py` 全过
- plan-012 §verification line 442 的 `velog-login --help` 仍 exit 0

---

- [ ] **Unit 4: Webui 绑定路由 + 子进程 job 服务（含 CSRF / 进程组 / 多 worker 防御）**

**Goal:** Settings 页 POST 启绑定子进程并 polling 状态，不阻塞 Flask 主线程；CSRF + Host allowlist + 进程组 kill + 多 worker 启动告警全部落地。

**Requirements:** R6（subprocess 启 Playwright headed Chromium）、R7（5min 超时）、R10（子进程退出后状态写入 store）；以及 Unit 1+2+5 共享的失效检测闭环承接

**Dependencies:** Unit 1（写 channel_status_store + CHANNELS）、Unit 2（启 bind-channel CLI + import `EVENTS` frozenset from `cli/_bind/channels/__init__.py`）

**Files:**
- Create: `webui_app/routes/channel_binding.py`
- Modify: `webui_app/routes/__init__.py:register_blueprints`
- Create: `webui_app/services/__init__.py`（若不存在）
- Create: `webui_app/services/bind_job.py`（in-memory job registry + Popen 管理 + reader thread + orphan reaper）
- Modify: `webui_app/__init__.py:create_app`（启用 Flask-WTF CSRF + 既有 blueprint 全部 `@csrf.exempt` + channel_binding blueprint 内 before_request Host/Origin 校验 + Flask bind loopback only 启动断言 + 启动调 `bind_job.reap_orphans()` + 启动调 `channel_status.reconcile_on_load()`）
- Test: `tests/webui/test_channel_binding_routes.py`
- Test: `tests/webui/test_bind_job_service.py`
- Test: `tests/webui/test_bind_csrf_and_host.py`
- Test: `tests/webui/test_bind_orphan_reaper.py`

**Approach:**
- **CSRF + Host 防御层（Unit 4 必交付契约，仅 channel_binding blueprint）**：
  - `webui_app/__init__.py` 启用 Flask-WTF `csrf = CSRFProtect(app)`；**立即对所有既有 blueprint 调 `csrf.exempt(bp)`**：oauth、queue、batch、sites、llm、dashboard、settings_basic、pipeline、drafts、checkpoint、history、main、profiles —— 全 exempt 不变行为。**只**让 channel_binding blueprint 受 CSRF 保护。系统级 CSRF 迁移是另一 plan
  - channel_binding routes 模板含 `{{ csrf_token() }}`；前端 JS fetch 加 `X-CSRFToken` header
  - `before_request` 在 channel_binding blueprint 内：校验 `request.host in {"127.0.0.1:8888", "localhost:8888"}` 否则 403；校验 `request.origin` / `request.referrer` 若存在则 host 须在白名单；**Origin 和 Referer 都为空时拒绝 mutating POST**
  - Session cookie `app.config["SESSION_COOKIE_SAMESITE"] = "Strict"` + `SESSION_COOKIE_HTTPONLY = True`
  - **`create_app` 启动断言 Flask bind 是 127.0.0.1**：检测 `webui.py` argv `--host` / Flask `app.run(host=...)` / `FLASK_RUN_HOST` env；非 loopback 立即 abort + 明示如何改回
  - Fallback：DNS-rebind 攻击场景 → Host header 校验 + loopback bind 双重防御
- **路由**（统一约定：`<channel>` 字符串参数 + 首行 `if channel not in CHANNELS: abort(400)`；不用 Flask `<any(...)>` converter 避免双重权威源）：
  - `POST /settings/<channel>/browser-bind` — 调 `bind_job.start(channel)` 返回 `{job_id, status:"running"}`；不 redirect
  - `GET  /settings/<channel>/browser-bind/status/<job_id>` — **owner_session check**：`job.owner_session_id != flask.session.get("id")` → 返回 **404**（不是 403，防 job 存在性泄漏）；否则返回 `{status: ..., progress_events: [...], error_code?: str}`（error_code 是枚举不是 raw msg）；TTL 后返回 `404`
  - `POST /settings/<channel>/browser-bind/cancel/<job_id>` — 同样 owner_session check；设 `cancel_requested` flag + `os.killpg(pgid, SIGTERM)`；5s 后由 reader thread 观察 returncode + cancel_requested 合成终态；**幂等**：status != running 时直接返回当前 status，不报错
- **`bind_job.start(channel)`**：
  - 二次校验 `channel in CHANNELS`（route 已校验但纵深防御）
  - 通过 Unit 2 `recipes[channel].resolve_output_path(_config_dir())` 取 output path
  - 构造命令 `[sys.executable, "-m", "backlink_publisher.cli.bind_channel", "--channel", channel, "--output", str(output_path)]`（参考 `helpers._rewrite_cli_cmd`）
  - **env 显式 allowlist**：`env={"PATH": os.environ.get("PATH","/usr/bin:/bin"), "PYTHONPATH": "src", "HOME": os.environ["HOME"], "DISPLAY": os.environ.get("DISPLAY",""), "BACKLINK_PUBLISHER_CONFIG_DIR": os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR","")}` — 父进程其他 env 不继承
  - `job_id = secrets.token_urlsafe(32)`；`owner_session_id = flask.session.get("id")` 落到 `BindJob.owner_session_id`
  - **`Popen(stderr=PIPE, stdout=DEVNULL, bufsize=1, text=True, start_new_session=True, env=<allowlist>)`**（POSIX 进程组语义；Windows 用 `CREATE_NEW_PROCESS_GROUP`，已声明 Windows v1 不支持，见 Scope Boundaries）
  - 写 PID 文件 `_config_dir() / "bind-channel.pid"` 给 orphan reaper 用（含 pgid + start_time）
  - reader Thread（**唯一调 `proc.wait()` 的代码路径**）：
    - try: 行循环 `json.loads`，把 event 名比对 `EVENTS` frozenset（从 `cli/_bind/channels/__init__.py` import）；命中的进 `job.progress_events`；**非 JSON 行或 event 不在 EVENTS 集合中的直接丢弃**，绝不进 progress_events 也不打 Flask logger（防泄密）
    - except 任何异常：`job.status = "internal_error"`；不让线程挂主进程
    - finally: `proc.wait(timeout=60)` 拿 returncode → 合成终态（cancel_requested + returncode != 0 → cancelled；returncode != 0 → failed；returncode == 0 → bound + 调 `channel_status.mark_bound`）；关 `proc.stderr` fd 释放
  - 360s wall-clock timer（threading.Timer）：仅 set `timeout_flag` + `killpg(SIGTERM)` + 5s 后 `killpg(SIGKILL)`；不直接动 status
- **Job state coherence**：
  - 每 `BindJob` 持有 `_lock: threading.Lock` 保护 status / progress_events / cancel_requested / timeout_flag 互斥
  - 模块级 `_registry: dict[job_id, BindJob]` + `_registry_lock: threading.Lock` 分离 — polling N 个 job 不串行
  - TTL 1h：lazy-on-read，polling endpoint 见 job 终态 + age > 1h 时从 registry 删除 + 返回 404
  - 409 conflict：start 时先扫 `_registry` 中同 channel 且 status=running 的 job → 409 + 返回该 job_id（让前端复用 polling）
- **Orphan reaper（启动时）**：
  - `bind_job.reap_orphans()` 在 `create_app` 末尾调一次
  - 扫 `_config_dir() / "bind-channel.pid"`：若文件存在 + PID 存活 + 命令行匹配 `bind_channel` → kill 整个 process group（防 zombie Chromium）；删 pid 文件
  - RECON 一行 warn 告知有几个 orphan 被 reap
- **Multi-worker guard（仅文档，不做运行时检测）**：
  - Scope Boundaries 已明确不支持多 worker WSGI
  - **不**在代码里加 env 启发式检测（`GUNICORN_CMD_ARGS` 大多数 ops 不 set，启发式 false-negative；scope-guardian + feasibility 一致意见删除）
  - Ops 文档（Unit 7）显眼位置标注："webui 假定单进程；不要用 `gunicorn -w >1` / `uwsgi --processes >1` / mod_wsgi 多进程"

**Patterns to follow:**
- `webui_app/helpers.py:_rewrite_cli_cmd` 的命令构造
- `webui_app/routes/oauth.py` 的 channel 白名单 + flash 风格

**Test scenarios:**
- Happy path: `POST /settings/velog/browser-bind` 带 CSRF token → 200 + `{job_id}`；mock Popen 走完 → polling 几次 → 最后 `status=bound`；`channel_status_store["velog"].status == "bound"` + path 指向 velog-cookies.json
- Happy path: progress_events 含 `launching / awaiting_login / login_detected / saved` 四阶段（断言每个 event 都在 `EVENTS` frozenset 内）
- Happy path: cancel 已绑成功的 job → 200 + status=bound（幂等，不报 ProcessLookupError）
- Edge case: 同一 channel 二次 `POST` 而前次未结束 → 409 + 返回前次 job_id
- Edge case: 不在白名单的 `POST /settings/foo/browser-bind` → 400 + 返回 CHANNELS 列表
- Edge case: 路径 traversal `POST /settings/..%2Fevil/browser-bind` → 被 Flask 路由 converter 或 channel 校验拦
- Edge case: `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/iso pytest` → bind-channel.pid + storage_state 都落 `/tmp/iso/`
- **Security: 缺 CSRF token 的 POST → 403**
- **Security: Host header `Host: attacker.com` 的 POST → 403**（DNS-rebind 防御）
- **Security: Origin `https://evil.com` 的 POST → 403**
- **Security: SameSite=Strict cookie 在跨源 POST 中不发送 → 403**（测 cookie header 不带 session）
- **Security: 路径 traversal via `mark_expired("../evil")` 在 webui 层被白名单挡（不依赖 Unit 1 单测）**
- Error path: Popen returncode != 0 → `status=failed` + `error_code` 是枚举（如 `TIMEOUT`/`NAVIGATED_OFF_ALLOWLIST`）而非 raw msg
- Error path: 子进程 360s 未退 → SIGTERM 发出 5s 后 SIGKILL；最终 `status=timeout`
- Error path: SIGTERM 命中已自然退出的 proc（mock killpg raise ProcessLookupError）→ 静默吞，状态由 returncode 决定
- Error path: reader thread mock `json.loads` raise → `status=internal_error`，进程仍能正常结束，主线程不挂
- Concurrency: 100 个 polling GET 并发 → 不卡（per-job lock + 分离 registry lock）
- Lifecycle: 启动时 PID 文件指向已死 PID → reap 静默；指向活的 bind_channel 进程 → killpg 之
- Lifecycle: 启动时 PID 文件指向活的**非** bind_channel 进程（PID 被复用）→ 检查命令行不匹配，不杀
- Loopback bind: `python webui.py --host 0.0.0.0` 启动 → loud abort + 错误信息含 "127.0.0.1 only" 字串
- CSRF blueprint scope: 既有 oauth/queue/batch 等 blueprint 的 POST 表单**无** csrf_token 仍 200（exempt 生效）；channel_binding routes 无 CSRF token 返 403
- Job IDOR: session A 启 job → session B GET `/status/<job_id>` 返 404（非 403）；session A 自己 GET 返 200
- Env allowlist: parent 设 `SENTINEL_SECRET=xxx` → 子进程 `/proc/self/environ` / `os.environ.get("SENTINEL_SECRET")` 为空
- Chromium user-data-dir: driver 退出后 `bp-bind-*` 临时目录被清
- Integration: 完整端到端（mock Playwright）：start → poll 5 次 → bound → channel_status_store 写 + storage_state 文件 0600 存在 + bound_at 是有效 ISO + path 是 masked-aware
- Integration: contract test — CLI emit 的 event 名 + reader 处理的 event 名 都来自同一 `EVENTS` frozenset（双方 import 同一常量，drift 不存在）

**Verification:**
- `pytest tests/webui/test_channel_binding_routes.py tests/webui/test_bind_job_service.py tests/webui/test_bind_csrf_and_host.py tests/webui/test_bind_orphan_reaper.py` 全过
- `python webui.py` 启动后 `curl -X POST localhost:8888/settings/velog/browser-bind`（无 CSRF）返回 403；带 CSRF（mock playwright）返回 200 + job_id
- `grep -rn 'POST\|methods=\["POST"\]' webui_app/routes/channel_binding.py | wc -l` 与 CSRF token 出现次数对齐

---

- [ ] **Unit 5: Settings 模板按钮 + status badge + 失效 banner**

**Goal:** UI 让用户能点绑定、看到状态、被失效提醒到。

**Requirements:** R1, R2, R3, R4, R5, R12

**Dependencies:** Unit 1（读 channel_status_store）、Unit 4（路由 URL 存在）

**Files:**
- Modify: `webui_app/templates/_settings_channel_velog.html`（替换 disabled 按钮）
- Modify: `webui_app/templates/_settings_channel_medium.html`（加并列「浏览器登录」按钮区）
- Modify: `webui_app/templates/_settings_channel_blogger.html`（加并列「浏览器登录」按钮区 + **blast-radius 警告**因 Google cookie 覆盖广）
- Modify: `webui_app/templates/settings.html`（顶部失效 banner + Playwright 未安装 banner + 引入新 JS 片段）
- Modify: `webui_app/helpers.py:_settings_context`（聚合 `<channel>_binding_status` / `<channel>_bound_at` / `<channel>_storage_state_path_masked` / `playwright_available` 探测结果）
- Create: `webui_app/static/js/channel_binding.js`（提交按钮 + 2s polling + 状态 UI 更新 + CSRF token header；约 80-150 行 vanilla JS）
- Test: `tests/webui/test_settings_render.py`
- Test: `tests/webui/test_settings_context_binding.py`
- Test: `tests/webui/test_playwright_probe.py`

**Approach:**
- velog tab：移除 disabled + Phase 0 alert（保留小字说明 Phase 0 已通过），加按钮 `<button data-bind-channel="velog">🌐 浏览器登录 velog</button>` + 状态 badge `{未绑定 / 已绑定 YYYY-MM-DD HH:mm / 已失效}`，OAuth 区不存在（velog 唯一路径）
- medium tab：在现有 OAuth 表单**之外**加一个 separator + "或浏览器登录"，按钮 + status badge。OAuth 区标 `Recommended`
- blogger tab：同 medium 模式 + **额外**警告框「⚠️ 浏览器登录将保存 Google 账号 cookie（覆盖广于单一 Blogger 应用授权），建议优先用 OAuth 路径」
- **Playwright 探测 banner**（顶部）：`_settings_context.playwright_available` 为 False → "⚠️ 未检测到 Playwright Chromium。请在终端运行 `playwright install chromium` 后刷新本页。<a>查看文档</a>"；所有浏览器登录按钮 `disabled` 并附 tooltip 同样提示
- 顶部失效 banner：`_settings_context` 算 expired 列表，模板 `{% if expired_channels %}<div class="alert alert-warning">⚠️ <strong>{{ expired_channels|length }}</strong> 个渠道凭据已失效：<a href="#channel-{{ c }}">{{ c }}</a>... 请重新绑定。</div>`
- channel_binding.js：
  - **CSRF**: 所有 fetch 加 `X-CSRFToken: <meta name="csrf-token">` header（meta tag 由 settings.html 渲染）
  - **Reopen-tab 恢复**：页面加载时 JS 检 `sessionStorage.getItem("bp_active_job_<channel>")`，若有 job_id → 立即恢复 polling；polling 返 404 → 显示"上次绑定已结束，当前状态：<store value>"
  - 监听 `[data-bind-channel]` click → POST → 拿 job_id → `sessionStorage.setItem("bp_active_job_<channel>", job_id)` → setInterval 2s polling → **inline progress region 替换按钮+badge 区域**（不用 modal，避免 dialog focus trap） → done 后清 sessionStorage + reload 当前 tab
  - error_code 枚举映射到中文文案（**完整映射表见下方**，不暴露 raw msg，配合 Unit 4 RECON 收口）
  - "取消"按钮 → POST cancel endpoint（带 CSRF），secondary 风格按钮放进度文本右侧，**单击不弹 confirm**（避免 dialog trap + 用户已"丢弃 5 分钟登录"是显式决策）
  - button disable + spinner 期间；polling 间隔 2s，最长 6min（超 webui 360s 上限）后自动停 + 提示用户刷新
  - 单 job 期间所有 channel 的 bind 按钮 disable（in-process 单 job，对应 Unit 4 409 conflict 语义）

**Error code → 中文文案表（v1，必须 inline 映射到 JS）：**

| error_code | 中文文案 | tone |
|---|---|---|
| `timeout` | 5 分钟内未检测到登录完成，已中止；请重试 | warning |
| `navigated_off_allowlist` | 浏览器被导航到不允许的域，已中止以保护账号安全；请确保仅在登录页操作 | error |
| `internal_error` | 内部错误，请查看 webui 日志 | error |
| `storage_state_missing` | 凭据文件不存在 | warning |
| `storage_state_unreadable` | 凭据文件无法读取（权限问题？） | error |
| `storage_state_corrupt` | 凭据文件损坏，请重新绑定 | error |
| `missing_playwright` | 未安装 Playwright Chromium，请在终端运行 `playwright install chromium` | warning |
| `cancelled` | 已取消 | info |
| `dependency_error` (兜底) | 浏览器登录依赖缺失：<arg> | warning |

**Accessibility 契约**：
- `role="status"` + `aria-live="polite"` 在 inline progress region（screen reader 听得到 5min 等待过程的 event 转中文播报）
- `role="alert"` 在顶部失效 banner（紧急程度更高）
- `role="status"` 在 Playwright 缺失 banner
- Status badge 用 **icon + 文字 + 颜色** 三重，不能只靠颜色区分（色盲友好）
- bind 按钮 click 后 focus 移到 progress region；terminal state 后 focus 回 badge
- cancel 键盘可达 + 可见 focus ring
- blogger 的 bind 按钮 `aria-describedby` 指向旁边的 blast-radius 警告框
- bound_at 时间用 `<time datetime="ISO">` 标签让 screen reader 按用户 locale 读出
- 路径 masked（不在 UI 暴露完整路径）：`~/.config/.../<channel>-state.json` → 用户名段替换为 `<user>`，参考既有 `medium_token_masked` 风格
- **Playwright 探测**：`_settings_context` 调 `_probe_playwright()`：try import `from playwright.sync_api import sync_playwright`；try `pw = sync_playwright().start()`；取 `pw.chromium.executable_path`；**`os.path.exists(executable_path)` 验证（executable_path 是构造路径，本身不保证文件存在）**；**`pw.stop()` 在 finally 释放 node driver 子进程避免泄漏**；任一失败 → `playwright_available=False`。结果按进程生命周期缓存（首次探测后存模块级 bool + `threading.Lock` double-checked-locking，避免请求线程并发双探测）

**Patterns to follow:**
- `webui_app/templates/_settings_channel_medium.html` 的现有 OAuth 块结构（保持视觉一致）
- `webui_app/helpers.py:_settings_context` 现有 medium_oauth_configured 等字段命名

**Test scenarios:**
- Happy path: store 含 `velog={status:bound, bound_at:...}` → 渲染含「已绑定」badge + 时间
- Happy path: store 含 `medium={status:expired}` → 顶部 banner 出现 + 计数 1 + 锚点链接 `#channel-medium`
- Happy path: 无任何 binding → velog tab 显示「未绑定」按钮可点；medium/blogger tab 显示 OAuth 表单 + 浏览器登录按钮并存（OAuth Recommended）
- Happy path: Playwright 可用 → 按钮可点 + 无 banner
- Edge case: 3 个 channel 同时 expired → banner 计数 3 + 三个锚点
- Edge case: bound_at 时区显示一致（用 ISO 字符串原样，或 `<time>` 标签让浏览器渲染）
- Edge case: `_settings_context` 在 channel_status_store 完全空时不抛
- Edge case: Playwright 未安装 → settings 页顶部 banner 出现 + 所有 bind 按钮 disabled + tooltip 提示装 chromium
- Edge case: blogger tab 始终显示 blast-radius 警告框（不论是否绑定）
- Edge case: `_probe_playwright()` 调一次后缓存；二次加载 settings 不重复 import
- Integration: 按钮 click → JS POST 含 `X-CSRFToken` header → mock fetch → 状态 UI 更新（用 jsdom 或纯模板/服务端渲染断言）—— 若 JS 端 e2e 复杂，至少断言 HTML 中 `data-bind-channel` 属性 + 路由 URL 正确 + meta csrf-token tag 存在
- Integration: error_code 枚举到中文映射完整（上方表中每个 code 都有对应文案）
- A11y: settings.html 渲染含 `role="status"` + `aria-live="polite"` 在 progress region；`role="alert"` 在失效 banner；badge 包含 icon + 文字 + 颜色三重信息
- A11y: 用 axe-core 或 pa11y 跑一次 lint，无 P0 报错
- Reopen-tab: 启 job → 关 tab → 新 tab 加载 settings 含同 sessionStorage → polling 立即恢复
- Reopen-tab: 同上但 job 已经在 store 中完成 → polling 返 404 → UI 显示"上次绑定已结束，当前状态：<store value>"

**Verification:**
- `pytest tests/webui/test_settings_render.py tests/webui/test_settings_context_binding.py` 全过
- 手动启 `python webui.py` 打开 `/settings`，三个 channel tab 都有可点的"浏览器登录"按钮 + badge；可点击触发（mock CLI 也可）

---

- [ ] **Unit 6: Adapter 失效信号升级 + publish-backlinks 写状态（瘦身版：仅 OAuth 401 → mark_expired）**

**Goal:** 把 medium_api / blogger_api 在现有 OAuth 401/403 路径 raise `AuthExpiredError(channel=...)`；publish-backlinks catch site 写 channel_status_store；让 settings banner 在 publish 失败后自动出现。**不重写 medium_browser**（仍 persistent_context），**不新建 blogger_browser adapter**，**不创建 `_auth_state.load_storage_state` 中心化 helper**。bind-channel CLI 写的 `medium-state.json` / `blogger-state.json` 在 v1 暂未被 adapter 消费 —— 它们是"预留"，供未来 medium_browser 重写或 blogger_browser 新建时接入。

**Requirements:** R11, R12, R13（OAuth 路径上的失效闭环）

**Dependencies:** Unit 1（`AuthExpiredError` + `channel_status.mark_expired` 存在）

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/medium_api.py`（401 / token preflight 失败 → `AuthExpiredError(channel="medium", ...)`，替代既有 `ExternalServiceError("Medium integration token invalid (401)")`）
- Modify: `src/backlink_publisher/publishing/adapters/blogger_api.py`（401/403 → `AuthExpiredError(channel="blogger", ...)`，替代既有 `ExternalServiceError("Blogger authentication failed")`）
- Modify: `src/backlink_publisher/cli/publish_backlinks.py`（在 `except DependencyError as exc:` 段第一行加 `if isinstance(exc, AuthExpiredError): channel_status.mark_expired(exc.channel)`）
- Test: `tests/test_adapter_medium_api_auth_expired.py`
- Test: `tests/test_adapter_blogger_api_auth_expired.py`
- Test: `tests/test_publish_backlinks_expired_signal.py`
- Modify: 既有 adapter 测试如断言了 `ExternalServiceError` 类型且场景是 auth-expired，改为断言 `AuthExpiredError`；非 auth 错误仍走 ExternalServiceError

**Approach:**
- 子类 `AuthExpiredError(DependencyError)` 已在 Unit 1 落（exit_code=3，与 plan-012 收敛）；本 unit 只换 raise 类与 catch 位置
- `medium_api.py`：401 / token preflight (< 5min) 失败处把 `raise ExternalServiceError(...)` 改 `raise AuthExpiredError(channel="medium", reason="oauth_token_invalid")`
- `blogger_api.py`：401/403 处同样改 `raise AuthExpiredError(channel="blogger", reason=...)`
- `medium_browser.py`：**不动**（redirect-to-signin 当前 raise `ExternalServiceError("Medium login expired...")` 留给未来 medium_browser 重写期连带升级；本 unit 的 publish_backlinks catch 段对它没新增分支，行为 0 变化）
- `cli/publish_backlinks.py`：现状 catch 顺序是 `DependencyError → ExternalServiceError → Exception`；本 unit 在 DependencyError catch 段第一行加 `if isinstance(exc, AuthExpiredError): channel_status.mark_expired(exc.channel)` —— 不打断后续 `AdapterResult(status="failed")` 构造。catch 顺序 0 变化（AuthExpiredError 是 DependencyError 子类，天然命中现有段）
- velog plan-012：本 unit 不动；plan-012 owner 后续可独立把 `DependencyError("velog cookie expired")` 升级到 `AuthExpiredError(channel="velog")` 接入自动 store 翻 expired（纯加法）
- **bind-channel CLI 写的 `medium-state.json` / `blogger-state.json` 在 v1 不被任何 adapter 读取**。它们是预留供未来 medium_browser 重写到 `launch + storage_state=` 时接入，以及未来 blogger_browser 新 adapter 接入。这一点在 ops 文档显眼标注（避免用户误以为绑了就生效）

**Patterns to follow:**
- `_util/errors.py` 现有家族抛错惯例
- 既有 `cli/publish_backlinks.py` catch chain 结构（不破坏 AdapterResult 输出契约）

**Test scenarios:**
- Happy path: medium_api 收到 401 → raise `AuthExpiredError(channel="medium")`
- Happy path: blogger_api 收到 403 → raise `AuthExpiredError(channel="blogger")`
- Happy path: publish-backlinks 跑 medium 失败 → returncode 非 0 但 `channel_status_store["medium"].status == "expired"` + 其他 channel 不受影响
- Edge case: AuthExpiredError 仍被现有 `except DependencyError` 捕到（继承关系）
- Edge case: 非 auth 401（如 Medium 服务端临时 401 而 token 实际有效）—— 本期归一为 expired 可接受，rebind 后即恢复；用户体验上 OAuth 路径自带 token 在；点 rebind 触发 OAuth flow 一次即可
- Edge case: `AuthExpiredError(channel="../evil")` 由 Unit 1 的白名单挡（cross-ref Unit 1 测试）
- Edge case: medium_browser redirect-to-signin 仍 raise 既有 `ExternalServiceError` —— 不算 AuthExpiredError，store 不被翻 expired（accepted v1 局限，future unit 升级）
- Error path: publish 同一批次多 channel 失败 → 每 channel 独立 mark_expired，互不污染
- Integration: 跑完后 `/settings` 加载 → banner 出现 + 计数正确（端到端串联 Unit 1/5/6）

**Verification:**
- `pytest tests/test_adapter_medium_api_auth_expired.py tests/test_adapter_blogger_api_auth_expired.py tests/test_publish_backlinks_expired_signal.py` 全过
- `grep -rn 'raise ExternalServiceError(\"Medium.*token invalid\|Blogger.*authentication failed' src/backlink_publisher/` 0 命中（替换完成）
- `medium_browser.py` diff 为空（确认本 unit 不动它）

---

- [ ] **Unit 7: 操作文档**

**Goal:** 给运营写一份"如何使用浏览器绑定 / 失效重绑 / 安全注意 / v1 已知限制"的 ops 文档。

**Requirements:** Success Criteria 友好度 + Scope Boundary v1 风险公开

**Dependencies:** Unit 2, 5（行为已稳定）

**Files:**
- Create: `docs/operations/channel-binding.md`
- Modify: `docs/plans/2026-05-18-012-feat-velog-adapter-plan.md`（加 amendment 区块：`velog-login` 现为 `bind-channel --channel velog` 的 alias；`AuthExpiredError(DependencyError)` 与 plan-012 收敛；output path 不变）
- Modify: `backlink-publisher/AGENTS.md`（"Channel binding" 一节小标题 + 指向 ops 文档）

**Approach:**
- `channel-binding.md` 结构：
  - 何时用浏览器绑定（vs OAuth）
  - 一次性 `playwright install chromium` 步骤（含 macOS/Linux 命令示例）
  - 三个渠道的具体操作演示（velog/medium/blogger 各 5 步）
  - 失效后怎么办（看 banner → 点重绑）
  - **v1 已知限制**：(1) bind-channel 写的 `medium-state.json`/`blogger-state.json` 在 v1 未被 adapter 消费 — Medium/Blogger 失效检测/重绑生效仍走 OAuth 路径；浏览器登录 v1 主要服务 velog + 提供未来 medium_browser 重写 / blogger_browser 新增的预留 (2) Windows 不支持 (3) 不支持 multi-worker WSGI (4) Flask bind 必须 127.0.0.1
  - 凭据文件位置 + 0600 + **同 UID 进程可读 v1 风险** + **同 UID 进程可驱动 webui binding v1 风险**
  - **Time Machine 排除示例**：`tmutil addexclusion ~/.config/backlink-publisher`（macOS）
  - **Flask 重启后清孤儿**：`pkill -f bind-channel`（POSIX）
  - 卸载步骤
  - TTL 期望（velog ≥24h 实测）
  - Blogger blast radius 警告（Google SID/HSID/SSID 全域 cookies）
- velog 退场 checklist **不**单独成文件，作为 §Risks 表内 velog 退场行的 inline expanded 形态保留即可（scope-guardian 建议）

**Test scenarios:**

Test expectation: none -- 纯文档，无行为变更。

**Verification:**
- `cat docs/operations/channel-binding.md` 内容存在 + 链接路径不死链（`markdown-link-check` 可选）
- AGENTS.md "Channel binding" 小节存在并 link 到 ops 文档

## System-Wide Impact

- **Interaction graph:** `publish-backlinks` ↔ `channel_status_store` ↔ `webui` settings 页 ↔ `bind-channel` CLI 是新闭环；`cli/_bind/channels/__init__.py:EVENTS` frozenset 是 CLI ↔ webui 事件名跨进程契约的单一权威源；既有 OAuth 路径（`webui_app/routes/oauth.py`）完全不动；velog plan-012 接口表面仅在 `velog-login` 实现处改成 alias 转发
- **Error propagation:** `AuthExpiredError(DependencyError)` 经现有 `except DependencyError` site 顺路捕获；publish_backlinks DependencyError catch 段加 isinstance 分支；catch chain 顺序必须保持 DependencyError → ExternalServiceError → Exception，否则 AuthExpired 被吃；adapter result 输出契约（status, error msg）不变
- **State lifecycle risks:**
  - storage_state.json 写盘失败但 CLI exit 0 — 由 atomic temp-rename + stat-check on save + finally unlink 三层防御
  - 子进程 reader thread 与 Flask 主进程竞态读 job 字典 — per-BindJob `_lock` + registry `_registry_lock` 分离，polling 不串行
  - Popen 子进程被 systemd / launchctl 提前杀 — `start_new_session=True` 进程组 + reader thread 唯一 wait() 拥有者；启动时 orphan reaper 清遗留
  - 用户关 webui 进程时 job 丢失 — 接受，用户重发；不持久化 in-progress job；但 PID 文件 + reaper 防 Chromium 僵尸
  - channel_status_store 与 storage_state.json 不一致（文件被手删 / 跨进程并发写）— `reconcile_on_load()` 启动自检 + `mark_bound` 时 verify 文件存在 + path `is_relative_to(_config_dir())`；v1 medium_api/blogger_api 走 OAuth 路径不读 storage_state 文件，不一致只影响"settings 显示态"，发布失败仍由 OAuth 401 → AuthExpiredError 触发 banner
  - cancel 命中已自然退出的 proc — reader thread 单一 wait() 拥有者；cancel 只设 flag + 发 signal；reader 合成终态时观察 flag
  - reader thread 抛异常 — try/except 包整循环，异常 → `status=internal_error`，finally 关 stderr fd 防泄漏
  - 多 worker WSGI 部署 — 启动告警；不阻止启动；明确文档化为 unsupported
- **API surface parity:** OAuth 现有路由完全不动，避免冲击。新增 `/settings/<channel>/browser-bind/*` 路由不与既有 `/settings/medium/oauth-*` `/settings/blogger/oauth-*` 路径冲突。**所有 settings POST 路由统一启用 CSRF**，既有 OAuth POST 路由也会附带（Flask-WTF `CSRFProtect(app)` 全局）— 既有路由模板需补 `{{ csrf_token() }}`，是 Unit 4 必须连带改的子项
- **Integration coverage:**
  - Unit 6 测试需走 publish_backlinks 真实 catch path（不能只测 adapter raise）
  - Unit 5 测试需走 _settings_context 真实聚合（不能只测模板片段）
  - Unit 4 + Unit 2 之间的 RECON JSON 协议需有 contract test（mock CLI 输出，验 webui 解析正确；事件集三方一致）
  - Unit 6 + Unit 1 + Unit 5：端到端"publish 失败 → store 翻 expired → settings 加载看到 banner"必须有一个全链 integration test
  - 既有 OAuth 路由 + 新 CSRF：现有 medium/blogger OAuth 表单 POST 必须升级带 csrf_token，否则 CSRF 启用后既有功能破。Unit 4 改 `webui_app/__init__.py` 时连带改 OAuth 模板
- **Unchanged invariants:**
  - Medium/Blogger OAuth 已绑定的用户路径 0 变化（既有 token 文件、callback URL、save_*_token 全不动；模板加 csrf_token 是新增字段不破已有逻辑）
  - publish-backlinks stdout JSONL 契约（每行 publish_output_*）不变；只是错误 message 字符串内容可能变（向"凭据已失效，请到 /settings 重新绑定"靠拢）
  - velog plan-012 的 `velog-login --help` exit 0 + cookie 文件 schema + host filter contract 不变（alias 透传）
  - webui_store 既有 5 个 singleton 行为 0 变化（新增第 6 个 channel_status_store）
  - `_util/errors.py` 既有 5 个 exit code 不变（`AuthExpiredError.exit_code = 3` 继承自 DependencyError；与既有 DependencyError 同 exit code）
  - velog plan-012 的 `DependencyError("velog cookie expired")` 现状不动；plan-012 owner 后续可选择升级到 `AuthExpiredError(channel="velog")` 接入自动 store 翻 expired，是纯加法

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| **CSRF / DNS-rebind 攻击 localhost:8888** | Flask-WTF `CSRFProtect`**仅作用 channel_binding blueprint**（其他 36 路由 `@csrf.exempt`，行为 0 变化）+ Host header allowlist + Origin/Referer 校验 + **Flask bind loopback only 启动断言** + `SameSite=Strict` cookie。Unit 4 hard precondition，含独立测试 |
| **`<channel>` URL 段 + storage_state_path 路径 traversal** | `CHANNELS = frozenset(...)` 单一权威源 + entry point 二次校验 + `mark_bound`/`mark_expired`/`AuthExpiredError` 构造都白名单 + `mark_bound` 二次校 path `is_relative_to(_config_dir())`。**每个写入路径独立测试 traversal payload** |
| **Job ID 可枚举 / IDOR** | `secrets.token_urlsafe(32)` 不可猜 + 每 job 绑 `owner_session_id`；status/cancel 跨 session 返 **404** 防存在性泄漏；绑定成功后 session rotate |
| **Popen 子进程继承父进程全部 env 泄漏无关 secrets** | `Popen(env=<显式 allowlist>)`：仅 PATH/PYTHONPATH/HOME/DISPLAY/BACKLINK_PUBLISHER_CONFIG_DIR；测试 sentinel env var 不进子进程 |
| **Chromium 误用真实 Chrome profile 灌入其他网站 cookies** | `tempfile.mkdtemp("bp-bind-")` ephemeral user-data-dir；finally `shutil.rmtree` 清理 |
| **Orphan Chromium 进程在 Flask 重启后僵尸** | `Popen(start_new_session=True)` 进程组语义 + PID 文件 + `reap_orphans()` 在 `create_app` 启动时跑一次 + ops 文档兜底 `pkill -f bind-channel`。**Windows 不支持，已声明在 Scope Boundaries** |
| **Multi-worker WSGI 部署破 in-memory job dict** | Scope boundary 明确不支持；ops 文档警告；**不**做运行时 env 启发式检测（false-negative + 为 unsupported 部署写代码自相矛盾） |
| **bind-channel 写的 medium/blogger storage_state 在 v1 未被 adapter 消费** | 接受为 v1 已知限制；ops 文档显眼标注；future unit 升级 medium_browser 重写 + 新建 blogger_browser 时接入。velog 由 plan-012 完整闭环 |
| **同 UID 进程驱动 webui binding（CSRF 不挡）** | 接受为 v1 风险；与"同 UID 可读凭据"取一致；不加 startup bearer token（有意决策）；ops 文档化 |
| **Cancel 命中已自然退出的 proc → `ProcessLookupError`** | reader thread 唯一 wait() 拥有者；cancel/timeout 只设 flag + 发 signal；killpg `try/except ProcessLookupError: pass`；状态合成由 reader 完成 |
| **Reader thread 异常挂主进程或泄漏 cookie 到 Flask logger** | 整循环 try/except 包；异常 → `status=internal_error`；**非 JSON stderr 行直接丢弃**绝不进 progress_events 或 Flask logger；finally 关 stderr fd |
| **`_SENSITIVE_KEYS` JSON key 黑名单漏 free-text cookie 字符串** | `_util/logger.py` 加 free-text 正则匹配（Set-Cookie/Authorization/Bearer 邻近的长 base64-like）；Unit 2 显式测构造 free-text 含敏感值场景 |
| **storage_state 文件与 channel_status_store 不一致（外部删 / 跨进程并发写）** | store `reconcile_on_load()` 启动自检；v1 medium_api/blogger_api 不读 storage_state 文件，不一致只影响 settings 显示态；跨进程并发是 v1 接受风险（文档化） |
| **storage_state 落盘后 mode != 0o600（umask 不一致）** | atomic write 后 stat-check 验证；非 0o600 → `InternalError` + finally unlink + 不写 store；Unit 2 显式测 |
| **Playwright 未安装首次绑定 UX 黑屏** | Unit 5 `_probe_playwright()` 探测 + 顶部 banner + 按钮 disabled + tooltip 提示。失败也只是按钮不响应，不显示晦涩错误 |
| **2FA 超时（用户邮件验证 >5min）→ 被 SIGKILL** | v1 接受 hard 300s 上限；ops 文档备注；future 增量：RECON `awaiting_login` 检测到用户交互信号后 reset timer |
| **Playwright `host_allowed` 用 substring/endswith 不严会被 `evil-medium.com` 钻空** | 强制 dotted-suffix `host == base or host.endswith("." + base)`；recipe 单元测试每个反例（`evil-X / X.evil / X-evil`）必测 |
| **绑定窗口期用户被诱导到攻击者域** | `page.on("framenavigated")` 监听；host 离开 allowlist + 非预期 OAuth 跳转域 → 立即 abort + RECON `EVENT_INTERNAL_ERROR reason=navigated_off_allowlist` |
| **Blogger 浏览器登录覆盖 Google 全域 cookies（SID/HSID/SSID）blast radius 大** | UI 显式警告（建议优先 OAuth）；recipe 把 Google domain 列入但仅 `accounts.google.com / google.com` 严格 dotted-suffix；发布顺序排最后让前两渠道稳定后再 ship blogger |
| 渠道侧（Medium/Blogger/velog）登录页 DOM 改版导致 `is_logged_in` predicate 失效 | recipe 用 cookie 名 + URL 模式 + DOM 三组合，任两通过即判登录；监控 timeout 率作为 canary |
| 渠道反爬虫指纹（user-agent / canvas / WebGL）阻止登录 | Playwright headed 真实浏览器 + 用户操作，比纯 headless 抗指纹强；问题真出现时考虑 attach-existing-Chrome（留 driver 抽象但 v1 不做） |
| **velog 6/8 telegra.ph 判决 fail 后渠道砍掉 — 实际触点 6 处** | 至少 6 处需协同清除：(1) `cli/_bind/channels/velog.py` (2) `cli/velog_login.py` alias + pyproject `[project.scripts]` 中 `velog-login` 入口 (3) `webui_app/templates/_settings_channel_velog.html` + settings.html include (4) `webui_app/helpers.py:_settings_context` 内 velog 字段 (5) plan-012 的 velog adapter 全链 (6) `channel_status_store` 内 `velog` key migration（ghost key 无害但 ops 文档要说明手清）。**本 Risks 行即触点 inline checklist**；不预写单独 `velog-retirement-checklist.md`（scope-guardian 建议） |
| 与 PR #66（velog plan-012 / Phase 0 ship-seal）冲突 | Unit 3 alias 兼容；`AuthExpiredError` 改继承 `DependencyError` 与 plan-012 收敛同 exit code；本 plan 不修改 velog adapter 的 raise 类（留 plan-012 owner）；本 plan 落地前协调 PR #66 land |
| 既有 adapter 测试断言错误类型 → Unit 6 改动后破坏 | `AuthExpiredError(DependencyError)` 继承保证 isinstance DependencyError；既有 ExternalServiceError 断言不动；只在 auth-expired 场景换断言 |
| 既有 OAuth 表单未带 CSRF → 启用 CSRFProtect 后破 | Unit 4 改 `webui_app/__init__.py` 时连带改 oauth.py 模板（既有 `/settings/medium/oauth-start` `/settings/blogger/oauth-start` POST 表单），加 `{{ csrf_token() }}` |
| Tests-coupled-to-operator-config-state 复发 | 所有新路径经 `_config_dir()`；Unit 1 测试显式 `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/iso` 验证隔离；merge 前 grep 审计 `Path.home()` 在新增文件内 0 命中 |
| storage_state 经 Time Machine / iCloud / rsync 备份外泄 | Ops 文档强警告 + 命令示例；**不**主动改用户系统状态；**不**在 `~/.config/backlink-publisher/` 写 SECURITY-NOTE.md（位置自毁 + 同 UID 可伪造） |

## Documentation / Operational Notes

- 新增 `docs/operations/channel-binding.md`（Unit 7）覆盖：
  - playwright install chromium 一次性步骤
  - 操作演示（截图或步骤列表）
  - 失效后重绑流程
  - 凭据文件位置 + 0600 + 同 UID 进程可读 v1 风险 + 同 UID 进程可驱动 webui binding v1 风险
  - **v1 已知限制**：bind-channel 写的 medium/blogger storage_state 文件在 v1 暂未被 adapter 消费；Medium/Blogger 失效检测仍走 OAuth 路径
  - **Time Machine / iCloud / rsync 排除建议**（用 `tmutil addexclusion ~/.config/backlink-publisher` 命令示例，但提醒用户自行决定）
  - **不要 `gunicorn -w >1` / `uwsgi --processes >1`** 说明（v1 不支持多进程 WSGI）
  - **Flask 必须 bind 127.0.0.1**（启动断言会拦非 loopback bind）
  - Flask 重启后兜底 `pkill -f bind-channel` 清孤儿进程
  - 卸载：删 `<channel>-state.json` + `channel_status_store` migration
  - TTL 期望（实测 velog ≥24h，medium/blogger 视 cookie 政策可能更长）
  - Blogger blast radius 警告（Google 全域 cookies）
- velog 退场触点已列在 §Risks 表内（删 6 处）；不再单独成 checklist 文件
- 不主动写 SECURITY-NOTE.md 到 `~/.config/backlink-publisher/`（位置自毁 + 同 UID 可伪造；ops 文档 + UI banner 已覆盖）
- `AGENTS.md` 加 "Channel binding" 小节指向 ops 文档与 `bind-channel` CLI
- `docs/plans/2026-05-18-012-feat-velog-adapter-plan.md` 加 amendment 块：`velog-login` 由 `bind-channel --channel velog` alias 实现，路径不变；`AuthExpiredError(DependencyError)` 与 plan-012 现有 `DependencyError` raise 收敛
- 不修改 README.md（webui 章节已存在，binding 是 webui 内嵌功能不需要 top-level 章节）
- 不修改 CI workflow（mock 路线，无 playwright install 步骤）
- 运维侧：用户第一次启动 webui 后点绑定前，需要在 terminal 跑过一次 `playwright install chromium`；ops 文档要在显眼位置写明。Unit 5 的 `_probe_playwright()` UI banner 是 first-line UX 提醒

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-19-settings-browser-binding-requirements.md](../brainstorms/2026-05-19-settings-browser-binding-requirements.md)
- **Adjacent locked plan:** [docs/plans/2026-05-18-012-feat-velog-adapter-plan.md](2026-05-18-012-feat-velog-adapter-plan.md) — `velog-login` 契约源
- **Solutions referenced:**
  - `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md`
  - `docs/solutions/best-practices/recon-log-level-for-always-on-signals-2026-05-15.md`
  - `docs/solutions/best-practices/standalone-page-vs-retrofit-webui-2026-05-15.md`
  - `docs/solutions/best-practices/tests-coupled-to-operator-config-state-2026-05-18.md`
  - `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md`
- **Spike:** `docs/spikes/2026-05-18-velog-phase0.md`（TTL / 社交登录 / cookie 名）
- **Code anchors:**
  - `src/backlink_publisher/_util/errors.py` — PipelineError family
  - `src/backlink_publisher/_util/logger.py` — `_SENSITIVE_KEYS`, RECON
  - `src/backlink_publisher/publishing/adapters/medium_browser.py` — Playwright import-guard 先例
  - `src/backlink_publisher/cli/publish_backlinks.py` — `except DependencyError`/`ExternalServiceError` catch chain 顺序
  - `src/backlink_publisher/config/loader.py:_config_dir` — 路径解析强契约
  - `src/backlink_publisher/config/tokens.py` — 0600 atomic 写盘惯例
  - `webui_store/base.py:JsonStore` — store 协议
  - `webui_app/routes/oauth.py` — channel 白名单 + flash 模式
  - `webui_app/helpers.py:_settings_context, _rewrite_cli_cmd` — context 聚合 + CLI 命令构造
  - `tests/test_cli_footprint.py:_run_regen_subprocess` — subprocess CLI 测试样板
  - `tests/test_adapter_medium_browser.py` — Playwright mock 样板
- **Deepening review agents（2026-05-19）：**
  - `architecture-strategist` — 5 finding：AuthExpiredError 改 DependencyError、recipe.resolve_output_path、reader-thread 加固、velog 退场 6 触点、共享事件常量契约
  - `security-sentinel` — 5 finding（2×P0 + 3×P1）：CSRF/DNS-rebind、channel 白名单 traversal、at-rest 威胁模型公开、RECON redactor 强化、Playwright context 加固 + navigation listener + 严格 dotted-suffix
  - `reliability-reviewer` — 4 finding：orphan Chromium + 进程组、cancel race + wait 拥有者、multi-worker 守门、store ↔ 文件 reconciliation
- **Document-review 二轮 reviewer（2026-05-19）：**
  - `coherence-reviewer` — 12 finding，主要 fixed via 8 个 auto-fixes（velog-login resolve_output_path 调用、RECON redactor 双正则、route converter 唯一、reconcile_on_load 改 eager、Playwright probe stop+lock、requirements traceability）
  - `feasibility-reviewer` — **P0 修正 Unit 6 前提**（医现状现 adapter 不读 storage_state 文件，瘦身为仅 OAuth 401 path）、Playwright probe 实现细节、reconcile_on_load 线程安全
  - `security-lens-reviewer` — **P0 新增**：webui 无用户级 auth 接受为 v1 风险 + Job ID IDOR fix（secrets.token_urlsafe + owner_session check）+ Flask bind loopback only 启动断言 + Popen env allowlist + Chromium ephemeral user-data-dir + mark_bound path 二次校验
  - `scope-guardian-reviewer` — **P1 简化**：删 `_bind/protocol.py`（→ EVENTS frozenset）+ 删 `_auth_state.load_storage_state`（配合 Unit 6 瘦身）+ 删 multi-worker 运行时检测（仅文档）+ 删 SECURITY-NOTE.md + 删 velog-retirement-checklist.md
  - `adversarial-document-reviewer` — premise pushback 已与用户决定保留（不 defer Unit 4+5）；AuthExpiredError 父类理由改诚实（"coordination 不 semantics"）
  - `design-lens-reviewer` — 加错误码→中文映射表 + Modal-vs-Inline 决定为 inline + Accessibility ARIA 契约 + Reopen-tab sessionStorage 恢复
