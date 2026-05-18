---
date: 2026-05-18
topic: monolith-loc-ceiling
---

# Monolith SLOC Ceiling Structural Test

## Problem Frame

Mass-concentration is the project's recurring failure mode. `content_fetch.py` went from new module to ~621 physical LOC in **7 days** via PRs #20/#25/#26/#27/#28 (5 stacked features). Four of the five largest source files are within hundreds of lines of a perceived "too big" threshold; without a ratchet, the next feature burst regrows the next monolith silently.

R5 ideation produced three surgical extraction candidates — F2 (`ErrorClass` enum oracle), F3 (`safe_write` carve from post-split `config/writer.py`), F5 (`ThrottleClock` class) — each of which would shrink a current monolith on landing. **This document does NOT decompose those monoliths.** F3 is the carve; F7 (this document) prevents the resulting smaller shape from regrowing.

Note on post-refactor reality: R5 ideation grounded against a pre-PR48/PR50 file layout (flat `config.py`, `content_fetch.py`, `markdown_utils.py`). PR #48 (`c23d43c`) split `config.py` into the `config/` subpackage; PR #50 (`aa41731`) moved 16 flat modules into domain subpackages. The 5 monitored files are the post-refactor paths; the historical PRs (#20/#25/#26/#27/#28 stacking on the old flat `content_fetch.py`) remain valid evidence of the failure mode this system protects against.

What's needed is a *journal-style budget* with a single enforcement rule (CI fails when a monitored file exceeds its budgeted ceiling) plus a warning-only canary for the next monolith forming outside the named set. The system is **not** a tamper-proof gate — a solo developer can self-bump ceilings — but git history records every intentional bump with an attached rationale, making the regrowth visible and reviewable.

## Requirements

**Metric and measurement**

- R1. Use radon's **SLOC** (Source Lines of Code) as the per-file metric. SLOC excludes blank lines, comments, AND multi-line strings (including docstrings). This matches the practical intent: documentation-only PRs and comment churn do not erode budget, but real code growth does. Radon's LLOC was rejected after empirical verification (radon 6.0.1) showed it counts docstrings as logical lines — violating the originally-claimed semantics.

