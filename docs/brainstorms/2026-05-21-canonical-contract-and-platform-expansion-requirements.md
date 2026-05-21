---
date: 2026-05-21
topic: canonical-contract-and-platform-expansion
---

# Canonical Contract + Platform Expansion (Consultant SEO Supplement)

## Problem Frame

外部顧問送來 6 平台優先排程 + SEO 規範 + 主動推送建議，目的是把現有 backlink 組合補齊 dofollow / entity 信號並縮短 index 時間。對照本 repo 現況有 3 個張力：

1. **顧問的 canonical_url「全平台都帶」建議**會打臉本項目核心戰略（dofollow gate + anchor proportions + footprint regression 都是 pure backlink builder 的設計，syndication 模式會讓外站頁退出 SERP、link juice 衰減）。
2. **顧問建議排除 Medium API** 走「Dev.to → 手動 Import」半自動，但項目剛在 PR #138 / #141 投入大量 Chrome/CDP backend 工作（OPEN 中）。
3. **顧問建議的 Google Indexing API** 官方只支援 `JobPosting` / `BroadcastEvent` schema，一般 URL 推送實際效果接近 0；IndexNow 才是實際可用的標準推送 protocol。

本 brainstorm 的目的是把顧問建議「當補充包」收編進現有架構，**保留既有 Chrome backend 投資**，並修正顧問裡的 2 個技術誤判。

## 平台補充對照

| # | 平台 | 現況 | 本次動作 |
|---|---|---|---|
| 1 | Telegra.ph | ✅ ship (telegraph_api + telegraph_node) | 補 canonical opt-in support（HTML `<link rel="canonical">`） |
| 2 | Notion.site | ❌ 不存在 | **新增 adapter**（REST API + 公開 Page） |
| 3 | Dev.to | ❌ 不存在 | **新增 adapter**（標記 NoFollow，dofollow gate 不放行） |
| 4 | Hashnode | ⚠️ ship 但 2026-05-13 GraphQL paywall | `available()` 加 Pro tier 偵測；free-tier raise `DependencyError` |
| 5 | Blogger.com | ✅ ship | 補 canonical opt-in support；驗 OAuth refresh token 在「正式上線」 |
| 6 | GitHub Pages | ✅ ship | 驗 publish post 路徑（不只 banner upload）；補 Jekyll front-matter canonical |
| — | Medium | 4 路徑已 ship（包含 Chrome backend 開發中） | **保留現狀，不採用顧問的「下線」建議**；Chrome backend 投資繼續 |

## Requirements

**SEO 契約（cross-cutting，先做）**

- R1. `payload.seo.canonical_url` 成為**官方 opt-in 欄位**，由 `schema.validate_publish_payload` 認可；未帶代表 pure backlink 模式（外站頁獨立 index、傳完整 link juice），帶了代表 syndication 模式（外站頁標 canonical→自站）。
- R2. 所有 dofollow 平台 adapter（Telegraph / Notion / Hashnode / Blogger / GitHub Pages / Velog）讀同一個 `payload.seo.canonical_url` 欄位；未帶就**完全不輸出 canonical 標記**。
  - Telegraph / Notion / Blogger / GitHub Pages 沒有原生欄位，靠在輸出 HTML 或 Jekyll front-matter 塞 `<link rel="canonical" href="…">`。
  - Hashnode 用 `input.originalArticleURL`。
  - Dev.to 用 `article.canonical_url`（即便 NoFollow，dev.to canonical 仍是 SEO 工具，需支援）。
- R3. 不在 schema 強制 `canonical_url`；對 plan/payload row 不帶 canonical 是合法狀態，gate 不擋。
- R4. 新增 `tests/test_canonical_contract.py` — 對每個 adapter 參數化測試：(a) payload 帶 canonical → 輸出含正確標記；(b) 未帶 → 輸出**完全沒有**任何 canonical 痕跡（防 default-on 退路）。

**新平台 adapter**

- R5. Notion adapter（`publishing/adapters/notion_api.py`）— Integration Token + Database ID，POST `https://api.notion.com/v1/pages`，自動 publish 為公開 Page；返回 page URL 寫回 publish artifact。
  - 標記 **dofollow**（待 `verify` 在 `_DOFOLLOW_BY_CHANNEL` map 加 entry 前 grep 確認 Notion 公開 Page 連結屬性）。
  - WebUI bind 走 token-paste card（reuse `_token_paste_status` pattern），需要同步 wire 5 處（feedback memory: `[[wire-token-paste-channel-five-sites]]`）。
