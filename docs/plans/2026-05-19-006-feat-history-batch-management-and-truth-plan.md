---
title: "feat: 草稿 & 历史页批量管理 + 发布状态真实性修复"
type: feat
status: completed
date: 2026-05-19
---

# 草稿 & 历史页批量管理 + 发布状态真实性修复

## Overview

`/ce:publish`（WebUI `historyPanel`）目前两个问题叠加：

1. **状态失真**：历史记录里很多条目挂着 ✓「已发布」绿标，但点开外站链接是 404 / 占位草稿 / 标题对不上 — 用户实际遇到"看起来已发布但其实都是失败"。
2. **逐条管理**：每一条都要点 → 确认 → 提交一次表单，没有勾选 / 全选 / 按状态批量清理。失败堆积越多，清理成本越线性放大。

本计划同时处理 **根因**（状态真实性）和 **UI 痛点**（批量操作），并加入一条"重新核实"链路，让用户能把历史里那些假成功识别并归位。

## Problem Frame

用户原文："現在每一個點擊才能管理 需要完善優化這個隊列管理工具 能夠有批量處理的功能 例如勾選後 可以批量刪除失敗的 因為現在看起來已經發布 但是其實都是失敗的狀況 請詳細分析根本狀況"。

### 根本状况（代码层面，已通过阅读源码确认）

**1. `publish-backlinks` CLI 的输出语义**（`src/backlink_publisher/cli/publish_backlinks.py:434-568`）：

- 每行 `payload` 走完 adapter 后 `outputs.append(...)`，每条带 `status` + `error`。
- 行级 `status` 取值集合：`drafted`、`published`、`drafted_unverified`、`published_unverified`、`skipped_unreachable`、adapter-specific 失败 status。
- 行级 `error`：成功为 `None`，失败为字符串（含 `service error: ...` / `unexpected error: ...`）。
- stdout 只写 `successful = [r for r in outputs if r.get("error") is None]`，**`_unverified` 行也算 "successful" 被写到 stdout**。
- 行级失败 → stderr 写 `publish failed:` + `SystemExit(4)`；零成功 → `emit_error("no payloads were published")` + exit 5；含 unverified → 同样写 stderr + exit 5。

**2. `webui_app/helpers.py:run_pipe`**（line 724-737）：

- `returncode != 0` → `raise Exception(result.stderr)`。**stdout 被无条件丢弃**。
- 这意味着 1 行成功 + 1 行失败 + exit=4 的部分成功批次，WebUI 拿不到那行成功，但 _publish_real / batch 又**靠拿到 stdout 行才决定 success**。这条不是当前主 bug，但是后续修真值时的重要边界。

**3. WebUI 三处写历史的代码路径**（都不查 per-row `status` / `error`，只查 `error is not None`）：

| 路径 | 文件 | 写入逻辑 |
|---|---|---|
| 草稿排程发布（APScheduler 后台 job） | `webui_app/scheduler.py:99-120` | exception 进 catch → `status='failed'`；非 exception → **硬写 `'drafted' if mode=='draft' else 'published'`**，完全无视 stdout 行的 `status` 字段 |
| 手动「正式发布」 | `webui_app/routes/batch.py:171-198` | 同上：catch → failed；成功路径硬写 `status='success'`，且写 `article_urls = [r.get('published_url') or r.get('draft_url')]` — 即便 published_url 为空，也把空串塞进列表 |
| 批量多 URL 发布 | `webui_app/routes/batch.py:104-141` | 逐 URL 用 `result_by_url[url]`，**只判 `r.get('error')`**，不看 `status`；最终历史条目还是按 publish_mode 硬写 `'drafted'` / `'published'` |

**4. _unverified 是最大的假成功来源**：CLI 把 verify 失败的行 status 改成 `xxx_unverified` 但 error 保持 `None`，**于是 WebUI 三处都把它当 success 写历史，badge 显示绿勾"已发布"**。从用户视角，链接打开是 404 / 缺标题 / 空 body，但 UI 说成功。

**5. 占位 URL**：部分 adapter（如 Medium browser-bind 走 OAuth 失败 fallback、Telegraph 节流后）在 result.published_url 为空时仍可能返回非空 draft_url（或反之）；UI 用 `published_url or draft_url` 取第一个非空值塞进 article_urls，没有"二者皆空就算失败"的兜底。

**6. 没有"重新核实"出口**：`linkcheck/verify.py:verify_published()` 已有现成的"GET 外站 HTML → 检查 title + anchor"primitive（max_wait=30s 轮询），但 webui 层从未调用 — 历史里写完就再也没机会自动校正。

