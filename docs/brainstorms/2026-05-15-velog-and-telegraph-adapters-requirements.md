---
date: 2026-05-15
topic: velog-and-telegraph-adapters
status: superseded
---

# 新增发布平台:telegra.ph 与 velog.io 适配器

> **本文已 superseded(2026-05-15 当日)**。document-review 后采纳"拆两份 + 加 Phase 0 索引实验前置门"的建议,拆分为:
> - `2026-05-15-telegraph-adapter-requirements.md`(自含 Phase 0)
> - `2026-05-15-velog-adapter-requirements.md`(自含 Phase 0)
>
> 本文保留作历史记录与设计推演脉络;**进入 planning 时请引用拆分后的两份文档**。

## Problem Frame

现有发布矩阵只覆盖 `blogger` 与 `medium`(`src/backlink_publisher/schema.py:26` `SUPPORTED_PLATFORMS`)。两者都受限于:Blogger 单账号 quota / Medium 站点权重逐年走弱。**为继续扩大外链分布并降低单点平台风险,需要引入两个新平台**,在保留现有 `AdapterResult` 契约、`verify_publish` 后置校验、`checkpoint`/幂等性等通用流水线的前提下,补两个不同形态的入口:

- **telegra.ph** —— 官方 REST API、零审核、Google 索引快;实测外链 `rel=null`(即 dofollow);0 摩擦扩量样板
- **velog.io** —— 韩国开发者博客,**无官方 API**,但内部 GraphQL `https://v3.velog.io/graphql` 存活且 schema 明确,Markdown 原生支持,外链 dofollow

两者机制差异极大(REST vs 内部 GraphQL、Node 树 vs Markdown、无草稿 vs 有草稿/私有态、零认证 vs JWT),需要在设计上**承认非对称**而不是强行抽象统一。

## Architecture

```
publish-backlinks (现有调度) ─┬─→ adapters/blogger_api.py            (REST + Google OAuth)
                              ├─→ adapters/medium_api.py / browser   (双轨)
                              ├─→ adapters/telegraph_api.py   [新]   (REST,token,Node 树)
                              └─→ adapters/velog_graphql.py   [新]   (GraphQL,cookie JWT,Markdown 原生)

新 CLI 子命令:
  backlink-publisher telegraph-init   → createAccount,token 写 ~/.config/backlink-publisher/telegraph-token.json
  backlink-publisher velog-login      → 有头 Playwright,社交登录后导出 cookie 写 velog-cookies.json
```

| 适配器 | 传输 | 认证 | 内容格式 | 草稿态 | 单条耗时 | 编辑能力 |
|---|---|---|---|---|---|---|
| `telegraph-api` | HTTPS REST | `access_token`(本地) | NodeElement JSON 树 | 无(直接公开) | <1s | `editPage` 需保留 token |
| `velog-graphql` | HTTPS GraphQL | cookie JWT(社交登录派生) | 原生 Markdown(`is_markdown=true`) | 有(`is_temp`) | <2s | `editPost(id, ...)` |

## Requirements

**Platform 注册与契约**
- R1. `SUPPORTED_PLATFORMS` 扩展加入 `"telegraph"` 与 `"velog"`;schema 校验、CLI `--platform` 选项、`publish_backlinks` 调度均能识别两个新值
- R2. 新适配器返回的 `AdapterResult` 字段语义与现有平台一致:`status` ∈ `{"published","failed"}`(两者均无 `drafted` 态使用场景,见 R10、R12);`draft_url` 保持空字符串;`platform` 分别为 `"telegraph"` / `"velog"`;`adapter` 字符串为 `"telegraph-api"` / `"velog-graphql"`。注:base.py 允许的 `"drafted"` 取值在这两个适配器中不会出现,与现有 Blogger 可能产生草稿态不同 —— 在 planning 阶段确认是否需保留 `_unverified` 后缀语义(见 `src/backlink_publisher/verify_publish.py`)
- R3. 两个适配器都必须经过现有 `verify_publish`(`src/backlink_publisher/verify_publish.py`)后置校验:target_url 必须出现在已发布页面并通过 `src/backlink_publisher/adapters/link_attr_verifier.py` 的 rel/target 属性校验,失败则 `status="failed"` 并保留 `error`

**telegra.ph 适配器**
- R4. 通过 `https://api.telegra.ph/createPage` 发布,鉴权使用本地持久化的 `access_token`(由 `telegraph-init` 子命令首次生成并保存)
- R5. 实现 Markdown → Telegraph Node 树转换器,需支持 telegra.ph 允许的标签子集(`a, p, h3, h4, b, em, i, strong, u, s, br, blockquote, code, pre, ul, ol, li, hr, img, figure, figcaption`);锚文本链接必须正确编码为 `{"tag":"a","attrs":{"href":...},"children":[...]}`
- R6. `telegraph-init` 子命令调用 `createAccount(short_name, author_name, author_url)` 并把返回的 `access_token` 写入 `~/.config/backlink-publisher/telegraph-token.json`(权限 600);config.toml 提供 `[telegraph]` 段覆盖 `short_name`、`author_name`、`author_url`、`token_path`
- R7. 单条发布失败(包括 4xx 与 5xx)走现有 `retry.py` 重试栈(`RETRYABLE_HTTP_STATUSES` 沿用),不引入特殊路径

