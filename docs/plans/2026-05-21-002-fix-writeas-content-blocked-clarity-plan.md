---
title: "fix: clarify Write.as contentisblocked policy rejection"
type: fix
status: completed
date: 2026-05-21
---

> **Ship status (2026-05-21)**: HTTP-adapter path (R2 + R4 partial) lands in
> this PR against `origin/main`. CDP-adapter path (R1 + R4 CDP tests) ships in
> PR #141 (Plan 001 chrome-cdp-multi-channel-publish) together with the
> `instant_web.py` Chrome backend it depends on. Two-adapter coverage is
> complete once Plan 001 lands.


# fix: clarify Write.as contentisblocked policy rejection

## Overview

Write.as 在判定内容触犯反垃圾策略时返回 HTTP 201（"accepted"）但 body 里 `data.id == 'contentisblocked'`、`slug` 为 `None`、`full_post_url` 为空。两个 Write.as adapter（CDP / HTTP API）都没识别这个 sentinel，分别走到通用 "returned no URL" / "returned no slug" 分支，operator 看到的报错把策略性内容拒绝伪装成网络故障或 collection/token 配置错。本计划只清晰化错误消息，让 publish 仍以 per-row drop + exit_code=3 收尾，不改路由、不改 taxonomy。

## Problem Frame

- 实际报错（来自 2026-05-21T04:09:45 run）：
  `service error: Write.as CDP publish failed: Write.as returned no URL (HTTP 201): {'code': 201, 'data': {'id': 'contentisblocked', 'slug': None, ..., 'full_post_url': ''}}`
- Operator 第一反应会去查 Chrome 会话 / 网络 / 模板（"no URL" 听起来像 parsing 错），实际根因是平台策略：seed 行里 anchor 指向 `51acgs.com`（漫画站），Write.as 主动屏蔽
- 同一形态响应也会经过 `WriteAsAPIAdapter`（HTTP token 路径）。它当前抛 "no slug — check collection_alias and ensure token has write permission" —— 同样把策略拒绝指向了错误方向
- Per-row drop 行为本身正确（`dropped: failed: 1` + exit_code=3）；只是消息没把根因 surface 出来

## Requirements Trace

- R1. CDP adapter 检测到 `data.id == 'contentisblocked'`（或 201 但 `slug`/`full_post_url` 同时为空且存在 `id` 标记）时，抛出明确指明"平台策略拒绝"的 `ExternalServiceError`，operator 一眼能读出根因 + 可操作建议
- R2. HTTP adapter 在同样响应形态下抛出同样语义的清晰错误（不再误指向 collection_alias / token 权限）
- R3. Per-row drop / exit_code=3 行为不变（不引入 halt-on-policy；不引入新错误基类）
- R4. 两个 adapter 都有回归测试，固化 contentisblocked → 明确错误这条路径

## Scope Boundaries

- 非目标：不引入新的错误类（如 `ContentPolicyError`）；不重构现有 taxonomy
- 非目标：不做 pre-flight 内容过滤 / 域名黑名单 / 跳平台
- 非目标：不动 `publish_backlinks.py` 的 reconciliation / exit_code 映射
- 非目标：不修改 `WriteAsCdpAdapter` 的 JS evaluate 逻辑或 `WriteAsAPIAdapter` 的请求构造
- 非目标：不动 `binding_status.HIDDEN_FROM_UI` 过滤层（PR #136 已经从 UI 退役 Write.as；本计划只针对 adapter 错误清晰化）

## Context & Research

### Relevant Code and Patterns

