---
title: "refactor: Delete legacy import bridge"
type: refactor
status: completed
date: 2026-05-20
claims: {}
---

# refactor: Delete legacy import bridge

## Overview

Plan 2026-05-18-001 Unit 6 引入了 `src/backlink_publisher/__init__.py` 里的
`_LegacyPathFinder`（一个 `sys.meta_path` 拦截器）+ `_REEXPORT_MAP`（17 条 flat
→ 新路径映射），让 `from backlink_publisher.errors import ...` /
`from backlink_publisher.adapters.medium_api import ...` 等老导入在不改动调用方
的情况下继续可用。

距离 Unit 6 已经过了一段时间，迁移窗口该收尾了：把剩下的 legacy import 全部
改写到新路径，删掉 bridge，让 import 解析回到 Python 默认机制——没有 meta-path
hook、没有 attribute 重定向、没有需要维护的映射表。

## Problem Frame

`_LegacyPathFinder` 是一个 transitional shim，它的存在让所有"该用新路径"的代码
可以躺在老路径上不动。当前实际情况：

- 生产代码（`src/`）依赖 bridge 的有 **7 行 / 6 文件**——已经接近零，迁移成本
  低于继续维护 bridge 的认知成本
- WebUI（`webui_app/` + `webui.py`）**9 行 / 6 文件**
- 测试套件 **~143 行 import + ~222 个 `mock.patch(...)` 字符串目标 / ~46 文件**（额外 2 处 `import backlink_publisher.adapters.X as Y` bare-import 形式在 `tests/test_adapter_medium_browser.py:321` 与 `tests/test_adapter_dispatcher.py:108`，与 `from ...` 形式合在一起处理）
- 文档（AGENTS.md, 历史 plan）多处声称 bridge 是合法 import 通道

留 bridge 的代价：
- 任何新人 grep `backlink_publisher.errors` 会看到 bridge 注释，分不清"哪条路径
  是 canonical"
- `sys.meta_path` 上多一个 finder，导入解析多一次失败-fallback 跳转（每个非
  legacy 模块也要先经过 `_LegacyPathFinder.find_spec` 才能交给默认 finder）
- 任何对 `backlink_publisher.adapters` / `backlink_publisher.errors` 这类老模块名
  做 IDE 跳转 / 静态分析 / type-checker 的工具都要面对一个"假模块"

删 bridge 的代价：
- 一次性机械重写 ~370 个 import / 字符串站点（~143 import 行 + ~222 patch-string + 2 bare-import）
- 测试 collection 时立刻验证：任何漏掉的 site → `ModuleNotFoundError` / `AttributeError`

净结果：少 ~110 SLOC，少一个 meta-path hook，import 解析路径变直，老路径名彻底
退役。

## Requirements Trace

- R1. `src/backlink_publisher/__init__.py` 不再包含 `_LegacyPathFinder` 类、
  `_REEXPORT_MAP` 字典、`sys.meta_path.insert` 调用
- R2. 全仓库 grep `from backlink_publisher\.(anchor_lang|anchor_metrics|anchor_profile|anchor_resolver|anchor_scheduler|content_fetch|work_scraper|work_themed_generator|language_check|verify_publish|errors|io_utils|jsonl|logger|markdown_utils|url_utils|adapters)\b` 在 `src/` + `tests/` + `webui_app/` + `webui.py` 内返回 0 行
- R3. 全仓库 grep `from backlink_publisher import (anchor_lang|...|adapters)\b` 在同样范围内返回 0 行
- R3b. 全仓库 grep `^\s*import backlink_publisher\.(anchor_lang|...|adapters)\b`（bare-import 形式，`as` 别名）在同样范围内返回 0 行
- R4. 全仓库 grep `["']backlink_publisher\.(anchor_lang|...|adapters)\b` （mock.patch / monkeypatch 字符串目标）在 `tests/` 内返回 0 行
- R5. `pytest tests/` 在 `PYTHONHASHSEED=0` 下全绿，xfail/xpass 计数与迁移前一致
- R6. `python -m py_compile src/backlink_publisher/**/*.py` 通过（CI 风格 lint）
- R7. AGENTS.md 的 "Import Conventions" 章节去掉 bridge 描述，改为单一 canonical 路径表

