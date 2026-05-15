---
title: "feat: Draft Queue with Rate-Limited Scheduled Publishing"
type: feat
status: completed
date: 2026-05-12
---

# Draft Queue with Rate-Limited Scheduled Publishing

## Overview

將「歷史記錄」tab 改造為「草稿欄」，增加本地草稿隊列機制。用戶可將生成的外鏈文章存入草稿欄，設定排程時間後由後台自動發布，並透過最小間隔+隨機抖動控制發布密度，避免批量發布被平台偵測。

## Problem Frame

當前發布流程是即時阻塞式：點「發布」→ `run_pipe(['publish-backlinks', ...])` 同步執行 → 寫入歷史。多篇外鏈文章若一次發布，時間戳完全集中，平台可透過發布頻率偵測到異常行為。

用戶需要：
- **草稿暫存**：生成計劃後不立即發布，先存入本地草稿欄
- **排程發布**：指定每篇文章的發布時間，或設定最小間隔後讓系統自動錯開
- **定量上稿**：每天最多 N 篇，時間隨機抖動，模擬人工發布節奏

## Requirements Trace

- R1. 「歷史記錄」tab 改版為「草稿欄 + 歷史」雙區塊 tab
- R2. 發布面板新增「加入草稿欄」按鈕，保存 validated JSONL + 配置到本地草稿隊列
- R3. 草稿欄每個 item 可設定「排程時間」（datetime picker）或「N 小時後發布」
- R4. APScheduler 後台線程在指定時間執行 `publish-backlinks`，完成後更新歷史
- R5. Settings 頁新增「最小發布間隔（小時）」和「隨機抖動（分鐘）」設定
- R6. 排程衝突自動推遲：若上一篇發布時間距今 < min_interval，新排程自動延後

## Scope Boundaries

- 不引入 Celery / RQ / Redis（過度設計，APScheduler in-process 足夠）
- 不實現 cron 週期排程（一次性排程即可，用戶手動補充）
- 不修改 CLI 工具（`publish-backlinks` 保持不變）
- Flask session 的 `validated` JSONL 由「加入草稿欄」動作持久化到磁碟，session 仍保持原有生命週期

## Context & Research

### Relevant Code and Patterns

- `_HISTORY_FILE` / `_load_history()` / `_append_history()` — 草稿隊列存儲遵循同一模式（`~/.config/backlink-publisher/draft-queue.json`）
- `run_pipe(cmd, stdin)` at line 2731 — scheduler job 內部調用此函數執行發布
- `ce_publish()` at line 2446 — 現有發布路由，scheduler job 複用其核心邏輯
- `_render(template, **kwargs)` at line 2230 — 自動注入 history/profiles/token status
- History item 結構（`id`, `target_url`, `platform`, `language`, `status`, `created_at`, `article_urls`, `error`）
- 現有發布面板 `publishPanel` div — 在此新增「加入草稿欄」按鈕
- `SETTINGS_HTML` / `_settings_context()` — 新間隔設定在此擴展

### Institutional Learnings

- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — `run_pipe()` 是阻塞調用；在 APScheduler 後台線程中執行沒問題，不影響 Flask 主線程
- APScheduler 3.10.4 已安裝（`pyproject.toml`），無需修改依賴
- Flask `debug=True` + reloader 會導致 scheduler 初始化兩次 → 用 `WERKZEUG_RUN_MAIN` 環境變量守護

### External References

- APScheduler 3.x BackgroundScheduler docs: in-process, daemon threads, JobStore 默認 memory
- `datetime-local` input：原生 HTML5，無需額外 JS 日曆庫

## Key Technical Decisions

- **APScheduler BackgroundScheduler（非 Celery）**：本工具是單用戶本地工具，無需消息隊列基礎設施。BackgroundScheduler 在同一 Python 進程內以 daemon thread 運行，Flask app 啟動時 `scheduler.start()`，app 關閉時自動結束。
- **草稿隊列獨立 JSON 文件**（`draft-queue.json`）而非混用 `publish-history.json`：隊列語義（待排程、已排程、已發布）與歷史語義（已完成記錄）不同，分離可避免過濾邏輯污染歷史讀取。
- **雙區塊 tab 設計**（草稿隊列上方 + 發布歷史下方）：保留歷史可見性，避免用戶喪失「已發布」記錄的參考。Tab 名稱改為「草稿 & 歷史」。
- **Rate limiting 策略**：全局 `min_interval_hours`（默認 4h）+ `jitter_minutes`（默認 ±30min）。排程時計算 `next_available = max(requested_time, last_publish_time + min_interval + jitter)`，存入 draft item 的 `scheduled_at` 字段。
- **Flask reloader 守護**：scheduler 初始化用 `if os.environ.get('WERKZEUG_RUN_MAIN') != 'false'` 條件，防止 debug reloader 啟動兩個 scheduler 實例。更簡單的方案：入口處 `app.run(use_reloader=False)`。

