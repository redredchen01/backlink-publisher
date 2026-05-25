# 分發渠道擴充計畫

**Date**: 2026-05-25
**Status**: Draft
**Context**: Phase 3 output of ce-brainstorm session. Precedes implementation spikes and adapter development.

---

## 1. Goal

擴充 backlink-publisher 的分發渠道網絡，從現有 10 個渠道系統性地增加到 23+ 個渠道，覆蓋各語言市場與內容形式，使整體分發網絡更多樣、穩健且具備高引流價值。

### 關鍵指標

| 指標 | 當前 | 目標 |
|------|:---:|:----:|
| 已註冊渠道 | 10 | 23+ |
| 整合機制類型 | 4 (REST/Form/Git/CDP) | 7 (+ MetaWeblog/Cookie-REST/SNS) |
| dofollow 渠道佔比 | ~70% | 維持 ≥70% |
| 語言覆蓋 | EN/KO | EN/KO/ZH/JA/RU |

---

## 2. Scope

### In Scope

- 新平台的 adapter 實作（每個平台一行 `register()` 註冊）
- 混合型整合機制：API / Browser Automation / MetaWeblog XML-RPC / Cookie-base REST 等
- 平台優先級依 dofollow 狀態 × DA/引流價值 × 整合成本 排序
- 支援現有 `registry.py` 架構

### Out of Scope (This Phase)

- 非平台渠道（email newsletter 自動發送、RSS feed 同步—需另案評估）
- 內容改寫/在地化（各平台 adapter 只負責發布，內容策略另計）
- manifest 架構重構（已在 `channel-manifest-architecture-requirements.md` 進行中）
- Web3 wallet 管理（Mirror.xyz 依賴錢包，需 infra 層支援）

---

## 3. Platform Matrix

### 3.1 REST / GraphQL API 🟢

| 平台 | DA | dofollow | 整合方式 | 驗證方式 | 關鍵資源 | 風險 | Effort | 優先級 |
|------|:--:|:--------:|----------|----------|---------|:----:|:-----:|:----:|
| **WordPress.com** | 92 | ✅ | WP REST API (`/wp/v2/posts`) | OAuth / API Key | `python-wordpress-xmlrpc` / `requests` | API rate limit 每小時 350 req | S | 🔥 P0 |
| **Hashnode** | 88+ | ✅ | GraphQL API (`gql.hashnode.com`) | Personal Access Token | `createDraft` → `publishDraft` 兩步驟 | 草稿→發布兩階段流程 | S | 🔥 P1 |
| **Write.as / WriteFreely** | 70+ | ✅ | REST API + Token Auth | Token in header | `writefreely-py` (PyPI, 9 stars) | 專案維護度中低 | S | 🔥 P1 |
| **Tumblr** | 88 | ✅ | REST API v2 + OAuth 1.0a | OAuth 1.0a 四腳流程 | `pytumblr` (官方, 729 stars) | OAuth 1.0a 整合較複雜 | M | 🔥 P1 |
| **Ghost** | 85+ | ✅ | Admin REST API + JWT | Admin API Key (JWT) | `ghost-admin-api` 文件 | JWT token 需定期刷新 | S | P2 |
| **Beehiiv** | 80+ | ✅ | REST API v2 (Bearer Token) | API Key | `developers.beehiiv.com` 官方文件 | Posts API 用 block JSON 非純 Markdown | M | P2 |
| **Note.com** | 75+ | ✅ | MCP Tool / REST API | 待確認 | 已有 MCP 介面提供 | API 公開程度待確認 | M | P3 |

### 3.2 MetaWeblog / XML-RPC 🟡

| 平台 | DA | dofollow | 整合方式 | 關鍵資源 | 風險 | Effort | 優先級 |
|------|:--:|:--------:|----------|---------|:----:|:-----:|:----:|
| **博客園 (CNBlogs)** | 65 | ✅ | MetaWeblog XML-RPC | `xmlrpc.client` 標準庫；已有 `cnblogs-publisher` skill 可參考 | XML-RPC 老協議但穩定 | S | 🔥 P0 |
| **Habr.com** | 80 | ✅ | Habr API (有封裝) | `habr_app` DeepWiki 有 API 整合文件 | 俄文平台，API 文件以俄文為主 | M | P2 |

### 3.3 Cookie-based / Browser Automation 🟠

