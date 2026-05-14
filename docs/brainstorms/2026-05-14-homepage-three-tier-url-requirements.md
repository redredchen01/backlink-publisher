---
title: Homepage 三層 URL 輸入結構（主網域 + 分類頁 + 漫畫頁）
date: 2026-05-14
status: ready-for-planning
product_decisions_resolved: true
---

# Homepage 三層 URL 輸入結構

## Problem Statement

當前 homepage `/` 的 `/ce:plan` POST 表單只有兩個概念：

```
主：[target_url]
+ ：[url_new]   (可重複添加)
```

操作員實際的心智模型是三層 ——「主網域 → 分類頁 → 漫畫頁」—— 但表單沒有結構化這個層次，所有非主 URL 都被丟進同一個 `url_new` 文本框，靠手動 + 號逐個添加。結果：

1. 沒視覺提示哪個 URL 該扮演什麼角色。
2. 所有 extras 平等地進 `url_inputs[1:]`，後端無法區分「分類頁」vs「漫畫頁」vs 隨手放的相關連結。
3. 操作員每次都得重新打三個 URL，沒有自動派生 / 預填。

## User Goal

操作員打開 homepage，看見三個結構化的輸入框，分別清楚標示：

- **主網域**：要做反向鏈接的根目標（例：`https://51acgs.com/`）
- **分類頁**：站內列表 / 分類頁面（例：`https://51acgs.com/comic/hot`）
- **漫畫頁**：站內具體作品 / 內容頁面（例：`https://51acgs.com/comic/11777`）

填入後按「分析連結」→ 生成 **1 篇文章**，以「主網域」為 `target_url` + `main_domain`；「分類頁」和「漫畫頁」作為文章內被輔助引用的鏈接素材（link-rich context）—— 不另開新文章，不重複生成多篇。

## Three-Tier Semantic Model

| Tier | 表單 label | 後端角色 | 文章內出現方式 |
|------|------------|---------|---------------|
| 1 | 主網域 | `main_domain` + `target_url`（同一個 URL）| `links[].kind == "main_domain"`，必填，1–N 次出現於正文 |
| 2 | 分類頁 | 持久化到 `[sites.<main>.url_categories.category]` | `links[].kind == "category"`，PR #19 後 B/C mode 從 config 讀；plan-backlinks 自動帶入 |
| 3 | 漫畫頁 | 持久化到 `[targets.<main>.work_urls[0]]`（如已有 ThreeUrlConfig），或新建 work_urls 列表 | `links[].kind == "extra"` / `"work"`，作為文章中的延伸閱讀引用 |

「分類頁」和「漫畫頁」是「連結隱藏富養」材料 ——	它們不是文章的目標，但出現在文章中讓正文看起來更有資訊密度、更不像純廣告。

## Functional Requirements

### F1. 表單結構化呈現三個 URL 欄位

Homepage `/` 的 `<form action="/ce:plan">` 取代當前的「target_url + url_new」結構，改為：

- 三個獨立的 `<input type="url">`，分別 `name="main_url"` / `name="category_url"` / `name="work_url"`
- 視覺：三個 url-item，分別帶 badge「主」/「類」/「漫」（或類似簡明標籤）
- 三個欄位都不是 HTML `required`（保留 Plan 006 的「只主網域必填」精神）—— 只有 main_url 缺失時提交才報錯
- 「+ 添加更多連結」textbox 仍保留（用於非結構化的補充 URL）—— 但不再是主要輸入路徑

### F2. 主網域必填 + 結構化驗證

- main_url 空 → 422 + 「請輸入主網域」錯誤
- main_url 非 https → 422 + 「必須 https」
- category_url / work_url 空 → 可接受；後端不寫對應的 config 條目
- category_url / work_url 非 https → 422 + 對應欄位錯誤訊息

### F3. 內容門 gate（沿用 Plan 007）

- main_url 強制過 content-fetch gate（HTTP 200 + 非空 title）。失敗 → 422 + 顯示 reason
- category_url + work_url 走同一個 gate；失敗 → 422 + 對應欄位錯誤（不要靜默忽略）
- `BACKLINK_NO_FETCH_VERIFY=1` 旁路保持有效

### F4. 持久化（讓下次 plan-backlinks 能自動帶入）

提交成功後：

- 寫入 `[blogger]"<main_url>" = "<existing blog_id>"`（若 blogger 整合需要）
- 寫入 `[sites."<main_url>".url_categories]`：
  - `home = "<main_url>"`
  - `category = "<category_url>"`（如有填）
- 寫入 `[targets."<main_url>"]`（如有 ThreeUrlConfig schema 或創建新 anchor_keywords 條目）：
  - `work_urls = ["<work_url>"]`（如有填）

對應的 plan-backlinks 走 url_mode B/C 時會從 config 讀回這些 URL。

### F5. 一次提交 → 一篇文章生成入口

提交後，後端 session 設置一個 url_inputs 列表（長度 1），值為 `[main_url]`。原有 `/ce:generate` flow 不變。category 和 work 不另開文章 —— 它們的角色是「persistent config 條目」+「文章生成時的鏈接素材」，由 plan-backlinks 走 `[sites.<main>.url_categories]` 讀取自動帶入。

### F6. Backward compatibility

- 舊 session（已有 `target_url` 但無 category/work 結構化欄位）繼續工作 —— 舊 `url_new` extras flow 保留
- 舊 `/ce:plan` POST 接受新欄位名（main_url/category_url/work_url）+ 舊欄位名（target_url/url_new）；新欄位優先

## Non-Functional Requirements