### UI 痛状

- 草稿队列每条要单独 `<form>` 提交才能 schedule/publish-now/cancel/delete；勾选/全选/批量没有。
- 历史每条同样：reuse / update-status / delete 三个独立表单。
- 没有"按状态批量删失败"、"批量重试"、"批量重新核实"快捷。
- 过滤 chips（`drafted/published/failed/all` + 平台）已经在 2026-05-18-010 上线，但**仅过滤显示，不能批量作用于过滤结果**。

## Requirements Trace

- **R1.** 历史条目的 `status` 必须精确反映 CLI per-row 实际 `status`（保留 `_unverified` 后缀），不能再硬写 `'drafted'/'published'`。
- **R2.** 历史/草稿 UI 必须能区分显示 ✓已发布 / ⚠ 未核实 / ✗ 失败 / 草稿 四类状态，且 unverified 单独 chip 可筛选。
- **R3.** 草稿页支持：勾选 + 全选 + 批量删除、批量发布、批量取消排程。
- **R4.** 历史页支持：勾选 + 全选 + 按当前过滤范围全选、批量删除、批量重新核实。
- **R5.** "批量重新核实"对选中历史条目逐个 GET `article_urls`，通过 `verify_published()` 判定 title+anchor 是否真实存在；不存在 → 改 status 为 `failed`，写 `verify_error` 字段；存在 → 升级为 `published`/`drafted`。
- **R6.** 用户操作"批量删除失败"必须一键完成（不需要先勾选）：单独 shortcut chip。
- **R7.** 现有逐条操作 / 现有过滤 chips / 现有 history 显示 100 条上限不破坏；批量操作走新路由不改老的（兼容）。

### 成功标准

- 在含 ≥1 条假成功（`_unverified`）的历史里，刷新页面后该条应显示 ⚠未核实 badge，不再是绿色 ✓。
- 用户在历史页点"按状态批量删失败"，所有 status=failed 一次性删除，无需逐条确认。
- 批量重新核实跑完后，外站 404 的条目自动从✓变 ✗。

## Scope Boundaries

**包含**：
- `webui_app/routes/drafts.py`、`history.py` 加批量路由
- `webui_app/templates/index.html` 草稿+历史区块改造（checkbox + bulk action bar）
- `webui_app/scheduler.py:_publish_draft_job` + `routes/batch.py:_batch / _publish_real` 三处历史写入路径修真值
- 新增 `webui_app/services/recheck.py`（或 helper）封装 verify_published 调用
- `webui_store/history.py` 增 `bulk_delete(ids)` / `bulk_update(ids, **fields)` helpers
- 测试覆盖

**不包含（明确 Out-of-scope）**：
- `publish-backlinks` CLI 本体 / adapter 行为修改（行级 status 语义已是真实的，问题在 WebUI 层覆写）
- 改 `run_pipe` 在 returncode!=0 时保留 stdout（这是另一条隧道，影响批量 partial-success；本计划只在 history 写入层用 per-row error 判定，不动 run_pipe）
- "假成功"在 publish 时的事前防御（adapter-level verify hook）— 那是 plan-side 的事
- 历史/草稿持久化的格式迁移（保持现有 JSON 文件 schema，新字段为可选 `verify_*`）
- 国际化（Chinese-only label）

## Context & Research

### Relevant Code and Patterns

- `webui_app/routes/drafts.py` (110 行) — 5 个单条路由（save / schedule / publish-now / cancel / delete），都走 `_drafts_store.update_item` / `delete_item`
- `webui_app/routes/history.py` (63 行) — `ce_history`、`/delete`、`/update-status`、`/reuse`
- `webui_app/routes/batch.py:104-141, 171-198` — 历史写入硬覆盖 status 的两处
- `webui_app/scheduler.py:99-120` (`_publish_draft_job`) — 第三处硬覆盖
- `webui_store/drafts.py:DraftsStore.delete_item / update_item` — 现成 per-id helper 模式，bulk 版本可参照
- `webui_store/base.py:JsonStore.update(fn)` — `update_*` 都内部走 `update(lambda items: ...)`，bulk 也用同样模式
- `webui_app/templates/index.html:1233-1432` — 现 `historyPanel` 草稿+历史区块；过滤 chips 在 1345-1359（`data-filter-group="status"`）
- `webui_app/helpers.py:_parse_publish_results` — 已经返回 raw dict 列表，下游应保留 `status` 字段
- `src/backlink_publisher/linkcheck/verify.py:verify_published(url, title, required_link_urls, max_wait=30)` — 现成 primitive
- `src/backlink_publisher/cli/publish_backlinks.py:441-528` — output 行 schema 含 `status`/`error`/`title`/`adapter`/`published_url`/`draft_url`/`created_at`

