---
title: "fix: WriteAsConfig ImportError + half-finished Write.as retirement cleanup"
type: fix
status: active
date: 2026-05-21
claims: {}
---

# fix: WriteAsConfig ImportError + half-finished Write.as retirement cleanup

## Overview

Hashnode 发布失败抛 `ImportError: cannot import name 'WriteAsConfig' from
'backlink_publisher.config.types'`。根因不是 hashnode 本身，而是 canonical
worktree (`backlink-publisher/`) 内一份大规模 Write.as 退役 WIP 仍处于半完成
状态——某个中间快照里 `config/types.py` 已删 `WriteAsConfig` 但
`config/__init__.py` 仍 `from .types import WriteAsConfig`，CLI 启动即炸。

继续编辑已结构性消除了那个具体导入对，但本次调查暴露了 4 类仍然致命的
衍生问题：

1. **`pip install -e .` 绑定到错误的 sibling worktree**
   (`bp-fix-verify-ascii/`)——canonical 改动不生效，运行时看到的是
   该 sibling 的 SHA `e9e0a69` 状态（含 WriteAs）。这是 `[[per-worktree-venv-for-editable-install]]`
   反复踩过的模式。
2. **`publishing/registry.py` ↔ `publishing/adapters/__init__.py` 存在
   循环导入**——只在 "adapters 先加载" 顺序下偶然能工作。直接
   `from backlink_publisher.publishing import registry` 立即抛
   "cannot import name 'dispatch' from partially initialized module"。
3. **半完成的 Write.as 退役本身**——`adapters/writeas.py` 已删但
   `webui_app/binding_status.py`、templates、test 文档串仍提
   writeas；4 条新 `/ce:*` 路由无契约测试；`tests/test_bind_channel_driver.py`
   import `webui_store` 失败（需 `PYTHONPATH=src:.`）。
4. **缺乏针对 "package `__init__` 引用已删符号" 的 import-smoke 守卫**
   ——`tests/test_cli_python_m_entrypoints.py` 已覆盖 CLI 五入口的
   `python -m`，但没单独锁 `config/__init__.py` 的全 `__all__`
   解析，导致这一类回归只能等用户在 WebUI 发布时摔出来。

本计划把以上四类问题以原子单元收尾，确保 hashnode（以及其他渠道）
发布在 canonical worktree 内可信地工作，并为下次 Write.as / 任意
渠道退役流程提供保护栏。

## Problem Frame

- **用户场景**：操作员在 WebUI 触发 hashnode 发布，CLI 子进程
  (`python -m backlink_publisher.cli.publish_backlinks`) 在
  `cli/publish_backlinks.py:18` 加载 adapters 时崩溃，stderr 留下
  `ImportError: WriteAsConfig`。
- **发生时刻状态**：`config/types.py` 已被编辑掉
  `WriteAsConfig` 定义；`config/__init__.py` 还残留 `from .types import (..., WriteAsConfig, ...)`。
- **现状**：用户已继续编辑使 `config/__init__.py` 与
  `config/types.py` 双向自洽；该具体 ImportError 不再可被复现，
  但 4 条衍生不稳定仍在，且任意一次类似中途编辑都会重新触发同
  一类崩溃。
- **现有 worktree 拓扑**：18+ 个 `bp-*/` sibling 共享 `.git/`；
  `pip install -e .` 当前绑定 `bp-fix-verify-ascii/`，操作员从
  canonical 工作树编辑的代码运行时不生效——这恰好是导致 user
  "为什么改了还是报错" 体验的核心放大器。

## Requirements Trace

- **R1**：canonical `backlink-publisher/` 内 `python -m backlink_publisher.cli.publish_backlinks --help` 在新鲜 shell 中 0 退出且 stderr 干净。
- **R2**：`from backlink_publisher.publishing import registry`（无论 adapters 是否先加载）必须成功，杜绝循环导入。
- **R3**：`config/__init__.py` 中任何 `from .types import X` 的 X 必须实际存在于 `types.py` 顶层并可 `getattr`——任意一次半成品提交会被 CI 而非用户先抓到。
- **R4**：`tests/` 在 `PYTHONPATH=src:.` 下全套通过，零 collection error、零 contract drift。
- **R5**：WebUI 不再向用户暴露 writeas 任何 UI 痕迹（HIDDEN_FROM_UI 模式按 PR #136 已落地，但 binding/templates/docstring 残留需补齐）。
- **R6**：从 WebUI 触发 hashnode 发布到落地 stdout 含 URL，端到端冒烟通过。

## Scope Boundaries

**In scope**

