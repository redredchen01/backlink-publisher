---
title: "Phase 0 报告 — telegra.ph 索引可达性 / dofollow 持续性实验"
date_started: 2026-05-18
date_day7_review: 2026-05-25
date_day14_review: 2026-06-01
date_day21_review: 2026-06-08
owner: <运营填写姓名>
status: in_progress  # in_progress | pass | fail | pass_with_warning
related_plan: docs/plans/2026-05-15-004-feat-telegraph-adapter-plan.md
related_brainstorm: docs/brainstorms/2026-05-15-telegraph-adapter-requirements.md
---

# Phase 0 报告 — telegra.ph 索引可达性 / dofollow 持续性实验

## 0. 实验目的

验证 telegra.ph 外链 **dofollow 持续性** 与 **Google 索引可达性** 是否达到 plan 设定的硬门槛，决定 Unit 2/4/5/6 是否解锁。

## 1. 达标线（整数硬门槛，AND）

| 指标 | 阈值 | 实测 | Pass? |
|---|---|---|---|
| `indexed_pages_at_day14` | ≥ 7（10 中至少 7 个被 Google 索引） | — | ☐ |
| `dofollow_retained_pages_at_day21` | == 10（10/10 三周后 rel 仍缺省/dofollow） | — | ☐ |
| velocity 子实验（3 页 24h 内连发） | 3/3 同样满足以上两条 | — | ☐ |

**最终判定**:三项皆 Pass → `status=pass`;任一不达即 `status=fail`。边界值(6/10、9/10)算 Fail。

**相对竞争门槛(soft)**:若 dev.to / hashnode baseline 14 天索引率 ≥ telegraph + 15pp,标记 `relative_underperformance=true`(不直接 Fail,但 Unit 2-6 启动前需 sign-off)。

---

## 2. 实验设计（来自 brainstorm P0-1 ~ P0-6）

### 2.1 页面分组（10 个 telegra.ph 页）

| 组 | 页数 | 外链/页 | 目标域类型 |
|---|---|---|---|
| A | 3 | 1 | 主目标域(主站) |
| B | 3 | 3 | 通用 TLD 混合(.com/.io/.dev) |
| C | 4 | 5 | 含 1~2 个受怀疑 TLD(.xyz/.top/.icu)以测试 telegra.ph 是否对垃圾域做服务端 nofollow 回填 |

其中 **3 个页面属于 velocity 子实验**(在 24h 内连发),建议从 B/C 组中抽取标记为 V1/V2/V3。

### 2.2 复查节奏

- **T0(发布日,2026-05-18)**:发布后立即抓 HTML,记录每个 `<a>` 的 `rel` 与 `target`
- **T+7(2026-05-25)**:重新抓 HTML,核对 `rel` 是否被 telegra.ph 服务端回填 `nofollow`
- **T+14(2026-06-01)**:`site:telegra.ph/<slug>` 查 Google 索引 + GSC "Links → Top linking sites" 核对(若运营者有目标站 GSC 权限)
- **T+21(2026-06-08)**:最终 dofollow 保持率复查 + velocity 子实验复查

### 2.3 baseline 对照(P0-6)

挑选 **dev.to** 或 **hashnode** 其中一个,发布 1 篇等量外链的对照文,同样跑 T0/T+7/T+14/T+21 节奏。

---

## 3. 数据表 — 10 个 telegra.ph 页面

> 运营填写。`rel_t0` 等字段填:`null`(无 rel 属性 = dofollow,达标) / `nofollow` / `ugc` / `sponsored`。索引列填:`yes` / `no` / `n/a`。

| # | URL | 组 | velocity? | 外链数 | 目标域示例 | rel_t0 | target_t0 | rel_t7 | rel_t14 | rel_t21 | indexed_t14 | GSC_referring_t14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | https://telegra.ph/<slug-01> | A | — | 1 | example.com | — | — | — | — | — | — | — |
| 2 | https://telegra.ph/<slug-02> | A | — | 1 | example.com | — | — | — | — | — | — | — |
| 3 | https://telegra.ph/<slug-03> | A | — | 1 | example.com | — | — | — | — | — | — | — |
| 4 | https://telegra.ph/<slug-04> | B | V1 | 3 | foo.com / bar.io / baz.dev | — | — | — | — | — | — | — |
| 5 | https://telegra.ph/<slug-05> | B | V2 | 3 | foo.com / bar.io / baz.dev | — | — | — | — | — | — | — |
| 6 | https://telegra.ph/<slug-06> | B | — | 3 | foo.com / bar.io / baz.dev | — | — | — | — | — | — | — |
| 7 | https://telegra.ph/<slug-07> | C | V3 | 5 | (含 1 个 .xyz / .top) | — | — | — | — | — | — | — |
| 8 | https://telegra.ph/<slug-08> | C | — | 5 | (含 1 个 .xyz / .top) | — | — | — | — | — | — | — |
| 9 | https://telegra.ph/<slug-09> | C | — | 5 | (含 1 个 .xyz / .top) | — | — | — | — | — | — | — |
| 10 | https://telegra.ph/<slug-10> | C | — | 5 | (含 1 个 .icu) | — | — | — | — | — | — | — |