## Open Questions

### Resolved During Planning

- **Q: Flask restart 後排程丟失怎麼辦？** A: Flask 重啟時從 `draft-queue.json` 讀取所有 `status == 'scheduled'` 的 item，重新向 APScheduler 注冊 job（`next_run_time = item['scheduled_at']`）。如果時間已過期，立即觸發（`misfire_grace_time=3600`）。
- **Q: 排程執行失敗怎麼辦？** A: scheduler job 捕獲所有異常，更新 draft item `status = 'failed'`，將錯誤信息寫入 `error` 字段，同時 `_append_history()` 寫一條失敗歷史。
- **Q: 並發問題（多個 job 同時執行）？** A: APScheduler 默認 `max_workers=10`，可設為 1（`ThreadPoolExecutor(max_workers=1)`）強制串行執行，天然符合「不要太密集」的需求。

### Deferred to Implementation

- 是否在 UI 提供「自動分散 N 篇到未來 X 天」的批量排程按鈕（實現簡單但 UX 需要確認）
- `jitter_minutes` 是對稱抖動 `±jitter` 還是單向 `[0, jitter]`（可在實現時根據語義決定）

## High-Level Technical Design

> *此圖為方向性指引，非實現規範。*

```
用戶點「加入草稿欄」
        │
        ▼
POST /ce:draft/save
  讀 session['validated'] JSONL + session['config']
  計算 next_available (min_interval + jitter)
  寫入 draft-queue.json  status='pending'
        │
        ▼
草稿欄 UI 顯示 item
用戶設定 scheduled_at（datetime picker）
        │
        ▼
POST /ce:draft/schedule
  更新 draft item  status='scheduled', scheduled_at=xxx
  向 APScheduler 注冊 DateTrigger job(id=item_id)
        │
        ▼ (後台線程，到時觸發)
_publish_draft_job(item_id)
  讀 draft item → run_pipe(['publish-backlinks',...], plans_jsonl)
  成功 → _append_history(成功記錄)
          更新 draft item status='published'
  失敗 → _append_history(失敗記錄)
          更新 draft item status='failed', error=xxx
```

## Implementation Units

- [ ] **Unit 1: Draft Queue Storage Layer**

**Goal:** 建立草稿隊列的持久化讀寫函數，並定義 draft item 的完整數據結構。

**Requirements:** R2, R4

**Dependencies:** None

**Files:**
- Modify: `backlink-publisher/webui.py`（在 `_HISTORY_FILE` 定義附近增加 `_DRAFT_FILE` 及相關函數）

**Approach:**
- `_DRAFT_FILE = _CONFIG_DIR / "draft-queue.json"`
- `_load_draft_queue() -> list` — 讀取，解析失敗返回 `[]`
- `_save_draft_queue(items: list) -> None` — 原子寫（先寫臨時文件，再 rename）
- `_get_draft_item(item_id: str) -> dict | None`
- `_update_draft_item(item_id: str, **fields) -> bool`
- Draft item schema（在現有 history item 上擴展）：
  ```
  id, target_url, platform, language, created_at,  # 同 history
  plans_jsonl: str,        # validated JSONL（base64 或直接 str）
  publish_mode: str,       # "draft" | "publish"
  status: str,             # "pending" | "scheduled" | "published" | "failed"
  scheduled_at: str|None,  # ISO 8601
  article_urls: list,
  error: str|None
  ```

**Patterns to follow:** `_load_history()` / `_append_history()` at lines 2191–2209

**Test scenarios:**
- Happy path: `_save_draft_queue([item])` → `_load_draft_queue()` 返回相同 item
- Edge case: `draft-queue.json` 不存在 → `_load_draft_queue()` 返回 `[]`
- Edge case: JSON 損壞 → `_load_draft_queue()` 返回 `[]`，不拋異常
- Happy path: `_update_draft_item(id, status='scheduled')` 正確更新目標 item，不影響其他 item

