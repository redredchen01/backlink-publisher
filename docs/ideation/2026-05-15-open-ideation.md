---
date: 2026-05-15
topic: open-ideation
focus: open-ended (post Round-3 + Plan 006 + Plan 009)
---

# Ideation: Backlink-Publisher Open Ideation

## Codebase Context

### Project Snapshot
- **Stack**: Python 3.11+ CLI, src-layout, JSONL pipeline (`plan-backlinks | validate-backlinks | publish-backlinks`)
- **Module surface**: 25+ modules under `src/backlink_publisher/`, 5 CLI entrypoints, adapters for `blogger_api`/`medium_api`/`medium_brave`/`medium_browser` + `retry`/`link_attr_verifier`
- **Tests**: 53 test files, Hypothesis property tests + edge + config-matrix
- **webui.py**: 217KB / ~1400 lines, single standalone Flask god-file (known retrofit hazard)
- **Hard rule**: NO runtime LLM (LLM only at dev time)
- **Recently shipped**: Round-3 7/7 + Plan 006 极简化 + Plan 009 url_categories merge — all on `main` (`41e1b43`, 1425 tests)

### Notable patterns
- JSONL pipeline contract (each stage enriches a record on stdin/stdout)
- Dated plan/brainstorm doc convention (`YYYY-MM-DD-NNN-*.md`)
- Adapter pattern + retry layer
- `recon()` log level for always-on operator-visible signals (bypasses --log-level gate)
- Strong test culture: property tests, edge cases, config matrix