- **延遲**：表單提交到 redirect 應 ≤ 15s（content-fetch gate 對 3 個 URL 的並發 batch）
- **觀測**：失敗 422 必須在頁面上明確顯示哪個欄位 + 什麼 reason
- **可訪問**：三個輸入框各自有 `<label>` + `aria-describedby` 連到對應錯誤訊息（沿用 PR #9 `/sites` 模板的無障礙模式）

## Scope Boundaries

- **不在範圍**：homepage 上的「進階」面板（添加更多 extras）—— 維持現狀的 `url_new` 文本框。本次只結構化主三層。
- **不在範圍**：`work_urls` 多個值的 UI（只支持單個 work_url 條目）。若操作員想批量，走 `/sites` 表單。
- **不在範圍**：實時 TDK 預覽（fetch 後立刻顯示 title）—— 這是 Plan 006 的東西，本次不重做。
- **不在範圍**：刪除舊 `target_url` / `url_new` 欄位。為向後兼容兩者並存一段時間。
- **不在範圍**：plan-backlinks 端的 url_mode B/C 邏輯改動 —— 沿用 PR #19 + #21 的 config-driven 讀取，本次只是讓 homepage 表單把 URL 寫進那些 config 段。
- **不在範圍**：work_themed_generator 路徑 —— 它有自己的 ThreeUrlConfig schema，不在 homepage 入口的職責內。
- **不在範圍**：「自動派生」邏輯（Plan 006）—— 本次三層全靠操作員手動填，留白 = 不寫入該段 config。

## Success Criteria

- [ ] Homepage `/` 渲染三個結構化 URL 輸入框 + badge 標籤
- [ ] 提交三個 URL（全填）→ 200 並 redirect 到原有 `/ce:generate` 預覽頁；`~/.config/backlink-publisher/config.toml` 出現 `[sites.<main>.url_categories]` 段含 `category = ...`，及 `[targets.<main>]` 段含 `work_urls = [...]`
- [ ] 提交只填 main_url → 接受，redirect，config 不寫 category/work 段
- [ ] 提交三個 URL（一個 404）→ 422 + 該欄位錯誤訊息顯示 URL + reason
- [ ] 後續 plan-backlinks 走 url_mode B → 從 config 讀到 category_url 並插入文章 links 陣列
- [ ] BACKLINK_NO_FETCH_VERIFY=1 旁路有效（不 gate 但仍寫 config）
- [ ] 原 `target_url` + `url_new` 表單欄位仍可工作（向後兼容）

## Resolved Product Decisions

由 2026-05-14 對話確認（已用 ✓ 標示，下一階段 /ce:plan 直接套用）：

- **Q1 ✓**：3 個 URL 全部 fetch metadata，沿用舊 `/ce:plan` flow 在 `/ce:generate` 預覽頁渲染 TDK。提交延遲 5-15s 可接受 —— 操作員想看到三層 URL 各自的 title / description 才能決定是否進下一步。實作：webui `ce_plan` handler 在現有 fetch_url_metadata 循環內把 category_url + work_url 加進迭代列表。

- **Q2 ✓**：自動升級到 ThreeUrlConfig。表單寫 work_url 時：(a) 創建 `[targets."<main>"]` 段，含完整 6 字段 schema；(b) 既有 anchor_keywords 轉存為 `branded_pool`（schema 要求三 pool 非空時的兜底材料）；(c) 其他 5 字段（list_url / partial_pool / exact_pool / work_anchor_templates / insecure_tls）按 Plan 006 的派生邏輯填入。操作員想要更細的配置可去 /sites 表單調整。

- **Q3 ✓**：只暴露 category 一個鍵，home 自動填為 main_url，其他 hot/animate/topic 不寫（已有的保留不動）。實作：寫 `[sites."<main>".url_categories]` 時只 set `home` + `category`，merge in place（如果該鍵已有其他子鍵，用 PR #12 Config Safety Net 的「保留 unmanaged」邏輯，不會被覆蓋）。

### Deferred to Implementation

- **延後**：DQ1. 三個欄位的 form-data 字段命名（`main_url` / `category_url` / `work_url` 是否與 PR #9 `/sites` 表單衝突？兩個表單在不同路由，命名空間獨立，但讀起來會有 mental load）。實作時看模板渲染體驗。
- **延後**：DQ2. 「+ 添加更多連結」extras textbox 與三層的視覺優先級 —— 三層在主要區、extras 摺疊在進階區。具體 layout 在 design review 時調。
- **延後**：DQ3. 提交後是否顯示「成功寫入 config」確認 banner（鏡像 `/sites?saved=...` 模式），還是直接走 `/ce:generate` redirect。

## Notes

- 與 Plan 006（/sites form 極簡化）關係：互補。Plan 006 改造 /sites 用於進階配置（針對既有 target，補充 anchor pool / templates），讓填寫量降低；本 brainstorm 改造 homepage 用於日常輸入入口，三層結構化幫操作員快速啟動。
- 與 PR #19 + #21（content-gate）關係：本表單借用同一個 gate 在 form-save 時拒絕無效 URL，繼承 422 + 字段錯誤模式。
- 與 work_themed_generator（ThreeUrlConfig）關係：work_url 寫入的 `[targets.<main>].work_urls` 是 ThreeUrlConfig 的字段；填入後若 main 已有 ThreeUrlConfig，會自然啟用 work-themed dispatch；若沒有，留作普通 anchor pool 參考材料。

## Decision Path

- Q1-Q3 由用戶在 ce:plan 階段前決定，本 doc 用 Open Questions 形式記下
- 一旦 Q1-Q3 答了，可直接走 `/ce:plan docs/brainstorms/2026-05-14-homepage-three-tier-url-requirements.md` 生成 implementation plan
