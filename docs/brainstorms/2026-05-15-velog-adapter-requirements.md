---
date: 2026-05-15
topic: velog-adapter
---

# 新增发布平台:velog.io 适配器

## Problem Frame

现有发布矩阵只覆盖 `blogger` 与 `medium`,扩量需要新平台。velog.io 候选理由:Markdown 原生、外链 dofollow、`v3.velog.io/graphql` 接口存活且 introspection 通过、`writePost` mutation schema 明确。

**但 velog 的成立条件比 telegraph 复杂得多**,本需求把这些不确定性提前到 Phase 0 spike:

1. velog 是韩语开发者社区 —— 与目标市场关键词的话题相关性需要回答
2. velog **没有官方 API**,使用内部 GraphQL,稳定性无 SLA;CSRF / Origin / UA 等隐式约束未验证
3. cookie JWT TTL 未知,可能短到使"批跑全程无人值守"破产
4. 是否会被风控、是否会封号 —— 内部 API + 批量自动发布是高风险组合

## Architecture

```
publish-backlinks (现有调度) ─┬─→ adapters/blogger_api.py            (REST + Google OAuth)
                              ├─→ adapters/medium_api.py / browser
                              ├─→ adapters/telegraph_api.py          [由独立需求文档定义]
                              └─→ adapters/velog_graphql.py   [新]   (GraphQL,cookie JWT,Markdown 原生)

新 CLI 子命令:
  backlink-publisher velog-login   → 有头 Playwright,社交登录后导出 cookie/storage_state → velog-cookies.json
```

## Phase 0 — 前置门:可达性 spike + 市场相关性论证

**只有 Phase 0 全部达标,才进入 Phase 1 适配器实现**。

- P0-1. **端到端最小 mutation 实测**:用一个真实 velog 账号(社交登录),手动通过浏览器抓出 cookie + 全部必需头部(CSRF / Origin / UA),用 curl 复现一次 `writePost(title, body, tags=[], is_markdown=true, is_temp=false, is_private=false)` 成功发布并在公开页面上可见;若 `url_slug` 或 `meta` 实际必需,R10 需相应调整
- P0-2. **JWT 存储位置确认**:Playwright 登录后同时 dump `context.cookies()` 与 `page.evaluate('Object.entries(localStorage)')`,确定 JWT 实际位置(cookie 还是 LocalStorage);决定持久化用 `velog-cookies.json` 还是 Playwright `storage_state` 全量
- P0-3. **Token TTL 实测**:登录后 1h / 6h / 24h / 72h 各发一次 mutation,记录 `access_token` 与 `refresh_token` 何时过期、是否需要主动刷新;若 TTL < 6h,R11"不自动重登"前提下"批跑无人值守"无法成立 —— 需要回到 brainstorm 评估
- P0-4. **市场相关性论证**:目标 backlink 关键词与 velog 受众(主要韩语开发者)的相关性 —— 若发文用韩语则需 译翻 / 找母语撰稿能力(本轮不在 scope);若发英文文章则需运营侧确认"非母语社区英文 backlink"在目标市场 GSC 中能产生 referring domain 信号
- P0-5. **手工索引实验(对照 telegraph)**:发 5 篇真实 velog post,14 天后核对 Google `site:velog.io/<user>/<slug>` 收录率与目标站 GSC referring URL 注册
- P0-6. **达标线**:P0-1 成功 + P0-3 中 token TTL ≥ 24h + P0-5 索引率 ≥ 70% + P0-4 由运营给出明确决断("用韩语 / 用英语 / 不接入")

不达标时:重新进入 brainstorm,讨论"是否需要 velog 浏览器 fallback 适配器"或"换 dev.to / hashnode 等英文开发者社区"。

## Requirements

**Platform 注册与契约(共享 with telegraph 适配器需求文档)**
- R1. `SUPPORTED_PLATFORMS` 扩展加入 `"velog"`;schema 校验、CLI `--platform`、`publish_backlinks` 调度均能识别
- R2. 适配器返回的 `AdapterResult` 字段:`status` ∈ `{"published","failed"}`(本平台 V1 不发草稿);`draft_url` 保持空;`platform="velog"`;`adapter="velog-graphql"`
- R3. 必须经过 `verify_publish` + `link_attr_verifier` 后置校验,失败 `status="failed"`