### Institutional Learnings

- `docs/plans/2026-05-18-010-feat-history-filter-status-platform-plan.md` — 已落地的 chip 过滤；本计划在它上面叠批量操作，**不动现有 chip 逻辑**，只在它的 `data-status` / `data-platform` 属性基础上加 `data-id` 让 JS 收集当前可见条目。

- 历史写入硬覆盖 status 模式是 2026-05-15 telegraph adapter ship 后留下的（彼时 status 集合只有 `drafted`/`published`/`failed`），_unverified 是 2026-05-18 verify_publish 上线后新增的 status，但 WebUI 历史层没跟着扩展。属于"上游加了新状态，下游硬编码集合没扩"经典 drift（参考 `feedback_invert_drift_check_when_invariant_becomes_dynamic.md` 同类型）。

### External References

不需要。批量 UI 是常规 Bootstrap 5 checkbox + JS pattern，无 framework-specific 风险。

## Key Technical Decisions

1. **历史 status 写入改"透传 per-row status"**：三处 (`_publish_draft_job` / `_batch` / `_publish_real`) 改为遍历 `_parse_publish_results(stdout)`，**为每行单独 push 一条历史**（而不是合并成一条）。理由：(a) 现在合并条目丢了 per-row title / status / error；(b) `_unverified` 必须保留后缀；(c) 单行写入和后续"按 article_url 重新核实"才能 1:1 对齐。

   **Tradeoff**：历史条数会膨胀（一次批量 5 URL 现产生 1 条，改后产生 5 条）— 用现有 100 上限自动裁剪即可；用户实际是想看每个 URL 是否真发出去，合并条目反而隐藏了部分失败。

2. **历史 status 集合扩展**：增 `published_unverified` / `drafted_unverified` 两个值。模板 + chip 同步加 ⚠未核实 chip。前端 normalize 函数（`norm_status`）增分支。

3. **"重新核实"语义**：选中条目逐条同步调用 `verify_published`（不是后台 job —— 数量通常 <50，最多 30s × N，前端可显进度）。HTTP 路由用 form 一次性接收 ids[]，后端阻塞遍历，结束 redirect 回 `/ce:history`。

   **Tradeoff**：阻塞同步 vs APScheduler 后台 job — 后台 job 模式已经在 `_process_queue_job` 用过，但 recheck 不是 mission-critical 且用户期待即时反馈；先做同步版，超时 10s/条；若用户实际跑 100+ 条再升级后台版。

4. **批量路由命名**：沿用现有 `/ce:draft/<op>` 和 `/ce:history/<op>` 前缀；新增 `/ce:draft/bulk-delete`、`/ce:draft/bulk-publish-now`、`/ce:draft/bulk-cancel`、`/ce:history/bulk-delete`、`/ce:history/bulk-recheck`、`/ce:history/purge-failed`（"purge-failed" 是不需要勾选的快捷）。

5. **HTML form vs fetch**：保持现有 `<form method=POST action=...>` 模式（无 JS framework），prefer POST + redirect。bulk 表单的 ids 用 `<input name="ids" value="<id>">` 重复出现（Flask `request.form.getlist('ids')`）。**理由**：与现有所有路由一致，无新依赖。

6. **`verify_published` 调用所需 anchor URL**：从 history `article_urls`（外站发布 URL）反查不到 target_url 的 anchor，但 history 已存 `target_url`，可用 `[target_url]` 作为 `required_link_urls`。title 由 history `title` 提供（如缺则用 `target_url` 域名作 fallback）。**前置**：rev #1 决策"每行单独 push"要求历史 schema 加 `title` 字段（现 history 没存 title — `webui_app/scheduler.py:88-97` `_push_history()` 不存 title）。

7. **失败兜底**：UI 决策 unverified 与 failed 视觉差异：`_unverified` = ⚠橙黄；`failed` = ✗红。前者意味着"发了但核实不了"，后者意味着"adapter 直接报错"。让用户能区分"也许是发布慢"vs"明显错"。

## Open Questions

### Resolved During Planning