## Scope Boundaries

**Out of scope:**
- `src/backlink_publisher/linkcheck/__init__.py:7` 的 `from .http import *` 通配再导出。
  这是另一种向后兼容机制：`linkcheck` 是真包，`from backlink_publisher.linkcheck import check_url`
  通过包级 `__init__` 的通配 import 工作，不走 `_LegacyPathFinder`。bridge 删了之后
  这条路径仍然可用。是否清理留作单独后续 PR
- `src/backlink_publisher/__init__.py` 中将来可能需要的真正包级公共 API（目前没有）
- 任何 `webui_app/` / `webui_store/` 内部 layout 调整
- monolith budget ceilings 更新（`__init__.py` 不在被监控的 6 个文件清单里）
- `docs/plans/` 历史 plan 文档里对 `_LegacyPathFinder` 的引用——这些是历史记录，
  不修改

**Explicit non-goal:** 不引入"deprecation period + warnings then remove"的两步走，
原任务文本是"删除"。一刀切。

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/__init__.py:32-103` — `_REEXPORT_MAP` (17 entries) +
  `_LegacyPathFinder` (find_spec / create_module / exec_module)
- `src/backlink_publisher/__init__.py:107-118` — finder install 入口
- `src/backlink_publisher/anchor/__init__.py`, `content/__init__.py`,
  `_util/__init__.py` — 当前都是空 docstring（不依赖 bridge）
- `src/backlink_publisher/linkcheck/__init__.py` — 唯一一个有 wildcard 再导出的
  子包；out of scope 但读懂它能避免误删
- `AGENTS.md:77-89` — Import Conventions 章节，需要重写

### Institutional Learnings

- `docs/plans/2026-05-18-001-refactor-architecture-health-roadmap-plan.md:228+
  Unit 6` — bridge 当初的设计意图与契约（`_REEXPORT_MAP` 显式枚举，AttributeError
  消息含建议路径），删除时确认这些 invariants 不再被任何调用方依赖
- 仓库历史模式：任何跨多文件机械替换都用 `git grep -l ... | xargs sed -i ...`
  + `python -m py_compile` 立刻验证（参考 Plan 2026-05-18-001 各 Unit 的执行）
- `tests/test_no_monolith_regrowth.py` 用 `PYTHONHASHSEED=0` 验 footprint；本
  PR 不动 hot file，footprint 应不变
- `feedback-grep-all-worktrees-before-claiming-existence` — 跨 worktree 状态。
  本仓库有 >15 个 `bp-*/` 并发 worktree。**执行时**必须 `git worktree list` 确认
  目标 worktree 内 main 是最新的，且本 plan 选定的 site 集合在最新 main 上仍准确
- `feedback-pythonpath-src-for-sibling-worktree` — 若在 `bp-*/` worktree 内执行，
  `pip install -e .` 只绑一个 tree；要么换 `PYTHONPATH=src pytest` 要么给该
  worktree 单独 venv 重装 dev deps

### External References

未做外部研究——PEP 451 / `sys.meta_path` 删除是 Python 标准机制，移除一个自定义
finder = 把它从 `sys.meta_path` 拿掉 + 删类即可，不需要外部最佳实践。

## Key Technical Decisions

- **一次性机械重写，不走两阶段 deprecation**：原任务字面"删除"。仓库测试套件本
  身就是覆盖网（任何漏掉的 site collection 阶段就炸），不需要 runtime warning
  做兜底
- **`mock.patch` 字符串目标和 `import` 语句用同一套替换规则**：两类都是
  `backlink_publisher.<legacy_head>` 前缀字符串匹配，可以用同一份 17 条映射 sed
  脚本处理。先做 import，再做 patch 字符串，分两次跑可以让 `git diff` 更清晰
- **import 改写顺序：src → webui → tests**：先动生产代码，再动 webui，最后动测
  试。这样在前两步如果哪里漏一行，下一步 `pytest -x` 立刻打回；如果反过来先动
  测试，生产代码漏一行只有运行时（甚至特定路径）才暴露
- **顶层"submodule attribute"形式 (`from backlink_publisher import errors`) 需要
  非平凡改写**：bridge 让 `backlink_publisher.errors` 看起来像 `backlink_publisher`
  的属性。删完之后这条形式直接报错。改写成 `from backlink_publisher._util import errors as errors`
  或 `from backlink_publisher._util.errors import <specific names>`，**优先后者**
  （更窄、更明确），仅当原代码用 `errors.SomeName` 风格大量访问时退回前者
- **`__init__.py` 精简到最小内容**：保留 module docstring + `from __future__ import annotations`，
  不再 `import importlib` / `import sys`。如果 `__version__` 之类有需要再补
- **不动 `linkcheck/__init__.py`**：见 scope boundary。该文件提供的兼容路径是
  通过真包结构 + wildcard re-export 实现的，与 bridge 正交。后续如果要进一步收
  紧 import 路径再开单独 PR

## Open Questions

### Resolved During Planning

- **Q: 是否要先打 `DeprecationWarning` 一个 release？** A：不。原任务字面"删除"；
  仓库 release 节奏是单 PR 落地，没有 multi-release deprecation 习惯；测试套件
  做覆盖网
- **Q: `linkcheck/__init__.py` 的 wildcard 算不算 bridge？** A：不算。它通过真
  包 + `from .http import *` 工作，不走 `sys.meta_path`。out of scope
- **Q: `from backlink_publisher import content_fetch` 这种"顶层属性"形式怎么改？**
  A：改成 `from backlink_publisher.content import fetch as content_fetch` 或直接
  `from backlink_publisher.content.fetch import <names>`。bridge 之外这个形式无
  法工作
- **Q: `__init__.py` 精简后还要不要保留 `from __future__ import annotations`？**
  A：保留——本仓库所有 `.py` 都有；CI py_compile 不强制但是约定
- **Q: 要不要顺便加一个 import-lint test 防回归？** A：暂不。Unit 3 的 grep 验
  证写进 plan 的 "Verification" 字段作为 PR 自检列表已够；正式 lint test 等出
  现回归再加（YAGNI）

### Deferred to Implementation

- **每个 `from backlink_publisher import X` 形式要展开成 `from .... import` 还是
  `from .... import X as X`** —— 实施时按调用点上下文选最窄形式
- **`mock.patch("backlink_publisher.adapters.medium_browser.sync_playwright")` 改完之后是否
  和模块内部 `from playwright import sync_playwright` 的 `_playwright` 别名冲突** —— 实施
  时 `pytest tests/test_adapter_medium_browser.py -x` 立验
- **sed 脚本是否能 100% 一次过**：实施时先 `git grep ... | sed -n 'sample'` 跑
  几条 dry-run，再批量 apply

## Implementation Units

- [ ] **Unit 1: 迁移 `src/` + `webui_app/` + `webui.py` 的 legacy import**

**Goal:** 把生产代码（CLI + WebUI）里残留的 7 + 9 = 16 行 legacy import 改写到
新路径，使生产侧完全脱离 bridge。

**Requirements:** R2, R3

**Dependencies:** 无

**Files:**
- Modify (src/, 6 files):
  - `src/backlink_publisher/config/loader.py` — `errors` → `_util.errors`
  - `src/backlink_publisher/config/writer.py` — `errors` → `_util.errors`, `logger` → `_util.logger`
  - `src/backlink_publisher/cli/plan_backlinks/__init__.py` — `anchor_profile` / `anchor_resolver` / `work_scraper` → `anchor.profile` / `anchor.resolver` / `content.scraper`
  - `src/backlink_publisher/cli/plan_backlinks/_zh_short.py` — `markdown_utils` → `_util.markdown`
  - `src/backlink_publisher/cli/plan_backlinks/_work_themed.py` — `markdown_utils` / `work_scraper` / `work_themed_generator` → `_util.markdown` / `content.scraper` / `content.themed_gen`
  - `src/backlink_publisher/publishing/adapters/telegraph_node.py` — `markdown_utils` → `_util.markdown`
- Modify (webui, 6 files):
  - `webui_app/scheduler.py` — `logger` → `_util.logger`
  - `webui_app/helpers.py` — `logger` → `_util.logger`, `content_fetch` (顶层) → `content.fetch`
  - `webui_app/routes/pipeline.py` — `logger` → `_util.logger`
  - `webui_app/routes/sites.py` — `logger` → `_util.logger`
  - `webui_app/routes/oauth.py` — `adapters.blogger_api` → `publishing.adapters.blogger_api`（两处）
  - `webui_app/routes/url_verify.py` — `content_fetch` (顶层，行 178) → `content.fetch`
  - `webui.py` — `content_fetch` / `work_scraper` (顶层) → `content.fetch` / `content.scraper`

**Approach:**
- 对每个 site 按 `_REEXPORT_MAP` 表（`__init__.py:32-65`）做 1:1 替换
- 顶层 `from backlink_publisher import X` 形式：展开成 `from backlink_publisher.<new_full> import <symbol>` 或在 `as X` 保留别名（参考 Key Technical Decisions）
- bridge 仍然存在——Unit 1 完成后跑 pytest 应该全绿，因为生产代码自己已经独立，bridge 只服务测试和 webui 之外的边角

**Patterns to follow:**
- `webui_app/routes/oauth.py:120` 已有的 `from backlink_publisher.adapters.blogger_api import _SCOPES, json_from_creds` 写法（保留 _SCOPES 私有 import 这件事可以做，bridge 也允许 dotted-tail 转发）→ 改成 `publishing.adapters.blogger_api`
- 同包内部从来不走 bridge：例如 `src/backlink_publisher/config/loader.py` 改完之后是 `from backlink_publisher._util.errors import DependencyError`，和 `src/backlink_publisher/anchor/resolver.py` 等已有的"新路径"调用站点一致

**Test scenarios:**
- Baseline (BEFORE any edits): `PYTHONHASHSEED=0 pytest tests/ -rA --tb=no | tail -5` 记录 4 个数：passed / failed / xfailed / xpassed。写进 PR description 作为 Unit 2 / Unit 3 完成时的硬性回归门
- Integration: `pytest tests/test_plan_backlinks.py tests/test_config_three_url.py tests/test_webui_three_url.py -x` ——这些 test 集中覆盖 Unit 1 改的生产路径，确认导入改写后调用图无副作用
- Integration: WebUI 烟测——`BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/bp-bridge-test python webui.py` 起服务后 `curl :8888/` 200，确认 `webui_app/helpers.py` / `routes/*` 改写后 Flask app 可以正常 import + 启动（不要 POST 任何 `/save-*` 端点，参考 `feedback-never-smoke-test-real-save-endpoints`）

**Verification:**
- `git grep -nE "from backlink_publisher\.(anchor_lang|anchor_metrics|anchor_profile|anchor_resolver|anchor_scheduler|content_fetch|work_scraper|work_themed_generator|language_check|verify_publish|errors|io_utils|jsonl|logger|markdown_utils|url_utils|adapters)\b" src/ webui_app/ webui.py` 返回 0 行
- `git grep -nE "from backlink_publisher import (anchor_lang|anchor_metrics|anchor_profile|anchor_resolver|anchor_scheduler|content_fetch|work_scraper|work_themed_generator|language_check|verify_publish|errors|io_utils|jsonl|logger|markdown_utils|url_utils|adapters)\b" src/ webui_app/ webui.py` 返回 0 行
- `python -m py_compile src/backlink_publisher/**/*.py webui_app/**/*.py webui.py` 通过
- `pytest tests/` 整体仍然全绿（bridge 还在，测试不受影响）

---

- [ ] **Unit 2: 迁移 `tests/` 的 legacy import 与 `mock.patch` 字符串目标**

**Goal:** 把测试套件里 ~143 行 import 语句 + ~156 个 `mock.patch(...)` / `monkeypatch.setattr(...)` 字符串目标改写到新路径。完成后 bridge 在仓库范围内
没有任何剩余调用方。

**Requirements:** R2, R3, R3b, R4

**Dependencies:** Unit 1

**Files:**
- Modify (~46 files in `tests/`)：按 `_REEXPORT_MAP` 17 条映射机械重写。具体涉及的测试文件清单见 grep 结果（plan 不在此一一列出，因为是机械 sweep）：
  - 高密度文件：`tests/test_content_fetch.py`（21 处 import + 大量 patch）、`tests/test_adapter_medium_browser.py`（13 处 patch + 4 处 import）、`tests/test_anchor_resolver.py`、`tests/test_config_three_url.py`、`tests/test_publish_backlinks_*.py` 等
  - 包括 `tests/conftest.py:111` 的 `from backlink_publisher import content_fetch as _content_fetch`
- Test: 复用现有 test 套件本身——不新增 test 文件

**Approach:**
- 一份替换映射表（同 `_REEXPORT_MAP`），四种语法模式：
  1. `from backlink_publisher.<legacy_head>(\.<tail>)? import` → `from backlink_publisher.<new_head>(\.<tail>)? import`
  2. `from backlink_publisher import <legacy_head>(, <legacy_head2>)*` → 展开成多条 `from backlink_publisher.<new_full> import <legacy_head>` 形式（或 `as <legacy_head>` 别名保持局部 symbol 不变）
  3. `import backlink_publisher.<legacy_head>(\.<tail>)? as <alias>` → `import backlink_publisher.<new_head>(\.<tail>)? as <alias>`（bare-import 形式；已知 2 处，sed 必须显式覆盖）
  4. 字符串目标：`"backlink_publisher.<legacy_head>(\.<tail>)*"` → `"backlink_publisher.<new_head>(\.<tail>)*"`（覆盖 `mock.patch(...)`, `mock.patch.object(...)`, `monkeypatch.setattr(..., "...")`, importlib 字符串、所有以 `"backlink_publisher."` 起头的字面值；注意 17 条 head 都必须用 word-boundary 匹配避免误伤如 `errors_*` / `loggers`）
- sed 脚本生成模板（从 `_REEXPORT_MAP` 17 条机械生成）：
  ```bash
  # 对每条 "<legacy>": "<new>" 映射，生成 4 条 sed 命令：
  # 1. from-dotted:     s/from backlink_publisher\.<legacy>\b/from backlink_publisher.<new>/g
  # 2. from-toplevel:   s/from backlink_publisher import <legacy>\b/from backlink_publisher.<new> import <legacy>/g   (顶层导入展开 + 保留 symbol 名)
  # 3. bare-import:     s/^\(\s*\)import backlink_publisher\.<legacy>\b/\1import backlink_publisher.<new>/g
  # 4. string-target:   s/\(["'\'']\)backlink_publisher\.<legacy>\b/\1backlink_publisher.<new>/g
  # 示例（errors → _util.errors）：
  #   s/from backlink_publisher\.errors\b/from backlink_publisher._util.errors/g
  #   s/from backlink_publisher import errors\b/from backlink_publisher._util import errors/g
  #   s/^\(\s*\)import backlink_publisher\.errors\b/\1import backlink_publisher._util.errors/g
  #   s/\(["'\'']\)backlink_publisher\.errors\b/\1backlink_publisher._util.errors/g
  ```
- 跑序：
  1. 先做 import 改写（模式 1+2）—— `pytest --collect-only` 立刻验证语法
  2. 再做字符串目标改写（模式 3）—— `pytest tests/test_adapter_medium_browser.py -x` 等"patch 密集"用例先跑
  3. 最后跑全套
- **不依赖手工挑选**——脚本驱动批量替换 + grep 验证。任何 sed 没覆盖的边角，全套 pytest 必定暴露（要么 import error，要么 patch target 解析失败）

**Patterns to follow:**
- 测试文件里凡是 `from backlink_publisher.adapters.X import Y` 改成 `from backlink_publisher.publishing.adapters.X import Y`——`adapters.X` 是 `_REEXPORT_MAP["adapters"] = "publishing.adapters"`，dotted-tail 自动延展
- `from backlink_publisher import content_fetch` （`tests/test_work_scraper.py:19` 形态）改成 `from backlink_publisher.content import fetch as content_fetch`（或 `from backlink_publisher.content.fetch import <specific names>`，二选一按调用点决定）
- patch 形如 `@patch("backlink_publisher.content_fetch._SSRF_OPENER.open")` 改成 `@patch("backlink_publisher.content.fetch._SSRF_OPENER.open")`

**Test scenarios:**
- Integration: `pytest --collect-only tests/` —— 任何漏改的 import 在 collection 阶段直接报 `ModuleNotFoundError`，0 个 collection error 才能进下一步。注意 `--collect-only` **不能**验证 `mock.patch(...)` 字符串目标（这些 lazy 解析在测试执行时）—— 全套 pytest 才是最终校验
- Integration: `PYTHONHASHSEED=0 pytest tests/` —— 全套绿；xfail/xpass 计数与 Unit 1 baseline（见下）一致
- Edge case: `pytest tests/test_adapter_medium_browser.py tests/test_content_fetch.py tests/test_verify_publish.py -x` —— 这三个文件是 `mock.patch` 密度最高的，先单独通过
- Edge case: `pytest -m real_ssrf_check tests/test_content_fetch.py` 与 `pytest -m real_content_fetch tests/test_content_fetch.py` —— opt-in live 标记的子集也要照常工作（路径解析改了不影响 marker 选择）

**Verification:**
- `git grep -nE "from backlink_publisher\.(anchor_lang|anchor_metrics|anchor_profile|anchor_resolver|anchor_scheduler|content_fetch|work_scraper|work_themed_generator|language_check|verify_publish|errors|io_utils|jsonl|logger|markdown_utils|url_utils|adapters)\b" tests/` 返回 0
- `git grep -nE "from backlink_publisher import (anchor_lang|anchor_metrics|anchor_profile|anchor_resolver|anchor_scheduler|content_fetch|work_scraper|work_themed_generator|language_check|verify_publish|errors|io_utils|jsonl|logger|markdown_utils|url_utils|adapters)\b" tests/` 返回 0
- `git grep -nE "^\s*import backlink_publisher\.(anchor_lang|anchor_metrics|anchor_profile|anchor_resolver|anchor_scheduler|content_fetch|work_scraper|work_themed_generator|language_check|verify_publish|errors|io_utils|jsonl|logger|markdown_utils|url_utils|adapters)\b" tests/` 返回 0（bare-import 形式；R3b）
- `git grep -nE '["'"'"']backlink_publisher\.(anchor_lang|anchor_metrics|anchor_profile|anchor_resolver|anchor_scheduler|content_fetch|work_scraper|work_themed_generator|language_check|verify_publish|errors|io_utils|jsonl|logger|markdown_utils|url_utils|adapters)\b' tests/` 返回 0
- `PYTHONHASHSEED=0 pytest tests/` 全绿，xfail/xpass 计数严格等于 Unit 1 baseline 记录的 4 个数（不准更少 passed，也不准 xfailed 漂移）

---

- [ ] **Unit 3: 删除 `_LegacyPathFinder` + `_REEXPORT_MAP`，简化 `__init__.py`，更新文档**

**Goal:** 物理删除 bridge 代码并把 `__init__.py` 缩到最小内容；更新 AGENTS.md
的 Import Conventions 章节使其反映新现实（单一 canonical 路径，无 fallback）。

**Requirements:** R1, R6, R7

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `src/backlink_publisher/__init__.py` — 删除 `_REEXPORT_MAP` (行 ~32-65)、`_LegacyPathFinder` 类 (行 ~68-103)、finder 安装代码 (行 ~107-118)；保留 module docstring（改写说明 bridge 已退役）和 `from __future__ import annotations`
- Modify: `AGENTS.md` 第 77-89 行 "Import Conventions" 章节 — 删除 bridge / `_LegacyPathFinder` / `_REEXPORT_MAP` 描述；改写为"以下是 canonical 路径，旧的 flat path（如 `backlink_publisher.errors`）已退役，新代码用以下"，保留映射表的"新路径"列作为参考但删掉"旧路径"列
- Test: 无新 test 文件——R1 由 grep 检查，R6 由 py_compile 检查

**Approach:**
- 删完 bridge 后 `__init__.py` 大致变成：
  ```python
  """backlink-publisher root package.

  After Plan 2026-05-20-006, the legacy ``_LegacyPathFinder`` sys.meta_path
  shim was removed. All imports use the canonical refactored paths
  (``anchor.*``, ``content.*``, ``linkcheck.*``, ``_util.*``,
  ``publishing.adapters.*``).
  """
  from __future__ import annotations
  ```
- 不引入新的 package-level public API；如果将来需要 `__version__` 等再加
- AGENTS.md 章节改写要点：
  - 不要保留"两种路径都行"的混合表（防止重新引入 bridge 的诱惑）
  - 保留 5 个 subpackage 的列表与一行职责说明
  - 添加一行"任何 `from backlink_publisher.<flat_legacy_name>` 形式将直接报 ImportError"作为正向校准

**Patterns to follow:**
- 删除 `__init__.py` body 时保留 docstring 与 `__future__` import 的写法参考 `src/backlink_publisher/anchor/__init__.py`（虽然那里只剩 docstring，本根包再加 `__future__` 是为了仓库一致性）
- AGENTS.md 章节改写参考 `AGENTS.md` 其他 "post-2026-XX-XX" 历史 note 的语气

**Test scenarios:**
- Happy path: `python -c "import backlink_publisher; print(backlink_publisher.__doc__[:60])"` 不抛错
- Happy path: `python -c "from backlink_publisher._util.errors import DependencyError; print(DependencyError.__name__)"` 工作正常（新路径）
- Happy path: `python -c "from backlink_publisher import linkcheck; print(linkcheck._check_url_once.__name__)"` 工作正常（验证 `linkcheck/__init__.py` 的真包级 wildcard re-export 不受 bridge 删除影响，scope-boundary 闭环）
- Error path: `python -c "from backlink_publisher.errors import DependencyError"` 抛 `ModuleNotFoundError`（旧路径已死，验证 bridge 真删了）
- Error path: `python -c "import backlink_publisher.adapters"` 抛 `ModuleNotFoundError`（顶层 `adapters` 已不映射）
- Integration: `python -m py_compile src/backlink_publisher/**/*.py` 通过
- Integration: `PYTHONHASHSEED=0 pytest tests/` 全绿，xfail/xpass 计数严格等于 Unit 1 baseline 的 4 个数

**Verification:**
- `grep -nE "_LegacyPathFinder|_REEXPORT_MAP|sys\.meta_path" src/backlink_publisher/__init__.py` 返回 0
- `wc -l src/backlink_publisher/__init__.py` 约 ≤ 10
- `grep -nE "_LegacyPathFinder|_REEXPORT_MAP" AGENTS.md` 返回 0
- 上面 5 个 Test scenarios 全部按预期结果发生（包括两个故意 ModuleNotFoundError）

## System-Wide Impact

- **Interaction graph:** 影响仅限"import 解析"。`sys.meta_path` 上移除一个 finder
  后，所有 `backlink_publisher.*` 导入直接走 Python 默认 finder 链——更快，但
  没有可观察的行为差异。对 CLI 入口（`plan-backlinks` 等 6 个 entrypoint）、
  WebUI Flask app、`pytest` collection、外部脚本（不可见但只能用 canonical
  path 调用）均无运行时副作用
- **Error propagation:** 任何漏迁的 site 在 import / collection 阶段抛
  `ModuleNotFoundError`——这是好事，比 runtime 隐性失败安全
- **State lifecycle risks:** 无。bridge 是 import 时机制，与运行时状态正交
- **API surface parity:** **外部脚本与下游消费者无契约保障**——`backlink_publisher`
  没有公开 wheel / PyPI 发行，但任何把这个仓库作为 submodule 或 `pip install -e .`
  使用、并写了 `from backlink_publisher.errors import ...` 风格代码的下游会断
  掉。本仓库目前没有 wheel 发布、AGENTS.md 也只对内宣传 canonical 路径，故评估
  影响域 = 0；但 PR 描述要点名"breaking import path change for any external consumer
  still on flat paths"
- **Integration coverage:** 测试套件覆盖 collection + 运行时；WebUI smoke
  （Unit 1）覆盖 Flask app 启动；`python -m py_compile`（CI 已有）覆盖 src
- **Unchanged invariants:**
  - 5 个 subpackage 的公共 API 不动（`anchor.*`, `content.*`, `linkcheck.*`,
    `_util.*`, `publishing.adapters.*`）
  - monolith budget 不动（`__init__.py` 不在被监控的 6 文件清单）
  - 测试 fixtures（4 个 autouse conftest fixture）不动
  - `linkcheck/__init__.py` 的 `from .http import *` wildcard 不动（out of scope）
  - CI workflow / `pyproject.toml` entry points 不动

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| sed 替换 17 条 head 时误伤同名子串（如 `errors` 误伤 `loggers`、`adapters` 误伤 `adapter_state`） | 替换规则全部用 `\b<head>\b` word-boundary；且只匹配 `backlink_publisher\.<head>` 前缀；每条替换跑 dry-run grep diff 抽检前 |
| 漏掉某个 `from backlink_publisher import X` 顶层属性形式 | Unit 2 验证 grep 模式 R3 显式覆盖该形式；`pytest --collect-only` 抓所有 import-time 失败 |
| 漏掉某个动态 `importlib.import_module("backlink_publisher.errors")` 或字符串拼接的导入 | Unit 2 验证 grep 模式 R4 覆盖所有 `["']backlink_publisher\.<legacy>` 字面字符串；运行时漏掉的会在对应 test 跑到时炸（已知运行点：`mock.patch` 的 ~156 处） |
| 并发 worktree（`bp-*/` >15 个）里某个 agent 正在新增 legacy import，本 PR 落地后那个 worktree 编不过 | 落地前 `git worktree list` + 各 worktree `git log --oneline -5` 看有没有新加 legacy import 的 commit；如有则先通知 / 等其落地再 rebase；本 plan 不并发其他人正在做的 import 改造 |
| `mock.patch` 字符串目标改写后没有等价语义（如改写后 patch 的对象实际不是同一个） | bridge 在的时候 `mock.patch("backlink_publisher.adapters.X.Y")` 实际定位到 `publishing.adapters.X.Y` 同一对象（bridge `create_module` 做了 `sys.modules[fullname] = module` 同一性保留）；改写后 patch 直接定位到 `publishing.adapters.X.Y`——同一个对象。语义等价 |
| `xfail` 计数漂移导致看不出真回归 | Unit 1 跑 baseline 前用 `pytest tests/ -rA` 记录 passed/failed/xfailed/xpassed 四个数，Unit 3 完成后必须一致 |

## Documentation / Operational Notes

- AGENTS.md "Import Conventions" 章节按 Unit 3 改写
- README.md 没有直接讨论 import 路径，无需改
- `docs/plans/2026-05-18-001-refactor-architecture-health-roadmap-plan.md` 历史
  记录不动（plan 是历史，不追溯改写）
- 无 migration runbook 需要——纯库内重构，无生产数据 / 用户配置 / 部署影响

## Sources & References

- Bridge 实现：`src/backlink_publisher/__init__.py:1-118`
- Bridge 设计源 plan：`docs/plans/2026-05-18-001-refactor-architecture-health-roadmap-plan.md:228+`（Unit 6 设计示意）
- 当前 Import Conventions 文档：`AGENTS.md:77-89`
- 17 条映射表：`src/backlink_publisher/__init__.py:32-65`（即 `_REEXPORT_MAP`）
