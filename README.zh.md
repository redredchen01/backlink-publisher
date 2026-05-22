# backlink-publisher

`backlink-publisher` 是一个本地优先、终端原生的反向链接发布流水线，专门用于 Blogger 和 Medium 平台。它能够生成、验证并发布短篇反向链接文章，具备完全的管道友好性、Cron 任务安全性，且无需交互即可运行。

## 工作空间布局

这是规范的项目仓库。它位于一个父工作空间目录中，该目录本身**不是**一个 git 仓库。名为 `bp-<topic>`（例如 `bp-events-u4`，`bp-ko-html`）的兄弟目录是同一个仓库在并行功能分支上的临时 `git worktree` 检出——它们与主检出共享 `.git/`。约定是每个活动功能分支对应一个 `bp-<topic>` 工作树；当分支合并后，删除该工作树 (`git worktree remove ../bp-<topic>`)。贡献者工作流程请参见 `AGENTS.md`。

## 快速开始

```bash
# 安装
pip install -e .

# 运行完整流水线（空运行/测试）
cat seeds.jsonl \
  | plan-backlinks \
  | validate-backlinks \
  | publish-backlinks --platform medium --mode draft --dry-run
```

## 前置要求

| 要求 | 详情 |
|---|---|
| **Python** | >= 3.11 |
| **Chromium** | 仅用于 Medium 浏览器后备模式: `playwright install chromium` |

> **无需 Node.js。** 发布过程直接使用 Blogger API v3 和 Medium API。只有在未配置 Medium 集成令牌的情况下，才需要 Chrome/Playwright 作为后备。

## 首次运行设置

```bash
# 1. 安装包及依赖
pip install -e .

# 2. 复制并编辑配置文件
cp config.example.toml ~/.config/backlink-publisher/config.toml
# 编辑：设置 Blogger blog_id 映射、OAuth 凭据、可选的 Medium 令牌

# 3. (可选) 安装用于 Medium 浏览器后备的 Chromium
playwright install chromium
#    然后在 Playwright 管理的配置中登录一次 Medium:
#    open ~/.config/backlink-publisher/chrome-profile-default/
```

## 流水线命令

### 1. plan-backlinks

从 stdin 或 `--input` 读取种子 JSONL，为每一行生成一个文章载荷。

```bash
cat seeds.jsonl | plan-backlinks
cat seeds.jsonl | plan-backlinks -i /dev/stdin
```

**输入架构 (seed):**

```json
{
  "target_url": "https://example.com/article",
  "main_domain": "https://example.com",
  "language": "en",
  "platform": "medium",
  "url_mode": "A",
  "publish_mode": "draft",
  "topic": "可选字符串",
  "seed_keywords": ["可选", "字符串"]
}
```

| 字段 | 必需 | 值 |
|---|---|---|
| `target_url` | 是 | 有效的 HTTPS URL |
| `main_domain` | 是 | 有效的 HTTPS URL |
| `language` | 是 | `en`, `zh-CN`, `ru` |
| `platform` | 是 | `medium`, `blogger` |
| `url_mode` | 是 | `A` (仅主域), `B` (主域+分类), `C` (主域+详情) |
| `publish_mode` | 是 | `draft`, `publish` |
| `topic` | 否 | 字符串 |
| `seed_keywords` | 否 | 字符串数组 |

**输出架构:**

```json
{
  "id": "sha256-truncated-16hex",
  "platform": "medium",
  "language": "en",
  "publish_mode": "draft",
  "target_url": "https://example.com/article",
  "main_domain": "https://example.com",
  "url_mode": "A",
  "title": "Exploring example.com: A Comprehensive Guide",
  "slug": "exploring-example-com-a-comprehensive-guide",
  "excerpt": "...",
  "tags": ["backlink", "reference", ...],
  "content_markdown": "# 标题\n\n...",
  "links": [
    { "url": "...", "anchor": "...", "kind": "main_domain", "required": true }
  ],
  "seo": {
    "title": "...",
    "description": "...",
    "canonical_url": "..."
  }
}
```

- 文章长度为 100–200 字。
- 6–8 链接/文章。
- `main_domain` 自然出现在正文中。
- 支持简体中文、英语和俄语。

## 贡献者指南
查看 `AGENTS.md` 了解如何添加新的发布者适配器以及项目贡献规范。