- **是否要保留旧的逐条按钮？** 保留。批量操作并存，不替换 — 单条工作流（点开一条立即处理）仍是常见模式，强行下线会触发肌肉记忆反弹。
- **过滤后批量是否只对可见条目生效？** 是。"全选"复选框只勾选当前 chip 过滤后可见的条目（前端用 `:not([style*="display:none"])`），符合直觉。
- **"purge-failed"是否需要确认？** 是。`onsubmit="return confirm('确定删除所有失败记录共 N 条？')"`，N 由 Jinja 渲染时计算。
- **批量重新核实进度反馈？** v1 用 stderr 流不可行（form POST）。改为：前端按钮 disabled + 转圈，路由完成后 flash 显示 `已核实 N 条：M 假成功改为失败，K 仍有效`。

### Deferred to Implementation

- 重新核实的并发模式（顺序 vs 线程池）— 先用顺序，跑 10 条若 >30s 再加 `ThreadPoolExecutor(max_workers=4)`，但 verify 内 `time.sleep` 已含轮询，并发收益取决于实测。
- 批量发布草稿在 APScheduler 队列中的去重 / 节流 — 若一次 bulk-publish-now 选 20 条，是同时 schedule 20 个 job 还是串行？v1 都用 `now + i*5s` 错开避免并发挤兑，实现时确认 misfire_grace_time 行为。
- "重新核实"是否要并行写 stdout 让前端 SSE — 留 v2。
- migration：旧历史条目没 `title` 字段时回填策略（v1 用空字符串，verify 接受 title="" 时退化为 "只查 anchor URL 在 body"）。

## High-Level Technical Design

> *以下示意当前数据流如何被修正。这是评审用的方向指引，不是实现规范。*

**修复前（错觉链）**：

```
publish-backlinks stdout (JSONL, per-row status incl. _unverified)
  ↓ _parse_publish_results → list[dict]
  ↓ WebUI 三处：取 article_url + 硬写 status = "drafted" if mode=="draft" else "published"
_history_store ← 单条聚合 (status 已丢失)
  ↓ index.html
显示 ✓已发布（即便 stdout 行其实是 published_unverified）
```

**修复后（真值链）**：

```
publish-backlinks stdout
  ↓ _parse_publish_results → list[dict]，保留 status / error / title
  ↓ WebUI 三处统一调用 _push_history_per_row(rows, mode):
      for r in rows:
          status = r["status"]  # 直接透传，含 _unverified
          push one history item with title/article_urls/error/status
_history_store ← 多条 per-row，含真实 status

[用户点 批量重新核实]
  ↓ POST /ce:history/bulk-recheck ids=[...]
  ↓ for id in ids:
        ok = verify_published(article_url, title, [target_url], max_wait=10)
        若 ok=False → status="failed", verify_error=reason
        若 ok=True + 原是 _unverified → status="published" / "drafted"
_history_store ← 真实状态
```

**UI 操作流**：

```
┌── 草稿队列 ──────────────────────────────────────────┐
│ [ ] 全选 (12)    [按状态批量] [删除选中] [取消排程]    │
│ ├─[x] target_url1   ⏰排程  [actions...]              │
│ ├─[ ] target_url2   ❌失败  [actions...]              │
│ └─...                                                 │
└─────────────────────────────────────────────────────┘
┌── 发布历史 ──────────────────────────────────────────┐
│ 状态 chips: [全部] [草稿] [已发布] [⚠未核实] [失败]   │
│ 平台 chips: ...                                        │
│ [ ] 全选可见 (8)  [删除选中] [重新核实选中] [一键清失败] │
│ ├─[x] target_url1  ⚠未核实  [actions...]              │
│ ├─[ ] target_url2  ✓已发布  [actions...]              │
└─────────────────────────────────────────────────────┘
```

## Implementation Units

- [ ] **Unit 1：状态真值透传 — 三处历史写入路径统一**

**Goal:** 修复"假成功"根因 — 让 _publish_draft_job / _batch / _publish_real 三处把 per-row `status` 真实写入 history，不再硬覆盖。

**Requirements:** R1, R2 (后端部分)

**Dependencies:** 无

**Files:**
- Modify: `webui_app/scheduler.py` (`_publish_draft_job`, `_push_history`)
- Modify: `webui_app/routes/batch.py` (`ce_batch` 写历史段；`ce_publish_real` 写历史段)
- Modify: `webui_app/helpers.py` (新增 `_push_history_per_row(rows, *, target_url, platform, language, default_publish_mode)`)
- Test: `tests/webui/test_history_truth_propagation.py` (新增)