- **CDP adapter `_no URL` 分支**：`src/backlink_publisher/publishing/adapters/instant_web.py:270-278`，在 `result["parsed"]["data"]` 上提取 `url` / `full_post_url`，缺则抛 `ExternalServiceError(f"Write.as returned no URL (HTTP {result.get('status')}): {parsed}")`
- **HTTP adapter `_no slug` 分支**：`src/backlink_publisher/publishing/adapters/writeas.py:209-215`，在 `parsed["data"]` 上找 `slug`，缺则抛 `"Write.as POST returned no slug — check collection_alias and ensure token has write permission"`
- **错误 taxonomy（不动）**：`src/backlink_publisher/_util/errors.py`：`ExternalServiceError` 是合适的桶（per-row drop，exit_code=3）。`DependencyError` 用于 binding/token 失效（exit_code=3 但语义不同），不要用；`AuthExpiredError` 显式给 token 401 用，也不要复用
- **CLI 层错误传播**：`src/backlink_publisher/cli/publish_backlinks.py:546` 与 `:956` `except ExternalServiceError` → `_error_class(exc), f"service error: {exc}"` 写入 RECON drop。改了 adapter 的错误 message 即自然传到 operator 视野
- **既有"清晰 error message"先例**：HTTP adapter 自己在 `:194-198` 已经对 401 给了 actionable 修复提示（"re-login at write.as and re-save to writeas-token.json"）。新 blocked 分支沿用这个模板：症状 + 可操作建议
- **测试夹具结构**：`tests/test_adapter_writeas.py`（483 行）已有 `_writeas_response`（line 74，201 happy path）、`_http_status_response`（line 91，任意 status）helper。在同文件追加 blocked 用例最自然

### Institutional Learnings

- `feedback_hidden_from_ui_pattern_for_retiring_channels.md`：UI 已经退役 Write.as，但 adapter source 保留处理残留 seeds。本计划与该 pattern 兼容：不删 adapter，只让残留 seed 撞策略时报错更明白
- `feedback_grep_before_writing_brainstorm_plan_claims.md`：本计划所有路径 / 行号 / 错误形态都来自现场 grep + 现场响应 sample，无猜测

### External References

- 无；行为完全来自现场捕获到的真实 response。无需 framework / 外部文档

## Key Technical Decisions

- **不引入新错误类**：`ExternalServiceError` 已经是 per-row drop 的桶；仅靠 message 区分 operator 视野的根因即可。新增类会扩大 taxonomy 但 CLI 层不需要新路由，不值得这个成本（参考 PR #94 / #98 决策风格）
- **检测条件**：`isinstance(data.get("id"), str) and data["id"] == "contentisblocked"`，而非"slug 缺 + full_post_url 空"等结构推断。文档里 `contentisblocked` 是 Write.as 自己的稳定 sentinel；结构推断更宽容但易误判（合法 default-feed 响应在某些状态下也可能没 slug + 没 url）
- **错误消息形态**：固定开头 `Write.as rejected content as blocked by site policy (id=contentisblocked)`，便于 grep / 监控；尾部带 operator 提示"修改 anchor 策略 / 跳过 writeas / 检查 seed 触发关键词"
- **HTTP adapter 检测位置**：在 `parsed = resp.json()` 之后、`slug` 检查之前 (`writeas.py:209` 与 `:211` 之间)；保证特定信号优先于通用"no slug"误判
- **CDP adapter 检测位置**：在 `data = parsed.get("data") or {}` 之后、`url = ...` 提取之前 (`instant_web.py:273` 与 `:274` 之间)
- **保持 raise 不带 cause**：不需要 `from exc`，因为信号是已经解析的 dict 字段，不是异常链；与 `:194-198` 401 处理一致

## Open Questions

### Resolved During Planning

- Q：是否需要在 `_publish_gate` 层加 "policy-rejected" RECON 子字段？  
  A：否。`dropped.failed` 已经能 surface；publish_reconciliation 的 `dropped_ids.failed` 也已含 row id。CLI 不需要新分类
- Q：是否要同步加 pre-flight？  
  A：否（user 已确认）。本计划只清晰化 post-hoc 信号
- Q：检测代码是否抽公共 helper？  
  A：否。两 adapter 各只一处、5 行内，抽 helper 会牵涉新 module + import 循环风险；inline 更清晰

### Deferred to Implementation

- 错误消息的具体中英文措辞（保持英文与 codebase 其他 `ExternalServiceError` 一致；细节由实现时定）
- 是否在错误里 echo `data.id` 完整 sentinel（推荐：是，便于 future Write.as 变更 sentinel 时报警显形）

## Implementation Units

- [ ] **Unit 1: 在两个 Write.as adapter 加 contentisblocked 检测**

**Goal:** CDP / HTTP adapter 都先于通用 fail 分支识别 `data.id == 'contentisblocked'`，抛出指明"平台策略拒绝"+ 可操作建议的 `ExternalServiceError`