- R2. Five named source files are monitored. The set is closed; explicit auto-discovery is warning-only (see R7). Path list reflects current main HEAD (post PR #48 config split + PR #50 16-module move):
  - `src/backlink_publisher/cli/plan_backlinks.py`
  - `src/backlink_publisher/cli/publish_backlinks.py`
  - `src/backlink_publisher/content/fetch.py` (formerly flat `content_fetch.py`; moved by PR #50)
  - `src/backlink_publisher/config/writer.py` (formerly part of flat `config.py`; split by PR #48)
  - `src/backlink_publisher/_util/markdown.py` (formerly flat `markdown_utils.py`; moved by PR #50)

- R3. A single `monolith_budget.toml` file at the repo root records each monitored file with exactly **two required fields**:
  - `ceiling` (int, radon SLOC)
  - `rationale` (string, ≥80 characters, free-form explanation of why this ceiling is the right shape today and what shape the file is expected to settle to)
  
  `bumped_by_pr`, `bumped_at`, and `linked_plan` fields are intentionally NOT required — `git blame` / `git log` on this file is the authoritative record of who bumped what, when, and in which PR.

**Enforcement rules (pytest, runs on every CI lane)**

- R4. The test fails when any monitored file's measured radon SLOC **exceeds** its budgeted `ceiling`. This is the **only** hard-fail enforcement rule.

- R5. The test fails when `monolith_budget.toml` is missing, malformed, missing a required field for any monitored path in R2, or has any entry with `rationale < 80 characters`.

- R6. radon is **pinned to an exact version** in `pyproject.toml`'s `[project.optional-dependencies].dev`. Bumping radon's version is treated as a budget edit — the radon-bump PR must re-measure SLOC for all 5 files and update ceilings accordingly. This protects against radon-release-induced SLOC drift firing the gate on files no one touched.

- R7. **Warning-only auto-discovery canary.** The test prints a `WARNING` (does not fail) when any file under `src/backlink_publisher/**/*.py` exceeds 500 radon SLOC AND is not listed in `monolith_budget.toml`. This is the cheap canary against future monoliths forming outside the named-5 (most likely in `webui_app/routes/*.py` after the recent webui split). Zero false-positive risk because warnings don't gate CI.

**Bump policy**

- R8. Bumping a ceiling requires editing `monolith_budget.toml` in the **same PR** that the new code lives in. Two-PR patterns are not required.

- R9. **The system is a journal, not a tamper-proof gate.** A solo developer can rubber-stamp any ceiling bump by writing 80 characters of rationale. The defense is not technical enforcement; it is `git blame` making every intentional bump visible to a future-self or external reviewer, paired with the rationale field as the human-readable record of *why* the bump was deemed acceptable. No override label exists because none would meaningfully change the trust model — the framing is honest acknowledgment, not security theater.

- R10. F7 (this document) **does NOT decompose `config.py`.** That decomposition (P1-queued, separately scoped) happens via discrete extraction plans like F3 (`safe_write` carve). F7's job is exclusively to prevent regrowth after such extractions land. If F3 carves out 80 SLOC from `config.py`, the F3 PR is expected to lower `config.py`'s ceiling in `monolith_budget.toml` *by review-time convention*, not by CI rule — F7 will only catch regrowth past whatever ceiling exists.

**Initial seeding**

- R11. Initial ceilings are set by the planner based on measured radon SLOC at the seed PR's HEAD, with `ceiling = current_SLOC + 30` rounded up to the nearest 10. The +30 absorbs routine concurrent in-flight work without forcing immediate ratchet ceremony.

- R12. Initial rationale fields explain what shape each file is *expected to settle to* over the next 3 sprints. Example for `config/writer.py`: "Largest piece of the post-PR48 config-subpackage split. Holds atomic-write + snapshot + section-quarantine. R5 F3 `safe_write` carve will lift the atomic-write + snapshot pair into a shared persistence module consumed by JsonStore + events/store; lower ceiling in that landing PR." Example for `content/fetch.py`: "Grew rapidly via PRs #20/#25/#26/#27/#28 (cache TTL + stats + SSRF + soft-404 + prefetch) while still flat; relocated by PR #50. Future SSRF-guard extraction may shrink further; lower ceiling in that PR."

## Success Criteria

- After 2 sprints from F7 landing, no monitored file has crossed its ceiling without a corresponding `monolith_budget.toml` edit visible in `git log` with a non-trivial rationale.
- After F3 (safe_write carve) merges, `config/writer.py`'s `ceiling` in `monolith_budget.toml` reflects the post-carve shape (review-time expectation, not CI-enforced).
- The 500-SLOC warning canary (R7) fires at least once when a new file in `src/` crosses 500 SLOC without being added to the budget — operator sees the warning and consciously decides to add it or extract.
- The monolith assertions add < 200ms to the existing `tests/` suite runtime.
- (Audit checklist, not CI-verifiable.) A monthly read of `git log -- monolith_budget.toml` shows each bump has a rationale a reader 6 months later can interpret.

## Scope Boundaries

- **In scope (v1):** the 5 named files; the 2-field `monolith_budget.toml`; the pytest assertion in `tests/test_no_monolith_regrowth.py`; the 500-SLOC warning canary (R7); planner-set initial ceilings via `current_SLOC + 30`; pinned radon dev dependency.
- **Out of scope, deferred to v2:** function-level SLOC ceilings (per-function size limit); cyclomatic complexity ceilings (radon `cc` supports this); import fan-in / fan-out limits; per-directory aggregate budgets; webui_app/ submodule-level budgets; promoting R7's warning to hard-fail.
- **Out of scope, never:** override labels; warning-only mode for R4 (R4 is hard-fail; R7 is warning-only by design); LLM-assisted rationale generation; per-file SLOC ratios or relative metrics.
- The system does **not** detect intent (extraction vs. refactor vs. feature). It captures budget edits in git history and trusts the rationale field. If extraction PRs land without a ceiling ratchet, that is captured (no edit to budget file) and discoverable on audit — but it is not blocked.

## Key Decisions

- **radon SLOC over LLOC or physical LOC:** SLOC matches the practical "documentation doesn't cost budget, real code does" intent. LLOC was the original design but was empirically rejected after radon 6.0.1 was shown to count docstrings as logical lines. Physical LOC was rejected because docstring/comment churn alone could trip the ceiling.
- **5 named files (closed list) + R7 warning-only auto-discovery:** named list is zero-noise and intent-clear for today's monoliths. R7 provides a near-free canary against tomorrow's monolith forming outside the list — particularly in `webui_app/routes/*.py` after the recent split. Warning-not-fail means R7 cannot produce false CI red, only operator-visible notice.
- **2-field schema (ceiling, rationale):** strictest dedupe with git history. `bumped_by_pr` / `bumped_at` are git-blame-derivable; `linked_plan` was speculative defense with no enforcement teeth. Removing them collapses R5 to ~10 LOC of pytest and eliminates 3 of the 5 Outstanding Questions.
- **No R5/R7 ratchet-down rule (cut from earlier draft):** the proposed 100-SLOC headroom-floor rule was *larger* than the named extractions (F3 expected ~80 SLOC drop), so it would not have fired on the exact PRs it was designed to catch. Ratchet-down is now a review-time expectation captured in rationale convention, not a CI rule. If extraction PRs are observed landing without ceiling drops in practice, reconsider with concrete examples.
- **Journal-not-gate framing (R9):** solo developer = no second reviewer = no possible technical enforcement against rubber-stamping. The honest claim is "this makes regrowth visible and reviewable in git history with attached rationale," not "this prevents regrowth." Closing escape hatches doesn't change the trust model in a solo-operator project.
- **Pinned radon version (R6):** radon's SLOC count walks the Python AST. Python minor versions (3.11 → 3.12 → 3.13) and radon releases have made AST/counter changes historically. Without a pin, a routine `pip install` could shift SLOC ±1-5 per file across CI runs.
- **F7 ≠ config decomposition (R10):** the prior "config.py decomposition out of scope this window" constraint was *already executed* during R5 ideation→brainstorm by PR #48 (`c23d43c`, "split config.py into config/ subpackage"). The 5 monitored files post-refactor are the new shape that F7 holds against regrowth. F3 (safe_write carve) targets the post-split `config/writer.py` (atomic-write + snapshot pair into a shared persistence module).

- **Visible filename `monolith_budget.toml` (no leading dot):** matches `pyproject.toml` / `config.example.toml` repo-root precedent. Earlier draft proposed a dotfile prefix; multi-persona review (3 separate findings across rounds) flagged the dotfile as friction for the exact workflow the file serves (contributor edits on every ceiling bump). Visible naming is rename-now cheaper than rename-later.

## Dependencies / Assumptions

- **New dev dependency: radon, pinned to exact version** (planner picks current stable; 6.0.1 verified working). Added to `[project.optional-dependencies].dev` in `pyproject.toml`.
- **CI invokes pytest with `tests/` as an explicit path argument** (`.github/workflows/ci.yml` runs `python -m pytest tests/`), so a new `tests/test_no_monolith_regrowth.py` is auto-discovered by pytest's default collection in that directory. (`pyproject.toml`'s `[tool.pytest.ini_options]` does not currently set `testpaths` — the explicit path in CI makes that irrelevant.)
- **Python 3.11+** guarantees `tomllib` is stdlib — no third-party TOML parser needed.
- **Stable paths.** The 5 monitored files are at stable repo paths; renaming any of them is treated as an atomic edit (the path key in the TOML must be updated in the same PR as the rename).

## Outstanding Questions

### Deferred to Planning

- [Affects R4, R5][Technical] How to handle a `SyntaxError` in a monitored file. Radon raises on AST parse failure. If a PR introduces a syntax error, the budget test fails with a confusing error rather than the underlying syntax-error signal from other tests. Planner picks: (a) catch and report "monitored file contains syntax error — fix that first," letting other pytest stages also fail; (b) fall back to physical LOC counting with a warning; (c) skip that file with an explicit warning.

- [Affects R4][Technical] How to handle `FileNotFoundError` on a monitored path (e.g., a rename happened without updating `monolith_budget.toml`). The test should fail with an explicit message naming the missing path and suggesting "delete the TOML entry or restore/rename the file." Planner: define the exact error contract.

- [Affects R6][Needs research] Exact initial ceilings. Planner measures current radon SLOC (`radon raw -s src/backlink_publisher/...`) at the seed PR's HEAD and sets `ceiling = round_up_to_10(current_SLOC + 30)`. Numbers must produce a green seed PR.

- [Affects R6][Technical] Whether to add a CI step printing `radon --version` so version drift is visible in logs. Planner: include or skip based on cost.

- [Affects CI determinism][Technical] Whether GitHub branch protection's "require branches to be up to date before merging" must be enabled, so two concurrent PRs each bumping the same file's ceiling don't produce a post-merge state that fails R4. Planner: document the requirement in the plan, or add a `push: branches: [main]` lane that also runs the monolith test as a safety net.

## Next Steps

→ `/ce:plan` for structured implementation planning