**Approach:**
- 新建 helper `_push_history_per_row(rows: list[dict], **ctx) -> list[dict]`，遍历 rows，每行：
  - status = row["status"]（保留 `_unverified` 后缀）
  - article_urls = [u for u in [row.get("published_url"), row.get("draft_url")] if u]（若两者都空则视为发布失败，status 改 `failed`，error 填 `"no URL returned by adapter"`）
  - title = row.get("title", "")
  - error = row.get("error") or None
  - 单独 push 一条 history item，保留 100 条上限裁剪
- 三个调用点删除原来的"硬写 status = 'drafted'/'published'"分支
- `_publish_draft_job` 在 catch 分支仍写一条 `status='failed'` 的 history（adapter 整体异常，没有 per-row 输出）

**Patterns to follow:** `webui_store/drafts.py:update_item` 的 per-id 模式；现有 `_push_history()` 闭包

**Test scenarios:**
- Happy path：mock `_parse_publish_results` 返回 `[{status:"published", error:None, title:"T1", published_url:"u1"}, {status:"drafted_unverified", error:None, title:"T2", draft_url:"u2"}]`，调用后 history 含两条独立条目，第二条 status == `drafted_unverified`
- Edge case：单行 `published_url=""` 且 `draft_url=""` 且 `error=None` → 强制视为 failed，error 填 `"no URL returned by adapter"`
- Edge case：rows 为空列表 → 不 push 任何历史，不抛
- Error path：`_publish_draft_job` 内 `run_pipe` 抛 Exception → push 一条 `status='failed'` history，含 stderr 作为 error
- Integration：`_publish_draft_job` 完整路径，mock CLI 返回 mixed `published` + `published_unverified` + 部分失败（exit=4），验证 stdout 行（_unverified 含）进历史；exit=4 时 run_pipe 抛 — 这条 case 暴露当前 run_pipe 丢 stdout 的二级缺陷，在测试里 xfail 标记并文档化 Out-of-v1 deferred-fix

**Verification:** 三处旧"硬覆盖 status"代码段 grep 零命中（`grep -n "'drafted' if .* else 'published'" webui_app/`）；新测试通过。

---

- [ ] **Unit 2：history schema 扩展 + JsonStore bulk helper**

**Goal:** 为后续批量操作和真值显示做存储层准备。

**Requirements:** R1, R3, R4

**Dependencies:** 无（可与 Unit 1 并行）

**Files:**
- Modify: `webui_store/base.py` 或新建 `webui_store/history.py`（如尚无独立 class — 当前 `webui_store/__init__.py` 暴露 `history_store` 应该是 JsonStore 实例，需确认）
- Modify: `webui_store/drafts.py` (`DraftsStore` 加 `bulk_delete(ids)` / `bulk_update(ids, **fields)`)
- Test: `tests/webui/test_store_bulk_helpers.py` (新增)

**Approach:**
- `bulk_delete(ids: list[str]) -> int` — 返回删除数，单次 `self.update(lambda items: [i for i in items if i.get("id") not in id_set])`
- `bulk_update(ids: list[str], **fields) -> int` — 返回更新数，update 内逐项 merge
- `purge_by_status(status: str) -> int` — 历史专用，删除所有 `item.get('status') == status` 的条目
- `recheck_one(item, *, verify_fn) -> dict` — 服务层 helper，verify_fn 注入便于测试
- history schema 容忍新字段：`title`、`verify_error`、`verified_at`、`adapter`（向后兼容）

**Patterns to follow:** `webui_store/drafts.py:update_item` 单 lock 取 items → mutate → save 模式

**Test scenarios:**
- Happy path：3 条 → bulk_delete(['a','b']) → 剩 1 条，返回 2
- Edge case：bulk_delete([]) → 返回 0，文件不变
- Edge case：bulk_delete 全部不存在的 id → 返回 0
- Edge case：purge_by_status('failed') 在无失败时 → 返回 0
- Integration：bulk_update 后 load() 立即看到 merged 字段（lock 内一致性）

**Verification:** `pytest tests/webui/test_store_bulk_helpers.py -v` 全过；现有 single-item helper 测试不退化。

---

- [ ] **Unit 3：草稿队列批量路由**

**Goal:** `/ce:draft/bulk-delete` / `/ce:draft/bulk-publish-now` / `/ce:draft/bulk-cancel` 三个端点。

**Requirements:** R3

**Dependencies:** Unit 2

**Files:**
- Modify: `webui_app/routes/drafts.py`
- Test: `tests/webui/test_drafts_bulk_routes.py` (新增)