**Verification:** 函數可在 Python REPL 中調用，draft-queue.json 文件在預期路徑創建/讀取。

---

- [ ] **Unit 2: APScheduler Setup & Publish Job**

**Goal:** 在 Flask 啟動時初始化 APScheduler，實現 `_publish_draft_job(item_id)` 後台發布邏輯，並在 app 啟動時從磁碟恢復已排程的 job。

**Requirements:** R4, R6

**Dependencies:** Unit 1

**Files:**
- Modify: `backlink-publisher/webui.py`（在 `app = Flask(__name__)` 之後，路由定義之前）

**Approach:**
- 初始化：`scheduler = BackgroundScheduler(executors={'default': ThreadPoolExecutor(max_workers=1)})`
- 啟動守護：`if os.environ.get('WERKZEUG_RUN_MAIN') != 'false': scheduler.start()` — 防止 debug reloader 雙啟動；或直接 `app.run(use_reloader=False)`
- `_publish_draft_job(item_id)`:
  1. `_get_draft_item(item_id)` → 若不存在或 status != 'scheduled' 直接返回
  2. `run_pipe(['publish-backlinks', '--platform', platform, '--mode', publish_mode], plans_jsonl)`
  3. 成功 → 解析 stdout 取 article_urls → `_update_draft_item(item_id, status='published', article_urls=...)` → `_append_history(...)`
  4. 異常捕獲 → `_update_draft_item(item_id, status='failed', error=str(e))` → `_append_history(..., status='failed')`
- **App 啟動時恢復**：在 `scheduler.start()` 後遍歷 `_load_draft_queue()`，所有 `status == 'scheduled'` 的 item 重新注冊 `DateTrigger(run_date=item['scheduled_at'])`，`misfire_grace_time=3600`（過期 job 最多延遲 1 小時執行）

**Patterns to follow:** `ce_publish()` at line 2446 的核心發布邏輯，`run_pipe()` at line 2731

**Test scenarios:**
- Happy path: scheduler job 在指定時間執行，`run_pipe` 成功 → draft status 變 `published`，history 新增一條
- Error path: `run_pipe` 拋異常 → draft status 變 `failed`，`error` 字段有內容，history 新增失敗記錄
- Edge case: item 已被用戶刪除（`_get_draft_item` 返回 None）→ job 靜默退出，不拋異常
- Integration: Flask 重啟後 `status='scheduled'` 的 item 被自動重新注冊，到時正常觸發

**Verification:** 手動設置一個 2 分鐘後的排程，等待觸發，確認 draft item status 更新，history 增加記錄。

---

- [ ] **Unit 3: "加入草稿欄" Route & Publish Panel Button**

**Goal:** 在發布面板新增「加入草稿欄」按鈕，並實現 `POST /ce:draft/save` 路由保存 draft item。

**Requirements:** R2

**Dependencies:** Unit 1

**Files:**
- Modify: `backlink-publisher/webui.py`（publishPanel HTML 區塊 + 新路由 `ce_draft_save()`）

**Approach:**
- 在 `publishPanel` 現有「發布」按鈕旁增加 `<button>` 提交到 `POST /ce:draft/save`
- 按鈕帶 hidden input 傳遞 `platform`、`publish_mode`、`plans`（validated JSONL，復用現有 `input[name="plans"]`）
- `ce_draft_save()` 路由：
  1. 讀 `request.form.get('plans')` 和配置字段
  2. 用 `_calc_next_available()` 計算建議排程時間（`now + min_interval + jitter`，見 Unit 5）
  3. 構建 draft item（`status='pending'`，`scheduled_at=None`）
  4. `_save_draft_queue()` 追加
  5. Redirect to `/?tab=draft&flash=saved`

**Patterns to follow:** `profiles_save()` at line 2783，`ce_history_delete()` at line 2542

**Test scenarios:**
- Happy path: 表單提交 → draft-queue.json 新增一條 `status='pending'` item，redirect 到草稿欄 tab
- Edge case: `plans` 為空 → 返回 400 或 redirect with error flash
- Edge case: `draft-queue.json` 不存在 → 自動創建

**Verification:** 點擊「加入草稿欄」後 draft-queue.json 有新記錄，UI redirect 到草稿欄 tab 並顯示 flash 提示。