| 平台 | DA | dofollow | 整合方式 | 關鍵資源 | 風險 | Effort | 優先級 |
|------|:--:|:--------:|----------|---------|:----:|:-----:|:----:|
| **掘金 (Juejin)** | 60 | ✅ | Cookie + REST 逆向 API | `juejin-api` (62 stars) GitHub repo; `JueJin-MCP` (go-rod CDP) | Cookie 需定期更新 | M | 🔥 P1 |
| **CSDN** | 75 | ✅ | Browser Automation + Cookie | `web-publish` 已有完整實作參考 | 驗證碼/反爬機制 | M | 🔥 P1 |
| **知乎 (Zhihu)** | 85 | ✅ | Browser Automation + Cookie | `web-publish` 已有實作參考 | 反爬較嚴，mobile API 可能更穩定 | M | 🔥 P1 |
| **SegmentFault** | 60 | ✅ | Browser Automation | `web-publish` 有 CodeMirror 處理邏輯 | CodeMirror 編輯器操作複雜 | M | P2 |
| **簡書 (Jianshu)** | 55 | ✅ | Browser Automation / Unofficial API | `Jianshu Egg API` 非官方封裝 | 非官方 API 穩定性未知 | L-M | P3 |

### 3.4 SNS（nofollow 但高流量價值）

| 平台 | DA | dofollow | 整合方式 | 策略 | 風險 | Effort | 優先級 |
|------|:--:|:--------:|----------|------|:----:|:-----:|:----:|
| **LinkedIn** | 95 | ❌ | REST API (`/ugcPosts`) | 品牌曝光 + B2B 引流 | API scope 限制嚴格；nofollow 不貢獻 SEO | S | P1 |
| **Quora** | 92 | ❌ | Browser Automation | 問答流量 + 高搜尋排名 | 需要帳號權重；nofollow | M | P2 |
| **Mastodon** | — | ✅ | REST API (OAuth) | ✅ **已實作** | — | — | — |

### 3.5 Newsletter / Web3 / 特殊

| 平台 | DA | dofollow | 整合方式 | 說明 | 風險 | Effort | 優先級 |
|------|:--:|:--------:|----------|------|:----:|:-----:|:----:|
| **Substack** | 88 | ✅ | 有限 API / RSS-Import | dofollow + 高品質讀者群 | API 極受限；需透過 RSS import 曲線發布 | M | P2 |
| **Mirror.xyz** | 70 | ✅ | Ethereum Smart Contract + IPFS | 去中心化發布 | 需要 Eth wallet + gas fee，Web3 UX 門檻高 | XL | P3 |
| **Rentry.co** | 55 | ✅ | HTTP POST (`/api/new`) | Markdown pastebin，零成本 | 引流價值低，後設資料少 | S | P3 |
| **Pikabu.ru** | 70 | ✅ | 非官方 API | 俄羅斯最大社群平台 | 語言屏障；非官方 API 穩定性未知 | M | P3 |

---

## 4. Integration Mechanism Taxonomy

### 4.1 機制分類

| 機制 | 成本 | 穩定性 | 維護需求 | 適合場景 |
|------|:---:|:------:|:--------:|---------|
| **REST / GraphQL API** | 🟢 低 | 🟢 高 | 🟢 低（API 穩定） | 有官方 API 的平台（WordPress, Hashnode, Beehiiv） |
| **MetaWeblog XML-RPC** | 🟢 低 | 🟢 高 | 🟢 低（標準協議） | 老牌 blog 平台（博客園, Habr, WordPress 也可） |
| **Cookie + REST 逆向** | 🟡 中 | 🟡 中 | 🟡 中（Cookie 過期需更新） | 有 API 但非公開的平台（掘金, 知乎） |
| **Browser Automation (CDP)** | 🟠 中高 | 🟠 中低 | 🔴 高（UI 變動即斷） | 無 API 的平台、需複雜交互（SegmentFault CodeMirror） |
| **OAuth 1.0a** | 🟡 中 | 🟢 高 | 🟡 中（簽名邏輯複雜） | 老牌平台（Tumblr） |
| **JWT Token** | 🟢 低 | 🟢 高 | 🟢 低 | 現代 API（Ghost） |
| **Blockchain Smart Contract** | 🔴 高 | 🟢 高 | 🔴 高 | Web3 平台（Mirror.xyz） |

### 4.2 混合策略建議

每個平台按以下策略選擇整合方式：

1. **優先使用官方 API** — REST / GraphQL 最穩定
2. **無官方 API 但有標準協議** — MetaWeblog XML-RPC 是備選
3. **有非公開 API** — Cookie-based REST 比完整 browser automation 更輕量
4. **最後手段** — Browser Automation (CDP)，僅用於無其他選項的平台

---

## 5. Phased Roadmap

