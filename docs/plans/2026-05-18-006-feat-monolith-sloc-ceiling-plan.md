---
title: "feat: Add monolith SLOC ceiling structural test"
type: feat
status: completed
date: 2026-05-18
origin: docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md
deepened: 2026-05-18
completed: 2026-05-18
---

# feat: Add Monolith SLOC Ceiling Structural Test (R5 F7)

## Overview

Add a pytest assertion that fails CI when any of 5 named monolith source files exceeds its budgeted radon SLOC ceiling, plus a warning-only canary for files outside the named set growing past 500 SLOC, plus a hand-counted SLOC fixture pinning radon's counter behavior to a tested invariant. Captures monolith regrowth in git history with attached rationale. Lays the structural foundation that makes the planned surgical extractions F2 / F3 / F5 durable beyond 2 sprints.

## Problem Frame

Mass-concentration is the project's recurring failure mode. PRs #20/#25/#26/#27/#28 stacked 5 features onto the flat `content_fetch.py` over 7 days. PR #50 (`aa41731`) subsequently moved 16 flat modules into domain subpackages, including the relocation of `content_fetch.py` → `content/fetch.py`. PR #48 (`c23d43c`) split `config.py` into the `config/` subpackage. **The relocations did not shrink the files; they changed the import paths.** The growth pattern is invariant under refactoring — without a ratchet, the velocity that produced the monoliths will reabsorb the next surgical extraction's wins.

R5 ideation produced three surgical extraction candidates that would shrink current monoliths — F2 (`ErrorClass` oracle), F3 (`safe_write` carve from `config/writer.py`), F5 (`ThrottleClock`). Without F7, those extractions evaporate within 2 sprints.

This plan installs the budget-with-rationale-journal: hard-fail when SLOC > ceiling, warning canary for undeclared files > 500 SLOC, plus a hand-counted SLOC canary fixture pinning radon's counter behavior. The system is honestly framed as a journal, not a tamper-proof gate — a solo developer can rubber-stamp ceilings, but every bump is captured in `git blame` with a non-trivial `rationale` field.

This plan **does NOT decompose any monolith**. F3 (`safe_write` carve from `config/writer.py`) is a separate plan that will land later.

## Requirements Trace

Carrying forward from origin doc (see origin: `docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md`):

- **R1** — radon SLOC as per-file metric (Units 2, 3)
- **R2** — 5 named monitored files **post-PR48/PR50** (Units 2, 3)
- **R3** — `monolith_budget.toml` with 2-field schema `(ceiling, rationale)` (Units 2, 3)
- **R4** — pytest hard-fail when SLOC > ceiling (Unit 3)
- **R5** — pytest hard-fail on missing/malformed budget file or rationale < 80 chars (Unit 3)
- **R6** — radon pinned to exact version + bump-radon-edit-budget rule (Unit 1, Unit 5)
- **R7** — warning-only auto-discovery canary at 500 SLOC for files not in budget (Unit 3)
- **R8** — same-PR bump policy (Unit 5 documentation)
- **R9** — journal-not-gate framing in failure messages and docs (Units 3, 5)
- **R10** — F7 explicitly NOT config decomposition (Unit 5 documentation)
- **R11** — initial ceilings = round_up_to_10(current_SLOC + 30) (Unit 2)
- **R12** — initial rationale fields explain expected settling shape (Unit 2)

Success criteria carried forward from origin (paths updated):
- After 2 sprints, no monitored file has crossed its ceiling without a visible `monolith_budget.toml` edit in `git log`.
- After F3 lands, `config/writer.py`'s ceiling reflects the post-carve shape (review-time, not CI-enforced).
- The 500-SLOC canary fires at least once when a new file in `src/` crosses the threshold without being added to budget.
- F7 assertions add < 200ms to existing `tests/` suite runtime.

## Scope Boundaries

**In scope (this plan):** the 5 named files (post-PR48/PR50 paths); the 2-field visible `monolith_budget.toml`; the pytest assertion in `tests/test_no_monolith_regrowth.py`; the 500-SLOC warning canary; the SLOC canary fixture pinning radon behavior; pinned radon dev dep + `radon --version` CI print; AGENTS.md note + branch-protection recommendation.

**Out of scope, deferred to v2:** function-level SLOC ceilings; cyclomatic complexity; import fan-in/fan-out; per-directory aggregate budgets; webui_app/ submodule-level budgets; promoting the warning canary to hard-fail; automatic monitored-set discovery.

**Out of scope, never:** override labels; warning-only mode for the primary R4 rule; LLM-assisted rationale; per-file SLOC ratios; F7 doing decomposition work itself.

## Context & Research