---

- [ ] **Unit 4: Draft Queue UI Tab（草稿欄 + 歷史）**

**Goal:** 將現有「歷史記錄」tab 改為「草稿 & 歷史」tab，上半顯示草稿隊列（含排程控件），下半顯示發布歷史。

**Requirements:** R1, R3

**Dependencies:** Unit 1, Unit 3

**Files:**
- Modify: `backlink-publisher/webui.py`（HTML 模板：tab 按鈕 + historyPanel 內容 + `_render()` 注入 `draft_queue`）

**Approach:**
- Tab 按鈕文字改為 `<i class="bi bi-calendar-check me-2"></i>草稿 & 歷史`
- Panel 上半「草稿隊列」：
  - 每個 draft item 顯示：target_url、platform、created_at、status badge（pending=灰、scheduled=藍、published=綠、failed=紅）
  - `status='scheduled'` 時顯示 `scheduled_at` 和「取消排程」按鈕
  - `status='pending'` 時顯示排程表單：
    ```html
    <input type="datetime-local" name="scheduled_at" min="now">
    <button>排程發布</button>  <!-- POST /ce:draft/schedule -->
    <button>立即發布</button>  <!-- POST /ce:draft/publish-now -->
    ```
  - 「刪除」按鈕（POST /ce:draft/delete）
- Panel 下半「發布歷史」：保留現有 history item 渲染邏輯（折疊默認展開）
- `_render()` 自動注入 `draft_queue=_load_draft_queue()`（加入 if 判斷，仿照 `profiles` 注入）

**Patterns to follow:** 現有 history item CSS（`.history-item`, `.status-badge`）直接複用，`datetime-local` input 原生 HTML5

**Test scenarios:**
- Happy path: draft-queue.json 有 3 條記錄 → UI 顯示 3 個草稿 item，正確渲染 status badge
- Edge case: draft-queue.json 為空 → 顯示空態提示「草稿欄暫無任務」
- Happy path: `status='scheduled'` item 顯示排程時間和「取消」按鈕，不顯示排程表單
- Integration: 發布歷史在同一 tab 下半正確顯示

**Verification:** 瀏覽器開啟草稿 & 歷史 tab，草稿和歷史分區清晰，排程控件可互動。

---

- [ ] **Unit 5: Schedule / Cancel / Delete Routes + Rate Limit Logic**

**Goal:** 實現草稿欄的三個操作路由，以及計算下一個可用排程時間的 rate limiting 邏輯。

**Requirements:** R3, R6

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `backlink-publisher/webui.py`（三個新路由 + `_calc_next_available()` 輔助函數）

**Approach:**
- `_calc_next_available(requested_dt: datetime) -> datetime`：
  1. 讀全局設定 `min_interval_hours`（默認 4）、`jitter_minutes`（默認 30）
  2. 找出 `draft_queue` + `history` 中最晚的 `scheduled_at` / `created_at`（已發布的）
  3. `earliest = last_publish + timedelta(hours=min_interval) + timedelta(minutes=random.randint(-jitter, jitter))`
  4. 返回 `max(requested_dt, earliest)`
- `POST /ce:draft/schedule`：
  1. 讀 `item_id` 和 `scheduled_at`（from form）
  2. `final_time = _calc_next_available(parsed_datetime)`
  3. `_update_draft_item(item_id, status='scheduled', scheduled_at=final_time.isoformat())`
  4. `scheduler.add_job(_publish_draft_job, 'date', run_date=final_time, id=item_id, replace_existing=True)`
  5. Redirect with flash（若 `final_time != requested`，flash 提示「時間已調整為 {final_time}」）
- `POST /ce:draft/publish-now`：
  1. 取 draft item，`_update_draft_item(item_id, status='scheduled', scheduled_at=now+5s)`
  2. `scheduler.add_job(..., run_date=now+5s)` — 給 UI 返回時間
  3. Redirect
- `POST /ce:draft/cancel`：
  1. `scheduler.remove_job(item_id, jobstore=None)` — 捕獲 JobLookupError
  2. `_update_draft_item(item_id, status='pending', scheduled_at=None)`
  3. Redirect
- `POST /ce:draft/delete`：
  1. `scheduler.remove_job(item_id)` — 捕獲 JobLookupError（若已排程先取消）
  2. 從 draft_queue 移除該 item → `_save_draft_queue()`
  3. Redirect