### Phase 1: 基礎建設（P0）

| 平台 | 機制 | 原因 | 相依性 |
|------|------|------|--------|
| WordPress.com | REST API | DA92 + dofollow + 成熟 API，最高 ROI | 需要 OAuth 流程支援 |
| 博客園 | MetaWeblog XML-RPC | 中文技術社群 + XML-RPC 低整合成本 | 無特殊相依 |

**Phase 1 邏輯**: 這兩個平台整合成本最低、回報明確，適合做為擴充流程的基礎測試。

### Phase 2: 快速擴張（P1）

| 平台 | 機制 | 原因 | 相依性 |
|------|------|------|--------|
| Hashnode | GraphQL API | 開發者社群 + dofollow + 乾淨 API | 需要 API Key 流程 |
| Write.as | REST API | 極簡平台 + 有現成 Python 套件 | 無 |
| Tumblr | REST API (OAuth 1.0a) | DA88 + 老牌部落格 | 需要 OAuth 1.0a adapter 原型 |
| 掘金 | Cookie + REST 逆向 | 中文技術社群 + MCP 套件可參考 | 需要 Cookie 管理機制 |
| CSDN | Browser Automation | 中文技術社群 + web-publish 可參考 | 需要 Browser automation adapter |
| 知乎 | Cookie + Browser | DA85 + 高品質中文內容 | 需要 Cookie 管理 + Browser |

**Phase 2 邏輯**: 覆蓋中英文技術社群核心平台，6 個平台可平行開發（因為機制獨立）。

### Phase 3: 品牌曝光（P1）

| 平台 | 機制 | 原因 | 相依性 |
|------|------|------|--------|
| LinkedIn | REST API | DA95 品牌曝光 + B2B 引流價值（即使 nofollow） | 需要 OAuth 2.0 + 會員 API scope |

**Phase 3 邏輯**: LinkedIn 不分類在技術部落格但高品牌價值，nofollow 但引流效果顯著。

### Phase 4: 特定市場補強（P2）

| 平台 | 機制 | 原因 | 相依性 |
|------|------|------|--------|
| Ghost | REST API (JWT) | 技術部落格 + dofollow | JWT token 管理 |
| Beehiiv | REST API | Newsletter + dofollow | 需要 posts API block JSON 轉換 |
| SegmentFault | Browser Automation | 中文技術社群補強 | Browser adapter |
| Quora | Browser Automation | 問答流量 + 高搜尋排名 | Browser adapter + 帳號管理 |
| Substack | 有限 API / RSS-Import | Newsletter 高品質讀者 | RSS-import 流程 |

### Phase 5: 實驗性 / 利基（P3）

| 平台 | 機制 | 原因 |
|------|------|------|
| Note.com | API / MCP | 日本市場 |
| Habr.com | API | 俄文技術社群 |
| 簡書 | Browser | 中文創作平台 |
| Mirror.xyz | Smart Contract | Web3 去中心化 |
| Rentry.co | HTTP POST | 零成本快速發布 |
| Pikabu.ru | Unofficial API | 俄文社群 |

---

## 6. Architecture Considerations

### 6.1 現有架構相容性

```python
# 現有註冊模式 — 新平台沿用同一模式
from backlink_publisher.publishing.registry import register

register("wordpress", WordPressAdapter, dofollow=True, referral_value=92)
register("cnblogs", CNBlogsAdapter, dofollow=True, referral_value=65)
```

### 6.2 新機制需要的支援

| 新機制 | 需要新增的 infra | 建議做法 |
|--------|-----------------|---------|
| **Cookie 管理** | Cookie 儲存/刷新/失效偵測 | 擴充 `settings` 子系統或 browser-binding |
| **OAuth 1.0a** | OAuth 簽名邏輯 | 可套用 `requests-oauthlib` |
| **MetaWeblog XML-RPC** | XML-RPC client 封裝 | Python `xmlrpc.client` 標準庫即支援 |
| **Browser Automation (CDP)** | 已在 prototype 中 | 持續使用 `playwright` / `selenium` |
| **JWT Token** | Token 生成/刷新 | 已有 `PyJWT` 生態系 |

### 6.3 平台分類 config 建議

為支援按機制類型分組，config 可能需要擴充 metadata：

```toml
[channels.wordpress]
enabled = true
mechanism = "rest_api"       # rest_api | xmlrpc | cookie_rest | browser | graphql | oauth1
auth_type = "oauth2"         # oauth2 | apikey | token | cookie | jwt | oauth1a
dofollow = true
priority = "p0"
```

### 6.4 混合型 adapter 模式