### Relevant Code and Patterns

- `tests/test_config_safety_net.py:14` — `import tomllib` precedent (line corrected per feasibility review).
- `tests/test_config_roundtrip.py:24-31` — repo-root resolution via `Path(__file__).resolve().parents[1]`. F7 test mirrors this.
- `tests/conftest.py` — autouse fixtures (`_isolate_user_dirs`, `_mock_publish_check_url`, `_mock_content_fetch`, `_disable_real_network`). All inert for F7 (no config IO, no network).
- `pyproject.toml:23-24` — `[project.optional-dependencies].dev` block; house convention is `>=` floor pins.
- `pyproject.toml:9` — `requires-python = ">=3.11"` guarantees `tomllib` stdlib.
- `.github/workflows/ci.yml:3-7` — CI triggers on `push: branches: [main, develop]` **AND** `pull_request: branches: [main]`. Post-merge safety-net lane is **already wired** — no workflow trigger change needed.
- `.github/workflows/ci.yml:25-28` — `Install dependencies` step (`pip install -e ".[dev]"` + `pytest-cov`).
- `.github/workflows/ci.yml:30-32` — `Run tests` step (`python -m pytest tests/ -v --tb=short --timeout=30`).
- `.github/workflows/ci.yml:34-45` — `Check code style` (py_compile + ast.parse) runs **after** pytest. F7 fails before this step on a syntax-error file.
- **Current top-5 by physical LOC (verified 2026-05-18 against `feat/event-substrate-u3` HEAD):**
  - `cli/plan_backlinks.py` — 1744
  - `cli/publish_backlinks.py` — 840
  - `content/fetch.py` — 657
  - `config/writer.py` — 498
  - `_util/markdown.py` — 466
- No top-level `.gitignore`; new repo-root files tracked by default.
- Root-TOML precedent: `pyproject.toml` and `config.example.toml` (both visible). New `monolith_budget.toml` matches the visible convention.
- `AGENTS.md` exists; no test-writing conventions documented (no constraint to mirror).

### Institutional Learnings

- `docs/solutions/test-failures/tests-coupled-to-operator-config-state-2026-05-18.md` — Tests must resolve repo paths explicitly (not via cwd). F7 uses `Path(__file__).resolve().parents[1]`.
- `docs/solutions/test-failures/negative-assertion-locks-in-bug-2026-05-15.md` — Per-file enumerated assertions with explicit filename + actual + ceiling in the failure message. F7 parametrizes over budget-file keys (NOT a hardcoded tuple — see Key Decisions) and reports per-path failures.
- `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md` — Dep additions affecting pytest must land in `pyproject.toml` *before* the test that imports them. Unit 1 precedes Unit 3 in the seed PR.
- MEMORY `reference_ci_workflow_pr_filter.md` — Existing `push: branches: [main]` lane confirmed.
- MEMORY `feedback_verify_repo_state_before_planning.md` — **Surfaced by this plan's own review**: R5 ideation grounded against a pre-PR48/PR50 file layout (flat `config.py`, `content_fetch.py`, `markdown_utils.py`). Document-review's feasibility agent caught the path-drift before implementation; the plan was rewritten in place. Lesson codified for future plans: paths cited in a plan must be re-verified against `git HEAD` at plan-write-time, not inherited from upstream brainstorm/ideation grounding which may have run hours-to-days earlier.

### External References

None used directly. radon, pytest, tomllib are mature standard tools. The empirical question of "does radon's SLOC counter actually behave as documented across our codebase's actual constructs (walrus, match, TYPE_CHECKING, multi-line strings, type aliases, f-strings)" is addressed by the new SLOC canary fixture in Unit 3 — earlier Phase 1 verification only probed docstring handling, leaving other AST shapes untested.

## Key Technical Decisions

- **Use radon SLOC, not LLOC.** Empirical verification (radon 6.0.1) showed LLOC counts docstrings as logical lines, contradicting origin R1's stated intent. SLOC excludes blank lines, comments, and multi-line strings/docstrings.

- **Pin radon to exact version (`==`).** Per origin R6 (user-confirmed during brainstorm P1 picks). Defense in depth pairs with the new SLOC canary fixture (Unit 3): the exact pin protects against `pip install` resolution drift; the canary fixture protects against undocumented counter changes within or across versions. The fixture also catches the R1-LLOC-class of empirical surprise on constructs not yet probed (walrus operators, match statements, TYPE_CHECKING blocks, etc.).

- **Visible filename `monolith_budget.toml` (no leading dot).** Three separate reviewer findings across brainstorm and plan rounds flagged the dotfile choice. The file is contributor-edited on every ceiling bump; hiding it in editor file trees creates friction for the workflow the file exists to serve. Visible naming matches `pyproject.toml` / `config.example.toml` repo-root precedent.