- R6. Dev.to adapter（`publishing/adapters/devto_api.py`）— API Key，POST `https://dev.to/api/articles`，必傳 `canonical_url`（若 R1 payload 有提供）。
  - 標記 **NoFollow**：`_DOFOLLOW_BY_CHANNEL["devto"] = False`，dofollow_gate 不放行 devto 為 dofollow 候選。
  - Dev.to 對 backlink 的價值在 entity 信號 / 收錄速度，而非 link juice。
- R7. 兩個新 adapter 必經 R9 extension readiness 路徑：一行 `register(...)` + schema validator + throttle gating + tier matrix 全自動生效，**不改 cli/\*.py 或 schema.py**。

**Hashnode paywall 處理**

- R8. `hashnode.py:available()` 增加 Pro tier 偵測：呼叫 `https://gql.hashnode.com` 的 introspection / `me { publication }` query 確認帳號可用 `publishPost`；free-tier 帳號回傳明確 `DependencyError("Hashnode GraphQL paywall — Pro plan required since 2026-05-13")` 並寫進 publish artifact `failure_reason`，**不**靜默 fallback。
- R9. WebUI Hashnode 卡片顯示 paywall 狀態（reuse channel-status JSON pattern）；綁定流程不破，但發布 row 直接 short-circuit 跳過。

**主動推送層（最後做）**

- R10. 主推送 protocol = **IndexNow**（覆蓋 Bing / Yandex / Seznam / Naver；GET/POST 推 URL，自動覆蓋多搜尋引擎），不是顧問建議的 Google Indexing API。
- R11. 新增 `publishing/indexing/indexnow.py` + CLI `report-indexing-push`（或併入 `publish-backlinks` 作為 post-publish step）— 讀 publish-history 取最新 success URL list，對每個 URL push IndexNow。
- R12. 若用戶內容剛好是 `JobPosting` schema，可選 opt-in Google Indexing API（保留為 R12 預留，默認不開）。
- R13. GSC sitemap ping（`https://www.google.com/ping?sitemap=…`）作為**輔助** push（zero auth），與 IndexNow 並排。

**Medium 戰略保留**

- R14. **不採用顧問建議的「下線 Medium API」**；保留現有 4 路徑（api / brave / browser / chrome），繼續 PR #138 / #141 的 Chrome backend 收尾。
- R15. 顧問建議的「Dev.to → 手動 Medium Import」**不**進系統自動化；操作者若想用 Medium Import 是手動行為，本系統不假設它存在。

## Success Criteria

- **SEO 契約**：所有 8 adapter（Telegraph, Notion, Dev.to, Hashnode, Blogger, GitHub Pages, Velog, Medium）通過 `tests/test_canonical_contract.py` 雙路徑（帶 / 不帶 canonical）測試。
- **新平台**：Notion + Dev.to 都能透過 WebUI bind → publish 完整跑通；publish artifact 帶正確 URL + canonical 狀態。
- **dofollow gate 一致性**：`_DOFOLLOW_BY_CHANNEL` 更新後跑 `tests/test_dofollow_gate.py`（或對應）confirm Notion = True / Dev.to = False。
- **Hashnode paywall**：free-tier 帳號 publish 跑 `DependencyError` 退出 code 3，**不**靜默誤報 success（防 `[[probe-then-pivot-when-api-unverifiable]]` 重演）。
- **IndexNow**：對最近 7 天的 publish-history URL 推送，可在 IndexNow 接受端（Bing Webmaster）看到 submission record。
- **回歸 zero**：R9 extension readiness test 仍 pass（即新加兩個 adapter 完全不動 cli/schema）；anchor proportions / footprint / monolith budget 三道 gate 全綠。

## Scope Boundaries

- ❌ 不動 Medium adapter family（包含 PR #138 / #141 OPEN 工作）；顧問的 Medium 下線建議**駁回**。
- ❌ 不採用 Google Indexing API 作為主推送（顧問建議駁回，技術理由：官方僅支援 JobPosting / BroadcastEvent）。
- ❌ 不採用「全平台強制 canonical」（顧問建議駁回，戰略理由：syndication ≠ backlink builder）。
- ❌ 不換 storage（顧問建議的 Google Sheets MVP 不適用；本項目 JSONL pipeline + WebUI 已是更穩態的設計）。
- ❌ 本期不做 IndexNow key auto-rotation（單 key 持久化即可）；不做 Bing Webmaster URL Submission API（IndexNow 已覆蓋 Bing）。
- ⚠️ Hashnode adapter 保留但 free-tier paywall 後**等同 disabled**；本期不做付費 plan 整合。