**velog.io 发布**
- R8. 通过 `https://v3.velog.io/graphql` 的 `writePost` mutation 发布,鉴权使用 cookie JWT(或 `storage_state`,由 Phase 0 P0-2 决定),由 `velog-login` 首次以有头 Playwright 完成社交登录后导出
- R9. 凭证持久化路径 `~/.config/backlink-publisher/velog-cookies.json`(0600);config.toml 提供 `[velog]` 段覆盖 `cookies_path`、`username`(显示用,不参与认证)
- R10. mutation 参数(以 Phase 0 P0-1 实测为准):`title`, `body`(原始 Markdown), `tags: []`, `is_markdown: true`, `is_temp: false`(明确直接发布、非默认值), `is_private: false`;其他字段以 P0-1 探活结果为准
- R11. cookie 过期(GraphQL 返回 `NOT_LOGGED_IN` / 401-等价错误)时:**不自动重登**,抛 `DependencyError("velog cookie expired, run `velog-login` again")`,与现有 `blogger` OAuth 失效语义对齐

**幂等性与重跑(共享)**
- R12. checkpoint 中已存在 `published_url` 的 row 直接跳过;V1 不实现 `editPost` 自动覆盖
- R12b. checkpoint key 内容感知延后到独立 brainstorm

**Operator UX(共享)**
- R14. 凭证缺失 / 过期时,错误信息必须包含具体修复命令(`Run: backlink-publisher velog-login`)
- R15. WebUI 下拉新增 `velog` 选项(数据源 `SUPPORTED_PLATFORMS`),凭证未配置时指向 `velog-login`;不做 in-webui 登录面板

**凭证安全(本平台特有)**
- R16. **凭证作用域**:`velog-login` 在 Playwright 导出 cookie 时必须按域名过滤,仅保留 `velog.io` / `*.velog.io` 域名下的 cookie/storage,**不得**把 Google/GitHub/Facebook 等社交登录 IdP 的 cookie 写入持久化文件
- R17. 日志脱敏:`Cookie`、`Authorization`、`access_token`、`refresh_token` 等字段在请求/响应日志中必须脱敏
- R18. **风控/封号防御**:
  - 单账号单日发布上限(planning 时校准,初步 30 篇)
  - 每条 mutation 间随机抖动 ≥ 30s
  - User-Agent 与登录浏览器一致(从 Playwright session 同步)
  - **Scope 共识**:运营者使用**专用账号**而非主身份(velog 账号封禁是已接受的失败模式)

## Success Criteria

**SEO 结果度量(主指标)**
- 上线后 30 天:本平台发布的页面 ≥ 70% 被 Google 索引,且 ≥ 50% 在目标站 GSC 中作为 referring URL 出现
- 上线后 30 天:本平台所有发布页面 100% 保持 dofollow

**流水线正确性(辅助)**
- 同一份 `targets.csv` 可以 `--platform velog` 跑通 plan → publish → verify 闭环
- `velog-login` 一次有头浏览器登录后,**单次典型批跑(由 P0-3 token TTL 反推批跑时长上限)内** 无人值守
- 单条 mutation → verify_publish 完成 < 5s(p95),不含 verify retry 等待
- cookie 失效信息一眼能看出修复命令

## Scope Boundaries

- **不做** 自动 cookie 续期 / 自动重登:cookie 过期一律提示运营者跑 `velog-login`
- **不做** velog 草稿态 / 私有发布 / 系列(series_id)支持:V1 全部 `is_temp=false, is_private=false`
- **不做** `editPost` 自动覆盖:V1 重跑跳过已发布;内容修正工作流需手动平台删除 + 重置 checkpoint 行
- **不做** in-webui 登录面板
- **不做** velog 浏览器 fallback 适配器:V1 仅 GraphQL 直发;Phase 0 中 GraphQL 路径不通时回到 brainstorm 重评估
- **不做** velog 韩语翻译管道:Phase 0 P0-4 决定语种,V1 单语种发布
- **5xx 接受 fail-fast**

## Key Decisions

