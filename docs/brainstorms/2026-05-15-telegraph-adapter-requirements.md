---
date: 2026-05-15
topic: telegraph-adapter
---

# 新增发布平台:telegra.ph 适配器

## Problem Frame

现有发布矩阵只覆盖 `blogger` 与 `medium`(`src/backlink_publisher/schema.py:26` `SUPPORTED_PLATFORMS`),两者都受单平台权重 / 单账号 quota 风险。telegra.ph 作为低摩擦扩量样板被纳入接入候选:

- 官方公开 REST API `https://api.telegra.ph/`,长期稳定、零审核
- 实测外链 `rel=null`(无 `nofollow`)+ `target="_blank"`(2026-05-15 当日单页验证)
- 单条发布 <1s,可作为"新平台接入"项目级管道的样板

**但这两项价值断言(dofollow + 索引可达)目前都建立在 N=1 的样本上**,本需求把它们升级为 **Phase 0 前置门(gate condition)**,而不是上线后再验证的假设。

## Architecture

```
publish-backlinks (现有调度) ─┬─→ adapters/blogger_api.py            (REST + Google OAuth)
                              ├─→ adapters/medium_api.py / browser
                              └─→ adapters/telegraph_api.py   [新]   (REST,token,Node 树)

新 CLI 子命令:
  backlink-publisher telegraph-init   → createAccount,token 写 ~/.config/backlink-publisher/telegraph-token.json
```

## Phase 0 — 前置门:索引可达性 / dofollow 稳定性手工实验

**只有 Phase 0 全部达标,才进入 Phase 1 适配器实现**。

- P0-1. 手工创建 **10 个** telegra.ph 页面,分组覆盖:每页 1 / 3 / 5 个外链各占 3~4 页;域名混合(主目标 + 通用 TLD + 受怀疑 TLD)
- P0-2. 发布后立即抓取 HTML 校验每个 `<a>` 的 `rel` 与 `target` 属性,记录"页内多链接是否仍全 dofollow"
- P0-3. 7 天后重新抓取同一批 URL,校验 `rel` 是否被服务端回填 `nofollow`(若为是,降级为"仅索引信号源",P0 即失败)
- P0-4. 14 天后用 `site:telegra.ph/<page-slug>` 查询 Google,且若运营者有目标站 GSC 权限,核对页面是否出现在 "Links → Top linking sites" 中
- P0-5. **达标线**:`≥ 70% 页面 14 天内被 Google 索引` 且 `100% 页面外链保持 rel=null/dofollow`
- P0-6. 同时手工 baseline 一份 dev.to / hashnode / qiita 等候选英文开发者社区平台(取一个即可),用相同指标对照,用于回答"为什么是 telegra.ph 而不是其他平台"

不达标时:本需求重新进入 brainstorm,讨论是否值得续做;不直接进入适配器开发。

## Requirements

**Platform 注册与契约(共享 with velog 适配器需求文档)**
- R1. `SUPPORTED_PLATFORMS` 扩展加入 `"telegraph"`;schema 校验、CLI `--platform` 选项、`publish_backlinks` 调度均能识别该值
- R2. 适配器返回的 `AdapterResult` 字段:`status` ∈ `{"published","failed"}`(本平台无 `drafted` 态);`draft_url` 保持空字符串;`platform="telegraph"`;`adapter="telegraph-api"`。注:`_unverified` 后缀语义在 planning 阶段对齐 `verify_publish.py` 现行约定后决定
- R3. 必须经过现有 `verify_publish`(`src/backlink_publisher/verify_publish.py`)后置校验:target_url 出现在已发布页面并通过 `src/backlink_publisher/adapters/link_attr_verifier.py` 的 rel/target 属性校验;失败 `status="failed"` 并保留 `error`(planning 阶段先验证 `link_attr_verifier` 把"rel 缺省"识别为 dofollow,否则需要扩展)

