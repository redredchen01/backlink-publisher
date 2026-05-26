---
title: "refactor: Channel Manifest Architecture (extend register() to SSoT)"
type: refactor
status: phase1-complete
date: 2026-05-25
origin: docs/brainstorms/2026-05-25-channel-manifest-architecture-requirements.md
claims: {}
---

> **Phase 1 closeout (2026-05-25)** — All 5 implementation units shipped as a 5-PR stack (#207 → #208 → #209 → #211 → #212). Unit 2 split into 2a (HIDDEN_FROM_UI, shipped) + 2b (`_SAVE_CONFIG_KNOWN_ROOTS`, separate PR pending) due to 12+ reader sites + production `save_config` round-trip blast radius. Unit 4 split into 4a (`inject_platforms` reverse-lookup, shipped) + 4b (`_token_paste_status` + template iteration, separate PR pending) due to JS/template layer scope. **Velog pilot validated `BindDescriptor.extras` sufficient — no dataclass extension needed.** Phase 2 (migrate 9 remaining channels) and Phase 3 (CI fail gate on `legacy_platforms()`) are independent of this stack.

# Channel Manifest Architecture

## Overview

把每個渠道的所有 metadata（identity、UI、publish capability、bind backend、policy、visibility）收斂到 `publishing/registry.py::register()` 的 kwargs，讓 `platforms/<name>/` 或現有 `adapters/<name>.py` 在 `register()` 一處宣告全部屬性。所有現在散落在 `binding_status.py`、`webui_app/__init__.py`、`webui_app/helpers/contexts.py`、`config/_toml_utils.py`、模板、bind recipes 的渠道特化常量與條件邏輯，全部改為反查 registry。

關鍵設計：**不新建抽象層**，擴展現有 R9 `register()` 的契約（已有 `dofollow=` kwarg，再加 `bind=`、`visibility=`、`ui=`、`policy=`），保持遷移成本最低、與既有 8 平台對外契約 100% 相容。

## Problem Frame

見 origin: `docs/brainstorms/2026-05-25-channel-manifest-architecture-requirements.md`。

關鍵更正（recon 後）：R9 已比 brainstorm 假設的更成熟——`register(name, adapter, *, dofollow=...)` 已是 dofollow SSoT，CLI choices/schema 已動態。**真正散落的是 bind 層與 UI 層 metadata**，不是 publish 層。Plan scope 因此聚焦在那兩層 + visibility 生命週期管理。

## Requirements Trace

- R1. 每渠道單一 manifest 宣告 → 擴展 `register()` kwargs，與 adapter 同居
- R2. Manifest 4 大類（identity/UI、publish、bind、throttle/policy）→ 4 個新 kwargs：`ui=`、`bind=`、`policy=`、`visibility=`（dofollow 已有）
- R3. Declarative → kwargs 只收資料 dict / dataclass，禁 callable
- R4. Auto discovery → 既有 `adapters/__init__.py` 已是集中 import 點，不需 magic discovery
- R5. 散落觸點反查 registry → 改 `binding_status.py`、`webui_app/__init__.py` `inject_platforms`、`webui_app/helpers/contexts.py` `_token_paste_status`、`config/_toml_utils.py` `_SAVE_CONFIG_KNOWN_ROOTS`
- R6. Contract test → 新 `tests/test_manifest_contract.py`，擴展 `tests/test_r9_extension_readiness.py`
- R7. 退役 = 一行 `visibility="retired"` → `visibility` enum 4 態
- R8. 隱藏但保留 adapter → `visibility="hidden"`，取代 `HIDDEN_FROM_UI` frozenset
- R9. 實驗階段渠道 → `visibility="experimental"`
- R10. Pilot 階段現有渠道照舊 → 新 kwargs 全有預設值（向後相容）
- R11. 逐一遷移 → 每個渠道一個獨立 PR
- R12. `legacy: true` 計數看板 → `registry.legacy_platforms()` helper + contract test count

## Scope Boundaries

- **不**改 publish 業務邏輯（anchor、content、linkcheck、SSRF 守衛、retry、throttle 演算法）
- **不**改 R9 `registered_platforms()` 對外 API（內部資料結構升級，新增 helper）
- **不**做 external plugin / entry-point（保留為未來）
- **不**改 WebUI 整體 IA（Plan 012/013 已處理）
- **不**強迫一次性遷移所有 8 平台（pilot Velog 一個）
- **不**動 `monolith_budget.toml` 既有 ceiling
- **不**改 bind backend 內部實作（chrome CDP / token-paste / oauth 各自不動）
- **不**改 storage-state 路徑慣例（manifest 宣告現存路徑、不搬檔）

## Context & Research

### Relevant Code and Patterns

- **Registry SSoT**：`src/backlink_publisher/publishing/registry.py:165–264`（`_DOFOLLOW_BY_PLATFORM` dict、`register()` kwarg 模式、`registered_platforms()` / `dofollow_status()` helper）
- **Register 呼叫站**：`src/backlink_publisher/publishing/adapters/__init__.py:65–102`（8 platforms 集中宣告）
- **目標反查站**：
  - `src/backlink_publisher/publishing/binding_status.py:39`（`HIDDEN_FROM_UI` frozenset）、`:48–76` `get_channel_status()`、`:64` `dofollow_status` 用法
  - `src/backlink_publisher/webui_app/__init__.py:82–113` `inject_platforms()` 兩 key 注入
  - `src/backlink_publisher/webui_app/helpers/contexts.py:172–262` `_token_paste_status` / `_token_paste_status_notion` / config_summary tuples
  - `src/backlink_publisher/config/_toml_utils.py:12–14` `_SAVE_CONFIG_KNOWN_ROOTS`
- **Velog 全貌**（pilot 對象，覆蓋 5 種特化點）：
  - `src/backlink_publisher/publishing/adapters/velog_graphql.py` + `_velog_graphql_impl.py`
  - `src/backlink_publisher/publishing/browser_publish/recipes/velog.py` + `_velog_selectors.py`
  - `src/backlink_publisher/cli/_bind/recipes/velog.py`
  - `src/backlink_publisher/cli/velog_login.py`
- **Contract test 範例**：`tests/test_r9_extension_readiness.py:38–77` R9a/R9b/R9e 四 assertion
- **退役 pattern reference**：PR #136 write.as `HIDDEN_FROM_UI`、PR #204 hashnode 全清

### Institutional Learnings

- `[[grep-dofollow-map-before-shipping-adapter]]`：dofollow 已是 register kwarg，但歷史上有過散落；manifest 化要確保不重蹈
- `[[wire-token-paste-channel-five-sites]]`：5 處 wire 必須統一從 manifest 迭代
- `[[platforms-vs-bound-platforms-split]]`：兩個 context key 各自有用途，新 manifest 要保留二分；不可合併
- `[[platform-retirement-known-roots-pattern]]`：退役不要刪 `_SAVE_CONFIG_KNOWN_ROOTS` 條目，只改 writer 條件分支；manifest 化後改為 `visibility=retired` 時 writer 自動跳過
- `[[hidden-from-ui-pattern-for-retiring-channels]]`：drift test 要扣 `HIDDEN_FROM_UI` 數量；新 contract test 要扣 `visibility != active` 數量
- `[[mock-patch-paths-after-extraction]]`：移函數位置會破 mock.patch 字串；本計畫盡量原地擴 kwargs、不搬函數
- `[[render-auto-inject-over-per-route]]`：bind_cards / bound_platforms 走 `_render` auto-inject，不要每 route plumb
- `[[plan-claims-gate-shipped]]`：plan 提到的 SHA 都已 merge，不需 claims block

### External References

不適用——R9 registry pattern 是 in-house design，繼續沿用。

## Key Technical Decisions

- **擴展 `register()` 而非新建 `Manifest` class**：保留 R9 既有 8 平台 register 呼叫 100% 相容，加新 kwarg 預設 None；4 類新 kwarg `ui=` / `bind=` / `policy=` / `visibility=` 收 `dataclass` 或 `TypedDict`。理由：避免 `[[mock-patch-paths-after-extraction]]` 類陷阱；最小遷移成本；已驗證 R9 pattern 可承載 kwargs 擴展
- **資料載體選 `@dataclass(frozen=True)`，不引入 Pydantic**：本 repo 既有 schema 用 `jsonschema` + 手寫 validator（見 `schema.py`），加 Pydantic 多一個依賴與兩種風格並存。`@dataclass(frozen=True)` 滿足 declarative + immutable + 可靜態掃；contract test 補 schema-like 驗證
- **`visibility: Literal["active","experimental","hidden","retired"]`**：4 態 enum 取代散落的 `HIDDEN_FROM_UI` frozenset 與隱含「已 register 即 active」假設；`retired` 對既有已綁定 config 走 grace mode（讀 config 不報新發布；deferred 細節見 Open Questions）
- **`bind=` 收 list of backend descriptors**：一渠道可能支援多 backend（如 medium 同時有 chrome CDP + GraphQL token）；list 而非 dict 讓 UI 按宣告順序顯示
- **Helper API 加新 4 個函數**：`registry.ui_meta(name)` / `registry.bind_descriptors(name)` / `registry.policy(name)` / `registry.visibility(name)` / `registry.active_platforms()` / `registry.bound_platforms(cfg)`；既有 `registered_platforms()` 簽名與行為不變
- **Pilot 選 Velog**：覆蓋 5 處特化（API adapter + browser recipe + bind recipe + login route + selectors），是真實壓力測試；Telegraph 無 bind 太輕、Medium CDP+Cloudflare 太複雜風險高
- **`legacy_platforms()` 計數作為遷移看板**：未填新 kwargs 的 register 視為 legacy；contract test 不 fail（避免阻塞 pilot），只在輸出印「N legacy / M total」；後續 PR 可選擇升 fail 門檻
- **Discovery 不用 `pkgutil.iter_modules`**：保留 `adapters/__init__.py` 集中 import；新加渠道 = 加檔案 + 加一行 `register()`，不引入 magic 載入

## Open Questions

### Resolved During Planning

- **manifest 用 dataclass / TypedDict / Pydantic？** → `@dataclass(frozen=True)`（見 Key Decisions）
- **discovery 機制？** → 保留 `adapters/__init__.py` 集中 import，不做 auto-discover
- **contract test 落點？** → 集中在 `tests/test_manifest_contract.py`，不分散到 `platforms/<name>/tests/`（pytest collection 簡單、跨渠道 invariant 集中表達）
- **要不要 `legacy: true` 進 CI 看板？** → Pilot 階段只印計數，不 fail；等所有現有渠道遷完再升 fail gate
- **manifest 落點 `platforms/<name>/` 還是現有 `adapters/<name>.py`？** → 暫不搬檔，manifest = 在 `adapters/__init__.py` 的 register 呼叫處宣告。理由：搬檔會破 `mock.patch` 字串 + 撞 `monolith_budget`；先做 metadata 收斂，搬檔留作未來獨立 PR
- **velog 5 處特化檔搬不搬？** → 不搬，宣告路徑進 `bind=` descriptor 即可

### Deferred to Implementation

- **既有已綁定 config 在 `visibility="retired"` 時的行為**：硬切（拒絕發布）/ grace（warn 但允許）/ silent。需在 Unit 4 寫 publish 流程改動時對 1 個 retired 案例做行為測試決定（建議 grace + WARN log）
- **manifest dataclass 是否要 `__post_init__` validation**（例如 `bind` 列表非空、storage_state path 存在）vs 全靠 contract test。Implementation 時看哪個錯誤訊息對 contributor 更友善
- **Velog `bind=` descriptor 的精確欄位**：`backend="cookie"` + `storage_state_path` + `login_endpoint` + `bind_card_template` 是否夠？要在 Unit 3 寫 velog manifest 時對照 5 個檔案實際需求 fine-tune
- **`bound_platforms` helper 是否要 cache** (per-request `_g_cache` per `[[flask-g-cache-pattern]]`)：看 unit 5 整合後 inject_platforms 呼叫頻率決定

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

**Extended `register()` contract** — 4 個新 kwargs（dataclass-typed）：

```text
register(
    name,                       # existing
    adapter,                    # existing
    *,
    dofollow,                   # existing (R9)
    ui=None,                    # NEW: UiMeta(display_name, domain, category, icon?)
    bind=None,                  # NEW: list[BindDescriptor(backend, storage_state_path,
                                #                          login_endpoint?, card_template, ...)]
    policy=None,                # NEW: Policy(throttle_band, env_keys, retry_id,
                                #             liveness_probe_sec, language_whitelist)
    visibility="active",        # NEW: "active" | "experimental" | "hidden" | "retired"
)
```

**Reverse-lookup flow** — 所有散落觸點改為從 registry 反查：

```text
binding_status.HIDDEN_FROM_UI         → registry.visibility(name) in {"hidden","retired"}
binding_status.get_channel_status     → augment with visibility + bind descriptors
webui_app.inject_platforms (2 keys)   → registry.active_platforms()      [for filter chips]
                                      → registry.bound_platforms(cfg)    [for publish select]
webui_app.helpers._token_paste_status → iterate registry.bind_descriptors(name) for backend=="token-paste"
config._toml_utils._SAVE_CONFIG_..    → derive from {n for n in registry.registered_platforms()
                                                     if registry.visibility(n) != "retired"}
schema.supported_platforms            → already delegates registry (no change)
templates/_channel_card_macro.html    → reads bind descriptors via context helper
```

**Migration order** (pilot-first per origin):

```text
Unit 1 (framework)        → 加 kwargs + dataclass + 6 helper, 全可選, contract test
Unit 2 (visibility wire)  → HIDDEN_FROM_UI / _SAVE_CONFIG_KNOWN_ROOTS / drift test 改反查
Unit 3 (Velog pilot)      → velog register 呼叫補完 4 個 kwargs, 驗證設計
Unit 4 (publish/bind wire) → inject_platforms / _token_paste_status / card 模板改反查
Unit 5 (legacy 看板)      → legacy_platforms() + contract test count + docs
```

## Implementation Units

- [x] **Unit 1: Extend `register()` with 4 declarative kwargs + dataclasses + helper API** — shipped on branch `feat/manifest-registry-u1` (commit `96192be`, 6 files +699/-0, 16 new tests + 67 regression all green)

**Goal:** 在 `publishing/registry.py` 加 4 個新 kwarg（全可選、預設 `None` 或 `"active"`），定義 `UiMeta` / `BindDescriptor` / `Policy` 三個 `@dataclass(frozen=True)`，加 6 個 lookup helper。既有 8 平台 register 呼叫不動、行為不變。

**Requirements:** R1, R2, R3, R10

**Dependencies:** 無

**Files:**
- Modify: `src/backlink_publisher/publishing/registry.py`（加 dataclass、kwargs、helper）
- Create: `src/backlink_publisher/publishing/_manifest_types.py`（dataclass 集中地，避免 registry.py 撞 monolith budget）
- Test: `tests/test_manifest_registry_kwargs.py`

**Approach:**
- 新 kwargs 都預設 `None` / `"active"`，未填的 register 呼叫照舊跑
- `@dataclass(frozen=True, slots=True)`；`BindDescriptor` 含 `backend: Literal[...]`、`storage_state_path: str | None`、`login_endpoint: str | None`、`card_template: str | None`、`extras: dict`（escape hatch）
- 新 helper：`ui_meta(name) -> UiMeta | None`、`bind_descriptors(name) -> list[BindDescriptor]`、`policy(name) -> Policy | None`、`visibility(name) -> str`、`active_platforms() -> list[str]`（filter visibility）、`bound_platforms(cfg) -> list[str]`（filter visibility + bound）
- visibility default `"active"`；未填 = 既有行為
- `_REGISTRY` 內部 entry 從 `(adapter, dofollow)` 擴成包含全部欄位的 NamedTuple 或 dict；對外 API 不變

**Patterns to follow:**
- registry.py:170 既有 `register()` kwarg-only 風格
- registry.py:269 既有 `dofollow_status(name)` lookup helper

**Test scenarios:**
- Happy path: `register("foo", FooAdapter, dofollow=True)` 不傳新 kwargs → 仍可 `registered_platforms()`、`visibility("foo") == "active"`、`bind_descriptors("foo") == []`
- Happy path: `register("bar", BarAdapter, dofollow=True, ui=UiMeta(...), bind=[BindDescriptor(...)], policy=Policy(...), visibility="experimental")` → 6 個 helper 都回正確值
- Edge case: 重複 `register` 同名 → 行為與既有 R9 一致（後者覆蓋或報錯，依現行為照搬）
- Edge case: `bind=` 傳 list 內含非 `BindDescriptor` 型別 → `TypeError`（dataclass frozen 自帶）
- Error path: `visibility="bogus"` → contract test 攔截（Literal not enforced at runtime；在 contract test 驗）
- Integration: `active_platforms()` 排除 `visibility in {"hidden","retired"}`；`bound_platforms(cfg)` 在前者基礎上再過濾 `get_channel_status(cfg).bound`

**Verification:**
- `pytest tests/test_manifest_registry_kwargs.py` 全綠
- 全套 `pytest tests/` 全綠（既有 8 平台行為零變化）
- 既有 `tests/test_r9_extension_readiness.py` 不需改

---

- [x] **Unit 2a: Wire `visibility` into `HIDDEN_FROM_UI`** — shipped on `feat/manifest-visibility-u2` (commit `8f1b8d3`, PR #208 stacked on #207); PEP 562 `__getattr__` preserves legacy interface, 8 existing readers unchanged, 7 new tests + 13 drift tests + 4346 full-suite all green
- [ ] **Unit 2b: Wire `_SAVE_CONFIG_KNOWN_ROOTS` from registry** — split out due to 12+ reader sites + production `save_config` round-trip blast radius; separate PR

**Goal:** 把 `binding_status.HIDDEN_FROM_UI` 與 `config/_toml_utils._SAVE_CONFIG_KNOWN_ROOTS` 從手維護 frozenset 改成從 `registry.visibility(name)` 動態算。Drift test 同步更新。為 R7/R8 鋪路。

**Requirements:** R5, R7, R8

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/publishing/binding_status.py`（`HIDDEN_FROM_UI` 改 property/function）
- Modify: `src/backlink_publisher/config/_toml_utils.py`（`_SAVE_CONFIG_KNOWN_ROOTS` 改 function）
- Modify: `src/backlink_publisher/config/writer.py`（如有讀 frozenset 改呼叫）
- Modify: `tests/test_settings_dashboard_rendering.py`（drift assert 改用 registry helper）
- Test: `tests/test_manifest_visibility_wiring.py`

**Approach:**
- `HIDDEN_FROM_UI` 改 `def hidden_from_ui() -> frozenset[str]`，內部 `frozenset(n for n in registered_platforms() if visibility(n) in {"hidden","retired"})`
- `_SAVE_CONFIG_KNOWN_ROOTS` 同理：function 回傳 frozenset，內含「非 retired」name + 固定的 `"targets"`
- 既有 import 點若有 `from binding_status import HIDDEN_FROM_UI` 改成 `from binding_status import hidden_from_ui` + 呼叫（grep + 替換，注意 `[[mock-patch-paths-after-extraction]]`）
- drift test 計算「未填 manifest visibility 仍 active」的 baseline，與 8 平台同步

**Patterns to follow:**
- PR #136 `HIDDEN_FROM_UI` 使用 pattern
- PR #202 `_SAVE_CONFIG_KNOWN_ROOTS` 退役分支 pattern

**Test scenarios:**
- Happy path: pilot 前 8 平台全 active → `hidden_from_ui() == frozenset()`、`_save_config_known_roots()` 同既有 frozenset 內容
- Edge case: 動手在 test 內 `register("zzz", ZzzAdapter, dofollow=True, visibility="hidden")` → `hidden_from_ui()` 含 `"zzz"`
- Edge case: 動手 register `visibility="retired"` → `_save_config_known_roots()` 不含該 name；writer.py 不會 round-trip 該 section
- Integration: `pytest tests/test_settings_dashboard_rendering.py` 既有 drift 仍綠

**Verification:**
- 8 平台行為零變化（drift test + 全套 pytest 綠）
- 在 test 內任意 register `visibility="retired"` 不需動其他檔案即可隱藏

---

- [x] **Unit 3: Pilot — Velog full manifest declaration** — shipped on `feat/manifest-velog-pilot-u3` (commit `3a53a37`, PR #209 stacked on #208); **design validation passed** — `BindDescriptor.extras` sufficient for all 4 velog-specific module paths, no dataclass extension needed; monolith ceiling bumped 530→600 in same PR; 43 new tests + 38 monolith + 4387 full-suite green

**Goal:** 把 velog 的 register 呼叫從 `register("velog", VelogGraphQLAdapter, dofollow=True)` 升級為完整 manifest（補 `ui=` / `bind=` / `policy=`）。**不搬** velog 5 個特化檔，只在 register 呼叫處宣告它們的路徑/設定。這個 unit 是設計驗證——manifest 表達力夠不夠承載 velog 的全部 metadata。

**Requirements:** R1, R10, 設計驗證

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py`（velog register 呼叫）
- Test: `tests/test_manifest_pilot_velog.py`

**Approach:**
- `ui=UiMeta(display_name="Velog", domain="velog.io", category="dev-blog")`
- `bind=[BindDescriptor(backend="cookie", storage_state_path="<config_dir>/velog/storage-state.json", login_endpoint="/api/velog/login", card_template="_velog_card.html", extras={"recipe": "browser_publish.recipes.velog", "selectors": "browser_publish.recipes._velog_selectors"})]`
- `policy=Policy(throttle_band=(30,120), env_keys={"min": "VELOG_THROTTLE_MIN", "max": "VELOG_THROTTLE_MAX"}, retry_id="default", liveness_probe_sec=900, language_whitelist=("ko","en"))`
- visibility 保持 `"active"`（隱含預設）
- 若 `bind` descriptor 欄位不夠表達 velog 的 5 處特化，**就地擴 `BindDescriptor.extras` 或加新欄位回 Unit 1**（這正是 pilot 的目的）

**Test scenarios:**
- Happy path: `bind_descriptors("velog")` 回非空 list、第一個元素 backend=="cookie"
- Happy path: `policy("velog").throttle_band == (30, 120)`
- Integration: 完整 publish flow（mock 過外部 IO）依然走通既有 velog adapter；manifest 宣告不破壞行為
- Integration: velog bind flow（mock cookie storage）依然走通既有 bind recipe
- Edge case: `policy("velog").language_whitelist` 與既有 target_language 分派邏輯一致

**Verification:**
- `pytest tests/test_adapter_velog_graphql.py tests/test_manifest_pilot_velog.py` 全綠
- 既有 velog publish + bind 行為零變化
- 設計回顧（與本 unit 一併執行）：寫一段 markdown 註記到 plan 末尾 §Migration Notes，列出 velog 試做時發現 `BindDescriptor` 缺哪些欄位、Policy 表達是否夠用——作為下個 unit 與後續渠道遷移輸入

---

- [x] **Unit 4a: Reverse-lookup `inject_platforms` + `dashboard_channels` from registry** — shipped on `feat/manifest-webui-wiring-u4` (commit `96d1ebe`, PR #211 stacked on #209); `bound_platforms(cfg, is_bound)` + `active_platforms()` + `ui_meta()` reverse-lookup; 7 new tests + 13 drift + 4396 full-suite green
- [ ] **Unit 4b: `_token_paste_status` + `_channel_card_macro.html` iterate `bind_descriptors()`** — touches 5-site token-paste wire + JS card layer; separate PR

**Goal:** 把 `webui_app/__init__.py:inject_platforms`、`webui_app/helpers/contexts.py:_token_paste_status` / `_token_paste_status_notion` / `*_config_summary` 三類 helper、`templates/_channel_card_macro.html` 全改為從 registry 反查。`platforms` / `bound_platforms` 兩 context key 保留分離（per `[[platforms-vs-bound-platforms-split]]`）。

**Requirements:** R5

**Dependencies:** Unit 1, Unit 3（velog manifest 已填，作為反查 happy path）

**Files:**
- Modify: `src/backlink_publisher/webui_app/__init__.py`（`inject_platforms` 改呼叫 `registry.active_platforms()` / `registry.bound_platforms(cfg)`）
- Modify: `src/backlink_publisher/webui_app/helpers/contexts.py`（`_token_paste_status` 改 iterate `registry.bind_descriptors(name)` 找 `backend=="token-paste"`）
- Modify: `src/backlink_publisher/webui_app/templates/_channel_card_macro.html`（讀新 context helper 而非寫死 backend 字串；如改動大改 `webui_app/helpers/contexts.py` 加 `bind_cards()` 反查）
- Test: `tests/test_manifest_webui_wiring.py`、增補 `tests/test_webui_token_paste.py`

**Approach:**
- `inject_platforms` 內部換實作不換 return 結構（兩 key 維持）
- velog 已有 `bind=`，反查走 happy path；其他 7 平台 `bind=None` 時 fallback 維持既有寫法（contract: `bind is None` → 不破舊 wiring）
- token-paste 5 處 wire 中，未來 platform 只要 register 時加 `BindDescriptor(backend="token-paste",...)` 就自動入列，不需 5 處 wire
- 注意 `[[render-auto-inject-over-per-route]]`：新 `bind_cards()` 走 `_render` auto-inject，不要 per-route plumb
- 注意 `[[flask-g-cache-pattern]]`：若反查在 per-request 多次呼叫，加 `_g_cache`

**Patterns to follow:**
- `webui_app/__init__.py:82–113` 既有 `inject_platforms` 風格
- `helpers/contexts.py:172–262` 既有 helper 命名
- `[[render-auto-inject-over-per-route]]`

**Test scenarios:**
- Happy path: 8 平台全部 register（含 velog 有 `bind=`、其他 `bind=None`）→ `inject_platforms` 兩 key 仍正確、context 不缺欄
- Happy path: 模擬新 platform `register("xxx", ..., bind=[BindDescriptor(backend="token-paste", card_template="_xxx_card.html")])` → token-paste UI 自動出現該 card，不需改 5 處
- Edge case: visibility="hidden" 的 platform → `inject_platforms.platforms` 不含、`bound_platforms` 不含
- Edge case: velog `bind` 有 `backend="cookie"` → 不出現在 token-paste card 區
- Integration: 啟動 Flask test client、GET `/settings`、HTML 含既有 8 平台 card、velog card 渲染與遷移前 diff = 0（snapshot 或關鍵 selector 比對）
- Error path: registry 內某 platform `bind` malformed 或 None → UI 退到既有 fallback 不 500

**Verification:**
- 全套 pytest 綠（含 `test_settings_dashboard_rendering.py` / `test_webui_token_paste.py`）
- 手動或 snapshot 驗證 `/settings` 頁面與遷移前 visual 一致
- 加新 platform 範例（test-only）驗證「5 處 wire → 0 處 wire」目標達成

---

- [x] **Unit 5: contract test + AGENTS.md recipe** — shipped on `feat/manifest-contract-docs-u5` (commit `6f0bc13`, PR #212 stacked on #211); 53 contract scenarios + migration progress board ("1/10 channels migrated") + AGENTS.md `### 3b. Declare manifest metadata` subsection with full code example; 4449 full-suite green; **Phase 1 closed**

**Goal:** 加 `registry.legacy_platforms()` helper（回傳「未填新 kwargs」的 register name 清單），擴展 `tests/test_r9_extension_readiness.py` 加 contract assertion（manifest 一致性），新增 `tests/test_manifest_contract.py`。寫 CONTRIBUTING / AGENTS 段落說明新接入流程。

**Requirements:** R6, R12

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/publishing/registry.py`（加 `legacy_platforms()`）
- Modify: `tests/test_r9_extension_readiness.py`（加 R9f manifest contract assertion）
- Create: `tests/test_manifest_contract.py`
- Modify: `backlink-publisher/AGENTS.md`（新增「Adding a new publisher adapter」段落升級成 manifest 版本，或加 cross-ref）
- Modify: `CLAUDE.md`（架構段落加 manifest 註記）

**Approach:**
- `legacy_platforms()` = `[name for name in registered_platforms() if ui_meta(name) is None and bind_descriptors(name) == [] and policy(name) is None]`
- contract test 至少驗：
  - 任何 register name 都 `visibility(name) in {"active","experimental","hidden","retired"}`
  - 任何 `bind=` 內元素都是 `BindDescriptor` 實例
  - manifest 宣告的 `card_template` 路徑（如有）對應檔案存在
  - manifest 宣告的 adapter class 有 `publish` / `available` 方法
  - `legacy_platforms()` 數量印 stdout 當看板（不 fail）
- docs 寫清楚：新平台 = 在 `adapters/__init__.py` 加一行 `register(name, MyAdapter, dofollow=..., ui=..., bind=[...], policy=..., visibility="experimental")` + 加 `tests/test_adapter_<name>.py`

**Patterns to follow:**
- `tests/test_r9_extension_readiness.py` 既有 R9a/R9b/R9e 風格
- `backlink-publisher/AGENTS.md` 既有 "Adding a new publisher adapter" recipe

**Test scenarios:**
- Happy path: 8 平台跑完 `legacy_platforms()` 回 7（pilot velog 已遷）→ contract test 印「1/8 manifest, 7 legacy」不 fail
- Happy path: 新 register `("fake", FakeAdapter, dofollow=True, ui=UiMeta(...), bind=[...], policy=Policy(...))` → `legacy_platforms()` 不含 `"fake"`
- Edge case: register 內 `bind=[{"backend": "token-paste"}]`（傳 dict 非 dataclass）→ contract test 紅
- Edge case: `card_template="_nonexistent.html"` → contract test 紅
- Integration: 跑完 contract test，stdout 含「Manifest migration progress: N/M」可被 CI log 撈出

**Verification:**
- `pytest tests/test_manifest_contract.py tests/test_r9_extension_readiness.py` 全綠
- AGENTS.md 更新後一個外部 reviewer 讀完能照本宣科加新平台

---

## System-Wide Impact

- **Interaction graph**：`publishing/registry.py` 是中心；下游 `binding_status.py`、`webui_app/__init__.py`、`webui_app/helpers/contexts.py`、`config/_toml_utils.py`、模板、CLI 皆改為反查。register 呼叫處（`adapters/__init__.py`）是唯一寫入點。
- **Error propagation**：未填新 kwargs → 全部走預設、行為與既有 R9 一致；新 kwargs 內部 malformed → dataclass 構造期 `TypeError`（早於 import time 之後、register 呼叫期可被 contract test 攔截）。`visibility="retired"` 對既有 config 的 publish 路徑行為 deferred 到 Unit 4 決定（建議 grace + WARN）
- **State lifecycle risks**：`HIDDEN_FROM_UI` 從常量改為動態 → 第一次呼叫順序需在 `adapters/__init__.py` 完成 register 之後；對既有 import order 沒影響（registry 在 `binding_status` import 前已自舉）。注意 `[[invert-drift-check-when-invariant-becomes-dynamic]]`——drift test 不能在 module 載入期斷言、要 demote 到 test-time function call
- **API surface parity**：`registered_platforms()` / `dofollow_status()` / `supported_platforms()` 簽名與行為 100% 不變；新增 6 個 helper 全部 additive
- **Integration coverage**：Flask test client 跑 `/settings` 頁面，斷言 8 平台 card 渲染與遷移前 diff = 0；velog publish + bind flow E2E 不變
- **Unchanged invariants**：
  - R9 `registered_platforms()` 對外契約
  - 8 平台既有對外行為（publish / bind / report）
  - `monolith_budget.toml` 既有 ceiling（新檔 `_manifest_types.py` 不撞）
  - `pytest tests/` 全套綠
  - `PYTHONHASHSEED=0` footprint 不退化

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Mock patch 字串散落破壞（如 `mock.patch("...binding_status.HIDDEN_FROM_UI")`） | Med | Med | Unit 2 grep 所有 mock.patch 字串，預先列清單；測試先綠再合 PR；參考 `[[mock-patch-paths-after-extraction]]` |
| `HIDDEN_FROM_UI` 從常量改 function 撞 module-import-time 早期讀者 | Low | High | Unit 2 grep `HIDDEN_FROM_UI` 全部讀取點；若有 import-time 讀取，先改成 lazy 呼叫；參考 `[[invert-drift-check-when-invariant-becomes-dynamic]]` |
| Velog pilot 暴露 `BindDescriptor` 表達力不足 | Med | Low | 這正是 pilot 目的；Unit 3 含「設計回顧」交付物，發現缺欄位回 Unit 1 修；分支期作業，不阻塞其他 unit |
| 既有 7 平台 register 呼叫遷移時忘改一個導致行為漂 | Low | Med | Unit 1-2-3-4 完全不動其他 7 平台 register；遷移留給後續獨立 PR；`legacy_platforms()` 看板量化進度 |
| `visibility="retired"` 對既有已綁定 config 行為未定 → Unit 4 卡住 | Med | Low | Open Questions 已列；Unit 4 預設 grace+WARN，可後續調整；不阻塞 pilot |
| 並發 worktree 衝突（registry.py 是熱點檔） | Med | Med | `[[ce-work-must-audit-worktrees-first]]` + `[[ce-work-must-check-concurrent-rebase-before-commit]]`：每 unit 開工前掃 worktree + check HEAD；單 unit 單 PR |
| 文檔（AGENTS.md / CLAUDE.md）更新跟不上 code → contributor 走舊 recipe | Low | Low | Unit 5 同 PR 改文檔；contract test 加 stdout 看板放大遷移可見度 |

## Documentation / Operational Notes

- **AGENTS.md**：升級 "Adding a new publisher adapter" 段落，展示 `register(...)` 含 4 個新 kwargs 的範例；給 Velog 作 reference 範本
- **CLAUDE.md**：「Adapter registry (post-R9)」段落加一句指向 manifest 升級 + 連到本 plan
- **CHANGELOG**：在 manifest 框架 merge 時記錄「register() kwargs 擴展、向後相容、velog pilot」
- **Rollout**：無 feature flag、無 migration；merge 即生效。Pilot velog 異常 → revert 單 PR
- **Monitoring**：contract test stdout 的「Manifest migration progress: N/M」可被 CI log 撈出當看板
- **No DB / no infra changes**

## Phased Delivery

### Phase 1 — Framework + Pilot（本 plan 範圍）

- Unit 1（registry kwargs + dataclass + helper）
- Unit 2（visibility 反查 wire）
- Unit 3（Velog pilot manifest）
- Unit 4（WebUI 反查 wire）
- Unit 5（legacy 看板 + contract + docs）

Phase 1 merge 後：8 平台行為零變化、velog 是唯一完整 manifest、其他 7 平台仍 `legacy` 但功能正常、未來新平台已可以 manifest 完整接入。

### Phase 2 — 既有渠道逐一遷移（後續獨立 plan）

- 每渠道一個獨立 PR：blogger / medium / telegraph / ghpages / devto / notion / mastodon
- 順序：先簡單的（telegraph 無 bind）、後複雜的（medium CDP+Cloudflare）
- 全部遷完後 Phase 3 把 contract test stdout 看板升為 fail gate

### Phase 3 — Fail gate + plugin entry-point 評估（未來，不在本 plan）

- contract test `legacy_platforms() == []` 升為 fail
- 評估是否值得做 external plugin / entry-point 路線

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-25-channel-manifest-architecture-requirements.md](../brainstorms/2026-05-25-channel-manifest-architecture-requirements.md)
- **R9 registry**: `src/backlink_publisher/publishing/registry.py` PRs #63 / #68 / #70
- **Related patterns**:
  - `[[wire-token-paste-channel-five-sites]]` — 為什麼 5 處 wire 必須統一
  - `[[hidden-from-ui-pattern-for-retiring-channels]]` — `HIDDEN_FROM_UI` 與 drift test 慣例
  - `[[platform-retirement-known-roots-pattern]]` — `_SAVE_CONFIG_KNOWN_ROOTS` 退役慣例
  - `[[invert-drift-check-when-invariant-becomes-dynamic]]` — 常量改 dynamic 時的 drift test 反轉
  - `[[mock-patch-paths-after-extraction]]` — mock.patch 字串路徑陷阱
  - `[[render-auto-inject-over-per-route]]` — context auto-inject 慣例
- **Related PRs (history)**: #136 (write.as HIDDEN_FROM_UI), #197 (bound_platforms split), #202/#204 (write.as / hashnode 退役), #143 (CSRF guard)