**Approach:**
- 接收 `ids = request.form.getlist('ids')`，空 list 直接 redirect with `flash_type=warning&flash_msg=未选择任何项`
- `bulk-delete`：先 try `_scheduler.remove_job(id)` per id（含 catch），然后 `_drafts_store.bulk_delete(ids)`
- `bulk-publish-now`：对每个 id 调 `_schedule_draft_job(id, now + i*5s)`，5s 错开，更新 status='scheduled'
- `bulk-cancel`：仅对 status='scheduled' 的子集 remove_job + 改 status='pending'

**Patterns to follow:** 现 `ce_draft_publish_now` / `ce_draft_cancel` 单条路由

**Test scenarios:**
- Happy path：POST 3 个 ids 到 bulk-delete → 全删，redirect 含 `已删除 3 项` flash
- Edge case：ids 为空 → warning flash，store 不变
- Edge case：含 1 个不存在的 id + 2 个存在的 → 删除 2，flash 含实际数
- Error path：APScheduler `remove_job` 抛 JobLookupError → 单独 catch 不影响其他 id 处理
- Integration：bulk-publish-now 后 `_scheduler.get_jobs()` 含 N 个 date trigger，run_date 间隔 ≥5s

**Verification:** 3 路由均 redirect 到 `/?tab=draft&flash_*`；APScheduler 状态与 store 一致。

---

- [ ] **Unit 4：历史页批量删除 + 一键清失败路由**

**Goal:** `/ce:history/bulk-delete` + `/ce:history/purge-failed`。

**Requirements:** R4, R6

**Dependencies:** Unit 2

**Files:**
- Modify: `webui_app/routes/history.py`
- Test: `tests/webui/test_history_bulk_routes.py` (新增)

**Approach:**
- `bulk-delete`：同 Unit 3 模式，`_history_store.bulk_delete(ids)`，redirect to `/ce:history`
- `purge-failed`：无需 ids，直接 `_history_store.purge_by_status('failed')`，flash 显示删除数

**Patterns to follow:** Unit 3

**Test scenarios:**
- Happy path：5 条历史，2 个 failed → POST `/ce:history/purge-failed` → 剩 3，flash `已清除 2 条失败记录`
- Edge case：无失败 → flash `没有失败记录可清除`
- Edge case：bulk-delete ids=[] → warning
- Integration：bulk-delete 含已过滤 (chip filter) 之外的 id（即用户跨筛选选了某些）→ 仍能删除（后端不重做筛选）

**Verification:** 历史文件长度变化 = redirect flash 中报告的数字。

---

- [ ] **Unit 5：历史「重新核实」服务层 + 路由**

**Goal:** 把 `_unverified` / 怀疑假成功的历史条目通过 `verify_published()` 重新外站校验，更新真实状态。

**Requirements:** R5

**Dependencies:** Unit 1（要先保证 history 有 title 字段）+ Unit 2

**Files:**
- Create: `webui_app/services/recheck.py`（封装 verify_one + verify_many）
- Modify: `webui_app/routes/history.py` (加 `/ce:history/recheck`、`/ce:history/bulk-recheck`)
- Test: `tests/webui/test_history_recheck.py` (新增)

**Approach:**
- `verify_one(item: dict, *, max_wait_per_url: int = 10) -> dict`：
  - 遍历 `item['article_urls']`，对每个 url 调 `verify_published(url, title=item.get('title',''), required_link_urls=[item['target_url']], max_wait=max_wait_per_url)`
  - 任何一个 ok → 整体 ok（同一条历史多 url 视为冗余备份）
  - 全失败 → 返回 `{status: 'failed', verify_error: <last reason>}`
  - ok → 返回 `{status: 'published' if 原非 drafted_* else 'drafted', verified_at: iso}`
- `verify_many(items)` → 顺序调用 `verify_one`，返回 summary `{checked: n, downgraded_to_failed: m, confirmed: k}`
- 路由：bulk-recheck 接 ids，加载对应 items → verify_many → `bulk_update` 每个 id 的新 status + 字段 → flash summary
- 注入 `verify_fn` 便于测试

**Patterns to follow:** `linkcheck/verify.py:verify_published` 是 sync HTTP；与 conftest 网络 mock 兼容（用 `pytest -m real_content_fetch` 跑真实路径或注入假 verify_fn）