### Known pain points / institutional knowledge (from MEMORY.md, 27+ feedback entries)
- `save_config` silent overwrite (PR #12 structural fix; class of bug recurred → narrow-TOML-merge workaround)
- HTTP 5xx idempotency lessons (retry safety)
- macOS Brave adapter test isolation
- Plan-vs-code drift; plan-time URL hallucination
- Force-push hooks blocking amends (workaround: new -v2 branch + single commit)
- Tests can lock-in bugs (negative-assertion anti-pattern; observed twice)
- Floating-point breaking tie-break ordering
- Standalone page > retrofitting webui.py (sibling not child)

### Past learnings search
- `docs/solutions/` 目前几乎为空（3-5 entries），27+ MEMORY feedback 条目尚未沉淀进项目 — 这是单个最大的 leverage gap

## Process Notes
- 41 raw ideas across 5 ideation sub-agents (frames: operator pain / missing capability / inversion / leverage / assumption-breaking)
- Dedupe + cross-frame synthesis produced 2 composite ideas:
  - **#2 操作骨干**: run_id (4.8) + --explain (1.1) + --replay (1.4) + log rotation (1.7)
  - **#3 SQLite StateStore**: consolidates 2.8/5.7 and unlocks monitor (2.1) + velocity (2.2) + persona (2.3) + index tracker (2.5)
- Adversarial filter: 7 survivors

## Ranked Ideas

### 1. 把 27+ MEMORY feedback 沉淀成 docs/solutions/ + bp lessons grep CLI
**Description:** 批量把 `~/.claude/projects/.../memory/feedback_*` 移植到 `docs/solutions/<category>/*.md`（带 YAML frontmatter：trigger / pattern / fix / refs）；新增 `bp lessons grep <symbol>` 在 plan/review 时浮现相关历史教训；feedback writer hook 自动 cross-link。
**Rationale:** 27 条血泪经验现在只对你自己可见 — PR、新 contributor、ce:review、未来 agent 都不知道。一次投入把"私人记忆"变成"项目资产"，所有未来工作自动复用。一位 sub-agent 直接称其为"single highest leverage move"。
**Downsides:** 迁移本身体力活；frontmatter schema 需先想清楚；hook 维护成本。
**Confidence:** 90%
**Complexity:** Low–Medium
**Status:** Explored (brainstorm started 2026-05-15)

### 2. 操作骨干（Operational Backbone）：run_id + --explain + --replay + 结构化日志轮转
**Description:** 在每个 CLI/webui 入口铸造 UUID `run_id`，贯穿 plan→validate→publish→adapter→retry；持久化 `~/.bp/runs/<run_id>.ndjson`；`publish-backlinks --explain <run_id>` 渲染 timeline + error_class 直方图 + RECON delta；`--replay-from <run_id> --dry-run` 用历史 checkpoint 回放当前代码看决策 diff。
**Rationale:** 今天 cron 失败后 operator 要在 5 个终端窗口里 grep；一个命令应该讲完故事。`run_id` 是未来分析 / ML / 审计的天然 pivot key。`recon()` 已经建立了 always-on 信号模式 — `run_id` 是缺失的 pivot。
**Downsides:** 合并了 4 个子构思 → scope 不小；要先约定 logger extra 协议。
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 3. SQLite StateStore — 一举解锁 monitor / velocity governor / persona rotation / index tracker
**Description:** `~/.local/share/backlink-publisher/state.db` 单一来源；先把 `anchor_profile` + `checkpoint` 迁到 `StateStore` 接口下；JSONL 仍作为导出格式。一旦有了 state 平台，廉价加上 4 个能力：
- (a) **living-link 监控** 周期复查链是否还活、是否仍指向预期 target、host 页是否仍 200
- (b) **link-velocity governor** 按 main_domain 节流（每天/每周新增 backlink 上限 + 渐增曲线）
- (c) **multi-persona rotation** 多账号 LRU + 冷却调度
- (d) **index-status tracker** 每条 published URL 用 `site:` 查询周期检测是否被收录
**Rationale:** 4 个独立"missing capability"想法的共同前提就是"有个真 state 层"。先建平台、后续 capability 才便宜。CLI/JSONL 契约不动。SQLite 本身就是 local-first / cron-safe 的 primitive — JSONL-as-DB 是 Unix nostalgia tax。
**Downsides:** 迁移期 dual-write 风险（save_config 静默覆盖坑过 2 次，DB 化才能根本性杜绝）；schema 演进策略要先想好。
**Confidence:** 80%
**Complexity:** High
**Status:** Unexplored

### 4. Adapter Protocol + Conformance 测试套件（吸收 Medium 三 adapter 合并）
**Description:** 定义 `class Publisher(Protocol)` + 一份参数化 `test_adapter_conformance.py`：dry_run shape、error_class 归一化、retry 幂等、redact-safe 错误、OAuth pre-flight 契约。每个具体 adapter 自动注册进 conformance 跑。Medium api/brave/browser 三件套统一进 `MediumAdapter` 内部 `[api → brave → browser]` 链。
**Rationale:** 每加一个 platform（Substack / dev.to / WordPress / Web 2.0 relay）从"几天"变"几小时"；每个 cross-cutting bug fix（HTTP 5xx idempotency 教训这类）一次同时落到所有 adapter。`adapters/__init__.py` dispatcher 已经隐式持有这条链 — 抽出来就是 Protocol。
**Downsides:** 先得抽出 Protocol；现有 adapter 重命名/小重构；Medium 三合一动作较大。
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 5. Recorded HTTP / AppleScript / Browser Cassettes + Snapshot Tests
**Description:** vcrpy 录所有 HTTP 调用、文件录 Brave/Playwright 的关键 HTML response；snapshot `AdapterResult`。Medium/Blogger schema 哪天偷偷改字段，CI 立刻红。Re-record only when remote schema 真的变。
**Rationale:** 今天 `fixtures/` 只有 3 行 seed.jsonl；外部平台无声 schema drift 是这类工具最大隐患（Medium 2023 弃用 Integration Tokens 就是先例）。和 #4 配对：Protocol 定契约，cassette 用真实响应验证契约 + 给 contributor 一份 "this is what real responses look like" 文档。
**Downsides:** 录制初次有摩擦；token redaction 要做对；定期 re-record。
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

### 6. 删 validate-backlinks 子命令；折叠为 publish --dry-run + 后置 audit
**Description:** 删除 `cli/validate_backlinks.py` (259 LOC)；预检规则统一收口到 `publish --dry-run`；新增 `publish --audit-only <published.jsonl>` 用于 publish 之后回头复检活链。
**Rationale:** 三套验证（validate / publish 内 dry-run / verify_publish）规则会 drift；最有价值的检查（linkcheck、language_check）反正只在 publish 时才能 catch；把"前置 gate"反转为"后置审计"才能抓到死链/重定向/de-index — 这些前置永远看不到。
**Downsides:** 是 user-facing CLI 行为变化；旧脚本/cron 调用 `validate-backlinks` 会断；要给 deprecation 路径。
**Confidence:** 65%
**Complexity:** Medium
**Status:** Unexplored

### 7. 假设破坏者：Kill auto-publish；改成 "link earnings brief CSV" 给人执行
**Description:** 停止往 Blogger/Medium 自动发。整个 pipeline 改成给操作者输出一份排序过的 CSV：`(高相关性 target URL, 推荐 anchor + 上下文段落, 触达角度)`。投放（guest post / HARO / 评论 / citation request）由人完成。
**Rationale:** 2026 年 Blogger/Medium 自动 post 的 SEO 价值近零（Medium 全 nofollow、Blogger 是僵尸平台）；anchor_*x5 + footprint 模块的存在本身就在承认"我们知道这看起来很 spammy"。真正的稀缺资源是"判断哪儿放一个链会被珍视且活得久"。把判断材料铺给人、由人执行 — 命中率高一个数量级。
**Assumption broken:** "programmatic publishing to free platforms is the 2026 bottleneck"
**Alternative frame:** "bottleneck is judgment about where a link will be valued and survive — surface judgment-ready candidates, let humans land the kill"
**Downsides:** 颠覆产品定位；webui + 25+ 模块的现有形态要重新定义；user 可能不接受。
**Confidence:** 50%
**Complexity:** High（形态重塑）
**Status:** Unexplored

## Rejection Summary

| # | Idea | 拒绝理由 |
|---|------|---------|
| R1 | webui.py 拆 Blueprint + 800 行 CI cap | 好但今天没烧起来；先做 #2 操作骨干、用了再决定要不要拆 |
| R2 | Auto-resume on cron + lockfile | `--resume` 已存在；operator 写 wrapper 即可，不需要一等公民 |
| R3 | Pre-flight `doctor` 子命令 | 部分被 #2 `--explain` 覆盖；moderate leverage |
| R4 | Error-class taxonomy Enum | 折进 #2 操作骨干 |
| R5 | Centralize ExitCode in Enum | Nice-to-have；折进 #2 |
| R6 | Collapse anchor_*x5 → anchor.py | 纯文件 shuffle，低 value；除非走 topical-entity-vector 重构（R13）才有意义 |
| R7 | Default every TOML knob (zero-config) | 与 Plan 006 极简化方向重叠；正在做 |
| R8 | `publish_one()` in-process API | 有用但没具体痛点证据；测试不依赖它 |
| R9 | Autogenerate adapters from OpenAPI | Medium 没有公开 OpenAPI；Blogger 200 LOC 不是瓶颈 |
| R10 | Auto-detect nested .git drift | 1 小时小脚本；不需要 ideation 待遇 |
| R11 | Footprint cross-row content fingerprints | 被 R15 penalty cluster subsume；R15 也未入选 |
| R12 | Web 2.0 / relay adapter family | 被 #4 Adapter Protocol 解锁后再说 |
| R13 | 替换 anchor_*x5 为 topical entity vector | 假设破坏强但 invasive，需要 Google 算法假设验证 |
| R14 | 重新考虑 no-runtime-LLM (discriminative-only) | 破"明文硬规则"应作为 brainstorm 入口而非 ideation 出口 |
| R15 | Penalty-cluster adversarial corpus | 想法漂亮但需维护 200+ deindexed 样本语料；维护成本高 |
| R16 | 内容物本身作为产品（reframe） | reframe-only 没具体动作；记下作未来讨论 |
| R17 | Drop Blogger+Medium，转向 GitHub/Substack/Reddit/Mastodon | 战略决策塞 ideation 太重；应作为单独 strategy 讨论 |
| R18 | 报表 dashboard | scope 模糊；先看 #2/#3 输出再说 |
| R19 | Hypothesis property test 层扩展 | 总是对的"多写测试"；leverage 比 cassettes(#5) 弱 |
| R20 | Self-dogfooding changelog → backlinks loop | 巧但低价值；项目自身没有 SEO 目标 |
| R21–R24 | Living-link monitor / Velocity governor / Persona rotation / Index tracker（单独立项） | 折进 #3 — 都是 StateStore 解锁的能力，不应单独立项 |
| R25 | Unlinked-mention reclamation crawler | 假设破坏好但需市场验证；推到 brainstorm 阶段考虑 |

## Session Log
- 2026-05-15: Initial ideation — 41 raw candidates generated across 5 frames, 7 survivors after adversarial filter + cross-frame synthesis (composites #2 操作骨干 and #3 SQLite StateStore)
- 2026-05-15: Selected idea #1 (Lessons kit) for brainstorm — handing off to ce:brainstorm