**velog.io 适配器**
- R8. 通过 `https://v3.velog.io/graphql` 的 `writePost` mutation 发布,鉴权使用 cookie JWT(`access_token` / `refresh_token`),由 `velog-login` 子命令首次以有头 Playwright 完成社交登录后导出
- R9. cookie 持久化路径 `~/.config/backlink-publisher/velog-cookies.json`(权限 600);config.toml 提供 `[velog]` 段覆盖 `cookies_path`、`username`(显示用,不参与认证)
- R10. mutation 参数:`title`, `body`(原始 Markdown), `tags: []`, `is_markdown: true`, `is_temp: false`(明确直接发布、不走草稿态;非默认值,而是有意选择), `is_private: false`(无私有发布)。其他字段(`url_slug`、`thumbnail`、`meta`、`series_id`)不传 —— planning 阶段需先用一次端到端 mutation 验证此最小参数集是否被服务端接受(见 Deferred 问题)
- R11. cookie 过期(GraphQL 返回 `NOT_LOGGED_IN` / 401-等价错误)时:**不在批跑里自动重登**,而是抛 `DependencyError("velog cookie expired, run `velog-login` again")`,与现有 `blogger` OAuth 失效语义对齐

**幂等性与重跑**
- R12. 与现有 `blogger`/`medium` 一致:`checkpoint` 中已存在 `published_url` 的 row 直接跳过;不在 V1 实现自动 `editPage`/`editPost` 覆盖(避免引入新的语义差异)
- R13. 两个适配器都参与现有 `verify-by-listing` / `publish-idempotency` 后置校验流程的等价行为:发布成功后 `published_url` 写回 checkpoint 即为终态

**Operator UX**
- R14. 两个 `*-init` / `*-login` 子命令在凭证缺失或过期时,出错信息必须包含具体修复命令(如 `Run: backlink-publisher velog-login`),沿用现有 blogger OAuth 的 operator-friendly 提示风格
- R15. WebUI(`webui.py`)的平台选择下拉新增两个选项(数据源沿用 `schema.py:SUPPORTED_PLATFORMS`,无独立硬编码);凭证尚未配置时显示提示语,链接到对应 CLI 命令(`telegraph-init` / `velog-login`);**不**做 in-webui 登录面板
- R16. **凭证作用域**:`velog-login` 在 Playwright 导出 cookie 时必须按域名过滤,仅保留 `velog.io` / `*.velog.io` 域名下的 cookie,不得把 Google/GitHub/Facebook 等社交登录 IdP 的 cookie 写入持久化文件
- R17. **日志脱敏**:两个适配器的请求/响应日志必须脱敏 `access_token`(query/form/header)、`Cookie`、`Authorization` 字段,确保 token 与 JWT 不出现在任何 INFO/WARN/ERROR 输出中

## Success Criteria

- 同一份 `targets.csv` 可分别以 `--platform telegraph` 和 `--platform velog` 跑通 plan→publish→verify 闭环,两个平台发布的页面都通过 `link_attr_verifier` 检查
- 首次安装的运营者按 README 一条命令完成 `telegraph-init`,无浏览器交互;`velog-login` 一次有头浏览器登录后,后续批跑全程无人值守
- `velog` 单条发布从 GraphQL 调用到 `verify_publish` 完成 < 5s(p95);`telegraph` < 3s(p95)
- cookie/token 失效时,失败信息一眼能看出怎么修复(不需要查代码或文档)

## Scope Boundaries

- **不做** 自动 cookie 续期 / 自动重登:velog cookie 过期一律提示运营者跑 `velog-login`
- **不做** Telegraph 多账号 / 多 `short_name` 切换:V1 单 operator 单账号(注:Telegraph `short_name` 仅作展示字段,不参与账号识别;每次 `telegraph-init` 都铸造一个全新独立账号/token,丢失 token 文件即永久失去对历史 page 的编辑权)
- **不做** velog 草稿态 / 私有发布 / 系列(series_id)支持:V1 全部 `is_temp=false, is_private=false`,无 series
- **不做** Telegraph `editPage` / velog `editPost` 自动覆盖路径:V1 重跑跳过已发布;**内容修正工作流(改 anchor / 改 target_url)需运营者手动在平台上删除原 page 后重置 checkpoint 行,V1 不提供内置删除-重发助手**
- **不做** in-webui 登录面板:WebUI 仅展示平台选项,凭证管理沿用 CLI 子命令引导(避免登录组件与 webui.py 现有架构冲突)
- **不做** velog 浏览器 fallback 适配器:V1 仅 GraphQL 直发;若长期遇到风控再单独立项
- **5xx 接受 fail-fast**:`retry.py` 现行策略仅重试 429;telegra.ph / velog 在 5xx 时即 `status="failed"`,运营者手动重跑(与 Blogger/Medium 现行行为一致)