**telegra.ph 发布**
- R4. 通过 `https://api.telegra.ph/createPage` 发布,鉴权使用本地持久化 `access_token`
- R5a. **MVP Markdown→Node 转换器**:仅支持核心标签 `a, p, h3, ul, ol, li, b, em, strong, br`;未支持标签的内容**降级为 `p` 包裹纯文本**(不抛错),并在转换日志中记录"降级条数"。覆盖 backlink 文章的 80% 真实需求(链接、段落、二级标题、列表)
- R5b. **(延后到 V2,不在本需求范围)** 扩展标签集 `i, u, s, blockquote, code, pre, hr, img, figure, figcaption` —— 仅当 Phase 0 验证通过且 V1 真实运营 4 周后,根据降级日志命中频率决定是否补做
- R6. `telegraph-init` 子命令调用 `createAccount(short_name, author_name, author_url)` 并把返回的 `access_token` 写入 `~/.config/backlink-publisher/telegraph-token.json`(0600);config.toml 提供 `[telegraph]` 段覆盖 `short_name`、`author_name`、`author_url`、`token_path`
- R7. 单条发布的 4xx / 5xx 行为沿用 `retry.py` 现策略(仅重试 429),5xx fail-fast 与现有 Blogger / Medium 一致

**幂等性与重跑(共享)**
- R12. 与现有 `blogger`/`medium` 一致:`checkpoint` 中已存在 `published_url` 的 row 直接跳过,本轮 V1 不实现 `editPage` 自动覆盖
- R12b. checkpoint key 内容感知 **延后到独立 brainstorm**(原 adversarial finding #6);V1 仍以 `(row_id, platform)` 为 key,内容变更场景下运营者通过 `--force-row <id>` 单行重置(planning 阶段命名待定)

**Operator UX(共享)**
- R14. `telegraph-init` 在凭证缺失 / 过期 / 被 revoke 时,错误信息必须包含具体修复命令(`Run: backlink-publisher telegraph-init`),并文档化"凭证泄漏响应路径":运营者调 `revokeAccessToken` 后重跑 `telegraph-init`
- R15. WebUI(`webui.py`)的平台选择下拉新增 `telegraph` 选项(数据源沿用 `schema.py:SUPPORTED_PLATFORMS`,无独立硬编码);凭证未配置时显示提示语并指向 `telegraph-init`;不做 in-webui 登录面板

**凭证安全(共享)**
- R17. 日志脱敏:所有 telegra.ph 调用的请求/响应日志必须脱敏 `access_token`(query/form/header)字段;不出现在 INFO/WARN/ERROR 中

## Success Criteria

**SEO 结果度量(主指标,无此项不算通过)**
- 上线后 30 天:本平台发布的页面 ≥ 70% 被 Google 索引(用 `site:` 或 GSC 验证),且 ≥ 50% 在目标站 GSC "Links → Top linking sites" 中作为 referring URL 出现
- 上线后 30 天:本平台所有发布页面 100% 保持 `rel=null/dofollow`(每周抽样 10 页校验)

**流水线正确性(辅助指标)**
- 同一份 `targets.csv` 可以 `--platform telegraph` 跑通 plan → publish → verify 闭环
- 首次安装的运营者按 README 一条命令完成 `telegraph-init`,无浏览器交互
- 单条发布 GraphQL → verify_publish 完成 < 3s(p95)
- 凭证失效时,失败信息一眼能看出修复命令

## Scope Boundaries

- **不做** Telegraph 多账号 / 多 `short_name` 切换:V1 单 operator 单账号(`short_name` 仅作展示字段,不参与账号识别;每次 `telegraph-init` 都铸造一个全新独立账号/token,丢失 token 文件即永久失去对历史 page 的编辑权)
- **不做** `editPage` 自动覆盖:V1 重跑跳过已发布;**内容修正工作流(改 anchor / 改 target_url)需运营者手动在平台上删除原 page 后重置 checkpoint 行**
- **不做** in-webui 登录面板:WebUI 仅展示平台选项,凭证管理沿用 CLI 子命令引导
- **不做** R5b 扩展标签集:V1 限定核心标签 + 降级,见 R5a
- **不做** checkpoint 内容 hash 感知:见 R12b
- **5xx 接受 fail-fast**:`retry.py` 现行策略仅重试 429;telegra.ph 在 5xx 时即 `status="failed"`,运营者手动重跑

## Key Decisions

- **Phase 0 索引实验作为前置门**:理由 —— "dofollow + 索引可达"是整个平台价值主张的根基,N=1 实验不够;14 天 + 10 页 + 多 TLD 是低成本高置信度的方式;未达标即放弃,避免几周工程打水漂
- **R5 切 MVP**:理由 —— 17 个标签的完整 markdown 兼容占整体 ~60% 代码量,但 backlink 文章 80% 只需要"链接 + 段落 + 标题 + 列表";降级为 `p` 包裹是无副作用的兜底
- **凭证存储模型与现有 Blogger OAuth 平齐**:`~/.config/backlink-publisher/` 下 0600 plaintext JSON;威胁模型为"单 operator 本地机器、信任 root/同 uid 进程";Telegraph token 实际上长期有效(无服务端过期),泄露后须运营者手动调 `revokeAccessToken` —— 此为已知风险,V1 不引入系统 keychain
- **`telegraph-init` 显式触发,不在首次 publish 时自动 createAccount**:与 `blogger-init` OAuth 显式触发风格对齐

## Alternatives Considered

| 候选 | 类型 | 官方 API | dofollow | 接入摩擦 | 受众语种 | 选择理由 |
|---|---|---|---|---|---|---|
| **telegra.ph** ✅ | 轻量发布器 | ✅ | 实测 rel=null(待 Phase 0 验证) | 极低(REST + token) | 全语种(无社区) | 项目级管道样板;0 摩擦扩量 |
| dev.to | 英文开发者社区 | ✅(API key) | dofollow | 低 | 英文 | Phase 0 baseline 对照之一 |
| hashnode | 英文开发者社区 | ✅(GraphQL) | dofollow | 中 | 英文 | Phase 0 baseline 对照之一 |
| qiita / zenn | 日语开发者社区 | ✅ | dofollow | 中 | 日文 | 受众语种与目标市场不匹配,本轮不接 |
| write.as / Notion 公开页 | 轻量发布器 | 部分 ✅ | 多为 nofollow | 中 | 全语种 | dofollow 不稳;不优先 |

**结论**:telegra.ph 与 dev.to/hashnode 一起进入 Phase 0 baseline 对照实验。Phase 0 数据决定 telegra.ph 是否值得进入 Phase 1。

## Dependencies / Assumptions

- 假设 telegra.ph 外链 dofollow 与索引可达性 **由 Phase 0 验证后** 成立;不再作为隐含假设
- 假设 telegra.ph API(`api.telegra.ph`)在交付窗口内保持稳定(过去 8+ 年无破坏性变更,低风险)

## Outstanding Questions

### Resolve Before Planning

(无)

### Deferred to Planning

- [Affects R5a][Technical] Markdown → Telegraph Node 树转换器实现路径:基于 `markdown_utils.py` 现有 md→html 输出再做 html→Node 二级转换(可复用,需要 HTML 标签白名单过滤)还是用 `mistune` / `markdown-it-py` 直接遍历 AST?planning 阶段评估后决定
- [Affects R3][Technical] `link_attr_verifier.py` 当前是否把"rel 属性缺省"识别为 dofollow?若否,本需求隐含包含一个小幅扩展(planning 阶段加 1 条测试用例验证)
- [Affects R4-R7][Technical] HTTP 客户端复用 `requests`(已是依赖)还是引入 `httpx`?倾向 `requests`,planning 时确认

## Next Steps

→ Phase 0 索引实验(运营手工执行 14 天)→ 达标后 `/ce:plan` 进入 Phase 1 适配器实现规划
