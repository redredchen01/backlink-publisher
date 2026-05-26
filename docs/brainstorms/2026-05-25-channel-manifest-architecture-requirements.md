---
date: 2026-05-25
topic: channel-manifest-architecture
---

# Channel Manifest Architecture

## Problem Frame

接入新發布渠道是這個專案的長期高頻動作（已 8+ 渠道，會持續增加）。R9（PR #63/#68/#70）把 **publish dispatch 層**收斂成 `register("x", XAdapter)` 一行，但「真實 onboard 一個渠道」仍要碰 6 大類觸點：

| 類別 | 散落點 | 證據 |
|---|---|---|
| 識別 / UI | binding_status 字串常量、settings/dashboard/history 模板、`HIDDEN_FROM_UI`、history filter chips | `[[platforms-vs-bound-platforms-split]]`、PR #197 |
| Publish 能力 | adapter.py、registry、`_DOFOLLOW_BY_CHANNEL`、target_language 白名單、post-verify 策略 | `[[grep-dofollow-map-before-shipping-adapter]]`、`[[target-language-schema-and-dispatcher]]` |
| Bind 機制 | WebUI bind card、bind backend wiring、storage-state path、last-account.txt、`/api/<channel>/login` route | `[[wire-token-paste-channel-five-sites]]`、`[[chrome-backend-per-channel-profile]]`、`[[velog-no-bind-channel-btn]]` |
| Throttle / Policy | env keys（如 `MEDIUM_THROTTLE_MIN/MAX`）、retry 策略、ContentRejected 分類、liveness probe | `[[content-rejected-error-sibling-pattern]]` |
| Config / 退役 | `save_config` known_roots、schema 驗證、`HIDDEN_FROM_UI`、writer.py 條件分支 | `[[platform-retirement-known-roots-pattern]]`、`[[hidden-from-ui-pattern-for-retiring-channels]]` |
| Test / Fixture | adapter test、R9 readiness、binding test、`fixtures/seed.jsonl` | `tests/test_r9_extension_readiness.py` |

**後果**：每接一個渠道 ≈ 改 10–15 個檔案、遺漏一處就破（PR #197 漏 filter chips、5 處 wire 漏一處就 Jinja2 UndefinedError）。退役同樣是反向 N 處清理。

**目標**：把「接入一個渠道」收斂成**單一 manifest 宣告**，所有散落觸點改為從 manifest 反查；R9 registry 升級成單一事實來源（SSoT）。

## User Flow（接入新渠道 — 目標態）

```
contributor                                          framework
─────────────                                        ─────────────
1. 在 platforms/<name>/ 建 package
   ├── manifest.py    (declarative: identity, capabilities, bind, policy)
   ├── adapter.py     (publish() / available())
   └── tests/         (adapter + manifest contract test)
                              │
                              ▼
2. 跑 pytest                  │
                              ├─► registry 自動 discover platforms/*/manifest.py
                              ├─► CLI choices / schema / UI cards 全自動 wire
                              ├─► dofollow tier / throttle band / 退役狀態自動就位
                              └─► contract test 強制檢查 manifest ↔ adapter ↔ binding 一致
                              │
                              ▼
3. 開 PR → merge → 渠道上線    ✓
```

對照現況：步驟 2 目前需要手動改 10–15 處。

## Requirements

**Manifest as Single Source of Truth**

- R1. 每個渠道有且僅有一個 **Channel Manifest**，位於 `platforms/<name>/manifest.py`（與 adapter 同 package），是該渠道所有屬性的唯一宣告點
- R2. Manifest 涵蓋 **4 大類**：
  - **Identity / UI**：name、display_name、domain、UI category、visibility（`active` / `experimental` / `hidden` / `retired`）
  - **Publish capabilities**：adapter class 引用、dofollow tier、支援 target_language、post-publish 驗證策略 ID
  - **Bind mechanism**：支援的 backend（`chrome` / `token-paste` / `oauth` / `cookie` / `cdp`）、bind card 類型、storage-state 路徑 template、bind 驗證 endpoint
  - **Throttle / Policy**：throttle band（min/max 秒）、env override key 名、retry 策略 ID、liveness probe 間隔、ContentRejected 分類規則
- R3. Manifest 是 **declarative**（資料 + class 引用，不寫流程邏輯）；流程邏輯仍在 adapter / bind backend / WebUI 各自分層內

**Discovery & Wiring**

- R4. Registry 啟動時自動掃描 `platforms/*/manifest.py`，**不再有任何渠道字串常量**散落在 `binding_status.py` / `schema.py` / templates / CLI argparse
- R5. 既有觸點全部改為**從 registry 反查 manifest**：
  - `_DOFOLLOW_BY_CHANNEL` → `registry.get(name).dofollow_tier`
  - `HIDDEN_FROM_UI` → `registry.filter(visibility != "retired" and not hidden)`
  - `platforms` vs `bound_platforms` → `registry.bound_platforms(config)` 統一 helper
  - Token-paste 5 處 wire → 模板從 `registry.bind_cards()` 迭代
  - CLI choices / schema known_roots → `registry.active_names()`
- R6. Contract test 強制檢查 manifest 與實作一致：
  - manifest 宣告的 adapter class 必須存在且實作 `publish` / `available`
  - manifest 宣告的 bind backend 必須在 backend registry 註冊
  - manifest 宣告的 storage-state path 在 bind / unbind / 退役流程都讀同一個 path

**Lifecycle / Retirement**

- R7. 渠道**退役** = 把 manifest `visibility` 改成 `retired`（一行），不需動其他檔案；UI 自動隱藏、CLI 自動排除、`save_config` 不再 round-trip
- R8. **隱藏但保留 adapter**（如 PR #136 write.as）= `visibility: hidden`；adapter 仍可被既有 config 載入，但不出現在新 bind / 新發布的 UI 選項
- R9. **實驗階段**渠道（dofollow 未驗證、bind flow 未穩）= `visibility: experimental`；只在 `--include-experimental` flag 或 WebUI 進階模式下出現

