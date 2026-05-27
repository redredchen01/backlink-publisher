---
date: 2026-05-27
topic: channel-binding-bug-sweep
---

# 分发渠道 — 绑定/认证流程 Bug 扫描与修复

> **状态修订(2026-05-27,ce:plan 期核实最新 origin/main `77ff53b`)**:本文初稿扫描跑在 stale base 上,
> 三项发现里两项已被并发修复:
> - **B2 已由 PR #259(`f9a0383`)修复**——真凶=`test_channel_bind_save.py`(#257 在 stale base 引入)的
>   `test_token_save_creates_0600_file`/`test_token_clear_unlinks_file` 用 `os.environ[]=` + 裸 `del`,污染 6 个
>   下游测试;改 `monkeypatch.setenv` 已修。初稿点名的两个嫌疑(inject_platforms/test_config)确为红鲱鱼。**B2 无需再做。**
> - **B3 已由 PR #259 修复**——cnblogs stale 测试已删。**B3 无需再做。**
> - **仅 B1 待做**,且框架修正(见下)。

## Problem Frame

对 28 个分发渠道的**绑定/认证流程**做了一轮 bug 扫描(跑测试 + 审代码)。本文档现仅承载唯一未解项 **B1**
(B2/B3 已由 PR #259 闭合)。

**B1 — medium 浏览器登录:死 CSRF 层 + 脆弱的污染依赖测试覆盖**(已于干净 origin/main `77ff53b` 核实):

- `_global_csrf_guard`(PR #143,app-level `before_request`)对所有 POST 校验规范 `csrf_token`(session key)+
  `X-CSRFToken`/form `csrf_token`,失败即 `abort(403)`,且在任何 blueprint 的 `before_request` **之前**跑。
- medium_login 路由另带一套 bespoke CSRF(`medium_login.py:26-50`:`_CSRF_COOKIE="medium_csrf"` +
  `_CSRF_FIELD="_csrf_token"` + `_validate_csrf` + `@bp.before_request _csrf_check`)。因全局守卫先跑,bespoke
  `_csrf_check` 的拒绝分支在生产**永不可达=死代码**;medium 是唯一带 bespoke 双 token 的路由 → copy-forward 陷阱。
- `test_medium_login_routes.py` 的 `csrf_client` 只 seed `medium_csrf`、发 `_csrf_token`,**不** seed 规范
  `csrf_token`。后果(实测):
  - **单跑 → 10 个 POST 测试 403**(全局守卫挡在门口,从未进路由逻辑)。
  - **全套 → 转绿**,仅因 sibling 测试(如 `test_history_bulk_routes.py`)把 `WTF_CSRF_ENABLED=False` 设到
    **模块级共享单例 `webui.app`** 且不恢复 → 全局守卫被泄漏的 config 关闭。即覆盖**脆弱、依赖测试顺序+跨文件
    config 污染**(已验证:`test_history_bulk_routes.py` 先跑 → medium 全过;medium 单跑 → 10 失败)。
- 生产**不坏**:模板 `_settings_channel_medium.html` 同时发 `csrf_token` + `_csrf_token`。

## Requirements

**B1 — 退役 medium 死 CSRF 层 + 让测试覆盖稳健**
- R1. **退役 bespoke medium CSRF 层**:删 `medium_login.py` 的 `_csrf_check` before_request、`_validate_csrf`、
  `_CSRF_COOKIE`/`_CSRF_FIELD`、`medium_csrf_token()` jinja global 及其 context processor;删
  `_settings_channel_medium.html` 里冗余的 `_csrf_token` 隐藏域(保留 `csrf_token`)。统一由 `_global_csrf_guard`
  防护(单一 CSRF 真相源)。
- R2. 修复 10 个 medium_login POST 测试,使其**单跑即绿、不依赖跨文件 config 污染**:
  - 7 个 happy-path(`TestMediumLoginRoutes` + `TestPWErrorRouteIntegration`)——改为 seed 规范 `csrf_token`
    并提交(学 `tests/test_webui_bind_routes.py:61` 的 `_seed_csrf`),不再 seed `medium_csrf`/发 `_csrf_token`。
  - 3 个 no-token 否定测试(`TestCSRFProtection::test_{launch,probe,clear}_without_token_redirects_danger`)
    ——断言改为**期望 403**(全局守卫为拒绝契约);退役后原 bespoke 302 danger UX 不再存在,这是预期。
- R3. medium 各分支断言真实行为:**flash 类别恒断言**;文案仅对**静态消息分支**断言(错误分支把原始异常文本插进
  `flash_msg` querystring,断言文案会脆;测试须先 `urllib.parse.unquote` Location);并尽量断言**凭证/会话副作用**
  (clear 真删 Chromium profile;probe 不轻信 logged-in)而非只断言 flash 文案。
- R4. 退役验证:`_global_csrf_guard` 已覆盖 medium 所有 POST 端点(launch/probe/clear,无 oauth_callback 豁免命中),
  退役后无端点失去 CSRF 防护;全仓 `grep` 确认无其他消费者引用 `medium_csrf_token()` / `medium_csrf`。

## Success Criteria
- `pytest tests/test_medium_login_routes.py` **单独运行即全绿**(不依赖其他文件先污染 `webui.app.config`)。
- medium 各分支有真实行为断言(类别/凭证副作用),不再只停在状态码。
- 全仓无 `medium_csrf`/`medium_csrf_token` 残留;medium POST 端点仍全部受 `_global_csrf_guard` 防护;
  CSRF 由双层收敛为单一全局源。

## Scope Boundaries
- **B1 是小而有界的生产改动**:删 medium bespoke CSRF 残骸 + 模板冗余隐藏域,不改 medium 发布/登录业务逻辑、
  不碰其他渠道 CSRF。
- **规划期 security review 追加(见 plan R5)**:退役同时给 3 个 medium POST 补 `_check_bind_origin_or_abort()` +
  `_refuse_when_allow_network()`,与 `bind.py` parity——否则退役会把高影响端点降为单层防护。此为净安全提升,非业务逻辑改动。
- **不做 B2/B3**:已由 #259 闭合。
- **不在本轮根治全局的 `webui.app` 共享 config 污染**(见 Deferred):很多 webui 测试文件把 `WTF_CSRF_ENABLED=False`/
  其他 config 设到模块级单例且不恢复——这是 medium 全套转绿的根因,也是更广的测试隔离隐患,但范围远超 B1。

## Key Decisions
- **退役 bespoke 层而非保留**(reviewer 实查驱动):`_csrf_check` 在全局守卫后跑→拒绝分支生产永不可达;保留=
  下个绑定贡献者照抄出同类脆弱覆盖。退役收敛单一 CSRF 源、移除死代码,改动小、风险低。
- **测试改为 seed 规范 `csrf_token` 而非 `CSRF_ENABLED=False` opt-out**:后者只会绕过守卫、继续不测真实 CSRF
  路径;seed 规范 token 才是稳健的真实覆盖。

## Dependencies / Assumptions
- 基于 origin/main `77ff53b`(含 #257/#258/#259/#256)。B1 改 `medium_login.py` + `_settings_channel_medium.html`,
  origin/main 现无其他在途分支动这两文件(#257 的 U3/U4 改的是 channel_bind_save 相关文件)。
- 测试在干净 worktree 跑(sibling worktree 用 `PYTHONPATH=src`);注意 main 工作树常被并发 session 推进。

## Outstanding Questions

### Resolve Before Planning
- (无 — 范围已定为 B1。)

### Deferred to Implementation
- [Affects R3][Technical] medium 各分支具体 flash 文案/类别取值,实现期对照 `medium_login.py` 路由逐分支确定。
- [Affects 全局][Needs research] 后续一轮:根治 webui 测试对模块级共享 `webui.app.config` 的污染(统一用
  per-test fixture set/restore CSRF 等 config),消除"单跑红/全套绿"这一类掩盖。范围独立于 B1。

## Next Steps
→ `/ce:plan`(本轮已在进行)