- **Parametrize over budget-file keys (NOT a hardcoded test-side tuple).** Per scope-guardian + adversarial consensus. The test loads `tomllib.load(budget_path)['files']` at module-collection time and parametrizes per-key. Removes the synthetic drift category between test code and budget file — the budget file is the single source of truth for monitored paths.

- **Hand-counted SLOC canary fixture pins radon's counter behavior.** Per adversarial ADV-008 (highest-leverage single fix). `tests/fixtures/sloc_canary.py` contains representative constructs (multi-line string, docstring, walrus, match, f-string, conditional `TYPE_CHECKING` import) with hand-counted expected SLOC documented inline. A test asserts `radon.raw.analyze(fixture_text).sloc == EXPECTED`. Catches radon counter drift and validates SLOC behavior on the *constructs the monitored files actually use*.

- **Per-file enumerated failure messages.** Format: `"<path>: SLOC=<actual> exceeds ceiling=<budgeted> by <delta>. Rationale: '<rationale>'. To resolve: lower ceiling in monolith_budget.toml (with updated rationale) or extract code from this file."`

- **No fallback for SyntaxError or FileNotFoundError on monitored files.** The test fails loudly with a path-named, action-suggesting message.

- **CI safety-net is post-merge, not pre-merge.** The existing `push: branches: [main]` lane catches budget violations after they land. Pre-merge protection requires GitHub branch protection "Require branches up to date before merging" — documented as a recommendation in AGENTS.md, NOT applied from this PR (per origin scope: solo-operator config, not code-shipping).

- **Warning-only canary uses `warnings.warn(UserWarning)`, NOT `pytest.warns(...)` around the real-tree scan.** `pytest.warns` as a context manager fails with `DID NOT WARN` when no matching warning fires — wrapping the real-tree scan would make CI red in the steady state (no undeclared >500-SLOC files) which defeats R7's warning-only intent. The implementation calls `warnings.warn` directly; pytest's default warning summary surfaces it.

## Open Questions

### Resolved During Planning

- **OQ-1 SyntaxError handling**: catch radon's parse exception per-file, fail with `"Monitored file <path> contains a syntax error — radon cannot parse it. The repo's CI py_compile sweep will surface the underlying error. Fix the syntax error and re-run."` No fallback.
- **OQ-2 FileNotFoundError on monitored path**: catch `FileNotFoundError` / `IsADirectoryError` / `PermissionError` per-file, fail with `"Monitored file <path> in monolith_budget.toml is not readable (<reason>). Update monolith_budget.toml (delete the entry, or fix the path if renamed) or restore the file."`
- **OQ-3 Exact initial ceilings**: Unit 2 implementer runs `python -m radon raw -s <5 paths>` at seed PR HEAD and sets `ceiling = ((current_SLOC + 30 + 9) // 10) * 10` (round-up-to-10 of current_SLOC + 30; `+9` makes integer floor-div behave as ceiling-div for nearest-10).
- **OQ-4 `radon --version` print in CI**: Yes — add one step between `Install dependencies` (ending ci.yml:28) and `Run tests` (starting ci.yml:30).
- **OQ-5 GitHub branch protection**: Existing `push: branches: [main]` lane is the post-merge safety net. Pre-merge "Require branches up to date" is recommended (documented in AGENTS.md by Unit 5) but **not applied from this PR**.

### Deferred to Implementation

- Exact pinned radon version (6.0.1 verified working at brainstorm review; verify current stable at Unit 1 write-time).
- Final wording of AGENTS.md addition (Unit 5; match house tone).
- SLOC canary fixture content — Unit 3 implementer selects which constructs to include (must cover at minimum: bare assignment, function def with docstring, class def with docstring, multi-line string, list/dict comprehension, conditional `TYPE_CHECKING` import).
- Local runtime profiling via `pytest --durations=5` to confirm < 200ms target.

## Implementation Units

- [x] **Unit 1: Pin `radon` as an exact-version dev dependency**

**Goal:** Make `radon` importable in pytest with deterministic SLOC output across the Python 3.11 / 3.12 matrix.

**Requirements:** R6.

**Dependencies:** None.

**Files:**
- Modify: `pyproject.toml` (the `[project.optional-dependencies].dev` list at line 23-24)

**Approach:**
- Append `"radon==6.0.1"` (or current verified-stable) to the existing dev list.
- Add an inline comment above the dev list: `# radon is pinned to exact version because SLOC counts must be deterministic across CI matrix rows; bumping radon requires re-measuring all 5 ceilings in the same PR. See docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md`
- No other pyproject.toml changes.