- 修复 pip editable install 绑定到错 worktree 的运营性问题（命令行操作 + 文档化）。
- 切断 `publishing/registry.py` ↔ `publishing/adapters/__init__.py` 循环导入。
- 收尾 Write.as 在 templates / binding_status / 注释中的残留。
- 为 `config/__init__.py` 公开符号实存性、CLI 入口在 "registry-first" 顺序下可导入两类回归补 import-smoke 测试。
- 修 4 条 `/ce:*` 路由的契约测试 + `tests/test_bind_channel_driver.py` 的模块解析。
- Hashnode 发布端到端冒烟。

**Out of scope（明确不做）**

- 不重写 `cli/`、不动 `schema.py`、不动 R9 adapter registry contract（PR #124 后已稳定）。
- 不重新引入 Write.as adapter 代码或测试（已被 PR #136 与本次 WIP 共同退役；本计划只是 cosmetic 收尾，不还魂）。
- 不动 monolith budget（除非 import 拆分客观引起 SLOC 漂移，那时同 PR 内 ≥80 字 rationale 上调）。
- 不接 LLM/banner 新逻辑、不动 image_gen、不碰 chrome CDP backend。
- 不解决 `webui_store/sqlite.py` 这条未追踪的 SQLite 迁移线（独立 plan）。
- 不为 `[targets.*]/[sites.*]/[anchor_alarm]/[anchor.proportions]/[llm.anchor_provider]` 解决 round-trip 已知坑（项目级技术债，独立处理）。

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/config/__init__.py` — 公开 re-export 面；
  当前已（在 WIP 中）移除 `WriteAsConfig`、`load_writeas_token`、
  `save_writeas_token`、`WriteAsConfig` from `__all__`。
- `src/backlink_publisher/config/types.py` — `WriteAsConfig`
  dataclass 与 `Config.writeas` 字段、`writeas_token_path` property 全部已删。
- `src/backlink_publisher/publishing/adapters/__init__.py` — `register("writeas", ...)`、
  `verify_adapter_setup` writeas 分支、`_verify_writeas_live` helper 已删；
  top-level imports 已去掉 `WriteAsCdpAdapter`、`WriteAsAPIAdapter`。
- `src/backlink_publisher/publishing/registry.py` — 现有循环：
  L49 `from .adapters.base import AdapterResult` ↔ `adapters/__init__.py` L29 `from ..registry import dispatch, register, registered_platforms`。
- `webui_app/binding_status.py` — `HIDDEN_FROM_UI` 模式由 PR #136 引入；
  diff 显示已被本次 WIP 删 4 行，但未确认是否符合
  `[[hidden-from-ui-pattern-for-retiring-channels]]` 完整契约（drift test 应减去 `len(HIDDEN_FROM_UI)`）。
- `tests/test_cli_python_m_entrypoints.py` — 已锁五 CLI 入口 `python -m` 0 退出
  `[[python-m-missing-main-guard]]`，但不锁 package `__init__` 公开符号
  完整性。
- `tests/test_webui_route_contract.py:990` — 契约 drift 测试，
  `/ce:cancel-task`、`/ce:dashboard/api/stats`、`/ce:queue-status`、
  `/ce:retry-all-failed` 4 条新路由无对应 client.get/post 测试。
- `tests/test_bind_channel_driver.py:30` — `from webui_store import channel_status_store`，
  collection 时需要 `webui_store` 在 sys.path 上；目前
  `pyproject.toml` 仅 `package = src` 不含 root 下的 `webui_store/`。

### Institutional Learnings

- `[[per-worktree-venv-for-editable-install]]`（feedback）—— 每 worktree
  自带 `.venv` + `pip install -e ".[dev]"` 是比 `PYTHONPATH=src` 更彻底
  的隔离；否则 sibling 间会互相 shadow。
- `[[pythonpath-src-for-sibling-worktree]]`（feedback）—— 没法每
  worktree 独立 venv 时退而求其次的最小逃逸路径。
- `[[hidden-from-ui-pattern-for-retiring-channels]]`（feedback）—— PR #136
  确立的退役流程：adapter source 保留 + `HIDDEN_FROM_UI` 过滤层 + drift test 减 `len()`。
- `[[python-m-missing-main-guard]]`（feedback）—— 拆包后 `__main__.py`
  缺失会 silent exit 0；CI 必须有 import smoke 锁住，否则用户 WebUI 时才中招。
- `[[grep-all-legacy-import-forms]]`（feedback PR #124）—— 删除一个
  symbol/path 时必须 grep 7 种形态（绝对/相对/多行/裸 import/`mock.patch` string targets），
  full pytest 才是最后兜底。
- `[[ce-work-must-audit-worktrees-first]]`（feedback）—— 动手前必查
  `git worktree list` + 主 worktree `git status`，本仓 >15 个 `bp-*` 并发常态。
- `[[atomic-write-canonical-for-secret-storing-json]]`（feedback）—— 写
  api_key/token 类 JSON 必须 `safe_write.atomic_write`，0600。本计划无新增 token 写入，但回归测试时需观察既存路径未被本 WIP 改坏。

### External References

不需要外部研究。问题 100% 在仓内：导入图、worktree 拓扑、测试套件——
全部本地可验证，无新框架/平台 API 表面。

## Key Technical Decisions

- **D1：先解决 editable install 绑定再动代码**。原始报错的放大器
  是用户在 canonical 改代码但运行时跑的是 sibling worktree 安装的
  版本。U1 先做这一步，否则 U2-U7 的任何修复在用户机器上都"看起来没生效"。
- **D2：循环导入修复方向选 "registry.py 把 `AdapterResult` import 下沉到函数体内"** 而非 "adapters/__init__.py 把 registry import 下沉"。理由：`AdapterResult` 在 `registry.py` 中仅出现在 `dispatch()`/`Publisher.publish` 的 type annotation，用 `from __future__ import annotations`（文件已有）+ `if TYPE_CHECKING:` 保护即可消除运行时依赖；adapters 端 register 调用必须在 import 时执行，下沉会破坏注册时机。备选 "把 `AdapterResult` 独立成 `publishing/_result.py` 顶层模块" 是更激进的备选，单元 SLOC 涨幅可控，但跨 PR 改动面大，留作未来重构。
- **D3：U3 import-smoke 测试覆盖两个不变量**：(a) `import backlink_publisher.config; [getattr(mod, name) for name in mod.__all__]` 全部 resolve；(b) `from backlink_publisher.publishing import registry` 单独子进程加载 0 退出（破除 import-order 偶然性）。两条断言放同一 test file，subprocess 隔离。
- **D4：U5 `test_bind_channel_driver.py` 修复路径选 "把 `webui_store/` 加进 `pyproject.toml` setuptools packages"** 而非 "保持 `PYTHONPATH=src:.`"。理由：本仓多次依赖 root-level 模块（`webui_app/`、`webui_store/`、`webui.py`），让 setuptools 显式声明 packages 一次到位；workspace-root Makefile 用 `PYTHONPATH=src` 是 workspace-wide sweep 的 workaround，不该传染给 canonical CI。
- **D5：U6 Write.as cosmetic 残留只清非语义部分**——templates 注释、test docstring、`binding_status.HIDDEN_FROM_UI` 校验。不删 `_DOFOLLOW_BY_CHANNEL` 等 registry-validated 数据结构里的 writeas 条目（`[[grep-dofollow-map-before-shipping-adapter]]` 反过来：删去前必查 R9 extension 测试是否仍 expect 它）。
- **D6：U7 hashnode 冒烟用 dry-run 模式 + 既存 hashnode-token.json**——不引入新 token、不发真 post；仅验证 verify_adapter_setup 通过 + adapter 进入 publish 路径直到 `dry_run=True` 拦截。

## Open Questions

### Resolved During Planning

- **Q：原始 `WriteAsConfig` ImportError 是否仍可在 canonical 复现？**
  → A：不能。`config/types.py` 与 `config/__init__.py` 已同步删除该符号。复现需 reset 到中间提交，不在本计划范围。
- **Q：现状 hashnode 测试套件是否绿？**
  → A：是。`PYTHONPATH=src pytest tests/test_hashnode_banner.py` 8 passed。
- **Q：当前总体测试套件状态？**
  → A：`PYTHONPATH=src:. pytest tests/ --ignore=tests/test_bind_channel_driver.py` 3201 passed / 2 failed / 10 skipped。失败为 (a) telegraph dispatcher pytest-socket block、(b) `test_every_route_has_at_least_one_contract_test` 4 路由缺测。
- **Q：循环导入是回归还是一直存在？**
  → A：一直存在但被 import 顺序掩盖。最早测试覆盖缺失。
- **Q：是否需重新发布 Write.as adapter？**
  → A：否。PR #136 已正式退役 UI，本次 WIP 删 adapter source 是延续退役。

### Deferred to Implementation

- **Q：U2 fix 后 `AdapterResult` 的 type hint 改 `"AdapterResult"` (string forward ref) 还是 `TYPE_CHECKING` import？**
  → 实现时决定；`from __future__ import annotations` 已在 registry.py 第 44 行，理论上裸 `AdapterResult` annotation 在运行时不解析，但 `dispatch()` 的返回类型若被 `inspect.signature(..., eval_str=True)` 触发会炸。倾向 `if TYPE_CHECKING` 守 import + 保持注解形态。
- **Q：U4 修复 4 条 `/ce:*` 路由契约测试要不要打通参数化？**
  → 实现时决定：每条路由独立 `test_<name>_route_smokes` 还是 `pytest.mark.parametrize`。看路由 method/body 形态差异多大。
- **Q：U5 `pyproject.toml` packages 怎么声明？**
  → 实现时决定 `packages = ["backlink_publisher", "webui_app", "webui_store"]` (find: 排除 webui.py 单文件) 还是 `find:` + `[tool.setuptools.packages.find]` exclude 策略。
- **Q：U7 冒烟用 CLI 直跑还是经 WebUI subprocess？**
  → 实现时决定。倾向 CLI 直跑 + 单独再做一次 WebUI 浏览器手动；自动化端到端进 WebUI E2E 成本高。

## Implementation Units

### Dependency Graph

```text
U0 (worktree audit) ── U1 (editable install realign) ── U7 (hashnode smoke)
                       │
                       ├── U2 (break circular import) ── U3 (import smoke tests)
                       │
                       ├── U4 (route contract tests)
                       │
                       ├── U5 (webui_store package + bind_channel test)
                       │
                       └── U6 (Write.as cosmetic cleanup)