**Test scenarios:**
- Happy path：item 含 1 个 article_url，mock verify_fn 返回 ok → item status 升 `published`, `verified_at` 写入
- Happy path：item 原 status=`published_unverified`，verify ok → 升 `published`
- Edge case：item.article_urls=[] → 直接 failed，verify_error="no article URL to verify"
- Edge case：title 为空 → 仍调 verify_published（容忍）
- Error path：verify_fn 抛超时 → 单条标 failed，bulk 继续
- Integration：bulk-recheck 3 条（mock 1 真 1 假 1 抛）→ summary `{checked:3, downgraded_to_failed:2, confirmed:1}`，store 内 3 条 status 各对应

**Verification:** 跑完后 history 内 status 集合包含 verify_fn 的真实判断，verify_error 字段已写入失败条目。

---

- [ ] **Unit 6：UI 模板 — checkbox + bulk action bar + unverified chip**

**Goal:** `index.html` 草稿+历史区块加 checkbox / 全选 / bulk action bar / 未核实状态 chip / unverified badge。

**Requirements:** R2 (UI 部分), R3 (UI), R4 (UI), R6 (UI)

**Dependencies:** Units 3, 4, 5（路由必须存在 form action 才能指向）

**Files:**
- Modify: `webui_app/templates/index.html` (草稿队列区块 1233-1336、历史区块 1338-1432、必要 CSS)
- Modify: `webui_app/templates/index.html` 文件内 `<script>` 段（全选 / 按 chip 联动）
- Test: `tests/webui/test_history_template_rendering.py` (新增 — 用 Flask test client GET 后断言 HTML 含 checkbox / unverified chip)

**Approach:**
- 每条 history-item / draft-item 内首位加 `<input type="checkbox" class="bulk-select" data-id="{{ item.id }}" form="historyBulkForm">`
- 在 card-header 内加 bulk action bar：
  - 全选 checkbox（联动可见项）
  - 「删除选中」按钮（submit form action=bulk-delete）
  - 历史额外：「重新核实选中」(form action=bulk-recheck)、「⚡ 一键清失败」(独立 form action=purge-failed，不需要 ids)
  - 草稿额外：「立即发布选中」(bulk-publish-now)、「取消排程选中」(bulk-cancel)
- 添加 `published_unverified` / `drafted_unverified` chip：data-filter-value="unverified"，前端 normalize 合并两种 _unverified 后缀
- badge 样式：unverified → `bg-warning text-dark`（与 failed 红色区分）
- JS：
  - 全选切换 → 给所有 `display !== none` 的 .bulk-select 设 checked
  - 联动 chip filter：chip 切换时，已勾选但被隐藏的 checkbox 取消 checked（避免提交不可见 id）
  - bulk button disabled 直到至少 1 个 checked

**Patterns to follow:** 现有 chip filter JS（在 `<script>` 段尾部 `data-filter-group` 处理）；现有 form-control / form-control-sm class 体系；Bootstrap 5 form-check

**Test scenarios:**
- Happy path：渲染含 3 条历史 + 1 条 `_unverified` → HTML 含 4 个 `<input type="checkbox" class="bulk-select"`、含 ⚠ 未核实 chip 元素
- Edge case：history 为空 → bulk action bar 不渲染或 disabled
- Edge case：单条历史 article_urls 为空数组 → 仍渲染但 recheck 按钮 disabled（前端 data-attr 控制）
- Integration：Flask test client POST `/ce:history/bulk-delete` with `ids=['a','b']` → 302 to `/ce:history`，flash 存在

**Verification:** 手动启动 `python webui.py` 浏览器打开 `/ce:publish`：每条前有 checkbox、全选可用、按状态 chip 切换后全选只影响可见、bulk 操作生效；DOM 含 unverified chip。

---

- [ ] **Unit 7：集成测试 + 文档**

**Goal:** end-to-end 验证 + 更新 README/AGENTS 提及。

**Requirements:** 全部 R

**Dependencies:** Units 1-6

**Files:**
- Create: `tests/webui/test_e2e_history_batch_management.py`
- Modify: `docs/operations/` 下加一条 cheatsheet（可选）
- Modify: `README.md`（如有 WebUI 章节，加一句"历史页支持批量操作"）

**Approach:**
- 用 Flask test client 串：模拟一次 publish-backlinks stdout 含 mixed status → 进 history → 渲染含 unverified chip → 调用 bulk-recheck（mock verify_fn）→ 状态降级 → 调用 purge-failed → 数量归零

**Test scenarios:**
- Integration: 完整 5 阶段串测，断言每阶段 store 状态符合预期
- Integration: 并发模拟（threading）：1 个线程 schedule_draft_job 写入 history，另 1 个线程 bulk-delete — 验证 `_history_store._lock` 保护

