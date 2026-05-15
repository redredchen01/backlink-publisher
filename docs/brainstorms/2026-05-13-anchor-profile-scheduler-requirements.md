---
date: 2026-05-13
topic: anchor-profile-scheduler
revised: 2026-05-13
---

# 外链文章生成器升级：zh-CN 短文迁移 + Anchor Profile 调度

> **2026-05-13 修订说明**：第一次 plan review 暴露出产品形态错配——用户贴的 brainstorm prompt 描述的是「150-200 字 + 2-3 链接」短文，但现有 backlink-publisher 生成的是 6-8 链接、多段、含 `## References` / `density_para` 的长文。
> 经过用户决策（"迁移 zh-CN 为短文形式（生产环境实际要用短文）" + "主链 + 副链都记账"），本文档相应改写：本次升级**同时**完成两件事——(A) zh-CN 文章形态从长文迁移到短文；(B) 在新短文形态上接入 anchor profile scheduler。en/ru 路径不动。

## Problem Frame

**两个并存问题**：

1. **现有 zh-CN 文章形态与外链投放目标不匹配**：当前 `_zh_body_a/b/c` 模板 + `_build_links` 产生 6-8 链接、附 `## References` 章节、含 `_build_link_density_paragraph` 的长文。这种文章在 Blogger/Medium/论坛上更像 SEO 内容农场而非自然推荐贴。生产环境希望的形态是 150-200 字、2-3 个自然嵌入链接的轻量推荐文。

2. **anchor 文本类型分布不可控**：当前的 anchor 选择逻辑（`select_anchor_keywords` 用 `(i+offset)%n` 轮换扁平池）在批量产出时高度同质化。Google Penguin 算法对外链锚文本分布的"自然度"采样判定——Branded/Partial Match/Exact Match/LSI 比例失衡或单一文本反复出现会触发 link spam 信号。

本次升级在新短文形态上**外挂一层调度器**，把"本次该用什么类型的 anchor、具体落到哪个文本、副链该指向哪个站内页面"从生成器的"软约束直觉"变成程序的"硬约束分配"。

## User Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│ 用户输入：keyword + target_url + site                                │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 1. Anchor Profile Scheduler                                          │
│    读取 site 最近 N 条 link 记录（主链+副链一起记账），             │
│    对比 Safe SEO 目标 (55/25/10/10)，输出本篇:                      │
│      .main_link.anchor_type ∈ {branded, partial, exact, lsi}        │
│      .secondary_links: [                                            │
│         {url_category, anchor_type},                                │
│         {url_category, anchor_type}  // 可选第二副链                │
│      ]                                                              │
│    副链 url_category ∈ {hot, animate, category, topic}              │
└────────────────┬─────────────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. Anchor Text Resolver（按 link 逐个）                              │
│    每条 link 拿 (url_category, anchor_type) 决策：                  │
│    - 从该 site 的 (url_category, anchor_type) typed pool 里抽，    │
│      过滤 forbidden / 字数 / 重复                                   │
│    - typed pool 空 → LLM 生成候选（按 keyword + 目标页主题）       │
│      → 候选过滤 → 取首选                                            │
└────────────────┬─────────────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. Short Article Generator（新模板，替换长文）                       │
│    生成 150-200 字简体中文短文：                                    │
│    - 自然口吻、轻度推荐、不像硬广                                   │
│    - 关键词自然出现、不堆砌                                         │
│    - 主链（首页）+ 1-2 个副链（hot/animate/category/topic）        │
│    - 所有 <a> 标签含 target="_blank" rel="noopener noreferrer"     │
│    - 不输出 ## References、不输出 density_para、不输出标题/FAQ    │
└────────────────┬─────────────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. Validator                                                         │
│    - 正文字数 150-200（去 HTML 后纯文本）                           │
│    - <a> 标签数 = 主链 1 + 副链 1-2 = 2-3 个                       │
│    - 每个 <a> 含 target/rel 属性                                    │
│    - anchor 文本不在 forbidden、2-8 字、无 _UNSAFE_IN_ANCHOR 字符  │
│    - 实际渲染 anchor 文本 == resolver 决策文本                      │
│    - 无裸 URL                                                       │
│    失败：重试 1 次；二次失败降级整篇为「主+副均 Branded 首页」       │
└────────────────┬─────────────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. Profile State Update                                              │
│    本篇所有 link（主+副）的 anchor_type 都写回 profile              │
│    实际类型记账（降级则按 branded 记，不伪装）                       │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                        输出：HTML 正文（短文）
```

## Requirements

**zh-CN 短文形态迁移**
- R1. zh-CN 路径生成 150-200 字简体中文短文，每篇含 2-3 个 `<a>` 标签：1 个主链（必含、指向 main_domain 首页）+ 1-2 个副链（指向 main_domain 下的子页面）
- R2. 短文不输出标题、FAQ、SEO metadata、`## References` 章节、`density_para` 等长文结构；输出仅正文 HTML
- R3. 语气自然、轻度推荐，不像硬广；关键词自然出现、不堆砌
- R4. 所有 `<a>` 标签必须含 `target="_blank" rel="noopener noreferrer"`；不输出裸 URL、不输出 Markdown link
- R5. 不写露骨细节（成人 ACG 站点合规约束）
- R6. en/ru 路径完全不动（继续使用现有 6-8 链接长文模板与 `select_anchor_keywords`）

