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

## Round 4 — New Angles (2026-05-15 PM)

新增 4 个 frame：**Security & supply chain / Contributor onboarding / Failure & recovery / Time-temporal lifecycle**。
4 个并行 ideation sub-agent，32 raw candidates → 7 new survivors（含 4 个 composite）→ 18 rejected。

### 8. Durability backbone (F1+T2+F5 composite)
**Description:** 闭环 publish 安全可重试：
- (a) **Crash-safe journal** — `~/.bp/journal/<run_id>.ndjson` per-line fsync，run 末原子 rotate 进 `published.jsonl`；`bp recover <run_id>` 隔离 corrupt 行（解 `jsonl.py:38` 一坏全坏）
- (b) **Pre-issued idempotency keys** — plan-time 生成 `idempotency_key = hash(target_url + content_fp + planned_at)` + `claim_window_until = planned_at + 24h`；Blogger 走 custom id；Medium 端作 published.jsonl 主键去重 + 5xx 安全重试
- (c) **Content escrow** — `adapter.publish()` 前把 title+body+tags 写到 `~/.bp/escrow/<run_id>/<item_id>.md`；verify 成功才删；账号封 / 失败 `bp escrow replay --adapter <name>` 切平台重投

**Rationale:** 闭环关掉 MEMORY 里 `HTTP 5xx Idempotency Lesson` 硬约束（idempotency key 让 5xx 安全可重）；同时解决 SIGKILL/OOM 丢已成功行、Medium 封号丢内容（work_themed_generator 滑窗导致 re-plan 不可重现）。三层一起才闭合。
**Downsides:** 文件系统多三处路径；schema 演进；escrow 清理策略。
**Confidence:** 85%
**Complexity:** Medium-High
**Status:** Explored (brainstorm started 2026-05-15 PM)

### 9. Token & secret hardening backbone (S2+S1+S8 composite)
**Description:** 多层防 token 泄漏（已复发 3 次的类）：
- (a) **At-rest**: `~/.config/backlink-publisher/blogger-token.json` 迁到 OS Keychain（macOS Keychain / Linux Secret Service via `keyring`）；首次启动 one-shot importer 迁完即抹明文（`config.py:1533 save_blogger_token` 现明文长期 refresh token）
- (b) **Stdout shape gate**: 包住 CLI stdout JSONL 发射，post-serialization regex 扫 `ya29.*` / `1//0e*` / `Bearer ...` / `ghp_*` / `sk-*` / 40+ hex；`--redact-stdout=strict|warn|off`。现 `logger._SENSITIVE_KEYS` 只 key-name 且只 stderr — stdout 是真出口
- (c) **Pre-publish canonical-shape grep**: `cli/publish_backlinks.py` adapter dispatch 前对 rendered body/anchor/title/_provider_meta regex 扫 Google client_id / OAuth blob / Medium [a-f0-9]{64} / `BACKLINK_LLM_API_KEY` env value。命中 abort + RECON log

**Rationale:** 现有 redact 只防 key-name 同 stderr — 把 token stringify 到 value 或写到 stdout 就 bypass。3 次复发都在 self-doc 路径，但 operator 把 token 误粘进 CSV cell 也走同样路径。三层分别守 at-rest、出口、最后一公里，任一层兜底。
**Downsides:** Keychain 跨平台 fallback；stdout regex false-positive；migration importer 风险点。
**Confidence:** 80%
**Complexity:** Medium
**Status:** Unexplored

### 10. Sandbox osascript via stdin（不走 argv f-string）
**Description:** `adapters/medium_brave.py:153` 当前用 `f"...{title}..."` 拼 osascript argv — 带 smart quote 或恶意构造的 Medium title（从 work_scraper 攻击者控域来）可逃出 AppleScript 字符串去执行 `do shell script`。改 `osascript - <<EOF` stdin 模式 + 环境变量传动态值（AppleScript `system attribute` 读）+ 固定 allow-pattern 拒绝带 `"` / 反引号 / `${` / `do shell script` 的 payload。
**Rationale:** 项目里权限最高（user shell）的注入点，title 来自外部 host 抓取。修很小（重写一函数），堵的是 worst sink。
**Downsides:** AppleScript 调试不直观；要补一组带恶意 title 的测试。
**Confidence:** 90%
**Complexity:** Low
**Status:** Unexplored