```

U0/U1 是所有后续单元的前置（否则改动不生效）。U2-U6 互相独立，可并行 PR。U7 在 U1-U6 全部 land 之后再做端到端验证。

- [ ] **Unit 0: Worktree audit + WIP triage**

**Goal**：在动任何代码前，把当前 `backlink-publisher/` 36 文件未提交 WIP
分成 (a) Write.as 退役收尾（属本计划）、(b) `webui_store/sqlite.py` 迁移线（独立）、(c) 4 条 `/ce:*` 路由新增（部分属本计划）、(d) `scheduler.py`/`queue.py`/dashboard 大改（独立 plan 013？），打 4 个 stash 或拆 4 条 feature branch。

**Requirements**：R1, R4

**Dependencies**：无

**Files**：
- 操作 only：`git stash push -m "<label>" -- <paths>` × N

**Approach**：
- `git status --short` 输出全部 36 文件 + 6 untracked 分类
- 与 `[[ce-work-must-audit-worktrees-first]]` / `[[worktree-concurrent-switching]]` 对齐：跨 sibling 是否有并行 agent 占用 `bp-fix-publish-false-success/` 等同主题 worktree
- 输出"本计划只动哪些文件"白名单；其他暂存

**Patterns to follow**：
- 多次走过的 stash-with-message 套路（见 memory）
- 严禁 `git add -A` —— `[[external-agent-concurrent-edits-in-shared-worktree]]`

**Test scenarios**：
- 无（运营单元，无代码）。Test expectation: none -- pure ops triage

**Verification**：
- `git diff --stat` 后只剩本计划要动的白名单文件
- 其他 WIP 在 `git stash list` 可定位回滚

---

- [ ] **Unit 1: Pin pip editable install to canonical worktree**

**Goal**：让 `python -c "import backlink_publisher; print(backlink_publisher.__file__)"` 输出 canonical `backlink-publisher/src/...`，杜绝 sibling shadow。

**Requirements**：R1

**Dependencies**：U0

**Files**：
- 操作 only：`cd backlink-publisher && python -m pip install -e ".[dev]"`
- 文档：`AGENTS.md` 追加一段 "Working across sibling worktrees" 子节，明确 editable install 一次只绑一个 tree 的事实 + 推荐每 worktree 自带 `.venv`

**Approach**：
- 在 canonical 重跑 editable install 把 entry-point 重定向回来（egg-info 模板可观察到已被本次 WIP 改动）
- 不动其他 sibling 的 install——它们各自 `.venv` 自治；本步骤是 canonical 这一棵树的本地修复
- 文档化此约定，下次跨 worktree 切换不必再踩

**Patterns to follow**：
- `[[per-worktree-venv-for-editable-install]]`
- AGENTS.md "Workspace shape" 已有讨论

**Test scenarios**：
- Happy path：新 shell `cd backlink-publisher && python -c "import backlink_publisher; print(backlink_publisher.__file__)"` → 输出 canonical 路径
- Happy path：`python -m backlink_publisher.cli.publish_backlinks --help` 0 退出，stderr 空
- Edge case：跑 `pytest tests/test_no_monolith_regrowth.py` 仍绿（footprint gate 不被 install path 翻转影响）
- Test expectation: 无新增 test 文件——这是运营 + 文档单元

**Verification**：
- `which python` 指向 canonical `.venv` 或全局 venv，但 `backlink_publisher.__file__` 必须 canonical
- AGENTS.md 新增子节通过 markdownlint（如有 hook）

---

- [ ] **Unit 2: Break circular import between `publishing/registry.py` and `publishing/adapters/__init__.py`**

**Goal**：`from backlink_publisher.publishing import registry` 在新鲜进程中 0 退出，无论 adapters 是否预加载。

**Requirements**：R2

**Dependencies**：U1

**Files**：
- Modify: `src/backlink_publisher/publishing/registry.py`
- Test: `tests/test_publishing_registry_import_isolation.py`（新）

**Approach**：
- 当前 registry.py L44 已有 `from __future__ import annotations`，所有 type annotation 默认 lazy
- L49 `from .adapters.base import AdapterResult` 是运行时 import，仅被
  `Publisher.publish(...)` 的返回类型注解使用
- 改为 `if TYPE_CHECKING: from .adapters.base import AdapterResult`；裸 `AdapterResult` annotation 在运行时不解析（PEP 563 + future annotations）
- 验证 `dispatch()` 函数体内未直接使用 `AdapterResult` 构造器；若有，引用 `Any`-typed 或 isinstance 处用 string 形式
- 同时确认 `Publisher` ABC 子类（在 adapters/*）能继承——它们 import `Publisher`，与本改动无关

**Patterns to follow**：
- registry.py 现有 `from __future__ import annotations` 已就位
- `[[grep-all-legacy-import-forms]]`：grep 全仓 `AdapterResult` 使用形态确认无运行时构造

**Test scenarios**：
- Happy path：`subprocess.run([sys.executable, "-c", "from backlink_publisher.publishing import registry"])` 退出 0
- Happy path：`subprocess.run([sys.executable, "-c", "from backlink_publisher.publishing.adapters import publish"])` 退出 0
- Integration：现有 `tests/test_adapter_dispatcher.py` 一组全套通过——验证 ABC + dispatch 行为不变
- Edge case：`inspect.signature(registry.dispatch, eval_str=True)` 不抛 NameError（说明 forward ref 能解析）
- Error path：若改坏，循环依然存在——测试应直接捕获 `ImportError`

**Verification**：
- 两个 subprocess import 都 0 退出
- `pytest tests/test_adapter_dispatcher.py tests/test_publishing_registry_import_isolation.py` 全绿

---

- [ ] **Unit 3: Add import-smoke test for `config/__init__.py` `__all__` integrity + canonical-name resolvability**

**Goal**：任何半成品提交（把 `__all__` 里的某名字从 `types.py` 删了但 `__init__.py` 还引用）在 CI 立即被 module-level 加载断言抓到，而非到用户 WebUI 发布时才崩。

**Requirements**：R3

**Dependencies**：U1（确保 canonical install）

**Files**：
- Test: `tests/test_config_public_api_resolvable.py`（新）

**Approach**：
- `import backlink_publisher.config as cfg`
- 对 `cfg.__all__` 中每一项 `getattr(cfg, name)` 必须非 None；附带断言 `name in dir(cfg)`
- 类似 `tests/test_cli_python_m_entrypoints.py` 的 subprocess 形态：另起进程 import 避免本进程 caching 隐藏问题
- 同时验证 `from backlink_publisher.config.types import *` 不残留已删 dataclass（grep `WriteAsConfig` 防御）

**Patterns to follow**：
- `tests/test_cli_python_m_entrypoints.py` 的 subprocess 隔离风格
- `[[grep-all-legacy-import-forms]]`：测试本身就是 grep + import 守护栏

**Test scenarios**：
- Happy path：当前 `cfg.__all__` 每一项 `getattr` 成功
- Happy path：`cfg.Config`、`cfg.HashnodeConfig` 等关键名字存在
- Error path（mutation test 心态）：模拟从 `types.py` 删 `HashnodeConfig` → test 应炸 ImportError；本计划不做实操 mutation，只在 docstring 注明意图
- Edge case：`from .types import *` 不带任何 `WriteAs*` 名字（grep style）

**Verification**：
- 新测试在 canonical 全绿
- 故意临时删 `types.py` 任一 dataclass，新测试立即失败（开发者本地验一次后还原）

---

- [ ] **Unit 4: Add contract tests for 4 missing `/ce:*` routes**

**Goal**：`tests/test_webui_route_contract.py::test_every_route_has_at_least_one_contract_test` 通过。

**Requirements**：R4

**Dependencies**：U0（确认这 4 条路由属于本计划 WIP 而非他人并发 agent）

**Files**：
- Modify or Create: `tests/test_webui_ce_routes_contract.py`（新；专门收纳 `/ce:*` 系列）
- Reference: `webui_app/routes/queue.py`、`webui_app/routes/dashboard.py`（4 路由源；diff 已知有 +140 / +91 行未提交）

**Approach**：
- 4 路由：`/ce:cancel-task`、`/ce:dashboard/api/stats`、`/ce:queue-status`、`/ce:retry-all-failed`
- 每条至少一个 `client.get/post` smoke：状态码 + 必有响应 key
- 路由方法（GET/POST/methods 列表）从源文件 `@bp.route(...)` 装饰器读出
- CSRF：参考 `[[webui-csrf-architecture]]`——POST 类需走 `csrf_client` fixture；GET 用 `client`
- 不做完整业务断言，仅契约 smoke

**Patterns to follow**：
- `tests/test_webui_route_contract.py` 现有 contract 风格
- PR #143 后 `_global_csrf_guard` 强制——`csrf_client` fixture 用法

**Test scenarios**：
- Happy path × 4：每条路由 200/302/4xx（按方法预期）落在合理状态码
- Edge case：缺 CSRF token 的 POST 返 400/403（验证守卫仍工作）
- Error path：未授权或 task 不存在的输入返 4xx，stderr 不含 stack trace 渗漏

**Verification**：
- `pytest tests/test_webui_route_contract.py tests/test_webui_ce_routes_contract.py` 全绿
- `test_every_route_has_at_least_one_contract_test` 在新文件加入后 0 缺测路由

---

- [ ] **Unit 5: Restore `tests/test_bind_channel_driver.py` collectability — package `webui_store/` properly**

**Goal**：`pytest tests/` 默认不需要 `PYTHONPATH=src:.` 也能 collect 全部测试。

**Requirements**：R4

**Dependencies**：U1

**Files**：
- Modify: `backlink-publisher/pyproject.toml`（`[tool.setuptools.packages]` 段）
- Test: `tests/test_bind_channel_driver.py`（现存；修后应 collect 成功）

**Approach**：
- 当前 `pyproject.toml` 仅声明 `src/backlink_publisher/`；root 下 `webui_app/`、`webui_store/`、`webui.py` 是"挂在仓根、运行时被 webui.py 加载"的非标准布局
- 选项 A（倾向）：在 `pyproject.toml` 显式 `packages = ["backlink_publisher", "webui_app", "webui_store"]`，让 `pip install -e .` 把它们都 link 进 site-packages
- 选项 B：保持现状，在 `tests/conftest.py` 顶部 `sys.path.insert(0, str(Path(__file__).parent.parent))` —— 比 A 弱，pytest-only fix
- D4 已定选 A，理由：webui.py launcher 已假设这些模块可直接 import；声明显化使 sibling worktree + 独立 `.venv` 时无需额外 PYTHONPATH

**Patterns to follow**：
- 同仓 `src/backlink_publisher` 已声明形式
- AGENTS.md "Sibling worktrees and editable installs" 段已暗示需要 per-tree 安装

**Test scenarios**：
- Happy path：`cd backlink-publisher && python -m pip install -e ".[dev]"` 后 `python -c "from webui_store import channel_status_store"` 0 退出
- Happy path：`pytest tests/test_bind_channel_driver.py` collection 成功（不需 PYTHONPATH）
- Integration：全套 `pytest tests/` collection 0 error
- Edge case：sibling worktree 也跑同样 `pip install -e .[dev]` 后，自己 venv 里同样工作

**Verification**：
- `pytest tests/ --collect-only 2>&1 | grep -c "errors"` 输出 0
- `tests/test_bind_channel_driver.py` 跑通

---

- [ ] **Unit 6: Cosmetic Write.as removal cleanup (templates, comments, binding_status)**

**Goal**：消除"看起来 writeas 还在"的视觉误导；确保 binding_status `HIDDEN_FROM_UI` drift test 减项正确。

**Requirements**：R5

**Dependencies**：U0

**Files**：
- Modify: `webui_app/templates/_settings_channel_token_paste.html`（注释 L4：`"ghpages" | "writeas"` → `"ghpages"` 或更通用措辞）
- Modify: `tests/test_plan_backlinks_banner.py`（docstring L203 提 writeas-style fallback）
- Modify: `tests/test_hashnode_banner.py`（docstring L4：`writeas-style 显式 opt-in` 改 `instant-web-style`）
- Modify: `tests/test_velog_banner.py`（同上）
- Modify: `tests/test_webui_route_contract.py`（docstring L925）
- Verify: `webui_app/binding_status.py` 当前 4 行删除是否符合 `[[hidden-from-ui-pattern-for-retiring-channels]]` 完整契约（HIDDEN_FROM_UI 集合是否仍含 writeas、drift test 减项是否对齐）

**Approach**：
- 不删 `_DOFOLLOW_BY_CHANNEL` 等映射里仍可能存在的 writeas 条目——`[[grep-dofollow-map-before-shipping-adapter]]` 反向：删前确认无 R9 extension test 依赖
- 注释/docstring 改"writeas-style"为"Telegraph/instant-web-style"或更直白的描述，保留语义信息（"这个 None-return 模式是从某个原型来的"）

**Patterns to follow**：
- `[[hidden-from-ui-pattern-for-retiring-channels]]`
- `[[grep-all-legacy-import-forms]]`：grep 全 7 形态后再下笔

**Test scenarios**：
- Happy path：grep `WriteAs\|writeas\|write_as` 在 `src/` + `tests/` + `webui_app/` 仅剩允许的语义保留（适合 PR description 罗列）
- Happy path：`pytest tests/test_dashboard_channels_drift.py`（或等价 drift gate）继续绿
- Edge case：`HIDDEN_FROM_UI` 集合本身的 unit test 仍锁住 channel 不在 dashboard
- Test expectation: 主要靠 grep + 现有 drift gate，无新增专属测试

**Verification**：
- `grep -rn "writeas\|WriteAs" src/ tests/ webui_app/ webui_store/ 2>&1` 仅剩故意保留的字面量（PR description 列出）
- Drift gate 测试绿

---

- [ ] **Unit 7: End-to-end hashnode publish smoke**

**Goal**：操作员复现"刚刚发布 hashnode 这个渠道"路径，从 CLI（或 WebUI subprocess）走到成功 dispatch，验证整条链路在 U1-U6 之后健康。

**Requirements**：R6

**Dependencies**：U1, U2, U3, U4, U5, U6

**Files**：
- 操作 only：CLI dry-run + 可选 WebUI 浏览器手动
- 可选 Test: `tests/test_hashnode_publish_smoke.py`（已存在则验证，不存在不强制新增——hashnode adapter contract 测试已覆盖单元层）

**Approach**：
- Step 1：构造最小 seeds.jsonl → plan-backlinks → validate-backlinks → publish-backlinks --platform hashnode --dry-run
- Step 2：观察 stdout JSONL `status=dry-run-intercept`、stderr 无 traceback
- Step 3：可选 live 模式（需操作员同意 + 既存 hashnode-token.json）：发一篇 throwaway 草稿到 hashnode，验证 URL 返回 + verify-poll 成功
- 不引入新 token、不发真 production link

**Patterns to follow**：
- `[[bind-channel-diagnostic-playbook]]` 验证型态
- 既存 `tests/test_hashnode_banner.py` 已覆盖 banner None-return 路径

**Test scenarios**：
- Happy path：dry-run 模式 publish 一条 hashnode → stdout 1 行 JSONL，含 `platform=hashnode`、`dry_run=true`
- Happy path：`verify_adapter_setup("hashnode", config)` 在有 token 时返回 None，无 token 时抛 DependencyError（既存契约，回归验证）
- Edge case：错配 platform 字符串触发 ExternalServiceError，不应误炸 ImportError
- Integration：CLI exit code 与 schema.exit_codes 一致（0/4/6 等正常表，2 不出现）

**Verification**：
- CLI dry-run 0 退出，stdout JSONL 合规
- 操作员从 WebUI 手动复发 hashnode 一次，截图/log 落档
- `pytest tests/test_hashnode_*.py tests/test_adapter_dispatcher.py` 全绿

## System-Wide Impact

- **Interaction graph**：
  - `cli/publish_backlinks.py` → `publishing/adapters/__init__.py` → `publishing/registry.py` → `publishing/adapters/base.py`（循环修复在此节点解开）
  - `webui_app/routes/queue.py`（subprocess 触发 publish CLI） → CLI subprocess → adapter dispatch
  - `webui_store/channel_status_store` 由 `webui.py` 主进程与 CLI subprocess 共写 → 包声明（U5）影响两方
- **Error propagation**：
  - U2 修复后 `from backlink_publisher.publishing import registry` 失败模式从 "运行时晚发 ImportError" 变成 "import time 立即 ImportError"——更早暴露，但行为契约更一致
  - U3 import-smoke 让"`__init__.py` 引用已删符号"从用户面 ImportError 上移到 CI red
  - U4 contract gate 让"新增路由忘加测试"从生产线 4xx 上移到 CI red
- **State lifecycle risks**：
  - 无新增 token / cookie / state JSON 写入；不动 `safe_write.atomic_write` 路径
  - `pip install -e .` 重跑会刷新 egg-info；diff 显示 PKG-INFO/SOURCES.txt/requires.txt 已被本 WIP 改 → U1 的 reinstall 会再次重写，需 git status 复核
- **API surface parity**：
  - U2 不改 `Publisher` ABC 或 `dispatch()` 签名；`AdapterResult` 仅 type annotation 形态调整
  - U5 显式声明 packages 是 setuptools metadata 变化，下游消费者（其它 sibling worktree、CI workflow）感知不到差异
- **Integration coverage**：
  - U3 + U4 + U5 三条增量测试共同构成"半成品提交检测三件套"，下次 Write.as 类退役流程不会重复此 root cause
- **Unchanged invariants**：
  - R9 adapter registry contract（PR #124）—— `register(name, *cls)` 形态不变；`registered_platforms()` 仍为 CLI/schema 唯一 SOT
  - `_DOFOLLOW_BY_CHANNEL` 映射数据（`[[grep-dofollow-map-before-shipping-adapter]]`）—— 不在本计划改动面，U6 仅 grep 验证
  - WebUI CSRF architecture（`[[webui-csrf-architecture]]`）—— U4 新测试遵循 `csrf_client` fixture，不动 `_global_csrf_guard`
  - Monolith budget —— 无新增大文件；如 registry.py 因 TYPE_CHECKING 守卫意外越线，同 PR rationale 上调（不预期）

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| U1 reinstall 重写 egg-info 与 WIP 冲突 | U0 先 stash 全部 WIP，U1 跑完后 `git checkout -- src/backlink_publisher.egg-info/` 接受 reinstall 结果，再 stash pop 仅业务文件 |
| U2 `TYPE_CHECKING` 改动破坏 `inspect.signature(eval_str=True)` 调用方 | grep 仓内 `inspect.signature.*eval_str` 用例（预期无）；保留 string forward ref 作 backup |
| U4 4 路由的 method/payload 不在测试者掌握 | U0 子 task：实读 `routes/queue.py` 与 `routes/dashboard.py` 装饰器；与操作员对一次 4 路由真实预期形态 |
| U5 setuptools packages 显式声明误把 `tests/` 也 pack 进去 | 用 `packages = [...]` 显式列表 + 不用 `find:`；或 `find:` + `exclude = ["tests*"]` |
| U6 误删仍在使用的 writeas 字面量 | grep 7 形态 + full pytest 双兜底 |
| U7 真 live hashnode 发布污染操作员账号 | 默认 `--dry-run`；live 模式仅在操作员明确同意 + throwaway draft + post-test 删除 |
| 并发 agent 在 `bp-fix-publish-false-success/` 等 sibling 同时改 queue.py / scheduler.py | U0 跨 worktree git status 巡查；若 conflict surface 先停手与操作员对齐拆分 |
| Editable install 文档化引起其他 sibling worktree 操作员误以为本计划要他们也 reinstall | AGENTS.md 措辞明确"每 worktree 自治"；不全局推 reinstall |

## Documentation / Operational Notes

- **AGENTS.md**：U1 在 "Sibling worktrees and editable installs" 子节后追加：
  "Symptom: CLI/test 改了不生效 → `python -c "import backlink_publisher; print(backlink_publisher.__file__)"` 检查 install 落点；若指向 sibling，`pip install -e ".[dev]"` 重绑当前 worktree。"
- **CHANGELOG.md**：本次 PR(s) 进 `### Fixed` 节，至少两行：
  - "fix(config): prevent half-finished symbol removal from crashing CLI at import time"
  - "fix(publishing): break circular import in registry/adapters that masked itself under specific load order"