**Patterns to follow:** Existing `[project.optional-dependencies].dev` block at `pyproject.toml:23-24`.

**Test scenarios:** Test expectation: none — pure dependency declaration; behavioral verification covered by Unit 3's import.

**Verification:**
- `pip install -e ".[dev]"` succeeds on Python 3.11 AND 3.12.
- `python -c "from radon.raw import analyze; print(analyze('x=1').sloc)"` prints `1`.
- `python -m radon --version` returns the pinned version.

---

- [x] **Unit 2: Seed `monolith_budget.toml` at repo root (visible filename, post-PR48/PR50 paths)**

**Goal:** Create the canonical budget file with measured initial entries for all 5 monitored paths at current main HEAD.

**Requirements:** R2, R3, R10, R11, R12.

**Dependencies:** Unit 1 (radon must be installed so the implementer can measure SLOC).

**Files:**
- Create: `monolith_budget.toml` (repo root, visible filename per Key Decisions)

**Approach:**
- Run `python -m radon raw -s src/backlink_publisher/cli/plan_backlinks.py src/backlink_publisher/cli/publish_backlinks.py src/backlink_publisher/content/fetch.py src/backlink_publisher/config/writer.py src/backlink_publisher/_util/markdown.py` at the seed PR's HEAD and read each file's `SLOC` value.
- For each file: `ceiling = ((current_SLOC + 30 + 9) // 10) * 10`.
- TOML structure (one `[files."<path>"]` table per monitored path):

```toml
# Monolith SLOC budget. See docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md
# and docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md.
# Schema: each entry must have integer `ceiling` (radon SLOC) and string `rationale`
# (>=80 chars). Enforced by tests/test_no_monolith_regrowth.py.

[files."src/backlink_publisher/cli/plan_backlinks.py"]
ceiling = <measured>
rationale = "Largest file in the repo (~1744 physical LOC at write-time). Generation orchestrator combining anchor handling, content-fetch gate wiring, work_themed callout. P1-queued decomposition will surface seams; lower ceiling in those landing PRs."

[files."src/backlink_publisher/cli/publish_backlinks.py"]
ceiling = <measured>
rationale = "Pipeline stage 3 — OAuth pre-flight + retry_transient + Medium throttle (3 inlined copies pending F5 ThrottleClock extraction) + real-publish verification + link_attr_verifier converge here. F5 carve will drop ~60 SLOC; lower ceiling then."

[files."src/backlink_publisher/content/fetch.py"]
ceiling = <measured>
rationale = "Grew rapidly via PRs #20/#25/#26/#27/#28 (cache TTL + stats + SSRF + soft-404 + prefetch) when still flat. Relocated by PR #50. Future SSRF-guard extraction (when 3rd caller emerges) may shrink; lower ceiling at that point."

[files."src/backlink_publisher/config/writer.py"]
ceiling = <measured>
rationale = "Largest piece of the post-PR48 config-subpackage split. Holds atomic-write + snapshot + section-quarantine. R5 F3 safe_write carve will lift atomic-write + snapshot into a shared persistence module consumed by JsonStore + events/store; lower ceiling in that landing PR."

[files."src/backlink_publisher/_util/markdown.py"]
ceiling = <measured>
rationale = "Markdown rendering helpers (_safe_anchor, link template). Stable shape; no carve planned. Ceiling is held against accumulated render-time helper accretion."
```

- Filename is `monolith_budget.toml` (visible, no leading dot).

**Patterns to follow:** pyproject.toml's nested-table TOML syntax. Origin R12 expected-settle template for rationales.

**Test scenarios:** Test expectation: none — pure config file. Unit 3's tests verify schema validity and SLOC bounds.

**Verification:**
- `python -c "import tomllib; d = tomllib.load(open('monolith_budget.toml','rb')); assert set(d['files'].keys()) == {<5 paths>}"` succeeds.
- For each entry: `ceiling` is `int` AND `len(rationale) >= 80`.
- Dry-run Unit 3's test against this seed: all 5 SLOC checks pass; policy-to-seed drift test passes (`0 <= ceiling - current_SLOC <= 50`).

---

- [x] **Unit 3: `tests/test_no_monolith_regrowth.py` — primary enforcement test + SLOC canary fixture**

**Goal:** The pytest assertion enforcing R4 (hard-fail on ceiling exceedance), R5 (schema validation), R7 (warning canary), AND the radon-behavior canary (pins SLOC counter to a tested invariant).

**Requirements:** R1, R2, R3, R4, R5, R7, R9.

**Dependencies:** Unit 1 (radon dev dep), Unit 2 (budget file exists).