**副链 URL 类别（zh-CN 短文）**
- R7. 副链 URL 必须来自该站点定义的 URL 类别集合，每个 site 在 config 中声明 `url_categories` 字典。51漫画的初始类别：
  - `hot` → `https://51acgs.com/comic/hot`（含 `/thisweek` 子类）
  - `animate` → `https://51acgs.com/animate`
  - `category` → `https://51acgs.com/category`
  - `topic` → `https://51acgs.com/topic/blog`
- R8. 一篇文章不重复使用同一 URL 类别；选 2 个副链时随机选 2 个不同类别
- R9. 副链数量（1 或 2）由 scheduler 决定，目标维持 "每篇平均 2.5 个链接"（即约一半文章 2 链、一半文章 3 链）

**Anchor Profile 目标分布（Safe SEO 预设）**
- R10. 目标比例：Branded 55% / Partial Match 25% / Exact Match 10% / LSI 10%
- R11. **比例统计包含主链 + 副链所有 anchor**（每个 `<a>` 计 1 票），不区分主副
- R12. 比例为**滑动窗口收敛目标**：每次调度时回看 site 最近 N 条 link 记录的实际类型分布，挑选当前偏离目标最大的类型
- R13. 支持按 site 独立维护 profile，不跨站混算

**Anchor 类型定义（4 类）**
- R14. Branded：包含品牌词的锚文本（例：「51漫画首页」「51漫画」「51漫画推荐」）
- R15. Partial Match：包含目标关键词但有自然修饰的锚文本（例：「成人漫画在线阅读平台」「热门成人漫画」）
- R16. Exact Match：与目标关键词高度一致的短锚文本（例：「成人漫画」「热门漫画」「本周热门漫画」）
- R17. LSI / Related：语义相关但不含品牌、不含关键词原文的锚文本（例：「ACG 内容平台」「漫画与动漫资源」「ACG 动画推荐」）

**调度器行为**
- R18. 输入：单次调用 = 1 个目标关键词 + 1 个 main_domain + 1 个 site 标识
- R19. 输出：`ScheduleDecision { main_link: {anchor_type}, secondary_links: [{url_category, anchor_type}, ...] }`，副链数量 1 或 2
- R20. 主链 + 每个副链的类型独立从"目标比例 - 实际比例"差值最大者挑选；并列时按 Branded > Partial > LSI > Exact 裁决
- R21. 一篇文章内 2-3 个 link 类型分布**不强制内部均衡**——单篇可能 2 个都是 Branded、也可能 1 Branded + 1 Partial + 1 LSI；由全局滑动窗口收敛保证整体比例

**Anchor 文本来源（混合策略，按 url_category × anchor_type 二维索引）**
- R22. typed pool 结构：`config.target_anchor_pools[main_domain][url_category][anchor_type] = [候选词列表]`
  - 例：`51acgs.com.home.branded = ["51漫画首页", "51漫画"]`
  - 例：`51acgs.com.hot.exact = ["热门漫画", "本周热门漫画"]`