對於同時支援多種整合方式的平台（如 WordPress 同時有 REST API 和 XML-RPC），可實作 **fallback chain**：

```
primary = REST API
fallback = XML-RPC
```

若 primary 失敗（rate limit / API 變更），自動降級到 fallback。

---

## 7. Open Questions & Risks

### 7.1 需驗證的問題

| # | 問題 | 影響 | 解決方式 |
|---|------|------|---------|
| Q1 | WordPress.com REST API 對 Free plan 的限制？ | Phase 1 是否需付費 plan | 創建測試帳號實測 |
| Q2 | 掘金 Cookie 的有效期多長？刷新流程？ | Phase 2 維護成本 | 實測 Cookie 時效 + 設計自動刷新 |
| Q3 | Tumblr OAuth 1.0a 簽名流程的維護成本？ | Phase 2 開發時間 | spike 評估後決定 |
| Q4 | Hashnote API 的 content 是否完整支援 Markdown？ | Phase 2 adapter 設計 | 查 API 文件確認 |
| Q5 | LinkedIn API 的 /ugcPosts scope 申請難度？ | Phase 3 可行性 | 申請測試帳號 |
| Q6 | Beehiiv Posts API 的 block JSON 格式是否需要內容轉換層？ | Phase 4 開發成本 | 研究 API 文件中的 block types |

### 7.2 已知風險

| 風險 | 可能性 | 影響 | 緩解方式 |
|------|:-----:|:----:|---------|
| Cookie-based 平台的 Cookie 過期 | 中 | 發布中斷 | Cookie 刷新機制 + 失效提醒 |
| 知乎/CSDN 反爬升級 | 中 | adapter 失效 | 用 mobile API 優先、降低 frequency |
| 非官方 API 的穩定性 | 高 | adapter 不定期失效 | 設定 monitor + alert |
| Medium API 不再發新 token | 已發生 | 無法新增 Medium adapter | 改用 Browser automation 或 RSS import |
| Mirror.xyz 需 Eth gas fee | 高 | 每篇發布有成本 | 評估 ROI 後決定是否實作 |
| OAuth 1.0a 套件相容性 | 低 | 開發延遲 | spike 確認 `requests-oauthlib` 可用 |

### 7.3 決策待辦

- [ ] Spike: WordPress.com API 對免費方案的限制
- [ ] Spike: 掘金 Cookie 管理機制
- [ ] Spike: Tumblr OAuth 1.0a 整合流程
- [ ] Decide: 是否需要新增 `mechanism` metadata 到 config
- [ ] Decide: Substack 的 RSS-import 路徑是否可行

---

## Appendix A: 現有渠道清單（對照基準）

| 現有渠道 | 機制類型 | dofollow | 備註 |
|----------|---------|:--------:|------|
| Blogger | REST? / Browser? | ? | 需確認 |
| Medium | REST API (舊 token) | ✅ | 不再發新 token |
| Telegraph | HTTP Form POST | ? | — |
| Velog | REST? | ? | 韓國平台 |
| GitHub Pages | Git push | ✅ | — |
| LiveJournal | ? | ? | 老平台 |
| txtfyi | ? | ? | 極簡平台 |
| Dev.to | REST API | ✅ | — |
| Notion | ? | ? | — |
| Mastodon | REST API (OAuth) | ✅ | — |

## Appendix B: 整合機制快速參考

### REST API 建立文章 (WordPress.com)
```
POST https://public-api.wordpress.com/wp/v2/sites/{domain}/posts
Authorization: Bearer {token}
Content-Type: application/json

{"title": "...", "content": "...", "status": "publish"}
```

### GraphQL 建立文章 (Hashnode)
```
mutation {
  createDraft(input: {title: "...", contentMarkdown: "..."}) { ... }
}
```
然後：
```
mutation {
  publishDraft(input: {id: "...", slug: "..."}) { ... }
}
```

### MetaWeblog XML-RPC (博客園)
```python
import xmlrpc.client
proxy = xmlrpc.client.ServerProxy(metaweblog_url)
proxy.metaWeblog.newPost(blogid, username, password, post_struct, publish)
```

### Write.as REST API
```
POST https://write.as/api/posts
Authorization: Token {token}
Content-Type: application/json

{"title": "...", "body": "..."}
```

### Tumblr REST API v2 (OAuth 1.0a)
```
POST https://api.tumblr.com/v2/blog/{blog}/post
OAuth 1.0a signed request
```

### Rentry.co HTTP POST
```
POST https://rentry.co/api/new
Content-Type: application/json

{"text": "# Hello"}
```