- **`docs/solutions/`**：本计划完成后产出 1 篇 `2026-MM-DD-half-finished-retirement-import-guard.md`，把"删 dataclass 时必须同步 `__init__.py` re-export + grep 7 形态 + import-smoke CI"沉淀为可搜索教训。注意按 `[[solutions-category-frontmatter]]` 不主动归一 `category` 写法。
- **运营**：U1 完成后周知操作员"如未来再遇到改代码不生效，先看 `backlink_publisher.__file__`"

## Sources & References

- 原始 traceback（用户输入）—— canonical worktree 中已结构性消除
- 仓内 `git diff --stat`（36 文件 1047/-1667 行）—— WIP scope 来源
- `tests/test_cli_python_m_entrypoints.py` —— `[[python-m-missing-main-guard]]` 配套
- `tests/test_webui_route_contract.py:990` —— contract drift 当前失败点
- PR #124（legacy bridge 删除）—— `[[grep-all-legacy-import-forms]]` 教训来源
- PR #136（Write.as UI 退役）—— `[[hidden-from-ui-pattern-for-retiring-channels]]` 母规则
- PR #143 / #148（WebUI CSRF + dedup）—— U4 CSRF fixture 依赖
- `[[per-worktree-venv-for-editable-install]]`、`[[pythonpath-src-for-sibling-worktree]]` —— U1/U5 设计依据
- AGENTS.md "Workspace shape" / "Sibling worktrees and editable installs" —— U1 文档基础