## Key Decisions

- **velog 走 GraphQL 直发 + 首次有头 Playwright 拿 cookie**:理由 —— `v3.velog.io/graphql` 实测存活、支持 introspection、`writePost` mutation 接受 `is_markdown=true` 可零转换发 Markdown,单条 <2s,比纯浏览器自动化快 15 倍;cookie 失效的代价(一次 30s 有头登录)远低于持续维护浏览器自动化脚本
- **telegra.ph 实测外链是 dofollow** (`rel=null` + `target=_blank`),与之前刻板印象相反 —— 因此正式接入,而不是仅作为索引信号源
- **两个平台都跳过已发布,不做 upsert**:理由 —— 与现有 `blogger`/`medium` 行为对齐,认知负担最低;如需更新内容,运营者手动重置 checkpoint
- **交付顺序:先 telegra.ph 再 velog**:理由 —— telegraph 技术风险最低(官方 REST + 单 token),先用它把"新增平台"的项目级管道(schema、CLI 选项、verify_publish 钩子)走通,作为 velog 的样板,减少 velog PR 的杂项变更
- **认证入口走 CLI 子命令而非 WebUI**:理由 —— 与现有 `blogger-init`(OAuth) 风格一致,避免在 webui.py 引入异构的登录组件;有头浏览器与 CLI 都对运营更直接
- **`telegraph-init` 显式触发,不在首次 publish 时自动 createAccount**:与 `blogger-init` OAuth 显式触发风格对齐;首次 publish 遇到无 token 直接报错并提示 `Run: backlink-publisher telegraph-init`
- **凭证存储模型与现有 Blogger OAuth 平齐**:`~/.config/backlink-publisher/` 下 0600 plaintext JSON;威胁模型为"单 operator 本地机器、信任 root/同 uid 进程";Telegraph token 实际上长期有效(无服务端过期),泄露后须运营者手动调 `revokeAccessToken` 处理 —— 此为已知风险,V1 不引入系统 keychain

## Dependencies / Assumptions

- Playwright Python(`medium-browser` 在用版本) + `playwright install chromium` 已在目标环境完成;`velog-login` 启动失败应抛 `DependencyError` 并提示安装命令
- 假设 `https://v3.velog.io/graphql` 的 `writePost` mutation schema 在交付窗口内保持稳定(本调研当日 introspection 通过、字段与 velopert/velog-server 仓库 master 一致)
- 假设 telegra.ph 外链 dofollow 行为保持(本调研当日实测一条新建 page 验证为 `rel=null` + `target=_blank`)
- 假设运营者使用的 velog 账号已经通过 Google/Github/Facebook 之一完成首次注册并能正常网页登录

## Outstanding Questions

### Resolve Before Planning

(无)

### Deferred to Planning

- [Affects R5][Technical] Markdown → Telegraph Node 树转换器是自己写(基于 `mistune` 或 `markdown-it-py` AST 遍历),还是先 md→html 再 html→Node?后者更易借用现有 `markdown_utils.py`,但需要 HTML 标签白名单过滤
- [Affects R8/R9][Needs research] velog 一次 10 分钟 Playwright spike:同时 dump `context.cookies()` 与 `localStorage` 全键,确定 JWT 实际存储位置 → 若仅在 cookies 则用 `velog-cookies.json` 现方案;若 LocalStorage 参与则改用 Playwright `storage_state` 全量持久化。同时记录 `access_token` 与 `refresh_token` 的 TTL,用以确认 R11 "不自动重登" 对典型批跑时长可承受
- [Affects R10][Technical] `writePost` mutation 在 schema 上所有参数都可选,但实际 velog 后端可能对缺省字段有隐式约束(比如必须给 `url_slug` 或 `meta`);planning 阶段必须先用一次端到端 mutation 探活、补齐必要字段、记录 CSRF / Origin / UA 头部需求,产出一份可复现的 curl 基线
- [Affects R11][Technical] velog GraphQL 错误返回格式(`errors[].extensions.code`)的具体取值需在实现阶段实测后再决定如何映射到 `DependencyError` vs `ExternalServiceError`
- [Affects R4-R7][Technical] GraphQL/REST HTTP 客户端选择:复用现有 `requests`(已是依赖,POST JSON 即可)还是引入 `httpx` / `gql`(类型化查询)?倾向 `requests`,避免新增依赖,但需在 planning 时确认 retry.py 的 `is_retryable` 谓词与 GraphQL `HTTP-200-with-errors[]` 失败模式如何对接

## Next Steps

→ `/ce:plan` 进入实施规划。建议两个适配器拆两份 plan 文档,按"先 telegra.ph、后 velog"顺序排期。
