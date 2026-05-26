---
date: 2026-05-26
topic: overview-inline-channel-binding
---

# 渠道綁定總覽 — 内联绑定缺卡渠道

## Problem Frame

`/settings` 页面有两个渠道区块,数据来源不同步:

- **渠道綁定總覽**（`#section-dashboard`）由注册表 `active_platforms()` 动态驱动 → 列出**全部** 10 个渠道。
- **发布渠道**（`#section-channels`）由模板**写死的 HTML** 卡片组成 → 只有 6 个。

总览每张卡的 `Configure ↓` 链接指向 `#channel-<name>`,但发布渠道区块没有对应卡片的渠道,链接是**死锚点**,且无处绑定。后果:**txtfyi / mastodon / livejournal** 出现在总览里却无法在 UI 中完成绑定。根因是"注册表驱动 vs 写死 HTML"的结构性漂移。

本次范围:让这 3 个渠道**直接在总览内联完成绑定**,不依赖发布渠道区块补卡。（telegraph 是自动 bootstrap、已绑定,本次不涉及。已工作的 6 张卡保持不动。）

## 三渠道绑定机制对比

| 渠道 | 绑定本质 | 凭证/输入 | 存储 | dofollow |
|---|---|---|---|---|
| **txtfyi** | 免绑定（匿名表单 POST,无账号/cookie/token） | 无 | 无 | uncertain |
| **livejournal** | 账号密码（密码 md5 → hpassword） | username + password | `livejournal-credentials.json` `0o600` | uncertain |
| **mastodon** | 实例地址 + 浏览器登录 | `instance_url` + Chrome profile 登录 | config.toml + per-channel Chrome profile | nofollow |

## Requirements

**总览内联绑定通用行为**
- R1. 总览中的 txtfyi / mastodon / livejournal 三张卡必须能在**总览区块内**完成绑定/配置,不要求用户跳转到发布渠道区块。
- R2. 三张卡的 `Configure ↓` 死锚点必须消除（改为内联展开,或移除该链接）。
- R3. 绑定动作完成后,卡片状态（已綁定/未綁定徽章、identity、last_verified_at）应即时刷新,无需手动刷新整页。
- R4. 沿用现有通用 `/api/<channel>/verify`：每张卡的 "Verify Token" 行为保持可用,用于验证绑定结果。

**txtfyi（免绑定）**
- R5. txtfyi 卡片不显示"未綁定 + 绑定按钮",改为绿色就绪状态徽章「免绑定 · 可直接发布」。
- R6. 提供一个「测试发布 / dry-run」按钮验证渠道连通性（替代绑定按钮的位置）。
- R7. 修正其绑定状态来源,使 txtfyi 不再被报告为 `bound=False` 的误导态（呈现为"无需绑定即就绪"）。

**livejournal（账号密码）**
- R8. 卡片内联展开一个含 **username + password** 两个字段的绑定表单。
- R9. 表单提交走一个新的保存路由,后端调用既有 `store_credentials(config, username, password)`,密码即时派生为 hpassword,凭证文件 `0o600`,明文密码绝不落盘。
- R10. 表单旁必须有醒目警告：**仅使用一次性小号**（凭证为 password-equivalent、不可吊销,只能改密码）。
- R11. 已绑定时支持"更新绑定/重新绑定"（rotation 走同一保存路径）与"清除凭证"。

**mastodon（实例 + 浏览器登录）**
- R12. 卡片内联提供 `instance_url` 输入（如 `https://mastodon.social`），保存到 config.toml `[mastodon] instance_url`。
- R13. 提供"浏览器登录"动作：在该实例对应的 per-channel Chrome profile 中打开登录页,登录态持久化到该 profile,供后续 chrome-backend 发布复用。
- R14. 卡片必须显式标注 **nofollow**（与发布渠道区块现有 nofollow 警示一致），避免误以为是 dofollow 主力渠道。
- R15. 未设置 instance_url 时,"浏览器登录"动作应被禁用并提示先填实例地址。

## Success Criteria
- 在 `/settings` 仅停留在总览区块,即可分别完成 txtfyi（确认免绑定）、livejournal（存账密）、mastodon（设实例 + 登录）三个渠道的就绪。
- 三渠道再无死锚点 `Configure ↓`；总览状态徽章与真实绑定态一致。
- 6 个已工作渠道的绑定流程零回归。

## Scope Boundaries
- **不**退役/重做发布渠道区块的 6 张卡（blogger/medium/velog/ghpages/devto/notion）——本次保持原样。
- **不**处理 telegraph（自动 bootstrap、已绑定;其在总览的 `Configure↓` 死链可顺手移除,但不强求）。
- **不**做 mastodon 多实例支持（单实例;多实例为后续）。
- **不**把发布渠道卡片改成注册表自动生成（"根治漂移"的更大改法）——留作后续。

## Key Decisions
- 绑定入口统一在**总览**内联,而非给缺卡渠道补发布渠道卡片：避免写死 HTML 继续扩张、漂移。
- txtfyi 呈现为「免绑定·可直接发布」而非隐藏:诚实反映其匿名发布特性,且不与 `active_platforms()` 驱动逻辑冲突。
- 三渠道按各自 auth 类型走**不同的内联绑定 UI**（免绑定就绪态 / 账密表单 / 实例+浏览器登录），不强行统一成一种表单。

## Dependencies / Assumptions
- 复用既有 `store_credentials()`（livejournal）、chrome per-channel profile 机制（mastodon,见 `[[reference_chrome_backend_per_channel_profile]]`）、通用 `/api/<channel>/verify`。
- 假设 mastodon 浏览器登录可复用 velog/medium 的 Chrome attach 模式,但 mastodon 当前**不在** bind `CHANNELS` frozenset、也无 `cli/_bind/recipes/mastodon.py` 登录 recipe（见下方待规划项）。

## Outstanding Questions

### Resolve Before Planning
（无 — 产品决策已全部确定）

### Deferred to Planning
- [Affects R13][Technical] mastodon 浏览器登录该走哪条路:扩 bind `CHANNELS` frozenset + 新增 `cli/_bind/recipes/mastodon.py` 登录 recipe（复用现有 bind job 基建）,还是新建一个独立的 instance-aware 登录流程?需评估两条路的改动面与 CHANNELS 安全校验影响。
- [Affects R9][Technical] livejournal 保存路由放在哪个 blueprint（token_paste vs bind vs settings_basic）、CSRF/`_global_csrf_guard` 接入、错误回显格式。
- [Affects R7][Needs research] txtfyi 的 `bound` 判定在 `binding_status.get_channel_status` 里如何产出?需确认改成"免绑定就绪态"是否影响 drift-check 测试与 `active_platforms()` 一致性断言。
- [Affects R6][Technical] txtfyi「测试发布/dry-run」走通用 `/api/<channel>/dry-run` 还是 verify 足矣?确认 dry-run 端点是否对 txtfyi 可用且不产生真实公开 paste。
- [Affects R3][Technical] 内联绑定后状态刷新:复用 `channel-binding.js` 的 `renderResult`/局部刷新,还是各动作返回最新 status JSON 后局部重渲染卡片?

## Next Steps
→ `/ce:plan` for structured implementation planning