**Test scenarios:**
- Happy path: POST schedule → draft status='scheduled'，APScheduler job 以正確 run_date 注冊
- Rate limit: requested_time 距上次發布 < min_interval → final_time 被自動推遲，flash 顯示調整後時間
- Happy path: POST cancel → job 從 APScheduler 移除，draft status 回到 'pending'
- Edge case: cancel 一個沒有 job 的 item（已發布）→ 捕獲 JobLookupError，不崩潰
- Happy path: POST delete → draft item 從 JSON 移除，若有 job 也一並取消

**Verification:** 設排程 → 確認 `scheduler.get_jobs()` 包含該 job ID；取消 → job 消失；`_calc_next_available` 手動測試返回值 ≥ last_publish + min_interval。

---

- [ ] **Unit 6: Settings Integration (Min Interval & Jitter)**

**Goal:** 在 Settings 頁新增「最小發布間隔」和「隨機抖動」設定，持久化到 config.toml 或獨立 JSON。

**Requirements:** R5

**Dependencies:** Unit 5

**Files:**
- Modify: `backlink-publisher/webui.py`（SETTINGS_HTML 表單 + `_settings_context()` + settings save 路由）
- Possibly modify: `src/backlink_publisher/config.py`（若將設定加入 TOML schema）

**Approach:**
- 最簡方案：獨立 `~/.config/backlink-publisher/schedule-settings.json` 存 `{"min_interval_hours": 4, "jitter_minutes": 30}`，與 config.toml 解耦（避免修改 config.py schema）
- `_load_schedule_settings() -> dict`（讀不到返回默認值）
- `_save_schedule_settings(data: dict) -> None`
- Settings 頁新增「排程設定」card，含兩個 `<input type="number">` 字段
- `POST /settings/schedule` 路由保存設定，redirect 帶 flash
- `_calc_next_available()` 調用 `_load_schedule_settings()` 讀取運行時值

**Test scenarios:**
- Happy path: 修改 min_interval_hours=6 → `_load_schedule_settings()` 返回 6 → `_calc_next_available` 使用新值
- Edge case: schedule-settings.json 不存在 → 使用默認值（4h / 30min），不報錯
- Happy path: Settings UI 顯示當前值（表單預填），保存後頁面 flash「已保存」

**Verification:** 修改設定後，新建的排程時間正確反映新間隔；重啟 Flask 後設定仍然生效（持久化）。

## System-Wide Impact

- **Interaction graph:** APScheduler job 在後台線程調用 `run_pipe()` 和 `_append_history()`，存在跨線程寫 JSON 文件的競態。`_save_draft_queue()` 需用原子寫（tmpfile + rename）或 `threading.Lock()` 保護。
- **Error propagation:** scheduler job 內部所有異常必須被捕獲並寫入 draft item `error` 字段；未捕獲異常會使 APScheduler job 靜默失敗。
- **State lifecycle risks:** draft item 的 status 轉換需嚴格（`pending → scheduled → published/failed`），避免重複排程（`replace_existing=True`）。
- **Flask reloader double-init:** `debug=True` 模式的 reloader 會啟動兩個進程，需守護 scheduler 初始化，推薦 `use_reloader=False` 最簡單。
- **Session dependency:** Unit 3 依賴 `session['validated']`；若 session 過期，`plans` 字段為空，需在路由做邊界驗證。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Flask reloader 雙啟動 scheduler | `app.run(use_reloader=False)` 或 `WERKZEUG_RUN_MAIN` 守護 |
| 跨線程寫 JSON 競態（scheduler thread vs Flask request thread） | `draft-queue.json` 寫操作加 `threading.Lock()` |
| Flask 重啟排程丟失 | 啟動時從 JSON 恢復 `status='scheduled'` 的 job，`misfire_grace_time=3600` |
| `run_pipe()` 阻塞 scheduler thread | max_workers=1 串行，單篇發布數秒內完成，可接受 |
| APScheduler 3.x API 差異 | 已確認版本為 3.10.4，使用 `BackgroundScheduler` + `DateTrigger` + `ThreadPoolExecutor` |

## Sources & References

- APScheduler 3.10.4 已安裝（`pyproject.toml`）
- Related code: `run_pipe()` line 2731, `ce_publish()` line 2446, `_append_history()` line 2200, `_load_history()` line 2191
- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md`