**Files:**
- Create: `tests/test_no_monolith_regrowth.py`
- Create: `tests/fixtures/sloc_canary.py`

**Approach:**

*Repo-root resolution:* `Path(__file__).resolve().parents[1]`, mirroring `tests/test_config_roundtrip.py:24-31`.

*Budget loading:* stdlib `tomllib`. Treat missing file or malformed TOML as a single explicit `pytest.fail("monolith_budget.toml not found at <repo-root>" | "malformed: <parse error>")`. **No hardcoded `MONITORED_PATHS` tuple in test code** — the budget file is the source of truth; the test parametrizes over `data['files'].keys()` collected at module import time.

*Schema validation* (parametrized over budget keys): each entry must have `ceiling: int` and `rationale: str with len(rationale) >= 80`. Failures explicit-per-entry.

*SLOC check* (parametrized over budget keys):
- `try` `radon.raw.analyze(file_text)`; catch parse exceptions / `SyntaxError` → fail with OQ-1 message.
- Catch `FileNotFoundError` / `IsADirectoryError` / `PermissionError` → fail with OQ-2 message.
- On success: `assert sloc <= ceiling` with explicit per-path message.

*Policy-to-seed drift test* (parametrized): `assert 0 <= (ceiling - current_SLOC) <= 50` per entry. Catches the typo-class error of mis-set ceilings (e.g., +300 instead of +30) that would silently grant 300 SLOC of free headroom on day 1.

*Synthetic assertion-fires test* (not parametrized; replaces the prior "force-grow runbook"): writes a temp source file with N SLOC to `tmp_path`, writes a temp `monolith_budget.toml` to `tmp_path` with ceiling = N - 50, calls a small `assert_files_within_budget(budget_path, root)` helper extracted from the main test, and asserts via `pytest.raises(AssertionError, match=r"exceeds ceiling")` that the assertion fires with the expected message format. **Requires extracting the SLOC-check logic into a function that takes paths as parameters**, so test fixtures can inject synthetic paths.

*Warning canary* (separate test function `test_warning_canary_for_undeclared_large_files`):
- Walk `repo_root / 'src' / 'backlink_publisher'` recursively for `*.py` via `Path.rglob`.
- For each `*.py` not in `data['files']` keys, measure SLOC. If `sloc > 500`, emit `warnings.warn(f"Undeclared monolith candidate: {path} has SLOC={sloc} (>500). Consider adding to monolith_budget.toml or extracting.", UserWarning)`.
- **The test calls `warnings.warn` directly. It does NOT wrap the scan in `pytest.warns(...)`.** `pytest.warns` as a context manager fails with `DID NOT WARN` when no matching warning fires — wrapping the steady-state real-tree scan (no undeclared >500-SLOC files) would make CI red on every run. Visibility comes from pytest's default warning summary.
- A *separate* synthetic-tmp-path test (`test_warning_canary_fires_for_synthetic_large_file`) builds a fake src tree with one 600-SLOC `.py` file under `tmp_path`, calls the canary scan function pointed at that root, and uses `pytest.warns(UserWarning, match=r"Undeclared monolith candidate")` correctly (warning IS guaranteed to fire there).

*SLOC behavior canary* (`test_radon_sloc_behavior_pinned`):
- `tests/fixtures/sloc_canary.py` is a hand-crafted Python file containing one of each construct the monitored files use: bare assignment; function def with docstring; class def with docstring; multi-line string assigned to variable; list/dict comprehension; walrus operator inside if; match statement; `if TYPE_CHECKING:` conditional import; f-string with embedded expression; type alias (PEP 695 if 3.12 only, else regular).
- Top-of-fixture comment records the hand-counted expected SLOC value (e.g., `# EXPECTED_SLOC = 27`).
- Test reads the fixture file, asserts `radon.raw.analyze(text).sloc == EXPECTED_SLOC`.
- Failure mode: radon's counter changed (new version, new Python AST shape) — fixes the seed PR's blind-spot in the same shape the brainstorm caught the LLOC/docstring error.

**Patterns to follow:**
- `tests/test_config_safety_net.py:14` — `import tomllib`.
- `tests/test_config_roundtrip.py:24-31` — repo-root resolution.
- pytest `parametrize` for per-file enumeration (matches `negative-assertion-locks-in-bug` learning).
- pytest `tmp_path` fixture for synthetic-tree tests.

**Technical design:** *Directional sketch — not implementation specification.*