### 3.1 velocity 子实验复查(T+21)

| velocity 标记 | URL | rel_t21 | indexed_t21 | Notes |
|---|---|---|---|---|
| V1 | (同表 #4) | — | — | — |
| V2 | (同表 #5) | — | — | — |
| V3 | (同表 #7) | — | — | — |

---

## 4. baseline 对照数据

| 平台 | URL | 外链数 | rel_t0 | rel_t14 | indexed_t14 | Notes |
|---|---|---|---|---|---|---|
| dev.to **或** hashnode(二选一) | <填入> | 3 | — | — | — | — |

---

## 5. 运营 SOP(每个 checkpoint 的操作步骤)

### 5.1 T0(发布日,2026-05-18)
- [ ] 用同一 Telegraph 账号(`createAccount` 一次,保存 `access_token`)发布 10 个页面,逐一记录返回的 `https://telegra.ph/<slug>` 到第 3 节表格
- [ ] 对每个页面跑 `curl -s <url> | grep -oE '<a [^>]*>'`,把每个 `<a>` 的 `rel` 与 `target` 记入 `rel_t0` / `target_t0`(`rel` 缺省记 `null`)
- [ ] 验证 velocity 三页(V1/V2/V3)在 24h 内全部发布完成
- [ ] 同日内发布 1 篇 dev.to **或** hashnode 对照文,记入第 4 节

### 5.2 T+7(2026-05-25)
- [ ] 重新 `curl` 抓 10 个 telegra.ph 页面,把 `rel` 填入 `rel_t7` 列
- [ ] 若任一 page 出现 `rel=nofollow`/`ugc`/`sponsored`,提早记入 followup(不影响后续复查继续)

### 5.3 T+14(2026-06-01)
- [ ] `site:telegra.ph/<slug-01>` ~ `site:telegra.ph/<slug-10>` 逐一在 Google 搜索,有结果填 `yes`,无填 `no`,记入 `indexed_t14`
- [ ] (若有目标站 GSC 权限)进入 Google Search Console → Links → External links → Top linking sites,查 `telegra.ph` 是否出现并记录引荐 page count → `GSC_referring_t14`
- [ ] 重新抓 `rel`,填 `rel_t14`
- [ ] 抓 baseline 平台同日数据,填入第 4 节

### 5.4 T+21(2026-06-08)
- [ ] 重新抓 10 个页面 `rel`,填 `rel_t21`
- [ ] velocity 子实验单独再核对一次(第 3.1 节)
- [ ] 在第 1 节"达标线"表格里填实测数,勾 Pass/Fail
- [ ] 在第 6 节写最终结论 + 任何观察到的非预期(如 telegra.ph 路径长度策略变更、目标域被自动剥离等)

---

## 6. 最终结论

> T+21 完成后填写。

- `indexed_pages_at_day14` = __ / 10
- `dofollow_retained_pages_at_day21` = __ / 10
- velocity 子实验:__ / 3 通过
- baseline 平台 14 天索引率:__ / __ → `relative_underperformance` = (true | false)

**判定**:☐ Pass(Unit 2/4/5/6 解锁) / ☐ Fail(plan `status=paused`,产出 brainstorm followup) / ☐ Pass with warning(`relative_underperformance=true`,需 sign-off)

**Sign-off**(若 Pass):
- 运营 owner:_______________ 日期:____
- 工程 owner:_______________ 日期:____

---

## 7. Followups(若 Fail / Pass with warning,产出 brainstorm followup)

- (若 dofollow 不保:`docs/brainstorms/<date>-telegraph-dofollow-regression-followup.md`)
- (若 relative_underperformance:`docs/brainstorms/<date>-platform-switch-evaluation-followup.md`)
- (若 indexation < 7:`docs/brainstorms/<date>-telegraph-indexation-failure-followup.md`)