### 11. Containerized stub-adapter dev sandbox
**Description:** `Dockerfile` + `docker-compose.yml`：Python 3.11 + playwright pre-installed，提供 `MediumStubAdapter` / `BloggerStubAdapter`（写本地 JSONL 不打外网）；`docker compose run bp <cmd>` 跑全 pipeline；`webui` 服务暴露 :5000。非 macOS contributor / 无 OAuth 凭证 contributor 可跑通 80% 改动（anchor_lang, plan, validate, render）。
**Rationale:** medium_brave macOS-only（.app）；medium_api 需 Integration Token（Medium 已弃用新账号）；blogger_api 需 GCP project + OAuth — 当前最大 contribution 门槛。Stub adapter 把这堵墙削掉，contributor 池从"会 macOS + 有 OAuth"扩到"有 Docker"。
**Downsides:** Dockerfile 维护成本；stub 必须严格匹配真 adapter error_class 否则反 mislead；playwright 镜像 1GB+。
**Confidence:** 75%
**Complexity:** Medium-High
**Status:** Unexplored

### 12. Onboarding execution kit (O3+O8 composite)
**Description:**
- (a) **`docs/good-first-pr/` 5 worked templates** — 每份完整 recipe：(1) 加新语言到 `anchor_lang.py`、(2) 加 url_mode 变体、(3) 收紧 `logger.py` 一条 redaction、(4) 加 error_class 到 `errors.py` + adapter mapping、(5) 加 anchor template entry。每份点名具体文件、抄哪个 test pattern、MEMORY gotcha、最小 diff 示例。AGENTS.md 链回
- (b) **`tests/onboarding/` smoke + `first-contribution.yml` GH Action** — 3 个测试：entrypoints 解析 / `bp doctor` 无 creds 退 0 / `cat fixtures/seed.jsonl | plan | validate --no-check-urls` 非空。GH workflow 仅对首次 contributor PR 跑该套件 — 30 秒拿绿，跟 1425-test 主矩阵的 macOS Brave flakies 隔离

**Rationale:** PR #33 lessons kit 沉淀"为啥这样写"；这套补"第一个 PR 怎么写"的执行套件。Templates 把 apprenticeship 转 recipe；smoke + 分离 workflow 是 contributor 第一次推就拿绿色信号、跟 flaky 解耦。
**Downsides:** 5 个模板要选对 — 选错引导出 wrong shape PR；smoke 套件自身要防 drift。
**Confidence:** 80%
**Complexity:** Low-Medium
**Status:** Unexplored

### 13. Vintage & quota discipline (T1+T6+F4 composite)
**Description:** 三条 time-and-rate 防御线，cron-friendly：
- (a) **Plan vintage gate** — plan row 带 `planned_at` + 输入 hash；publish 时 `now() - planned_at > 72h` 拒绝 (exit 5)；`--accept-stale-plan` override
- (b) **Checkpoint TTL** — `--resume` 时 if `now - started_at > 7d` 拒绝 + 打印"creds 可能轮换 / plan vintage 会触发 / anchor pool 可能漂"；`--resume-stale` override；30d 后随 `--delete-complete` auto purge
- (c) **Adaptive quota governor** — `~/.bp/quota/<platform>-<YYYY-MM-DD>.json` 记 attempted/succeeded/last_429_at/cooldown_until；429 触发 exp backoff cooldown（clamp 6h），后续 item 在该平台短路成新状态 `deferred`（不是 `failed`），checkpoint 标记由明天 `--resume` 自然续

**Rationale:** 三件全是"silent state rot → explicit failure"模式。Vintage gate 关 plan-time URL hallucination 复发面；checkpoint TTL 关"3 周前 checkpoint 拉起来报奇怪错"；quota governor 关 429 retry storm + 让 cron 不卡。`deferred` 是当前 checkpoint 缺的 status — 加上才有 graceful degrade。
**Downsides:** 3 处分别测；`deferred` 状态要全 publish path 都识别。
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