- **GraphQL 直发 + 首次有头 Playwright 拿凭证**:理由 —— GraphQL 路径单条 <2s,比纯浏览器自动化快 15 倍(此为理论值,Phase 0 P0-1 实测确认);cookie 失效的代价(一次 30s 有头登录)远低于持续维护浏览器自动化脚本。**但前提是 Phase 0 P0-1/P0-3 均达标**
- **Phase 0 把"端到端 mutation 探活"作为前置门**:理由 —— Schema introspection 与运行时成功是两件事;CSRF/Origin/UA 隐式约束、cookie TTL、token 刷新行为都未实测;V1 没有 fallback,不能等上线才发现
- **专用账号 + 频率上限 + 抖动作为防御性接受**:velog 账号封禁是已知风险;限频不能保证不封,但能延后封禁阈值并保护运营者主身份
- **凭证作用域过滤强制**:社交登录会在 Playwright context 里产生 IdP cookie(Google/GitHub),不过滤会把这些写入本地文件,泄露后等于交出主账号

## Alternatives Considered

| 候选 | 类型 | 官方 API | dofollow | 风控风险 | 受众语种 | 选择理由 |
|---|---|---|---|---|---|---|
| **velog.io** ⚠️ | 韩文开发者社区 | ❌(内部 GraphQL) | 是 | 中-高(私有 API) | 韩语 | Phase 0 验证后决定 |
| dev.to | 英文开发者社区 | ✅(API key) | 是 | 低 | 英文 | 受众更广、API 摩擦低;**强候选,Phase 0 中应同时手工 baseline** |
| hashnode | 英文开发者社区 | ✅(GraphQL) | 是 | 低 | 英文 | API 形态相近、官方端点;**Phase 0 中应同时手工 baseline** |
| qiita / zenn | 日语开发者社区 | ✅ | 是 | 低 | 日语 | 与目标市场关键词相关性弱;暂不接入 |

**结论**:velog 是不是正确选择,本身就是 Phase 0 P0-4 + P0-5 的产出。如果 P0 显示 velog 索引率低或语种不匹配,**应直接转向 dev.to 或 hashnode 重启 brainstorm**,而不是硬上 velog。

## Dependencies / Assumptions

- Playwright Python(`medium-browser` 在用版本) + `playwright install chromium` 已在目标环境完成;`velog-login` 启动失败应抛 `DependencyError` 并提示安装命令
- 假设 `https://v3.velog.io/graphql` 的 `writePost` mutation schema 在交付窗口内保持稳定(本调研当日 introspection 通过)—— 高于 telegraph 的不确定性,需在 planning 阶段评估 schema 漂移监测策略
- 假设运营者使用的 velog 账号已通过 Google/Github/Facebook 之一完成首次注册并能正常网页登录

## Outstanding Questions

### Resolve Before Planning

(无 —— Phase 0 spike 由 plan 的 Unit 1 承载,非 brainstorm 阻塞)

### Deferred to Planning(Phase 0 spike 输出会回填到 R8/R9/R10/R11)

- [Affects R10, R11][Phase 0 spike] 端到端 mutation 实测结果(参数集 / 必需头部 / CSRF 行为)
- [Affects R8/R9][Phase 0 spike] JWT 存储位置确认 —— 决定持久化文件结构是 cookie jar 还是 storage_state
- [Affects R11, Success Criteria][Phase 0 spike] token TTL 实测 —— 决定"无人值守"窗口长度
- [Affects R8] velog GraphQL 错误返回格式(`errors[].extensions.code`)的具体取值,如何映射 `DependencyError` vs `ExternalServiceError`
- [Affects R8-R11] HTTP 客户端复用 `requests` 还是引入 `httpx`/`gql`;以及 GraphQL `HTTP-200-with-errors[]` 失败模式如何对接 `retry.py` 的 `is_retryable` 谓词
- [Affects R8] schema 漂移监测:是否设一个 daily smoke `writePost` canary,失败时通知运营;否则破坏式 schema 变更会静默腐蚀 checkpoint
- [Affects R18] 限频参数(每日 N 篇、抖动下限 M 秒)在 planning 中校准的依据 —— 是否参考社区已知封号阈值

## Next Steps

→ Phase 0 spike(运营 + 工程联合执行,~1 人日 + 14 天等待)→ 达标后 `/ce:plan` 进入 Phase 1 适配器实现规划
