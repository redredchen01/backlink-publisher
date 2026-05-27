---
title: "fix: Retire dead medium bespoke CSRF layer + reach origin-guard parity + harden tests"
type: fix
status: completed
date: 2026-05-27
deepened: 2026-05-27
origin: docs/brainstorms/2026-05-27-channel-binding-bug-sweep-requirements.md
---

# fix: Retire dead medium bespoke CSRF layer + reach origin-guard parity + harden tests

## Overview

medium_login 路由带一套自有 bespoke CSRF(`medium_csrf` session key + `_csrf_token` 字段 +
`@bp.before_request _csrf_check`)。但 app-level `_global_csrf_guard`(PR #143)在任何 blueprint
`before_request` **之前**就校验规范 `csrf_token` 并 `abort(403)`——所以 bespoke 的**拒绝分支在生产永不可达**
(它只在测试里全局守卫被禁时才执行)。其测试只 seed `medium_csrf`,单跑时 10 个 POST 测试全 403、从未进路由逻辑;
全套之所以转绿,纯因 sibling 测试把 `WTF_CSRF_ENABLED=False` 泄漏到模块级共享单例 `webui.app`。

更关键(规划期 security review 实查):`bind.py`/`url_verify.py` 的 POST 都带 `_refuse_when_allow_network()` +
`_check_bind_origin_or_abort()`(防 off-loopback / 跨源 / DNS-rebinding),但 **medium 一个都没有**。medium 这 3 个
POST 启 headed Chromium 并删除登录凭证 profile,属高影响端点。

本计划:(1) 退役死的 bespoke CSRF 层;(2) 给 3 个 medium POST 补 origin/allow-network 守卫,达成与 bind.py 的
**真 parity**(退役变成净安全提升,而非削减);(3) 把 medium 测试改为 seed 规范 `csrf_token` + 带 loopback Origin
header + fixture 强制 CSRF 启用,使覆盖**单跑稳健且顺序无关**。生产 medium 登录流程零回归(模板本就发规范
`csrf_token`;新守卫对 loopback 操作员放行)。

## Problem Frame

见 origin 文档 B1(已于干净 origin/main `77ff53b` 核实)。B2/B3 已由 PR #259 闭合,不在范围。
(see origin: docs/brainstorms/2026-05-27-channel-binding-bug-sweep-requirements.md)

## Requirements Trace

- R1. 退役 bespoke medium CSRF 层(路由代码 + 模板冗余 `_csrf_token` 隐藏域),统一靠 `_global_csrf_guard`。
- R2. 10 个 medium POST 测试单跑即绿、顺序无关(7 happy-path seed 规范 `csrf_token`;3 否定测试断言 403)。
- R3. 各分支断言真实行为(flash 类别恒断言;文案仅静态分支;尽量断言凭证/会话副作用)。
- R4. 退役验证:全局守卫覆盖 medium 所有 POST;repo 无 `medium_csrf`/`medium_csrf_token` 的**功能性**残留。
- R5. **(规划期 security review 新增)** 给 3 个 medium POST(launch/probe/clear)补 `_refuse_when_allow_network()` +
  `_check_bind_origin_or_abort()`,与 `bind.py` parity;退役后 medium 的防护是「全局 CSRF + origin/allow-network」双层,
  不弱于现状。
- R6. **(规划期 feasibility review 新增)** medium 测试 fixture 必须显式把 `webui.app.config` 的 `CSRF_ENABLED` 和
  `WTF_CSRF_ENABLED` 设为 `True` 并在 teardown 恢复,使 403 否定断言**不受 sibling 测试泄漏的 config 影响**(确定性)。

## Scope Boundaries

- 仅动 `webui_app/routes/medium_login.py` + `webui_app/templates/_settings_channel_medium.html` +
  `tests/test_medium_login_routes.py`。
- 不改 medium 发布/登录业务逻辑(启浏览器/删 profile 的代码不动)。
- 不做 B2/B3(已由 #259 闭合)。

## Follow-up Work (明确不在本计划范围)

- **全局 `webui.app` 模块级 config 污染根治**:很多 webui 测试文件(`test_history_bulk_routes.py:18` 等)把
  `WTF_CSRF_ENABLED=False` 设到共享单例且不恢复——这是 medium 全套"假绿"的根因,也是更广的测试隔离隐患。本计划用
  R6(medium fixture 自己强制 + 恢复 CSRF 状态)局部免疫,但**全局根治留作独立后续一轮**(per-test set/restore 模式)。
  security 备注:根因是 sibling 把 CSRF enforcement 静默关闭到全 app——后续轮须确认无生产代码路径能同样关闭。

## Context & Research

### Relevant Code and Patterns

- `webui_app/routes/medium_login.py:26-60` — 退役目标:`_CSRF_COOKIE`(26)/`_CSRF_FIELD`(27)、`_ensure_csrf_token`
  (32-36)、`_validate_csrf`(39-45)、`@bp.before_request _csrf_check`(47-50+)、context processor
  `{"medium_csrf_token": _ensure_csrf_token}`(57-60);退役后 `import secrets`(11)变 unused → 一并删。
- `webui_app/routes/bind.py:57-58,107-108,170-171` — **R5 要照搬的形态**:每个 POST handler 顶部
  `_refuse_when_allow_network()` 然后 `_check_bind_origin_or_abort()`;import 自 `..helpers.security`(bind.py:39,41)。
- `webui_app/helpers/security.py` — `_check_bind_origin_or_abort`(121)、`_refuse_when_allow_network`(161)、
  `_check_csrf_or_abort`(认 form `csrf_token`/`X-CSRFToken`,校验 `session['csrf_token']`);`_global_csrf_guard`
  在 `webui_app/__init__.py:170-184`,`inject_csrf_token` context processor(143-156)负责 seed `session['csrf_token']`。
- `webui_app/templates/_settings_channel_medium.html` — launch/probe/clear 三个表单各有 `csrf_token`(保留)+
  `_csrf_token`(行 32/39/48,删除)隐藏域。
- `tests/test_webui_bind_routes.py` — **Unit 2 的模板**:`_seed_csrf`(61,`sess["csrf_token"]=...`)+
  `_bind_origin_headers()`(73,`{"Origin": f"http://127.0.0.1:{_FLASK_PORT}"}`)。medium 测试要复用这两个模式。
- `tests/test_medium_login_routes.py` — `client` fixture(37-50,已 monkeypatch.setenv 隔离 config)、`csrf_client`
  (86-90)、7 happy-path(`TestMediumLoginRoutes` 5 个 + `TestPWErrorRouteIntegration` 2 个)、`TestCSRFProtection`
  3 否定。其余非-POST 的直接函数单元测试类不受 CSRF 影响、不在改写范围。happy-path POST 的 `data=` 字典在行
  230/241/250/261/271/409/426。

### Institutional Learnings

- `docs/solutions/test-failures/tests-coupled-to-operator-config-state-2026-05-18.md`(PR #43)、
  `best-practices/never-smoke-test-real-save-endpoints-2026-05-19.md` — config 隔离纪律(medium `client` fixture 已合规)。
- `[[feedback_global_csrf_guard_makes_blueprint_csrf_dead_code]]`(本计划根因)、`[[reference_webui_csrf_architecture]]`。

## Key Technical Decisions

- **退役 bespoke 层而非保留**:拒绝分支生产永不可达;medium 是唯一带 bespoke 双 token 的路由,保留=copy-forward 陷阱。
- **退役同时补 origin/allow-network 守卫(达真 parity)**:否则退役会把高影响端点降到「仅 CSRF 单层」——比现状更弱。
  补 `_check_bind_origin_or_abort()`+`_refuse_when_allow_network()` 使退役成为净安全提升。
- **测试 seed 规范 `csrf_token` + fixture 强制 CSRF 启用**,而非 `WTF_CSRF_ENABLED=False` opt-out:opt-out 既绕过守卫
  不测真实路径,又正是泄漏到共享单例造成"假绿"的根源;seed token + 强制启用才能确定性、稳健、真实覆盖。
- **prod 改动(Unit 1)与测试改写(Unit 2)同一 PR**:bespoke 层在时若先把 happy-path 去掉 `_csrf_token`,bespoke
  `_csrf_check` 会因缺字段先拒,测试无法转绿。故退役与测试改写一起落地,Unit 1 先于 Unit 2。

## Open Questions

### Resolved During Planning

- 真凶是否 B2(env 泄漏)? — 否,B2/B3 由 #259 修复;只剩 B1。
- 是否需找出"污染源" sibling 测试? — 否;R6(fixture 强制 CSRF 状态)+ seed 规范 token 后,medium 测试不再依赖
  全局守卫被关闭,顺序无关。全局污染根治列 Follow-up。
- medium 退役后是否会降低防护? — 会(若不补 origin 守卫);R5 补守卫达 parity,化解。

### Deferred to Implementation

- 各分支具体 flash 类别/文案取值 — 实现期对照 `medium_login.py` 逐分支确定(R3,下方给初步映射)。

## Implementation Units

> 依赖链:Unit 1(prod)→ Unit 2(tests),同一 PR 落地。仅 2 个单元、线性依赖,无需依赖图。

- [ ] **Unit 1: 硬化 medium 认证姿态(退役死 CSRF 层 + 补 origin/allow-network 守卫)**

**Goal:** 删 medium 不可达的 bespoke CSRF 机制 + 模板冗余 `_csrf_token`;给 3 个 medium POST 补
`_refuse_when_allow_network()`+`_check_bind_origin_or_abort()`,与 bind.py parity。

**Requirements:** R1, R4, R5

**Dependencies:** None

**Files:**
- Modify: `webui_app/routes/medium_login.py`(删 `_CSRF_COOKIE`/`_CSRF_FIELD`/`_ensure_csrf_token`/`_validate_csrf`/
  `@bp.before_request _csrf_check`/`medium_csrf_token` context processor + unused `import secrets`;在 launch/probe/clear
  三个 handler 顶部加 `_refuse_when_allow_network()` 然后 `_check_bind_origin_or_abort()`,import 自 `..helpers.security`)
- Modify: `webui_app/templates/_settings_channel_medium.html`(删行 32/39/48 的 `_csrf_token` 隐藏域;保留 `csrf_token`)
- Test: 由 Unit 2 覆盖

**Approach:**
- 照搬 `bind.py:57-58` 的守卫顺序(先 `_refuse_when_allow_network()` 再 `_check_bind_origin_or_abort()`)。
- 确认 launch/probe/clear 均 POST、endpoint 不以 `oauth_callback` 结尾(不触发全局守卫豁免)。
- R4 grep:`grep -rn "medium_csrf\|medium_csrf_token"`。注意会**误中** `tests/test_webui_routes_oauth.py:22` 的
  docstring(纯注释,非功能引用)——要么把它一并更新,要么 grep 排除注释;别误判为残留。功能性残留须为零。

**Patterns to follow:** `webui_app/routes/bind.py` 的双守卫;退役后 medium 与其他渠道路由形态一致。

**Test scenarios:** 行为断言全在 Unit 2;此单元的代码级验证见下 Verification。

**Verification:**
- WebUI 启动正常;`/settings` 渲染含 medium 卡片无 Jinja `UndefinedError`(模板冒烟,Unit 2 加正向断言)。
- `grep` 全仓无 `medium_csrf`/`medium_csrf_token` **功能性**残留(docstring 命中已知并处理)。
- 三个 medium POST 顶部均有 `_refuse_when_allow_network()`+`_check_bind_origin_or_abort()`,与 bind.py 一致。

- [ ] **Unit 2: medium 测试改 seed 规范 csrf_token + Origin header + 强制 CSRF 启用,单跑稳健**

**Goal:** `test_medium_login_routes.py` 独立运行即全绿、顺序无关,真实覆盖退役+加守卫后的各分支。

**Requirements:** R2, R3, R6

**Dependencies:** Unit 1

**Files:**
- Modify: `tests/test_medium_login_routes.py`

**Approach:**
- **R6 确定性**:在 `client`(或 `csrf_client`)fixture 里显式 `webui.app.config["CSRF_ENABLED"]=True` +
  `webui.app.config["WTF_CSRF_ENABLED"]=True`,并在 teardown 恢复原值(防 sibling 泄漏的 `False` 让 403 否定测试失效)。
- **canonical token**:`csrf_client` 改 seed `sess["csrf_token"]`(学 `test_webui_bind_routes.py:61` `_seed_csrf`),
  不再 seed `medium_csrf`。
- **Origin header**:7 happy-path 的每个 POST 加 `headers={"Origin": f"http://127.0.0.1:{_FLASK_PORT}"}`(复用
  `test_webui_bind_routes.py:73` `_bind_origin_headers` 模式),以过新的 `_check_bind_origin_or_abort()`。
- **改全部 7 个 `data=` 字典**(行 230/241/250/261/271/409/426):把 `_csrf_token` 改为 `csrf_token`(或移到
  `X-CSRFToken` header)——只改 fixture key 不改 data 字典会让 happy-path 仍 403。
- **3 否定测试**(`TestCSRFProtection`):带 Origin header 但**不带 csrf token** → 断言 **403**(全局守卫);
  重命名为 `*_without_token_forbidden`。
- **新增 origin 否定测试**:带合法 csrf token 但 **Origin 缺失/非 loopback** → 断言 403(新 origin 守卫,R5)。
- **R3 断言**:flash 类别恒断言(从 `flash_type=` querystring,先 `urllib.parse.unquote(Location)`,沿用文件现有
  做法);文案仅对静态消息分支断言;对 clear 断言 Chromium profile 目录被删、probe 不轻信 logged-in 等副作用。
- **R4 正向渲染断言**:render `/settings`(或 medium 卡片),断言每个 medium 表单的 `name="csrf_token"` 隐藏域
  **非空**,再 POST 该值断言**不** 403——证明全局 `inject_csrf_token` 在 bespoke processor 删除后仍可靠 bootstrap
  `session['csrf_token']`。

**初步 flash 映射(R3,实现期对照路由确认)**:probe(logged_in)→`info`;probe(not logged_in)→`info`;
launch 成功→`success`/跳转;clear 成功→`success`+profile 删除;no-playwright→`warning`;PWError/closed-window→
launch `danger`/probe `warning`。

**Execution note:** 与 Unit 1 同一 PR;可 test-first——先按退役+加守卫后的契约改测试(会红),再随 Unit 1 落地转绿。
注意 CI 用 pytest-randomly(sibling 见 `-p no:randomly`)→顺序随机,R6 的 fixture 强制 CSRF 状态是 403 测试不 flaky 的前提。

**Patterns to follow:** `tests/test_webui_bind_routes.py`(`_seed_csrf` + `_bind_origin_headers` + per-POST headers)。

**Test scenarios:**
- Happy path:probe(logged_in)→info「已登录」;probe(not logged_in)→info「未登录」;launch 成功→success/跳转;
  clear→success flash。各带规范 `csrf_token` + loopback Origin。
- Edge case:no-playwright 时 probe/launch→warning flash。
- Error path:(a) 带 Origin、无 csrf token → 403(全局 CSRF 守卫);(b) 带 csrf token、Origin 缺失/非 loopback →
  403(origin 守卫,R5);(c) closed-window(PWError)→ launch danger / probe warning。
- Integration:clear 调用后磁盘 medium Chromium profile 目录确实不存在(副作用断言);render `/settings` 后 medium 表单
  `csrf_token` 非空且用它 POST 不被 403(R4 正向证明)。

**Verification:**
- `pytest tests/test_medium_login_routes.py` **单独运行**全绿,且重复多次/随机顺序稳定(R6 生效)。
- 各分支断言 flash 类别 + 关键副作用,而非只断言状态码。

## System-Wide Impact

- **Interaction graph:** 删 `medium_csrf_token()` jinja global + context processor——功能消费者仅
  `_settings_channel_medium.html`(另有 oauth 测试 docstring 注释,已知);删 `@bp.before_request _csrf_check`——仅 medium bp。
- **API surface parity:** 退役 + 补 origin/allow-network 守卫后,medium POST 防护与 `bind.py` **真正一致**
  (全局 CSRF + origin + allow-network),不再是初稿误称的"已对齐"。
- **Error propagation:** medium POST 的 CSRF 失败统一走全局守卫 403;跨源/off-loopback 走 origin/allow-network 守卫 403;
  不再有 medium 专属 302 danger。
- **State lifecycle risks:** 不动 clear 删 profile / launch 启浏览器的代码;新增守卫只前置拦截,不改这些副作用本身。
- **Unchanged invariants:** `_global_csrf_guard`/`inject_csrf_token` 行为不变;模板继续发规范 `csrf_token`;loopback
  操作员的 medium 登录流程零回归(新 origin 守卫对 loopback 放行)。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 退役后 403 否定测试顺序依赖(sibling 泄漏 `WTF_CSRF_ENABLED=False`)| R6:medium fixture 显式强制 CSRF 启用 + 恢复;新增 origin 否定测试不依赖 CSRF 状态 |
| 退役把高影响 medium POST 降为单层防护 | R5:补 `_check_bind_origin_or_abort()`+`_refuse_when_allow_network()` 达 bind.py parity |
| 漏改某个 happy-path `data=` 字典 → 仍 403 | Unit 2 明列 7 处行号(230/241/250/261/271/409/426)全改 |
| 遗漏 `medium_csrf_token()` 消费者 → Jinja `UndefinedError` | R4 grep(排除 oauth docstring 命中)+ Unit 2 正向渲染断言 |
| 加 origin 守卫后 happy-path 测试缺 Origin header → 误 403 | Unit 2 每个 POST 加 `_bind_origin_headers` 式 Origin |
| Unit 1 单独落地使 CI 红 | 与 Unit 2 同一 PR;Unit1 先于 Unit2 |
| 并发 session 推进 main(本轮已遇文档被清)| 落地前 `git fetch` + `rev-parse HEAD vs origin/main`;spot-check medium 两文件未被在途分支改动 |

## Documentation / Operational Notes

- 无 operator 可见行为变化(loopback 操作员 medium 登录流程不变),无需用户文档更新。
- 落地后在 `[[reference_webui_csrf_architecture]]` 记一笔:medium 不再有 bespoke CSRF,已与 bind.py 同获 origin/
  allow-network 守卫。

## Sources & References

- **Origin document:** docs/brainstorms/2026-05-27-channel-binding-bug-sweep-requirements.md
- Related code: `webui_app/routes/medium_login.py`, `webui_app/routes/bind.py:57-58`,
  `webui_app/helpers/security.py:121,161`, `webui_app/__init__.py:170-184`,
  `webui_app/templates/_settings_channel_medium.html`, `tests/test_medium_login_routes.py`,
  `tests/test_webui_bind_routes.py:61,73`
- Related PRs: #143(全局 CSRF 守卫)、#257(U3/U4)、#259(B2/B3 修复)
- Memory: `[[feedback_global_csrf_guard_makes_blueprint_csrf_dead_code]]`, `[[reference_webui_csrf_architecture]]`