**Verification:** `pytest tests/webui/test_e2e_history_batch_management.py -v` 通过；`python -m py_compile webui_app/**/*.py` 通过；手测 happy path + 失败清理路径。

## System-Wide Impact

- **Interaction graph**：
  - `_publish_draft_job` (APScheduler 后台 thread) ↔ `_history_store` ↔ HTTP 路由的并发 — 现有 JsonStore._lock 提供保护，bulk helper 必须也走 `self.update()` 不能直接读写
  - `verify_published` 走真实 HTTP → conftest 的 `disable_network` autouse fixture 会拦，必须在 recheck 测试用 marker 或注入 fake verify_fn
  - 与 `webui_app/scheduler.py:_process_queue_job` 共享 store — bulk 操作的写入对 queue processor 是 read-then-act 风险（processor 取 task 时 status 可能已被 bulk 改），但 queue 用的是 `_queue_store` 不是 `_drafts_store`/`_history_store`，无交互

- **Error propagation**：bulk 路由内单条失败不能影响其他条（per-id try/except）；最终 flash 报告失败数

- **State lifecycle risks**：
  - bulk-delete 包含 status='scheduled' 的草稿时，必须先 remove_job 再删 store，否则 APScheduler 触发时找不到 item（_publish_draft_job 第一行已有 `if not item` 防御，但 misfire 日志会脏）
  - history `_push_history_per_row` 改后历史条数膨胀 → 100 上限裁剪可能更激进吃掉老条目；用户感知不到但需要 release note 一句
  - verify_published max_wait=10 × N 条 → request 阻塞，gunicorn / werkzeug 单 worker 下会阻塞其他请求；本地 dev server 单线程 OK，部署到多 worker 再考虑后台 job 化

- **API surface parity**：单条路由全部保留，bulk 是叠加。前端没有外部 consumer。

- **Integration coverage**：Unit 7 覆盖跨层 publish → history → recheck → purge 全链路

- **Unchanged invariants**：
  - `publish-backlinks` CLI 行为不变（stdout / stderr / exit code 契约保持）
  - `_parse_publish_results` 签名不变（返回 list[dict]）
  - `linkcheck/verify.py:verify_published` 签名不变
  - 历史 JSON 文件格式向后兼容（仅新增可选字段）
  - chip filter（status / platform）现有行为不退化，仅增 `unverified` 一档

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 历史条数膨胀 → 100 上限裁掉旧条目过快 | release note 提示 + 可考虑 200 上限（unit 1 实现时确认） |
| `verify_published` 阻塞 dev server | max_wait=10/条 + 文档化 v1 限制（≤20 条/次推荐）；v2 后台化 |
| `_unverified` 状态从 v1.0 起从未被 WebUI 显示 → 老历史里大量条目本应是 unverified 但存成 published | "重新核实"功能正好用来追溯校正；用户主动跑一次即可 |
| 网络 mock conftest 干扰 recheck 测试 | 用注入式 `verify_fn` 参数，跳过 network fixture |
| bulk 路由暴露 mass-delete 风险 | onsubmit confirm + 仅同源 Flask session 可访问（dev server 默认 loopback） |
| APScheduler `remove_job` 对已触发 job 抛 JobLookupError | per-id try/except |

## Documentation / Operational Notes

- README WebUI 章节加一句"`/ce:publish` 历史页支持勾选批量删除/重新核实"
- 可选：`docs/operations/history-recheck.md` 简述何时跑 recheck（外站发布后 24h 内、节流爆发后、平台变更绑定后）
- 无 CI 影响（py_compile + ast.parse 不变）；无 monolith budget 影响（无 6 个 hot file 修改）

## Sources & References

- Code: `webui_app/scheduler.py:99-120`, `webui_app/routes/batch.py:104-141, 171-198`, `webui_app/routes/history.py:30-37`, `webui_app/routes/drafts.py`, `webui_app/templates/index.html:1233-1432`, `webui_app/helpers.py:398-406, 724-737`, `webui_store/drafts.py`, `webui_store/base.py`, `src/backlink_publisher/cli/publish_backlinks.py:434-568`, `src/backlink_publisher/linkcheck/verify.py:68-110`
- Related plan: `docs/plans/2026-05-18-010-feat-history-filter-status-platform-plan.md`（chip 过滤）
- Related learnings: `feedback_invert_drift_check_when_invariant_becomes_dynamic.md`（上游加状态下游硬编码集合 drift 是反复出现的模式）