- R23. 每个 (url_category, anchor_type) 单元至少 3 个候选；未填或不足 → resolver 调用 LLM 按 keyword + 目标 URL 主题生成 3-5 个候选
- R24. 所有 anchor 文本必须通过 Forbidden List 校验（沿用现有禁词：点击这里、看这里、更多、官网、入口、这个网站、相关页面、了解更多）
- R25. 所有 anchor 文本字数 2-8 个中文字
- R26. 同一 site 最近 20 条 anchor 文本去重（不论 URL 类别和类型）

**校验与降级**
- R27. 生成完成后机器校验：正文字数 150-200、`<a>` 数量 2-3、属性齐全、每个 anchor 文本通过 forbidden/字数/字符校验、实际渲染 anchor == resolver 决策、无裸 URL
- R28. 校验失败重试 1 次；二次失败降级为「主链 + 1 副链 = 2 链、全部 Branded、副链指向首页」最保守方案，写入告警日志
- R29. 降级事件计入 profile 状态时按降级后的实际类型记录（branded），不"伪装"为原计划类型
- R30. 降级率作为可观测指标：滚动 100 篇内降级率 >10% 触发显式告警，避免系统性 LLM 拒绝被静默掩盖

## Success Criteria

- 任意 site 在生成 ≥ 50 篇后，**全部 link**（主+副）的 anchor 类型分布与目标比例（55/25/10/10）的最大偏差 ≤ 5 个百分点
- 任意 site 最近 50 条 link 记录里，**单一 anchor 文本**重复出现的次数 ≤ 3 次（窗口大于 dedup 窗口的 20，避免同义反复）
- 滚动 100 篇文章降级率 ≤ 10%（避免系统性 LLM 拒绝被静默掩盖）
- 100% 文章通过短文校验（字数 150-200、链接 2-3、属性、禁词、裸 URL）
- 单次调用延迟相对当前实现增加 ≤ 30%（每篇最多 3 次 LLM 调用，每次 typed pool 命中即跳过）

## Scope Boundaries

- **本次升级 = 短文迁移 + anchor scheduler 两件事一起做**，不分两期；理由：现有长文模板的 anchor 注入点太多（_build_links / _build_link_density_paragraph / body），在长文上接 scheduler 等于继续在错误的形态上叠功能
- en/ru 路径**完全不动**——仍使用 `select_anchor_keywords` + 长文模板
- 不引入"长度变体"（仅 150-200 字一种）或"格式变体"（仅自然推荐口吻）；多模板归后续升级
- 不做多站点参数化重构——继续以 51漫画 为首要场景，但所有新模块写成 site-agnostic（其他站点只需补 config）
- 不引入第三方 SEO API（Ahrefs / Semrush）；LSI / Partial 兜底完全靠 LLM
- 不改 Penguin 比例预设之外的策略（Aggressive / Branded-Heavy 留接口扩展点）
- 不做 ranking 回灌（无法在本 codebase 内验证 SEO 实际收益；本次只优化"分布达标"这个 leading indicator）
- 不引入跨进程文件锁（单进程顺序运行约定）；多进程并发延后处理

## Key Decisions