```
# Constants
REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGET_FILE = REPO_ROOT / "monolith_budget.toml"
RATIONALE_MIN_CHARS = 80
SEED_HEADROOM_MAX = 50   # used by policy-to-seed drift test
WARNING_CANARY_SLOC_THRESHOLD = 500  # used by R7 canary
SLOC_CANARY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sloc_canary.py"
SLOC_CANARY_EXPECTED = <hand-counted>  # Unit 3 implementer sets after constructing fixture

# Collection-time load (pytest discovers parametrize values)
BUDGET = tomllib.loads(BUDGET_FILE.read_text())

@pytest.mark.parametrize("path", list(BUDGET["files"].keys()))
def test_entry_schema(path): ...

@pytest.mark.parametrize("path", list(BUDGET["files"].keys()))
def test_sloc_within_ceiling(path): ...

@pytest.mark.parametrize("path", list(BUDGET["files"].keys()))
def test_policy_to_seed_drift(path): ...

def test_warning_canary_for_undeclared_large_files(): ...          # warnings.warn, no pytest.warns
def test_warning_canary_fires_for_synthetic_large_file(tmp_path): ...   # uses pytest.warns correctly
def test_assertion_fires_when_synthetic_ceiling_exceeded(tmp_path): ... # uses pytest.raises
def test_radon_sloc_behavior_pinned(): ...                          # the SLOC canary fixture check
```

**Test scenarios:**
- **Happy path**: All 5 budget entries SLOC ≤ ceiling → `test_sloc_within_ceiling` green for all parametrized cases.
- **Happy path**: All 5 rationales ≥80 chars → `test_entry_schema` green.
- **Happy path**: Seed headroom within bounds → `test_policy_to_seed_drift` green.
- **Happy path**: SLOC canary fixture matches expected → `test_radon_sloc_behavior_pinned` green.
- **Edge case**: SLOC == ceiling exactly → `<=` passes.
- **Edge case**: Rationale exactly 80 chars → passes; 79 → fails per-path.
- **Edge case**: `ceiling - current_SLOC == 50` exactly → policy-to-seed drift passes; == 51 → fails per-path with "headroom 51 exceeds policy maximum 50."
- **Error path**: A monitored file overgrown by 50 SLOC → `test_sloc_within_ceiling` for that path fails with explicit message; other 4 cases green.
- **Error path**: `monolith_budget.toml` missing → module-level fail at collection time with explicit message.
- **Error path**: Budget file malformed TOML → module-level fail at collection time, tomllib error wrapped with the path.
- **Error path**: One entry missing `ceiling` → `test_entry_schema` for that path fails with `"Entry '<path>' missing required field 'ceiling'"`.
- **Error path**: Rationale 50 chars → `test_entry_schema` fails with `"Entry '<path>' rationale length 50 < 80 minimum"`.
- **Error path**: A monitored file is deleted/renamed without budget update → `test_sloc_within_ceiling` for that path fails with OQ-2 message.
- **Error path**: A monitored file has a syntax error → `test_sloc_within_ceiling` for that path fails with OQ-1 message.
- **Error path**: A future radon upgrade shifts SLOC counter behavior → `test_radon_sloc_behavior_pinned` fails with `"SLOC fixture expected=<X> but radon returned=<Y>; counter behavior changed (new radon version? new Python minor? re-baseline all 5 monitored ceilings)"`. R6's bump-radon-edits-budget rule activates.
- **Integration (synthetic-tree)**: `test_warning_canary_fires_for_synthetic_large_file` builds `tmp_path/src/big.py` with 600 SLOC, calls the canary function with `tmp_path/src` as root, asserts `pytest.warns(UserWarning, match=...)` fires.
- **Integration (synthetic-budget)**: `test_assertion_fires_when_synthetic_ceiling_exceeded` writes `tmp_path/source.py` with N SLOC and a `tmp_path/budget.toml` with ceiling N-50, asserts `pytest.raises(AssertionError, match=r"exceeds ceiling")` fires.
- **Integration (real-tree, informational)**: `test_warning_canary_for_undeclared_large_files` runs over the real `src/backlink_publisher/`. Calls `warnings.warn(...)` for any undeclared >500-SLOC file. **Asserts nothing**; visibility comes from pytest's default warning summary.