## Key Decisions

- **canonical = opt-in via `payload.seo.canonical_url`**：理由是本項目核心戰略 = pure backlink（dofollow gate + anchor proportions + footprint regression 都印證），全平台強制 canonical 會讓外站頁退出 SERP / link juice 衰減；少數 syndication 場景仍能 per-row opt-in。
- **Dev.to = NoFollow 明確標記**：dofollow_gate 不放行；保留是為了 entity 信號 / 收錄速度價值，**不**作為 dofollow 主力，避免重蹈 PR #108→#109（9 分鐘 revert nofollow 平台被誤當 dofollow）。
- **IndexNow over Google Indexing API**：顧問的 Indexing API 建議基於誤解（官方限 JobPosting）；IndexNow 是零驗證、跨搜尋引擎、實際生效的標準。
- **Medium 戰略不變**：Chrome backend 投資 ≠ 沉沒成本；近期 PR #138 / #141 仍在主線推進，與顧問建議的「下線」直接衝突，採用後者會棄置數週工作。
- **執行順序：canonical 契約 → Notion + Dev.to → IndexNow**：避免新 adapter ship 後再 retrofit canonical 的 churn。

## Dependencies / Assumptions

- 假設 Notion 公開 Page 連結屬性為 dofollow（plan 階段需 grep 確認 + Brave/Chrome 跑 link_attr_verifier 抽樣驗證）。
- 假設 IndexNow API 對本項目發送頻率（每日數十 URL）不會觸發 rate-limit；若觸發再加 throttle middleware。
- 假設 Blogger OAuth refresh token 已切「正式上線」（顧問提到測試中 7 天失效）；若未切，bind 流程要先處理（不在本 brainstorm scope）。
- PR #138 / #141 合併不阻塞本工作（canonical contract / Notion / Dev.to 都不動 medium 路徑）。

## Outstanding Questions

### Resolve Before Planning

（無 — 戰略決定全部已 close）

### Deferred to Planning

- [Affects R5][Needs research] Notion 公開 Page 是 dofollow 還是 nofollow？plan 階段先抽樣建一個公開 Page 跑 `link_attr_verifier` 驗證；若為 NoFollow，按 Dev.to 同等對待（保留為 entity 信號，從 dofollow_gate 排除）。
- [Affects R8][Technical] Hashnode Pro tier 偵測用哪個 GraphQL query？`me { publication }` 還是直接 dry-run `publishPost` 看錯誤碼？需在 plan 階段試打確認最不擾的 detection。
- [Affects R10][Technical] IndexNow key 儲存位置：`~/.config/backlink-publisher/indexnow.key` 還是 env var `BACKLINK_PUBLISHER_INDEXNOW_KEY`？plan 階段對齊既有 credential storage pattern（`telegraph_api.py` rotation pattern 是 repo canonical reference）。
- [Affects R11][Technical] IndexNow push 觸發時機：併入 `publish-backlinks` post-step（同事務）vs 獨立 `report-indexing-push` CLI（顯式排程）？前者簡單但失敗會混淆 publish status；後者契合現有 6 CLI 對等架構。
- [Affects R6][Technical] Dev.to API Key 是否要支援多帳號（multi-publication）？目前其他 token-paste channel 多為單帳號，先按單帳號 ship，若需求出現再擴。
- [Affects R2][Technical] GitHub Pages 的 canonical 注入點：Jekyll front-matter（`canonical_url:`）vs HTML 直接塞 `<link rel="canonical">`？依當前 ghpages.py 輸出格式決定，plan 階段 read 一下實際模板。

## Next Steps

→ `/ce:plan` for structured implementation planning

建議 plan 把工作拆成 3 個 PR（對應 3 個工作批次）：
1. **PR-A**: canonical 跨 adapter 契約（R1-R4）— 影響所有現有 adapter，先 ship 立基。
2. **PR-B**: Notion + Dev.to + Hashnode paywall（R5-R9）— 3 個垂直整合 + Hashnode 護欄。
3. **PR-C**: IndexNow + GSC sitemap ping（R10-R13）— 跨頻道推送層。