- **同步做两件事：短文迁移 + scheduler 接入**。理由：在长文形态上接 scheduler 注入点太多且 anchor 槽位语义不清（首次 plan review F1/F2/F3 暴露），等于继续在错误的形态上叠功能。
- **比例预设 = Safe SEO (55/25/10/10)**：在程序化外链场景下风险最低，先收敛这个再说其他。Aggressive / Branded-Heavy 后续可作为可选 preset。注：该比例为业界经验值（非官方 Google 文档），目标定为"分布达标"这个 leading indicator，不与排名结果绑定。
- **控制层放在程序而非 prompt**：LLM 自行轮换 anchor 类型不可靠（语料偏好会让它默认偏向 Branded 或 Partial），必须由调度层硬分配。
- **主链 + 副链都参与 profile 统计**（修订于本版）：原来计划"副链一律 Branded 不计入"会导致全局 55/25/10/10 目标只对主链生效，副链类型实际不可控；改为全部 link 都进入统计且 scheduler 对每条 link 独立决策类型。
- **typed pool 按 (url_category × anchor_type) 二维索引**（新增）：用户提供的 anchor 候选词天然按 URL 分组（首页/热门/动漫/分类/专题各有自己的语义），所以池结构按"URL 类别 × 类型"组织。同一 url_category 下若某 type 池空，则 LLM 兜底。
- **anchor 库混合策略**：每个 (url_category, anchor_type) 至少 3 个静态候选；不足则 LLM 按 keyword + 该 url_category 主题动态生成。
- **副链不允许全部指向 main_domain 首页**（修订）：副链 URL 必须来自 hot/animate/category/topic 之一，每篇内不重复使用同类别。原来"副链一律指首页"会让单篇出现多个 `<a href=main_domain>` 重复链接。
- **副链数量：scheduler 决定 1 或 2，平均落到 2.5 链/篇**：通过 scheduler 在副链数 1/2 间交替来达到。
- **降级行为：主+1 副 Branded 全指首页**（修订自原 R22）：保留降级路径但只产 2 链（不再尝试副链 URL 类别选择），简化降级实现。
- **滑动窗口 = 最近 100 条 link 记录**（修订自"100 篇"，因 link 是统计单位而非文章）：足够大平滑抖动，足够小快速收敛。
- **anchor 文本去重窗口 = 最近 20 条**：避免同一文本短期反复。Success Criteria #2 单独看 50 条窗口，避免被 dedup 窗口同义反复满足。
- **降级率显式监测**（新增）：滚动 100 篇内降级率 >10% 触发显式告警，防止系统性 LLM 拒绝被静默掩盖。

## Dependencies / Assumptions

- 现有 `_zh_body_a/b/c` 与 zh-CN 路径下的 `_build_links` / `_build_link_density_paragraph` 调用会被绕过（zh-CN 走新短文 generator）；en/ru 路径继续使用旧逻辑
- 站点配置在 toml 中扩展两段：`url_categories`（URL 类别 → URL 映射）+ `anchor_pools`（按 url_category × type 索引的候选词二维表）
- LLM provider 通过 OpenAI-compatible HTTP API 接入；具体 vendor 操作期评估（成人 ACG 内容拒答率未知）
- profile 状态以 JSON 形式持久化到 `~/.cache/backlink-publisher/anchor-profile/<site>.json`（不入 config.toml，避免 save_config 静默丢数据风险）

## Outstanding Questions

### Resolve Before Planning
- 无（产品层决策已收敛；剩余技术决策见 Deferred）

### Deferred to Planning
- [Affects R23][Operational] LLM provider 选型（OpenAI-compatible host）+ 拒答率前置验证（建议 plan 期跑一个 20-keyword 小批次确认拒答率 <20%）
- [Affects R22][Technical] anchor pool 数据迁移：当前 `target_anchor_keywords` 扁平池如何过渡到新的二维结构（一次性手工填表 vs 自动迁移工具）
- [Affects R28][Technical] 重试 / 降级的具体实现位置（generator 层 vs pipeline 层）
- [Affects R26][Technical] anchor 文本去重的精确语义（精确字符串相等 vs 编辑距离阈值）
- [Affects R7][Technical] URL 类别集合是 site 级 config 硬编码，还是允许 per-input row 覆盖？短期不重要，先 site 级

## Risk Acknowledgments（新增于本版）

- **Penguin 55/25/10/10 是业界经验值不是官方规范**：本次升级的成功指标是"分布达标"而非"排名提升"；如 6 个月后 SERP 无效果，需要 retro 反思指标本身是否对路
- **成人 ACG 内容 LLM 拒答风险**：load-bearing 依赖；planning 期前置验证至关重要；如拒答率高需 typed pool 必须填满才能上
- **位置权重（首链 vs 末链）未建模**：本次假设 Google 不区分位置；如未来研究表明位置权重显著，需引入"随机化主链/副链顺序"机制

## Next Steps

→ `/ce:plan` for structured implementation planning（重写原 plan，作用域显著扩大：现 unit 集 = 短文 generator 重写 + scheduler + resolver + profile store + validator + report 增强）
