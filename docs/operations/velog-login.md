# Velog 操作指南

## 概述

velog.io 没有官方 API。本工具通过内部 `v2.velog.io/graphql` 的 `writePost` mutation 发布，
鉴权使用社交登录（Google / GitHub / Facebook）产生的 cookie 文件。

---

## 首次登录

### 前置要求

```bash
pip install playwright
playwright install chromium
```

### 步骤

```bash
velog-login
```

1. 浏览器自动弹出。
2. 在浏览器窗口完成 velog 社交登录（Google / GitHub / Facebook）。
3. 登录成功、主页加载后，窗口自动关闭。
4. 凭证写入：`~/.config/backlink-publisher/velog-cookies.json`（权限 0600）。

终端打印成功提示：

```
[velog-login] ✔ Cookies saved to ~/.config/backlink-publisher/velog-cookies.json (0600)
  Stored cookies: ['access_token', 'refresh_token', ...]

Next steps:
  Run: backlink-publisher publish-backlinks --platform velog --dry-run targets.csv
  Or refresh your /settings page in the browser — the velog channel badge will turn green.
```

---

## Token TTL 与无人值守窗口

| Token | TTL | 说明 |
|-------|-----|------|
| `access_token` | 24 h（首次登录）/ 1 h（自动 refresh） | 每次请求时服务端通过 Set-Cookie 自动续期 |
| `refresh_token` | **30 天** | 本地 cookie 文件的有效期上限 |

**单次批跑无人值守上限：30 天。**

access_token 到期后，只要 refresh_token 有效（30 天内），
`publish-backlinks` 第一次请求会自动通过 Set-Cookie 取得新 access_token（1 h）并继续。
**无需手动干预**，除非 refresh_token 本身已过期（即 30 天未操作）。

> **建议**：每 25–28 天重新运行一次 `velog-login`，防止 refresh_token 过期中断批跑。

---

## 每日发布上限（Phase 1 → Phase 2）

| 阶段 | 每日上限 | 生效时间 |
|------|----------|---------|
| Phase 1 | **5 篇/天** | 当前（至 2026-06-02） |
| Phase 2 | **30 篇/天** | 2026-06-02 00:00 UTC 后自动切换 |

- 计数在 UTC 午夜自动重置（基于本地文件 `velog-rate-limit.json`）。
- 上限由代码常量控制。提前解锁或调整上限需要 PR（diff 即审计记录）。
- 每两篇之间有 60–180 秒随机抖动（P0-5b 实测 30 s 间隔无风控；实际部署保守取 60–180 s）。

---

## Cookie 失效处理

症状：发布返回 `status=failed`，错误含 `"velog-login"` 或 `"Cookie may be expired"`。

```bash
# 重新登录
velog-login
```

WebUI 设置页的 velog 徽章变红（`err`）或橙（`warn`）时也应运行此命令。

---

## 跨 UID 部署限制（⚠ 重要）

velog-cookies.json 权限为 **0600**，只有创建该文件的 uid 可读。

**WebUI Flask 进程与 CLI（velog-login / publish-backlinks）必须运行在同一 uid 下。**

若不同 uid 部署（例如 Flask 跑 `www-data`，CLI 跑 `dex`）：

- WebUI 设置页的 velog 徽章将显示 `permission_denied`（紫色）。
- `publish-backlinks` 会抛出 `DependencyError`（exit 3）。

修复方式：

```bash
# 方式 A（推荐）：统一 uid，重新登录
velog-login   # 以 Flask 进程的 uid 运行

# 方式 B：放宽权限至组可读（安全降级）
chmod 640 ~/.config/backlink-publisher/velog-cookies.json
chown <cli-uid>:<flask-gid> ~/.config/backlink-publisher/velog-cookies.json
```

---

## 多机器并行限制

每日发布计数基于 **本地文件**（`velog-rate-limit.json`），不跨机器同步。

- 多台机器分别运行 `publish-backlinks --platform velog` → **每台都有独立的 5/30 上限**，合计可超过单账号安全阈值。
- 多机并行时需人工协调（分配不同 target URL 批次 / 使用不同 velog 账号）。

---

## 文件系统兼容性

`velog-rate-limit.lock` 使用 `fcntl` advisory lock，**仅兼容 local POSIX 文件系统**：

✅ 支持：ext4、APFS、XFS、tmpfs  
❌ 不支持：NFS、overlayfs、Docker bind-mount（宿主目录）

**Docker 部署注意：**
把 `~/.config/backlink-publisher/` 挂载为 **named volume**（local driver），而非 bind-mount 宿主目录。

```yaml
# docker-compose.yml（正确）
volumes:
  - backlink_config:/root/.config/backlink-publisher

volumes:
  backlink_config:
    driver: local
```

此外不支持 gunicorn `gevent` / `eventlet` worker class（lock 会阻塞事件循环）；仅支持 `sync` / `thread` workers。

---

## 设备指纹绑定

P0-3 臂 C（跨设备测试）**尚未确认**。目前无证据表明 velog 绑定设备指纹，
但若将 cookie 文件复制到另一台机器后发布失败，需在该机器重新运行 `velog-login`。

---

## Success Criteria（T+30 retro 直接对照）

以下 4 项在 2026-06-18 前核对：

| 指标 | 目标 | 数据来源 |
|------|------|---------|
| 文章发布成功率 | ≥ 95%（30 天内） | `publish-backlinks` checkpoint JSONL |
| Google 索引率 | ≥ 70%（14 天内） | `site:velog.io/@<user>/<slug>` 手动核查 |
| 每日 cap 触发次数 | ≤ 5 次（Phase 1 期间） | `velog-rate-limit.json.count` 历史 |
| 凭证无效中断次数 | 0（30 天内主动 refresh） | checkpoint `error` 字段含 "velog-login" |