**Requirements:** R1, R2, R3

**Dependencies:** 无

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/instant_web.py`（CDP adapter，line 273 后插入检测）
- Modify: `src/backlink_publisher/publishing/adapters/writeas.py`（HTTP adapter，line 209 后插入检测）

**Approach:**
- 两处都在 `data = parsed.get("data") or {}` 之后立即做 `if isinstance(data.get("id"), str) and data["id"] == "contentisblocked": raise ExternalServiceError(...)`
- 错误 message 模板：`"Write.as rejected content as blocked by site policy (id=contentisblocked); review anchor URLs / payload body for terms triggering Write.as anti-spam, or drop the writeas target from this seed row"`
- 不带 `from exc` —— 信号是结构化字段不是异常
- 不在错误里 dump 整个 parsed dict（避免日志噪音；现场 sample 已表明 body 包含原文，无 PII 但冗长）—— message 简洁，operator 需要原文时可看上游 logger 已经打的 ERROR record

**Patterns to follow:**
- `writeas.py:194-198` 的 401 处理：症状 + 可操作建议的双段式 message
- `instant_web.py:286-289` 的 `except DependencyError: raise; except Exception:` 收敛模式不变

**Test scenarios:**
- Happy path（已有，无需新增）：201 + 有效 slug/url → `AdapterResult(status="published")`
- Error path — CDP blocked：`page.evaluate` 返回 `{"status": 201, "parsed": {"code": 201, "data": {"id": "contentisblocked", "slug": None, "full_post_url": ""}}}` → 抛 `ExternalServiceError`，message 包含 `"contentisblocked"` 与 `"site policy"`，不包含 `"no URL"`
- Error path — HTTP blocked：`requests.post` mock 返回 `status_code=201`，`resp.json()` 返回 `{"code": 201, "data": {"id": "contentisblocked", "slug": None, "full_post_url": ""}}` → 抛 `ExternalServiceError`，message 同上；不再含 `"no slug"` / `"collection_alias"` 字串（验证 misdiagnosis 已修）
- Edge case — id 存在但非 blocked sentinel（譬如未来 Write.as 新 sentinel `"contentisflagged"`）：跳过检测，落回通用 "no slug" / "no URL" 分支（防止过度泛化）
- Edge case — `data` 整体缺失或非 dict：现有 `parsed.get("data") or {}` 已 fail-safe，检测不报 KeyError
- Regression：现有 happy path / 401 / non-201 status / non-JSON 用例继续通过（验证插入位置没扰动已有分支）

**Verification:**
- 用 contentisblocked sample payload 直接调 `WriteAsCdpAdapter.publish` / `WriteAsAPIAdapter.publish` 在 unit test 里能看到 ExternalServiceError 携带新 message
- 全套 `tests/test_adapter_writeas.py` 与 `tests/test_writeas_banner.py` 全绿
- `pytest tests/` 全套通过（无 footprint regression / 无 sibling test 撞）

---

- [ ] **Unit 2: 回归测试固化 blocked → 清晰错误这条路径**

**Goal:** 在 `tests/test_adapter_writeas.py` 增加 CDP / HTTP 双路径的 blocked 用例，捕获 sentinel + message 关键词；future Write.as 协议改 sentinel 时 test 显形失败

**Requirements:** R4

**Dependencies:** Unit 1

**Files:**
- Modify: `tests/test_adapter_writeas.py`（在 `WriteAsAPIAdapter` 章节 + 新增 `WriteAsCdpAdapter` 章节追加用例）

**Approach:**
- 复用现有 `_http_status_response(201, body=...)` helper 喂 HTTP 路径
- CDP 路径：mock `_ChromeSession.open(...).evaluate(...)` 返回 contentisblocked dict（参考 instant_web.py CDP 测试是否已有 fixture；若无，新增最小 mock）
- 断言三件事：(a) 抛 `ExternalServiceError`；(b) message 含 `"contentisblocked"` 与 `"site policy"`；(c) message **不**含 misdiagnosis 关键词（HTTP: `"no slug"` / `"collection_alias"`；CDP: `"returned no URL"`）—— 这个负断言固化 misdiagnosis 已修
- 用 `pytest.raises(ExternalServiceError, match=...)` 而非手动 try/except，与文件其他用例一致

**Patterns to follow:**
- `tests/test_adapter_writeas.py` 现有 401 / non-JSON / no-slug 用例的 mock + raises 模式

**Test scenarios:**
- `test_writeas_api_blocked_content_raises_policy_error`：HTTP 路径，blocked sentinel → ExternalServiceError + 关键词正负断言
- `test_writeas_cdp_blocked_content_raises_policy_error`：CDP 路径，blocked sentinel → ExternalServiceError + 关键词正负断言
- 可选：`test_writeas_api_other_sentinel_falls_back_to_no_slug`：验证非 contentisblocked sentinel 不触发新分支，保护"不过度泛化"决策

**Verification:**
- `pytest tests/test_adapter_writeas.py -v` 全绿；新加用例都进
- 故意把 Unit 1 检测条件改成 `== "contentisbloked"`（typo）→ 新加用例必须 fail（验证测试真的在断言 sentinel，不是被宽松 match 蒙混）

## System-Wide Impact

- **Interaction graph:** 只动两个 adapter 的 publish 内部；`publishing/adapters/__init__.py` registry 不动；`schema.validate_publish_payload` 不动；publish CLI 错误处理 (`publish_backlinks.py:546`/`:956`) 不动
- **Error propagation:** `ExternalServiceError` → CLI per-row drop → RECON `dropped.failed += 1` → exit_code=3。和当前路径完全一致，只是 message 更明白
- **State lifecycle risks:** 无；adapter 是无状态调用，错误本来就 drop row
- **API surface parity:** Two writeas adapters 同步改，避免 CDP / HTTP 分支 message 不一致让 operator 二次困惑
- **Integration coverage:** Unit 2 双路径用例已覆盖；不需要 e2e
- **Unchanged invariants:** Exit code map、retry policy、`HIDDEN_FROM_UI` 过滤、binding-status dashboard 全部不动

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Write.as 未来改 sentinel 名（`contentisblocked` → 其他）后检测失效 | Unit 2 negative-case 用例与日志里 message 含原 sentinel 字串，便于第一时间发现；adapter 退回到通用 "no URL" / "no slug" 分支，行为不崩 |
| 误把合法 default-feed 响应（slug/url 都空但无 id）当 blocked 处理 | 检测严格用 `data.id == "contentisblocked"` 字符串相等，不基于结构推断；测试里有"非 sentinel 落通用分支"用例守住 |
| 错误消息变化触发其他模块 / 监控 grep 失败 | 当前两条 message（"returned no URL" / "no slug — check collection_alias"）通过 `grep -rn` 在 src/ 外仅出现在 docs/solutions/ 引述，无监控代码依赖 |
| Sibling worktree 并发改同一文件 | 落 commit 前先 `git status` + `git log -1 -- instant_web.py writeas.py`；目前 instant_web.py / writeas.py 没在 active worktree 列表 |

## Documentation / Operational Notes

- 实施完成后可在 `docs/solutions/` 写一条短 note："Write.as contentisblocked sentinel → adapter 抛 site-policy ExternalServiceError"，便于以后 grep；非必须
- 不需要 changelog / 不需要 user-facing docs；这是 internal 错误消息改进
- 不需要 schema / config 改动

## Sources & References

- Bug 现场 log（用户提供）：2026-05-21T04:09:45 publish-backlinks run_id=20260521T040940-f55d2b2f
- CDP adapter：`src/backlink_publisher/publishing/adapters/instant_web.py:226-291`
- HTTP adapter：`src/backlink_publisher/publishing/adapters/writeas.py:150-244`
- 错误 taxonomy：`src/backlink_publisher/_util/errors.py:45-54`
- CLI 错误处理：`src/backlink_publisher/cli/publish_backlinks.py:546`, `:956`
- 现有测试：`tests/test_adapter_writeas.py`（483 行）
- 相关 PR：#136 (Write.as UI retire, MERGED 2026-05-21), #111 (writeas dofollow, MERGED 2026-05-20)
- Memory：[[hidden-from-ui-pattern-for-retiring-channels]]