### 14. Time-decayed anchor profile
**Description:** `anchor_profile.py` 现 `_MAX_ARTICLES_PER_TARGET = 100` 滑窗，scheduler 平权 count。引入 `weight = 0.5 ** (age_days / half_life_days)`（默认 half_life = 30d, 可配）替换 `recent_type_counts` / `recent_url_category_counts` 纯计数。窗口不变，count math 变 float-weighted。
**Rationale:** 一个 6 个月前的 Branded 和昨天的不该平权 — SEO 直觉"近期匹配更代表当前曝光"。Scheduler 已 side-effect-free + `anchor_metrics.py:137` 支持 `now` 注入 — 引入 decay 测试模式已铺好。和 #3 StateStore 正交（StateStore 是 ops state 持久化；这是 decision math 改良）。
**Downsides:** 默认 half-life 是产品判断 — 选 14 / 30 / 60 直接改 scheduler 行为；需要真实数据验证。
**Confidence:** 70%
**Complexity:** Low-Medium
**Status:** Unexplored

## Rejection Summary — Round 4

| # | Idea | 拒绝理由 |
|---|------|---------|
| R26 | Pin deps + pip-audit (S4) | 标准卫生，cheap — 直接做就行不占 ideation slot |
| R27 | Adapter response body caps (S5) | 防御价值合理但无已知 exploit；优先级低于 S3 |
| R28 | Cassette signing (S6) | 折进 survivor #5 作为必备属性 |
| R29 | Agent self-tampering tripwire (S7) | 概念独特但受众窄 + 实现成本高，记下待 brainstorm 重审 |
| R30 | Hermetic fixtures (O2) | 折进 survivor #5（cassettes）— 同一基础设施 |
| R31 | bp doctor (O1) | R3 已拒；onboarding 框架下也被 O4 + O8 覆盖 |
| R32 | bp tour 交互式 walkthrough (O5) | 一次性 demo，长期会 drift；leverage 不够 |
| R33 | ARCHITECTURE.md 模块图 (O6) | 价值真实但标准 docs ask；本身不带改进杠杆 — 写就是了 |
| R34 | bp scratchpad <slug> (O7) | tmp dir 够用；工具化收益边际 |
| R35 | Adapter idempotency receipts (F2) | 被 T2 的"pre-issued idempotency keys"取代（更简洁） |
| R36 | Brave/AppleScript probe (F3) | 真问题但 Brave-specific；直接在 adapter 修 |
| R37 | Living-link re-verify (F6) | sub-agent 自己说"折进 #3 StateStore" |
| R38 | Portable state bundle (F7) | tar 手动够用；F5 escrow + #3 StateStore 落地后再看 |
| R39 | Forensic replay bundle (F8) | 扩展 survivor #2 `--explain --bundle` 维度 |
| R40 | Cohort week_id tag (T4) | 字段加法 + report 切片，small；report-anchors 自然演化即可 |
| R41 | Host-timezone-aware cadence (T5) | SEO 假设需验证；推到 brainstorm |
| R42 | Historical re-plan dry-run (T7) | 探索性诊断工具；reconstruct as-of state 高成本低紧迫 |
| R43 | Anchor-decay early warning (T8) | 报告层版本 — T3 decision-math 一来 T8 自然出现 |

## Session Log
- 2026-05-15: Initial ideation — 41 raw candidates generated across 5 frames, 7 survivors after adversarial filter + cross-frame synthesis (composites #2 操作骨干 and #3 SQLite StateStore)
- 2026-05-15: Selected idea #1 (Lessons kit) for brainstorm — handing off to ce:brainstorm
- 2026-05-15 PM: Round 4 — 4 new frames (security / onboarding / failure-recovery / temporal-lifecycle), 32 raw → 7 new survivors (#8-#14, 4 composites), 18 rejected (R26-R43)
- 2026-05-15 PM: Selected idea #8 (Durability backbone — F1+T2+F5) for brainstorm — handing off to ce:brainstorm