**Verification:**
- Seed PR after Units 1+2+3: all parametrized cases green, schema check green, SLOC canary fixture green, no spurious warning failures, real-tree canary either silent or emits informational warnings.
- The synthetic-budget and synthetic-tree tests are the real red-path verification — they prove the assertion and the canary actually fire (replacing the prior plan's "force-grow content_fetch.py manually" runbook step).
- `pytest tests/test_no_monolith_regrowth.py --durations=5` shows the total contribution < 200ms.

---

- [x] **Unit 4: CI ergonomics — print radon version**

**Goal:** Make radon version visible in CI logs.

**Requirements:** R6.

**Dependencies:** Unit 1 (radon installed).

**Files:**
- Modify: `.github/workflows/ci.yml`

**Approach:**
- Insert a new step in the existing `test` job, **between** the `Install dependencies` step (ending around `ci.yml:28`) and the `Run tests` step (starting around `ci.yml:30`):

```yaml
- name: Print radon version
  run: python -m radon --version
```

- Do NOT modify `on:` triggers (already correct), matrix, or any other step.

**Patterns to follow:** Existing step structure in `.github/workflows/ci.yml`.

**Test scenarios:** Test expectation: none — CI config change; CI itself is the verification.

**Verification:**
- A no-op commit triggers CI; the new step prints `Radon <version>` on both Python 3.11 and 3.12 matrix rows.
- The step runs BEFORE pytest, so an uninstalled radon fails here rather than during pytest collection.

---

- [x] **Unit 5: AGENTS.md note + branch-protection recommendation**

**Goal:** Document the monolith-budget convention and the recommended GitHub branch-protection setting.

**Requirements:** R8, R9, R10, plus origin Documentation/Operational Notes.

**Dependencies:** Units 2 + 3 (the convention must exist).

**Files:**
- Modify: `AGENTS.md`

**Approach:** Add a new section `## Monolith Budget` near the end of `AGENTS.md` (~12-18 lines):
- One sentence: what `monolith_budget.toml` is and which 5 files it monitors.
- When to edit: "If your PR would push a monitored file's radon SLOC past its `ceiling`, the test fails. Edit `monolith_budget.toml` in the same PR to raise the ceiling (or extract code to shrink the file)."
- Rationale field expectation: "1-2 sentences naming the code growth that motivated the bump and the shape this file is expected to settle to over the next few sprints."
- Journal framing (origin R9): "This is a journal, not a tamper-proof gate. A solo developer can rubber-stamp any bump. The defense is `git blame` on `monolith_budget.toml` — every intentional bump leaves a reviewable record."
- Explicit (origin R10): "F7 does not decompose anything. The surgical extraction plans (F2, F3, F5) are separate work."
- Operator recommendation: "Branch protection on `main`: enable 'Require branches to be up to date before merging' to protect against the two-concurrent-PRs-bumping-the-same-file race. The existing `push: branches: [main]` CI lane catches violations post-merge regardless, but pre-merge prevention is cheaper."
- Cross-references: `docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md`, this plan.

**Patterns to follow:** Existing AGENTS.md tone (lessons-capture, terse, action-oriented).

**Test scenarios:** Test expectation: none — documentation.

**Verification:**
- `AGENTS.md` renders cleanly.
- The convention described matches Unit 3's actual behavior (5 files, 80-char rationale, visible filename).

## System-Wide Impact

- **Interaction graph:** F7 runs inside the existing `python -m pytest tests/` invocation. No interaction with any runtime path. Conftest autouse fixtures are inert (F7 does no config IO and no network).
- **Error propagation:** A failing F7 short-circuits CI before the `Check code style` step (ci.yml:34-45). The OQ-1 message redirects to that step's later output if the failure was a syntax error.
- **State lifecycle:** None. F7 reads tracked repo files via stdlib `tomllib` and radon; no caching, no disk writes, no global state.
- **API surface parity:** None.
- **Integration coverage:** The synthetic-budget test (`test_assertion_fires_when_synthetic_ceiling_exceeded`) and synthetic-tree test (`test_warning_canary_fires_for_synthetic_large_file`) prove the assertion and canary fire on real inputs. The SLOC canary fixture (`test_radon_sloc_behavior_pinned`) proves radon's counter is stable. No human-discipline runbook step required.
- **Unchanged invariants:** All existing tests pass identically. CI matrix unchanged. Runtime application behavior unchanged. No new public configuration surface; `monolith_budget.toml` is contributor-only.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `radon==6.0.1` pin breaks pip resolver on Python 3.11 or 3.12 (PyPI metadata silent on 3.11/3.12 classifiers — install empirically verified during Unit 1) | Unit 1 verification step installs on both versions before opening the seed PR. If conflict, fall back to previous stable radon; update the inline rationale comment. |
| Unit 2 SLOC measurement off-by-one or wrong-file | Unit 2 verification dry-runs Unit 3 tests before opening seed PR. The new policy-to-seed drift test (`0 <= ceiling - current_SLOC <= 50`) catches headroom typos that prior plans would have shipped silently. |
| F7 contributes more than 200ms to suite | Module-level radon imports (one-time cost); profile via `pytest --durations`. Radon `analyze` on a 1500-line file is sub-10ms; cold import is the only meaningful cost. |
| Two concurrent PRs bumping the same file's ceiling produce a post-merge state that fails R4 (the existing `push: branches: [main]` lane catches it AFTER main is broken — it is a detection lane, not a prevention lane) | AGENTS.md (Unit 5) recommends GitHub branch protection "Require branches up to date before merging." Plan deliberately does not change branch protection from this PR (origin scope: solo-operator config). Recovery runbook: revert the last-merged bump PR, rebase the other PR, re-measure, re-ship. Pre-merge prevention is a follow-up if the race is observed. |
| Radon's SLOC counter behaves differently than documented on a construct in the monitored files | The SLOC canary fixture (`test_radon_sloc_behavior_pinned`) pins the counter against hand-counted constructs (walrus, match, TYPE_CHECKING, multi-line strings, etc.). Failure surfaces with a precise diagnostic before the budget tests run. Catches the R1-LLOC-class of empirical surprise on shapes not yet probed. |
| Future contributor extracts code from a monitored file but forgets to lower the ceiling | By-design tolerated (R9: journal, not gate). Captured in AGENTS.md as a review-time expectation. Discoverable on monthly `git log -- monolith_budget.toml` audit. |

## Documentation / Operational Notes

- AGENTS.md gains a `## Monolith Budget` section (Unit 5; see Unit 5 Approach for content shape).
- PR description for the seed PR should call out: the exact-pin `==` deviation from house `>=` style; the path corrections vs R5-grounding-era assumptions (post-PR48/PR50 reality); the SLOC canary fixture rationale.
- Recommend (do not change in this PR): GitHub branch-protection "Require branches up to date before merging" on `main`. Documented in AGENTS.md (Unit 5).
- No new monitoring/observability surface.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md](../brainstorms/2026-05-18-monolith-loc-ceiling-requirements.md)
- **Origin ideation:** docs/ideation/2026-05-18-round5-fresh-pass-ideation.md (Idea #4 F7, marked Explored 2026-05-18)
- **Repo patterns:**
  - `tests/test_config_safety_net.py:14` (tomllib precedent)
  - `tests/test_config_roundtrip.py:24-31` (repo-root resolution)
  - `tests/conftest.py` (autouse fixtures — inert for F7)
  - `pyproject.toml:23-24` (dev deps)
  - `.github/workflows/ci.yml:3-7` (existing CI triggers)
- **Critical refactor PRs that moved the monitored files post-grounding:**
  - PR #48 `c23d43c` — split `config.py` into `config/` subpackage
  - PR #50 `aa41731` — moved 16 flat modules into domain subpackages
- **Institutional learnings:**
  - docs/solutions/test-failures/tests-coupled-to-operator-config-state-2026-05-18.md
  - docs/solutions/test-failures/negative-assertion-locks-in-bug-2026-05-15.md
  - docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md
- **MEMORY:**
  - `reference_ci_workflow_pr_filter.md` (`push: branches: [main]` lane confirmed)
  - `feedback_verify_repo_state_before_planning.md` (surfaced by this plan's review — codified in Context section)

## Session Log

- 2026-05-18: Initial F7 plan written with 5 monitored paths inherited from R5 ideation grounding pass.
- 2026-05-18: document-review on the plan caught the path drift (3 of 5 paths refactored away by PR #48 + #50 between R5 grounding and plan write). Plan revised in place:
  - 5 monitored paths updated to current `feat/event-substrate-u3` HEAD (which inherits both refactors)
  - Filename changed `.monolith_budget.toml` → `monolith_budget.toml` (multi-persona consensus — visible matches repo precedent)
  - Unit 3 redesigned: hardcoded `MONITORED_PATHS` tuple dropped (scope-guardian + adversarial consensus); pytest.warns misuse fixed (P0 blocker — would have made CI red in steady state); SLOC canary fixture + `test_radon_sloc_behavior_pinned` added (adversarial highest-leverage fix); `test_policy_to_seed_drift` added; "force-grow runbook" verification replaced with real `test_assertion_fires_when_synthetic_ceiling_exceeded` using tmp_path
  - Risks table: dropped speculative dotfile-confusion row (subsumed by rename); clarified two-PR race as post-merge-detection-not-pre-merge-prevention; added radon-counter-behavior-drift risk addressed by canary
  - Citations corrected: `test_config_safety_net.py:14` (not :11-13); ci.yml step boundaries clarified (lines 28 / 30)
  - `deepened: 2026-05-18` added to frontmatter
- 2026-05-18: Lesson codified — paths cited in a plan must be re-verified against `git HEAD` at plan-write-time, not inherited from upstream grounding. Recorded under Context & Research → Institutional Learnings as a forward-facing reminder.