**Backward Compatibility**

- R10. Pilot 階段**現有 7+ 渠道照舊運作**；只有 pilot 渠道（Velog，理由見 Key Decisions）走 manifest 路徑
- R11. Pilot 完成後，舊渠道**逐一**遷移；每次遷移 = 一個小 PR，可獨立 review / revert
- R12. 遷移期間提供 `legacy_adapter` shim：舊 adapter 不寫 manifest 也能 register，但 contract test 把它們標 `legacy: true` 並計數，作為遷移進度看板

## Success Criteria

- **接入成本**：加一個新渠道 PR diff = `platforms/<name>/` 1 個資料夾 + 1 個 `register()` 呼叫；其他位置零改動
- **退役成本**：改 1 行 `visibility: retired` + 跑 test
- **零散落**：`grep -r "velog\|medium\|telegraph" src/ --include="*.py" \| grep -v platforms/` 應幾乎為空（除 docstring）
- **Contract test 覆蓋**：每個遷移完的渠道都有 manifest ↔ adapter ↔ binding ↔ storage path 的一致性測試
- **回歸零**：pilot + 遷移期間，現有 3700+ test suite 全綠；不引入 footprint regression

## Scope Boundaries

- **不**改 publish 業務邏輯（anchor、content、linkcheck、SSRF 守衛皆原樣）
- **不**動 R9 registry 對外契約（`registered_platforms()` API 簽名不變，內部換實作）
- **不**做 external plugin / entry-point 路線（保留為未來選項；本期框架要先在 monorepo 內驗證）
- **不**改 WebUI 整體 IA / settings 折疊行為（Plan 012/013 已處理）
- **不**強迫一次性遷移所有渠道（明確 pilot-first，舊渠道並存）
- **不**改 `monolith_budget.toml` 的執行模型（manifest package 是新檔，不撞既有 ceiling）

## Key Decisions

- **Manifest 落點 `platforms/<name>/`**：與 adapter 同 package，contributor 一個資料夾交付全部；不放單一 `manifests.toml` 因為要直接 import adapter class（避免 string-based late binding 增加 `[[mock-patch-paths-after-extraction]]` 類陷阱）
- **Pilot 選 Velog**：覆蓋面最大（cookie bind + storage-state + post-verify + null-retry edge case + 多 backend 候選），能真正驗 manifest 設計；Telegraph 太簡單（無 bind）驗不出 bind 層、Medium 太複雜（CDP + Cloudflare）做 pilot 風險高
- **Visibility 4 態 vs 純 boolean**：`active/experimental/hidden/retired` 把 PR #136 的 `HIDDEN_FROM_UI`、PR #197 的「bind 過才出現」、未來 dofollow probe 階段三個正交需求收進一個欄位；單一 boolean 表達不下
- **Declarative-only manifest**：禁止 manifest 內寫 if/else 或 callback；複雜行為走 adapter / backend 的 protocol method。理由：保證 manifest 可被靜態工具掃（生 docs、生 CLI help、做 diff）
- **保留 R9 registry 對外介面**：`registered_platforms()` 仍然是入口；manifest 是 registry 內部資料結構升級，不破壞 7+ 個既有呼叫點
- **`legacy_adapter` shim 過渡**：避免 big-bang，給每個舊渠道單 PR 遷移空間；同時 contract test 量化「還有多少個 legacy」當作專案進度

## Dependencies / Assumptions

- 假設 `publishing/registry.py` 與 `publishing/adapters/__init__.py` 可以擴充（已驗，R9 即此處）
- 假設 `webui_app/contexts.py` 或 helpers `_render` auto-inject 可以承載 `bound_platforms` / `bind_cards` 動態列表（`[[render-auto-inject-over-per-route]]`）
- 不依賴未 merge 的 plan（Plan 009/016 等獨立進行）
- 不依賴外部 service / API 變更

## Outstanding Questions

### Resolve Before Planning

（無——pilot-first + Velog 已選定 + manifest 4 類已決，planning 可直接動工）

### Deferred to Planning

- [Affects R2][Technical] manifest 是 Python module、`@dataclass`、TypedDict 還是 Pydantic？trade-off：dataclass 最輕但弱型別、Pydantic 強驗證但加依賴。Planning 階段比對既有依賴與測試風格決定
- [Affects R4][Technical] 自動 discovery 用 `pkgutil.iter_modules` 還是顯式 `__init__.py` 中 import？前者 magic 多但真零接入成本、後者顯式但每加一個渠道仍要動 `__init__.py`。Planning 評估啟動時間 + import-time side effect
- [Affects R5][Needs research] `binding_status.py` 內常量被多少處引用？replace 影響面要 grep 量化，決定遷移 PR 拆幾個
- [Affects R6][Technical] Contract test 放 `tests/test_manifest_contract.py` 還是每個 `platforms/<name>/tests/` 自己一份？前者集中、後者就近。看 pytest collection 行為
- [Affects R7][Technical] `visibility: retired` 對 **既有已綁定**的渠道行為是什麼？硬切（拒絕發布）還是 grace（讀 config 不報新）？需 product 在 planning 階段給一條 deprecation 路徑
- [Affects R10][Needs research] Velog 目前的 bind / publish / post-verify 路徑分佈在哪些檔案？planning 階段要寫遷移 checklist
- [Affects R12][Technical] `legacy: true` 計數要不要進 CI 當看板（fail when 數字不降）？

## Next Steps

→ `/ce:plan` for structured implementation planning
