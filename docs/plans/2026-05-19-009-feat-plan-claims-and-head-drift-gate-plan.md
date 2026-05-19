---
title: "feat: Plan-Doc Claims Contract + Merge-Time HEAD-Drift Gate"
type: feat
status: active
date: 2026-05-19
deepened: 2026-05-19
origin: docs/brainstorms/2026-05-19-plan-doc-claims-and-head-drift-gate-requirements.md
claims:
  paths:
    - src/backlink_publisher/cli/report_anchors.py
    - src/backlink_publisher/cli/footprint.py
    - src/backlink_publisher/_util/errors.py
    - src/backlink_publisher/_util/cli_flags.py
    - src/backlink_publisher/phase0/validation.py
    - .github/workflows/ci.yml
    - pyproject.toml
    - tests/test_cli_footprint.py
  shas: []
---

# Plan-Doc Claims Contract + Merge-Time HEAD-Drift Gate

## Overview

Add a machine-checkable contract for plan-docs under `docs/plans/*.md`: a `claims:` frontmatter sub-block listing repo paths and SHAs that must still be reachable from `origin/main` at merge time. Ship a new flat CLI `plan-check` (mirroring `report_anchors.py`'s single-purpose shape) plus a standalone GitHub Actions workflow `plan-claims-gate` that runs `plan-check` on any plan-doc touched by a PR.

The plan eliminates a recurring drift class — plan-doc cites a SHA, path, or PR state that has since been rebased away, renamed by an upstream refactor, or superseded by a parallel PR. The compensating control today is operator memory (5 separate `feedback_*.md` entries in the last 14 days); this plan replaces that fragile control with a CI-enforced contract.

## Problem Frame

See origin doc for the full Problem Frame. Five incidents in 2026-05-18 → 2026-05-19 share the same root cause: a plan-doc was written against a state of `origin/main` that no longer holds at merge time. Operator memory currently catches these manually — `feedback_check_upstream_refactor_before_fixing_stale_branch`, `feedback_validate_main_before_planning_off_feat_branch`, `feedback_verify_repo_state_before_planning`, `feedback_scan_parallel_prs_before_blocker`, and PR #87 `webui_store` wipe.

The gate moves the discipline from operator memory into the merge pipeline.

## Requirements Trace

Requirements numbered R1-R17 as defined in the origin doc (post-review). Each implementation unit below cites the requirements it advances.

- R1-R4 — Claims schema (frontmatter sub-block, paths/shas keys only, unknown-key rejection, one-directional forward-compat)
- R5-R8 — Local CLI (`plan-check` entry, exit-code semantics, `--json`, origin/main freshness)
- R9-R11 — Grandfather rule (date cutoff 2026-05-20, empty claims escape hatch, missing-claims rejection on post-cutoff)
- **R11b — Filename ↔ frontmatter.date consistency lock** (defeats backdate exploit; added during document-review)
- R12-R14 — PR gate (touched-on-PR trigger, fail-on-non-zero, runs against `origin/main` at CI time)
- R15 — Update-plan-on-ship discipline (one convention to remember; safety-netted by R16-R17)
- **R16-R17 — Nightly drift radar** (scheduled workflow + per-drift-day GitHub issues with auto-close + positive-control canary fixture; added during document-review to close the structural coverage gap that touched-only-on-PR cannot reach. Semantics revised in third pass after P0 finding flagged rolling-issue + manual-close model as unfalsifiable.)

Success criteria SC1-SC4 from origin doc carried forward with SC1/SC3/SC4 reframed post-review:
- **SC1 measurement (per-drift-day issues, see D19 revised):** count unique `plan-claims drift radar:` issues opened in 30 days where the issue body lists at least one non-canary drifting plan. Per-day issue + auto-close-on-clean semantics make the count meaningful — each drift event produces exactly one issue, and persistent drift produces one per day until resolved. **Liveness guarantee:** the canary fixture (D19) means radar working = canary always surfaced; radar broken = canary missing from issue body or no recent successful workflow run (`gh run list --workflow=plan-claims-radar.yml`). "Zero issues" can only mean "no real drift" (not "radar offline") if canary is observed alive in the most recent run. **Kill criterion:** if 30 days elapse with (a) zero non-canary drift issues AND (b) canary observed in every successful run (radar alive) AND (c) any new drift incident surfaces by other means (PR gate or operator-reported), then the radar's coverage model is wrong — redesign trigger.
- **SC3 simplified** to "zero `git fetch` failures in CI in the first month" — D5/D16 staleness design survived review.
- **SC4 reframed**: R15 is the one convention to remember; everything else machine-checked. Lint messages are self-describing.

## Scope Boundaries

Carried forward from origin (see origin: `docs/brainstorms/2026-05-19-plan-doc-claims-and-head-drift-gate-requirements.md`):

- Out of v1: `symbols:` claim type, `prs:` claim type, PR-body trailer convention, backfill of 43 existing plan-docs, pre-commit hook, `ce:plan` auto-emit.
- ~~Out of v1: nightly drift radar~~ — **moved IN v1 as R16-R17** after document-review (close structural coverage gap that touched-only-on-PR cannot reach).
- Out of v1: globs in `claims.paths` — rejected with a clear error message (exit 2). Deferred to v1.1.
- Out of v1: enforcement of R15 (`--require-shipped-claims` mode). Documented convention only; safety-netted by R16-R17.

## Context & Research

### Relevant Code and Patterns

**Canonical CLI precedent** — `src/backlink_publisher/cli/report_anchors.py`:
- Single-file CLI, `main(argv=None) -> None`, custom exit codes via `raise SystemExit(int)`, no `UsageError`, no `choices=`.
- Uses shared `add_input_arg` + `add_json_output_arg` helpers from `_util/cli_flags.py`.
- `--json` branches between markdown and JSON formatters near end of `main()`.
- Reserves exit code 6 for `_EXIT_CODE_ALARM` — extends the 0-6 contract idiomatically.

**Named-exceptions precedent** — the footprint regression gate (PR #57). The named exception classes live in `tests/test_footprint_regression.py:62-86` (not `cli/footprint.py` — `cli/footprint.py` only imports `SCHEMA_VERSION`):
- `FootprintGateError`, `FootprintGateDrift`, `FootprintGateAlarmCrossed`, `FootprintGateSchemaMismatch`, `FootprintGateZeroLinks`, `FootprintGateOverrideMalformed`, `FootprintGateBaselineMissing` — so failures self-explain in CI logs.
- `SCHEMA_VERSION` constant pinned in `cli/footprint.py`.
- `--reason` flag with rubber-stamp rejection (≤15 char, blocks "regen"/"wip") — handled via `FootprintGateOverrideMalformed`.
- Two-phase atomic write (`.tmp` then rename).

**Errors hierarchy** — `src/backlink_publisher/_util/errors.py`:
- `UsageError=1`, `InputValidationError=2`, `DependencyError=3`, `ExternalServiceError=4`, `PipelineError=InternalError=5`. Code 6 used by `report_anchors`.
- `argparse` itself exits 2 on invalid arguments (collides with `InputValidationError`).

**YAML usage today** — `src/backlink_publisher/phase0/validation.py:209-214`:
- Lazy `import yaml` with `RuntimeError` fallback; `yaml.safe_load` only.
- `pyyaml` is **not** pinned anywhere in `pyproject.toml` currently.

**CI workflow shape** — `.github/workflows/ci.yml`:
- `actions/checkout@v4` with default depth=1 (shallow).
- `pull_request: branches: [main]` trigger (no `paths:` filter).
- Job-level `PYTHONHASHSEED: "0"`.

**Test pattern** — `tests/test_cli_footprint.py`:
- Imports `main` directly, calls in-process with `monkeypatch` + `capsys`.
- `pytest.raises(SystemExit)` for exit-code assertions. No `click.testing`. No subprocess for the unit tests.

### Institutional Learnings

- **Drop `choices=` for closed sets** (memory `feedback_argparse_choices_vs_usage_error_exit_code_clash`). argparse exits 2; this repo's `UsageError` exits 1. Validate post-parse.
- **Footprint gate is the design template** (memory `reference_footprint_gate_design`). Mirror its named-exceptions / `SCHEMA_VERSION` / atomic-write / `--reason` rubber-stamp filter.
- **CI `pull_request: branches: [main]` is a sharp edge** (memory `reference_ci_workflow_pr_filter`). Stack PRs based off non-`main` bases get permanently empty checks — workaround is `gh pr close && gh pr reopen`. Document, don't try to fix.
- **No module-level asserts reading registry/git state** (`docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md`). Lazy-resolve inside functions.
- **Plan-time refusal + RECON-level signal on skip** (`docs/solutions/best-practices/plan-time-url-validation-prevents-publish-404-2026-05-15.md`). `plan-check` runs locally first; CI gate is the backstop.
- **feasibility-reviewer is the manual analogue** (`docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md`). Reuse P0/P1 finding shape so CI output looks like manual review.
- **`git fetch` before any `origin/main` resolution** (3 memory feedback files converge). Local CLI must fetch; CI checkout must use `fetch-depth: 0`.

### External References

External research skipped — local patterns are dense enough that no external grounding adds value. (See origin doc Dependencies section for the lone exception: GitHub Actions `pull_request` event semantics for `$base`/`$head`, resolved below in Key Technical Decisions.)

## Key Technical Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Flat single-file CLI at `src/backlink_publisher/cli/plan_check.py`** | Mirrors 6 of 7 existing CLIs (only `plan_backlinks/` is a package, and that was post-decomposition under monolith budget). ~250-350 LOC fits a single file. Package-style is YAGNI. |
| D2 | **PyYAML `>=6.0` added to base `[project] dependencies`** | User-confirmed. Eliminates the lazy-import sharp edge across the codebase (2nd callsite now, more likely tomorrow). Follow-up can clean up `phase0/validation.py` defensive import in a separate PR — not in scope here. |
| D3 | **Exit codes 0/2/7/8/9** (diverges from origin R6 prose; exit 9 added in P0 review to disambiguate stale-pass) | Origin R6 named `1=drift, 2=malformed, 3=missing-claims-on-post-cutoff`. R6 collides with `UsageError=1`, `InputValidationError=2` (matches malformed, ok), `DependencyError=3` (collides). Resolution: keep `0=pass`, use `2=schema violation` (aligns with `InputValidationError` AND argparse's hard-coded 2), introduce `7=drift detected`, `8=missing-claims-on-post-cutoff`, and **`9=stale-pass` (claims resolved against possibly-stale `origin/main` because `git fetch` was skipped — see D16)** extending the 0-6 contract (precedent: `report_anchors` uses 6). Exit 9 is operator-visible but treated as success by default consumers; CI gate (Unit 4) MAY pass `--strict-fetch` to upgrade exit 9 → exit 1 (network failure must fail CI). Brainstorm explicitly said "May renumber to a non-overlapping range." |
| D4 | **No `choices=` anywhere; validate post-parse, raise `UsageError`** | Confirms `feedback_argparse_choices_vs_usage_error_exit_code_clash`. Keep `help=` listing valid values for `--help`. |
| D5 | **Staleness detection via `mtime` of `FETCH_HEAD` in the common gitdir** (resolve via `git rev-parse --git-common-dir`) + **always emit `fetch_head_age_seconds` on every run** | Linked worktrees (`bp-*/`) keep `FETCH_HEAD` in the shared common gitdir — hard-coding `.git/FETCH_HEAD` would break on every sibling worktree. Pure stat, one cheap git invocation to resolve gitdir. >300s → `git fetch origin main --quiet`. CI skips the freshness branch (CI's explicit `git fetch` step makes `FETCH_HEAD` always fresh). Age is always surfaced — stale-and-skipped is the dangerous quadrant; surfacing the age in machine-parseable form lets CI (and a future pre-push hook) decide independently. |
| D6 | **Separate workflow `.github/workflows/plan-claims-gate.yml`** | Extending `ci.yml` would force the gate to wait for full Python matrix + `[dev]` install. Lightweight standalone is faster feedback and decouples failure modes. |
| D7 | **Workflow trigger `pull_request: branches: [main]`** | Matches existing CI; stack-PR sharp edge documented, not fixed. |
| D8 | **`fetch-depth: 0` in plan-claims-gate workflow + explicit `git fetch origin main`** | Mandatory for `git merge-base --is-ancestor` and `git cat-file -p origin/main:<path>` to work. Brainstorm Dependencies section flagged this as hard prerequisite. |
| D9 | **Required-check toggle: non-required at ship, promote after 14 days clean** | Mirrors Phase 0 ship-seal pattern (`reference_phase0_remote_routines`). Avoids first-day false-positive lockout. |
| D10 | **Globs in `claims.paths` rejected in v1** with clear error → exit 2 | Defers v1.1 work; tightens schema today. |
| D11 | **Short SHAs accepted, normalized to 40-char in error output** | `git merge-base --is-ancestor` accepts both; normalization is display-only. |
| D12 | **`$base`/`$head` semantics**: `git diff --name-only origin/${{ github.base_ref }}...${{ github.event.pull_request.head.sha }}` paired with `fetch-depth: 0` | Resolves origin doc's Deferred-to-Planning item on workflow ref semantics. |
| D13 | **Named module-local exceptions** (`PlanClaimsPathMissing`, `PlanClaimsSHANotInAncestor`, `PlanClaimsFrontmatterSchemaError`, `PlanClaimsMissingOnPostCutoff`, `PlanClaimsGlobUnsupported`) | Mirrors footprint gate (`FootprintGateDrift` etc.). Self-explaining CI failure messages. No module-level asserts. |
| D14 | **`SCHEMA_VERSION = 1` constant in `plan_check.py`** | Mirrors footprint gate. Forward-compat: v1 tool reading future plan with new `claims.symbols` key fails on unknown-key. The R4 forward-compat clause is one-directional. |
| D15 | **Date-cutoff comparison is date-typed**, not lexical | Origin R9 explicit: parse `frontmatter.date` to `datetime.date`, compare against `date(2026, 5, 20)`. Non-conforming format = exit 2. |
| D16 | **Offline `git fetch` failure: skip + exit 9 (stale-pass) by default; `--strict-fetch` upgrades to exit 1** (revised in P0 review) | `plan-check` is primary (every authoring edit), CI gate is backstop. Failing on flaky network turns authoring into a hostage situation, contradicting SC2's sub-60s authoring overhead. **Exit 9 distinguishes "I couldn't actually check" from "0=pass" so downstream consumers (radar driver, future pre-push hook) can branch.** Default semantic: claims resolved against possibly-stale `origin/main` → exit 9 (operator-visible, not failure). `--strict-fetch` flag (Unit 3) upgrades to exit 1 if fetch fails; CI (Unit 4) passes `--strict-fetch` so transient network in CI fails the gate red. The radar driver treats exit 9 as "drift state unknown for this plan" (NOT a clean pass — surfaces in body as "freshness unknown" section, distinct from "Real drift"). Always emit structured `RECON warn fetch_skipped reason=<network\|auth\|no_remote\|other> fetch_head_age_seconds=<int\|null>` line; classify subprocess stderr into the reason taxonomy. Operator sees "8h stale, skipped due to network" AND non-zero exit 9 — cannot misread as clean. |
| D17 | **R11b filename ↔ frontmatter.date lock** (new) | Defeats backdate exploit (adversarial finding A3). The grandfather cutoff key (`frontmatter.date < 2026-05-20`) is operator-typed YAML — trivially backdatable. The filename's `YYYY-MM-DD-NNN-` prefix is a stronger anchor: all 43 existing plans already follow it (regex-confirmed). Lint asserts string-equality between the filename prefix and `frontmatter.date` (both rendered as `YYYY-MM-DD`). Failure raises `PlanClaimsFilenameDateMismatch` → exit 2 (schema violation). Tests in Unit 1. |
| D18 | **Nightly drift radar workflow `.github/workflows/plan-claims-radar.yml`** (new) | Closes the structural coverage gap that touched-only-on-PR cannot reach: untouched post-cutoff plans never trigger the PR gate (R12), so a plan whose implementation forgot R15 silently rots. Scheduled `cron: '0 9 * * *'` (09:00 UTC daily) + `workflow_dispatch` for manual runs. Workflow enumerates `docs/plans/*.md` with `frontmatter.date >= 2026-05-20`, runs `plan-check --json` on each, aggregates JSON outputs. Uses `gh issue create` for issue filing (consistent with project's CLI-heavy style; first GHA-native issue filer in the repo). Permissions: `issues: write, contents: read`. No matrix; single Python `3.12` job. NOT a required check (informational signal, not a merge gate). |
| D19 | **Radar issue rollup semantics: per-drift-day issues + auto-close on next clean run + canary fixture** (revised after P0 review found SC1 unfalsifiability) | Title pattern `plan-claims drift radar: YYYY-MM-DD`. ONE issue per drift-day. Body lists drifting plans for that day. **Auto-close behavior**: each radar run that finds NO drift (after subtracting the canary fixture, see below) automatically closes all open `plan-claims drift radar:` issues with comment "auto-closed: no drift detected on YYYY-MM-DD". Idempotency: lookup via `gh issue list --label plan-claims-radar --state open` (label-based, not title-substring — labels are atomic and not subject to search-index lag). Issues are tagged with label `plan-claims-radar` at creation. **Positive control (canary)**: `tests/fixtures/radar-canary-drifted-plan.md` is a fixture plan with a deliberately-unreachable path (`src/this-file-does-not-exist.py`) and post-cutoff date. Radar always surfaces it — the issue body lists it under "Canary (radar liveness check)" so operators can distinguish "radar is alive" from "radar is broken." If the canary is NOT in the report, the radar is broken; if the canary is the ONLY entry, the radar is alive and no real drift exists. SC1 measurement (see SC1 reframing): count unique issues opened where `count(real_drift_plans) > 0` (excluding canary-only days) — this is meaningful because every drift event opens a new issue and auto-closes on resolution. Rejects per-plan issues (noise) and rolling-issues (unfalsifiable). |

## Open Questions

### Resolved During Planning

- CLI module placement → flat `cli/plan_check.py` (D1).
- Exit-code numbering → 0/2/7/8, diverges from R6 prose (D3). Brainstorm-review proposed collapsing to 0/1; we kept 0/2/7/8 because the plan's choice leverages an existing repo invariant (`_util/errors.py` 1-5 + argparse=2 collision avoidance + `report_anchors` precedent of code 6). The simplification critique was made without that grounding.
- `--json` schema shape → `{plan, date, schema_version, status, exit_code, fetched_at, fetch_head_age_seconds, fetch_skip_reason, drift: {paths_missing[], shas_unreachable[]}}` (Unit 3). `fetch_skip_reason` is `null` when fetch succeeded or was unneeded; otherwise one of `"network"|"auth"|"no_remote"|"other"`. Brainstorm-review proposed dropping `--json`; we kept it because the nightly radar (Unit 6 / D18) IS the first v1 consumer — it parses `--json` to aggregate drift across plans into a single GitHub issue.
- Staleness detection → `FETCH_HEAD` mtime (resolved via `git rev-parse --git-common-dir` for worktree safety) + 300s threshold (D5). Brainstorm-review proposed always-fetch; we kept staleness because D5+D16 together (always emit `fetch_head_age_seconds`, never lie about freshness) addresses the freshness-trust concern more cleanly than unconditional network round-trips.
- Workflow file location → separate `plan-claims-gate.yml` (D6); nightly radar in separate `plan-claims-radar.yml` (D18).
- Required-check toggle policy → non-required ship + 14-day promotion (D9). Radar (R16-R17) is NEVER a required check — it's an informational safety net, not a merge gate.
- Globs in `claims.paths` → reject in v1 (D10).
- Short SHAs → accept (D11).
- `$base`/`$head` semantics → `github.base_ref` + `head.sha`, `fetch-depth: 0` (D12).
- PyYAML strategy → base deps (D2).
- Filename ↔ frontmatter.date lock → R11b enforced in schema layer; mismatch is exit 2 (D17).
- Nightly radar exists in v1, not deferred → D18.
- Radar issue rollup semantics → one rolling open issue (D19).

### Deferred to Implementation

- **Exact RECON-level log surface in `plan-check`**: the project's `plan_logger.recon(...)` lives in CLI tools that already wire structured logging. plan-check's first version may print directly to stderr; promoting to a structured logger is a follow-up if the CLI grows. The line format itself is contract-bound (see D16), only the underlying logger is deferred.
- **Workflow CPU/runner choice**: default `ubuntu-latest` is fine for v1. If the gate dominates PR feedback time later, consider a smaller runner.
- **Exact error-table formatting for drift output**: human-readable text shape decided at implementation. JSON shape is contract-bound (Unit 3 test scenarios).
- **Subprocess stderr classification regexes** for `_maybe_fetch_origin_main`'s `network|auth|no_remote|other` taxonomy: exact substring/regex patterns decided in Unit 2. Initial seed: `Could not resolve host` → `network`; `Authentication failed`/`Permission denied` → `auth`; `does not appear to be a git repository`/`No such remote` → `no_remote`; else `other`. Refinable as real failure modes surface.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
plan-check docs/plans/X.md
   │
   ├─► read file → split frontmatter (YAML `---...---` block) from body
   │
   ├─► yaml.safe_load(frontmatter) → dict
   │    │
   │    ├─► validate schema:
   │    │     - `date` parses as datetime.date            [→ exit 2 if not]
   │    │     - if date < 2026-05-20: exit 0 (grandfather)
   │    │     - else: `claims` block REQUIRED              [→ exit 8 if missing]
   │    │     - `claims` accepts only {paths, shas}        [→ exit 2 if unknown key]
   │    │     - `paths` rejects globs (*, ?, [)            [→ exit 2 if glob]
   │    │
   │    └─► extract claims.paths[], claims.shas[]
   │
   ├─► freshness check:
   │     - if (now - mtime(.git/FETCH_HEAD)) > 300s:
   │         git fetch origin main --quiet
   │
   ├─► resolve each path:
   │     git cat-file -e origin/main:<path>                [exit 0 = exists, 1 = missing]
   │
   ├─► resolve each sha:
   │     git merge-base --is-ancestor <sha> origin/main    [exit 0 = reachable, 1 = not]
   │
   └─► aggregate results:
        - any missing/unreachable      → exit 7 + drift table (or JSON)
        - all resolved                  → exit 0
        - fetched_at timestamp in output
```

CI workflow shape:

```yaml
# .github/workflows/plan-claims-gate.yml
on:
  pull_request:
    branches: [main]
jobs:
  plan-claims-gate:
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: git fetch origin main
      - run: pip install -e .        # base only, NO [dev]
      - run: |
          touched=$(git diff --name-only \
            origin/${{ github.base_ref }}...${{ github.event.pull_request.head.sha }} \
            | grep '^docs/plans/.*\.md$' || true)
          for f in $touched; do plan-check "$f"; done
```

## Pre-Implementation Setup

This work lands on a **fresh branch off `origin/main`** in a dedicated worktree. The canonical `backlink-publisher/` worktree currently sits on `feat/webui-tdk-anchor-velog-settings` with 18+ sibling `bp-*/` worktrees in flight; starting from there would entangle this plan with parallel work and fail D7's `pull_request: branches: [main]` trigger.

From the canonical repo:
```
cd backlink-publisher
git fetch origin
git worktree add ../bp-plan-claims-gate -b feat/plan-claims-gate origin/main
cd ../bp-plan-claims-gate
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

This gives a clean main-based worktree with its own editable install (per `feedback_per_worktree_venv_for_editable_install`), no `PYTHONPATH` gymnastics, and a base branch where the gate's own dogfood smoke test will fire on PR open.

## Implementation Units

- [ ] **Unit 1: Frontmatter parser + claims schema**

**Goal:** Add `pyyaml` base dep; create `cli/plan_check.py` skeleton with frontmatter parsing, claims schema validation, and named module-local exceptions. No CLI dispatch yet, no git calls yet.

**Requirements:** R1, R2 (path schema), R3 (sha schema), R4 (unknown-key rejection, forward-compat semantics), R9 (date-typed comparison), R10 (missing-claims-on-post-cutoff), R11 (empty claims escape hatch), **R11b (filename ↔ frontmatter.date lock, D17)**.

**Dependencies:** None.

**Files:**
- Modify: `pyproject.toml` (add `"pyyaml>=6.0"` to `[project] dependencies` list — appended to end of list; the existing list is not alphabetical, so adopt the file's actual convention: insertion order is "as added").
- Create: `src/backlink_publisher/cli/plan_check.py` (module scaffolding, `SCHEMA_VERSION = 1` constant, named exceptions, `_parse_frontmatter`, `_validate_claims_schema`, `_grandfathered`).
- Create: `tests/test_cli_plan_check.py` (schema-tier tests; later units extend the same file).

**Approach (validation order is load-bearing — see P0 fix in Session Log 2026-05-19 third pass):**
1. `_parse_frontmatter(text: str) -> dict`: split on first `---` and second `---`, `yaml.safe_load` the middle, return dict (or raise `PlanClaimsFrontmatterSchemaError`).
2. **`_check_filename_date_lock(plan_path: Path, fm: dict) -> None` runs FIRST** (before grandfather and before claims-schema). Extract the `YYYY-MM-DD-` prefix from `plan_path.name` via regex `^(\d{4}-\d{2}-\d{2})-`. Compare to `fm["date"].isoformat()` (string equality). Mismatch raises `PlanClaimsFilenameDateMismatch` (exit 2) with message naming both values. **Critical: the lock runs unconditionally on every plan-doc — pre-cutoff and post-cutoff alike — because the lock's whole purpose is to defeat backdating (an exploit that operates AT the cutoff boundary). If the lock were skipped for grandfathered plans, a backdated plan-doc (filename 2026-05-21 + frontmatter.date 2026-05-19) would exit 0 as grandfathered before the lock fires. Order matters; do not reorder.**
3. `_grandfathered(fm: dict) -> bool`: parse `fm["date"]` to `datetime.date`, return True if `< date(2026, 5, 20)`. If True, exit 0 (grandfather skip) — schema validation and resolution layers do not run.
4. `_validate_claims_schema(fm: dict) -> ClaimsBlock | None`: reject unknown keys under `claims:`, accept only `paths:` and `shas:`. Reject glob characters in `paths` entries (`*`, `?`, `[`). Reject malformed SHA strings via `_validate_sha_format(s: str) -> bool` (regex `^[0-9a-f]{7,40}$`, lowercase hex only — 7-char short SHAs and 40-char full SHAs both pass; mixed case, non-hex, wrong length all fail) — schema layer rejects garbage early with a clear message rather than letting git emit `bad object 'foo123'` mid-resolution in Unit 2. (Coherence-reviewer flagged a test-vs-approach inconsistency; G3 resolution: validate format here, validate reachability in Unit 2.)
5. Named exceptions: `PlanClaimsFrontmatterSchemaError`, `PlanClaimsMissingOnPostCutoff`, `PlanClaimsGlobUnsupported`, `PlanClaimsFilenameDateMismatch`. Each carries an `exit_code` class attribute mirroring `_util/errors.py` style.
- `SCHEMA_VERSION = 1` at module top with comment pointing at this plan for forward-compat semantics (R4).

**Execution note:** Test-first for the schema layer — the contract surface is small enough that RED→GREEN→REFACTOR keeps the API honest. (Unit 2 and Unit 3 do not require this posture.)

**Patterns to follow:**
- `phase0/validation.py:209-233` for `yaml.safe_load` + `isinstance(dict)` + explicit schema validation.
- `tests/test_footprint_regression.py:62-86` for named-exception placement (module-local), and `cli/footprint.py` for the `SCHEMA_VERSION` constant convention.
- `_util/errors.py` for `exit_code` class attribute convention.

**Test scenarios:**
- *Happy path*: well-formed plan-doc with `claims: {paths: [src/foo.py], shas: [abc1234]}` parses to a `ClaimsBlock`.
- *Happy path*: empty `claims: {}` parses to an empty `ClaimsBlock`, marked as "explicit opt-out".
- *Happy path*: pre-cutoff plan-doc (`date: 2026-05-19`) with no `claims:` block returns "grandfathered, skip".
- *Edge case*: post-cutoff plan-doc (`date: 2026-05-20`) with no `claims:` block raises `PlanClaimsMissingOnPostCutoff` (exit 8).
- *Edge case*: `claims.paths` containing `src/*.py` raises `PlanClaimsGlobUnsupported` (exit 2) with message naming the glob char.
- *Edge case*: `claims.shas` containing a 7-char SHA accepted; 40-char SHA accepted; non-hex string raises schema error.
- *Edge case*: `date:` field is not ISO-8601 (e.g., `May 19 2026`) raises `PlanClaimsFrontmatterSchemaError` (exit 2).
- *Edge case*: `date:` field comparison is date-typed (string `"2026-05-20"` vs `date(2026,5,20)` comparison works).
- *Error path*: `claims.symbols: [foo]` (unknown key) raises schema error (exit 2) naming the unknown key.
- *Error path*: missing closing `---` raises `PlanClaimsFrontmatterSchemaError`.
- *Error path*: frontmatter is not a top-level YAML dict (e.g., a list) raises schema error.
- *Error path*: plan-doc has **no frontmatter at all** (no leading `---`) raises schema error with message "plan-doc missing YAML frontmatter".
- *Error path*: plan-doc has **empty frontmatter block** (`---\n---\n` with nothing between) — `yaml.safe_load("")` returns `None`; treat as schema error (not a dict).
- *Edge case*: plan-doc has **UTF-8 BOM** prefix (`﻿`) — strip the BOM before frontmatter split so the leading `---` is still detected.
- *Error path*: plan-doc is **non-UTF8** (e.g., latin-1 with non-ASCII characters) — `Path.read_text(encoding="utf-8")` raises `UnicodeDecodeError`; catch and re-raise as `PlanClaimsFrontmatterSchemaError` with a helpful message.
- *Happy path* (R11b): plan-doc filename `2026-05-21-001-feat-foo-plan.md` with `frontmatter.date: 2026-05-21` passes the filename-date lock.
- *Error path* (R11b): plan-doc filename `2026-05-21-001-feat-foo-plan.md` with `frontmatter.date: 2026-05-19` (backdate attempt to escape grandfather cutoff) raises `PlanClaimsFilenameDateMismatch` (exit 2) with message citing both `2026-05-21` (filename) and `2026-05-19` (frontmatter).
- *Error path* (R11b): plan-doc filename without `YYYY-MM-DD-` prefix (e.g., `foo-plan.md` — legacy stray file) raises `PlanClaimsFilenameDateMismatch` with message "filename does not match required `YYYY-MM-DD-NNN-` prefix pattern". Validates D17's "all 43 existing plans already follow this naming pattern" assumption.

**Verification:**
- `pytest tests/test_cli_plan_check.py -k schema` passes.
- `python -c "import backlink_publisher.cli.plan_check; print(backlink_publisher.cli.plan_check.SCHEMA_VERSION)"` prints `1`.
- `plan-check` invoked against every existing plan-doc (post-cutoff AND grandfathered) passes the filename-date lock — all 43 existing plans have matching filename ↔ frontmatter.date (validates D17's "regex-confirmed" claim on the actual corpus). Then grandfathered plans exit 0 silently from the cutoff branch; post-cutoff plans (zero on day 1) proceed to claims-schema validation. The order — lock first, then grandfather — is verified by a regression test introducing a synthetic backdated plan-doc (filename 2026-05-21 + frontmatter.date 2026-05-19) and asserting `PlanClaimsFilenameDateMismatch` (exit 2), NOT exit 0 grandfather skip.

- [ ] **Unit 2: Git resolution + origin/main freshness**

**Goal:** Add git subprocess helpers to `plan_check.py` for path-existence and SHA-reachability against `origin/main`, plus a freshness gate that re-fetches when `.git/FETCH_HEAD` mtime is older than 300 seconds.

**Requirements:** R2 (paths resolve on origin/main), R3 (shas reachable from origin/main), R8 (5-min staleness threshold).

**Dependencies:** Unit 1.

**Files:**
- Modify: `src/backlink_publisher/cli/plan_check.py` (add `_fetch_head_age_seconds`, `_maybe_fetch_origin_main`, `_path_exists_on_main`, `_sha_reachable_from_main`).
- Modify: `tests/test_cli_plan_check.py` (add git-tier tests using `tmp_path` fixtures + `subprocess.run(["git", "init", ...])`).

**Approach:**
- `_fetch_head_age_seconds() -> float`: resolve the gitdir via `git rev-parse --git-common-dir` (this repo runs in 18+ linked worktrees where `.git` is a *file* and `FETCH_HEAD` lives in the *common* gitdir, not the per-worktree one). Then `time.time() - os.stat(f"{common_dir}/FETCH_HEAD").st_mtime`. If `FETCH_HEAD` does not exist, return `inf` so the staleness check always re-fetches on first run. **Hard-coding `.git/FETCH_HEAD` would break on every `bp-*/` worktree** (feasibility-reviewer P0).
- `_maybe_fetch_origin_main(threshold_seconds: int = 300) -> FetchOutcome`: returns a structured outcome `(fetched: bool, fetch_head_age_seconds: int | None, skip_reason: Optional[Literal["network", "auth", "no_remote", "other"]])`. Subprocess: `git fetch origin main --quiet`. On non-zero exit, classify stderr (per D16) into the `skip_reason` taxonomy and return — **never raise** for fetch failure. (Path/SHA resolution functions still raise on git infra errors that aren't fetch-fail.) The CLI surface (Unit 3) prints `RECON warn fetch_skipped reason=<reason> fetch_head_age_seconds=<n>` on stderr when `skip_reason is not None`, and `RECON info fetch_head_age_seconds=<n>` on every other run. Per D16 this lets operators continue authoring offline without lying about freshness.
- `_path_exists_on_main(path: str) -> tuple[bool, Literal["exists", "missing", "git_error"]]`: `subprocess.run(["git", "cat-file", "-e", f"origin/main:{path}"], check=False)`. Exit 0 → `(True, "exists")`. Exit 1 → `(False, "missing")` (path genuinely not on main). Exit 128 → `(False, "git_error")` with stderr captured (object not in DB, corrupt repo, or other git-fatal). **Distinguishing 1 from 128 matters**: exit 128 should be surfaced as a distinct CLI error (likely exit 3 `DependencyError` or a new code) rather than masquerading as drift, otherwise transient git infra failures produce misleading "path missing on origin/main" messages.
- `_sha_reachable_from_main(sha: str) -> tuple[bool, Literal["reachable", "unreachable", "unknown_object", "git_error"]]`: `subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=False)`. Exit 0 → reachable. Exit 1 → unreachable (sha exists in DB but not on main). Exit 128 → object not in object DB at all (most common case for a stale plan-doc claim where the SHA was rebased away on a force-push, or a typo). Same surfacing rule as `_path_exists_on_main`. SHA shorter than 40 chars is fine (git resolves).
- All git subprocess calls run with `env={"LC_ALL": "C", "LANG": "C", **os.environ}` to keep stderr taxonomy locale-independent — feasibility-reviewer flagged `致命錯誤` vs `fatal` regex divergence on non-en CI runners and dev machines.
- All subprocess calls lazy (inside the function bodies), never at module import. Mirror `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md`.

**Patterns to follow:**
- `scripts/prune-stale-worktrees.sh` and `tests/scripts/test_prune_stale_worktrees.py` for the pattern of git subprocess + tmp_path-isolated test fixtures.

**Test scenarios:**
- *Happy path*: in a tmp repo where `origin/main` has `src/foo.py`, `_path_exists_on_main("src/foo.py")` returns True.
- *Happy path*: in a tmp repo where `abc1234` is on `origin/main`, `_sha_reachable_from_main("abc1234")` returns True.
- *Edge case*: short SHA (7 chars) works the same as full SHA (40 chars).
- *Edge case*: path with directory components (`src/foo/bar.py`) resolves correctly.
- *Edge case*: `.git/FETCH_HEAD` does not exist → returns `inf` age → triggers fetch.
- *Edge case*: `.git/FETCH_HEAD` mtime is 299s ago → no fetch (under threshold).
- *Edge case*: `.git/FETCH_HEAD` mtime is 301s ago → fetch triggered.
- *Error path*: `_path_exists_on_main("missing.py")` returns False (no exception).
- *Error path*: `_sha_reachable_from_main("deadbeef")` returns False (sha exists on a feature branch but not main).
- *Error path*: `git fetch origin main` fails with "Could not resolve host" stderr → returns `FetchOutcome(fetched=False, skip_reason="network", fetch_head_age_seconds=<n or None>)`. No exception raised.
- *Error path*: `git fetch origin main` fails with auth error stderr → `skip_reason="auth"`.
- *Error path*: `git fetch origin main` fails with "does not appear to be a git repository" / "No such remote" → `skip_reason="no_remote"`.
- *Error path*: `git fetch` fails AND `.git/FETCH_HEAD` does not exist → outcome carries `fetch_head_age_seconds=None`, `skip_reason` still classified.
- *Edge case*: `fetch_head_age_seconds` is always populated (or explicitly `None`) on every code path, including the happy path — the field is part of the function's output contract per D16.
- *Integration*: a real tmp repo with two commits — one on main, one only on a feature branch — exercises the full path/sha resolution layer.

**Verification:**
- `pytest tests/test_cli_plan_check.py -k git` passes.
- Manual: `cd backlink-publisher && python -c "from backlink_publisher.cli.plan_check import _path_exists_on_main; print(_path_exists_on_main('src/backlink_publisher/cli/footprint.py'))"` prints `True`.

- [ ] **Unit 3: CLI wiring (argparse, exit codes, `--json`, output formatting)**

**Goal:** Wire `main(argv=None) -> None`, post-parse validation (no `choices=`), `--json` flag, human-readable and JSON output formatters, end-to-end exit-code dispatch (0/2/7/8). Add `plan-check` console-script entry.

**Requirements:** R5 (CLI entry, single positional arg), R6 (exit codes — using D3 numbering), R7 (`--json` flag), R8 (freshness applied automatically), R9-R11 (grandfather + missing-claims dispatch), R13 (failure output is the table operator sees locally).

**Dependencies:** Unit 1, Unit 2.

**Files:**
- Modify: `src/backlink_publisher/cli/plan_check.py` (add `main(argv)`, output formatters, `if __name__ == "__main__": main()`).
- Modify: `pyproject.toml` (add `plan-check = "backlink_publisher.cli.plan_check:main"` to `[project.scripts]`, appended to end — existing scripts are not alphabetical).
- Modify: `tests/test_cli_plan_check.py` (add CLI-tier tests using `main(argv)` in-process + `capsys` + `pytest.raises(SystemExit)`).

**Approach:**
- `main(argv: list[str] | None = None) -> None`:
  - `parser = argparse.ArgumentParser(prog="plan-check", description=...)`.
  - Positional `plan_path`. `--json` boolean from `add_json_output_arg`. NO `choices=` on anything.
  - Parse → resolve `plan_path` → if not file: raise `UsageError` (exit 1).
  - Read file → call Unit 1 schema layer → if grandfathered: exit 0.
  - Call Unit 2 freshness + resolution → aggregate `paths_missing[]`, `shas_unreachable[]`.
  - If `--json`: dump `{plan, date, schema_version, status, exit_code, fetched_at, drift: {...}}` to stdout.
  - Else: human-readable table to stderr ("Drift detected on docs/plans/X.md:" + 2-column path/sha → status table), one-liner summary to stdout.
  - Dispatch: 0 (pass) / 2 (schema violation) / 7 (drift) / 8 (missing claims) / **9 (stale-pass: claims resolved against possibly-stale main; D16)**. NO exit code 1 except via `UsageError`, OR via `--strict-fetch` upgrading exit 9 → 1.
  - `--strict-fetch` flag: post-parse boolean. When set, `_maybe_fetch_origin_main` raising `skip_reason` causes `main()` to dispatch exit 1 instead of exit 9. CI gate (Unit 4) passes `--strict-fetch`; local default omits it.

**Patterns to follow:**
- `cli/report_anchors.py:380-471` — argparse setup, shared helpers, dual markdown/JSON output, `SystemExit(int)` for domain errors.
- `_util/cli_flags.py:40-48` — `add_json_output_arg` reuse.

**Test scenarios:**
- *Happy path*: grandfathered plan-doc (`date: 2026-05-19`) → exit 0, silent.
- *Happy path*: post-cutoff plan-doc with empty `claims: {}` → exit 0, silent.
- *Happy path*: post-cutoff plan-doc with all paths/shas resolving on `origin/main` → exit 0, summary line on stdout.
- *Happy path*: `--json` flag emits valid JSON with `schema_version: 1` and `status: "pass"`.
- *Edge case*: positional arg is a directory → `UsageError` (exit 1).
- *Edge case*: positional arg is a non-existent file → `UsageError` (exit 1).
- *Error path*: plan-doc has malformed YAML frontmatter → exit 2, schema-error message on stderr.
- *Error path*: plan-doc has unknown key under `claims:` → exit 2, message names the key.
- *Error path*: plan-doc has glob in `claims.paths` → exit 2.
- *Error path*: drift on paths (one path missing on `origin/main`) → exit 7, drift table on stderr, summary on stdout. `--json` returns matching JSON with `paths_missing: ["..."]`.
- *Error path*: drift on shas (one sha not reachable from `origin/main`) → exit 7, table cites the sha.
- *Error path*: post-cutoff plan-doc missing `claims:` block → exit 8, message on stderr explains the rule.
- *Integration*: end-to-end test seeded with a tmp git repo + a tmp plan-doc, calling `main([plan_doc_path])` in-process and asserting stdout/stderr/exit-code.
- *Integration*: `--json` and human output describe the same drift consistently (same `paths_missing` set in both).
- *Integration*: when `_maybe_fetch_origin_main` returns `skip_reason="network"`, the CLI exits 0, emits `RECON warn fetch_skipped reason=network fetch_head_age_seconds=...` on stderr, AND still attempts path/sha resolution against the (possibly stale) `origin/main` ref — drift detection still runs, operator just sees the freshness warning.
- *Integration*: on the happy path (fetch succeeds or freshness OK), `RECON info fetch_head_age_seconds=<n>` is always emitted to stderr.
- *Integration*: `--json` output when `fetch_skip_reason="network"` includes `"fetch_skip_reason": "network"` and `"fetch_head_age_seconds": <int|null>` keys with the correct values — locks the JSON contract for downstream tooling (e.g., a future pre-push hook) so the field is not silently dropped on the skip path.

**Verification:**
- `pytest tests/test_cli_plan_check.py` (full file) passes.
- `pip install -e .` then `which plan-check` resolves to the venv bin.
- `plan-check docs/plans/2026-05-19-009-feat-plan-claims-and-head-drift-gate-plan.md` exits 0 (this plan-doc's own `claims:` block is honest at write time — see Unit 5 for dogfood verification).

- [ ] **Unit 4: GitHub Actions workflow `plan-claims-gate`**

**Goal:** Standalone workflow that on every PR computes `docs/plans/*.md` touched by the diff and runs `plan-check` on each. Non-required at ship; promoted to required after 14 days clean.

**Requirements:** R12 (touched-on-PR trigger), R13 (fail on non-zero), R14 (runs against `origin/main` at CI time).

**Dependencies:** Unit 3 (the console-script must exist for the workflow to invoke it).

**Files:**
- Create: `.github/workflows/plan-claims-gate.yml`.

**Approach:**
- `on.pull_request.branches: [main]` matching existing `ci.yml`. No `paths:` filter — the cost of running this on every PR is dominated by `pip install`, and skipping when no plan-docs change is the inner loop, not the outer.
- `actions/checkout@v4` with `fetch-depth: 0`.
- Explicit `git fetch origin main` step (belt+suspenders alongside `fetch-depth: 0`).
- `pip install -e .` (base only, NO `[dev]` — the gate doesn't need pytest).
- Shell loop:
  - `touched=$(git diff --name-only "origin/${{ github.base_ref }}...${{ github.event.pull_request.head.sha }}" | grep '^docs/plans/.*\.md$' || true)`
  - `if [ -z "$touched" ]; then echo "no plan-docs touched, skipping"; exit 0; fi`
  - `for f in $touched; do plan-check "$f"; done`
  - Each `plan-check` failure is a non-zero shell exit → fails the job.
- Job name: `plan-claims-gate / check` (slash-separated so the required-check rule binds cleanly later).

**Patterns to follow:**
- `.github/workflows/ci.yml` for trigger spec, `python-version` matrix shape (use a single version `3.12` for the gate — no need for matrix).
- Phase 0 ship-seal pattern (`reference_phase0_remote_routines`) for the "ship non-required, promote after 14 days" cadence.

**Test scenarios:**
- *Happy path (smoke)*: this very PR (the one shipping plan-check) will trigger the gate against this plan-doc itself. Unit 5's dogfood claims must resolve at merge time.
- *Smoke*: a PR touching no plan-docs → gate runs but logs "no plan-docs touched, skipping" and exits 0.
- *Smoke*: a PR touching a grandfathered plan-doc only → gate runs `plan-check` on it, exits 0 silently.
- *Smoke*: a PR introducing a new plan-doc with bad `claims:` → gate fails the job with the same human-readable table the local CLI emits (R13).
- **Test expectation: none for the YAML file itself** — workflow correctness is verified by being run on the introducing PR. No `tests/test_workflow_plan_claims_gate.py`.

**Verification:**
- This plan's own PR reaches GitHub Actions; `plan-claims-gate / check` shows green on the PR checks panel.
- Failure mode rehearsal (optional, in a separate scratch PR or local act-runner): introduce a deliberately-broken plan-doc, confirm CI fails with the expected table.

- [ ] **Unit 5: Docs + dogfood claims block**

**Goal:** Document the contract in `AGENTS.md`. Add this plan's own `claims:` block as the dogfood example (already done in frontmatter above). Brief mention in `README.md`.

**Requirements:** R15 (update-plan-on-ship discipline — documented, not enforced).

**Dependencies:** Unit 3 (CLI surface is stable enough to document).

**Files:**
- Modify: `backlink-publisher/AGENTS.md` (add "Plan-doc claims contract" section, ~30 lines, between "Lessons capture" and "Worktree Cleanup"). AGENTS.md is the canonical contributor surface per `CLAUDE.md`; no README touch needed.
- Modify: `docs/plans/2026-05-19-009-feat-plan-claims-and-head-drift-gate-plan.md` (this file — `status: active` → `status: shipped` and `claims:` refreshed at merge time, per R15 discipline). **The `date:` field is NOT bumped** — see Risks table for R15 + grandfather interaction.

**Approach:**
- AGENTS.md section covers: what `claims:` is, the two accepted keys (`paths`, `shas`), the grandfather date, the exit-code table, how the CI gate works, the empty-`claims: {}` escape hatch, and the "update plan-doc on ship" discipline (R15).
- Cross-reference: `docs/plans/2026-05-19-009-...-plan.md` as the canonical implementation reference.
- README: one line in the existing developer-facing section. Don't invent a new heading.
- Update-plan-on-ship: at merge time, the implementing operator (likely the author of the PR landing this plan) flips `status: active → shipped`, re-resolves the `claims:` block against post-merge `origin/main`, and refreshes any drifted entry.

**Patterns to follow:**
- AGENTS.md sections "Monolith Budget" and "Adding a new publisher adapter" for tone (operational, short, with file references and CLI invocations).

**Test scenarios:**
- **Test expectation: none — this unit is pure documentation + frontmatter edits.** Verification is by re-running `plan-check` on this plan-doc post-merge (Unit 4's gate does that automatically).

**Verification:**
- `plan-check docs/plans/2026-05-19-009-feat-plan-claims-and-head-drift-gate-plan.md` exits 0 at merge time (gate runs on the PR; if it passes, this is automatic).
- A new contributor reading `AGENTS.md` "Plan-doc claims contract" can author a compliant plan-doc without consulting this plan-doc.

- [ ] **Unit 6: Nightly drift radar workflow (`plan-claims-radar.yml`)**

**Goal:** Standalone scheduled GitHub Actions workflow that runs `plan-check --json` against every post-cutoff plan-doc in `docs/plans/*.md` daily at 09:00 UTC, aggregates drift across plans, and maintains one rolling open GitHub issue summarizing current drift. Closes the structural coverage gap where untouched post-cutoff plans never trigger the PR gate.

**Requirements:** R16 (scheduled enumeration of all post-cutoff plans), R17 (per-drift-day issue + auto-close, revised in P0 review).

**Dependencies:** Unit 3 (the `plan-check --json` surface must exist), Unit 4 (the `plan-claims-gate.yml` precedent supplies the install + fetch step shape).

**Files:**
- Create: `.github/workflows/plan-claims-radar.yml` (the workflow shell + step ordering).
- Create: `scripts/plan_claims_radar.py` (~60 LOC driver — enumerate plans, filter by post-cutoff date, subprocess `plan-check --json`, aggregate, render issue body). Workflow YAML calls this driver via `python scripts/plan_claims_radar.py`; keeps shell minimal and the logic testable. (P0 review feasibility-F2 fix: surfacing the helper module location prevents the "30-60 LOC python-embed in YAML" anti-pattern.)
- Create: `tests/fixtures/radar-canary-drifted-plan.md` (canary fixture — see Approach).
- Create: `tests/test_plan_claims_radar.py` (pytest coverage for the driver: filtering correctness, canary detection, JSON aggregation, body-rendering, label query parsing). The workflow YAML itself stays operational-smoke-only (Unit 4 convention).

**Approach:**
- Trigger: `on: { schedule: [ { cron: "0 9 * * *" } ], workflow_dispatch: {} }`. The `workflow_dispatch` lets operators trigger on demand (mirrors Phase 0 remote-trigger routine ergonomics).
- Permissions block: `permissions: { issues: write, contents: read }`. Issue token: `${{ secrets.GITHUB_TOKEN }}` — no PAT needed for same-repo issue ops.
- Single job, Python `3.12` only (no matrix; faster + the gate is logic, not platform-conditional).
- `actions/checkout@v4` with `fetch-depth: 0` + explicit `git fetch origin main` (same as Unit 4 — the `plan-check` core depends on it).
- `pip install -e .` (base only, no `[dev]`).
- **Driver invocation:** `python scripts/plan_claims_radar.py --canary-fixture tests/fixtures/radar-canary-drifted-plan.md --output /tmp/radar-body.md --exit-code-out /tmp/radar-summary.json`. Driver responsibilities: enumerate `docs/plans/*-plan.md`, parse frontmatter, filter `date >= 2026-05-20`, append the canary fixture to the work list, run `plan-check --json` on each, aggregate, render the issue body to `/tmp/radar-body.md`, write a summary to `/tmp/radar-summary.json` with shape `{real_drift_count: int, canary_drift_detected: bool, plans: [{path, exit_code, paths_missing, shas_unreachable}]}`. Exit codes from driver itself: 0 always (driver succeeded); the SUMMARY tells the workflow whether to file/close issues.
- **Canary fixture:** `tests/fixtures/radar-canary-drifted-plan.md` — a tracked fixture plan with `date: 2026-05-20` (post-cutoff) and `claims: { paths: [src/this-file-does-not-exist.py] }`. Always drifts. Driver appends it to every run. Body groups output as "## Canary (radar liveness check)" + "## Real drift" so operators can distinguish "radar broken" (canary missing) from "no real drift" (canary alone). The summary's `canary_drift_detected` MUST be `true` on every run — if false, driver exits 1 and the workflow fails loudly (the radar is broken).
- **Issue management step** (shell + `gh`, **revised P0 fix per D19**):
  - Label-based lookup: `existing=$(gh issue list --label plan-claims-radar --state open --json number,title)`. Labels are atomic; no search-index lag (P0 fix for race condition).
  - Bootstrap: workflow first ensures the `plan-claims-radar` label exists: `gh label create plan-claims-radar --color C9DAF8 --description "Auto-filed by plan-claims-radar workflow" || true`.
  - Parse `real_drift_count` from `/tmp/radar-summary.json`.
  - If `real_drift_count > 0`:
    - Check whether today's issue exists: `today_title="plan-claims drift radar: $(date -u +%Y-%m-%d)"`; `today_num=$(echo "$existing" | jq -r ".[] | select(.title==\"$today_title\") | .number")`.
    - If today's issue doesn't exist: `gh issue create --title "$today_title" --body-file /tmp/radar-body.md --label plan-claims-radar`.
    - If today's issue already exists (e.g., manual re-dispatch on same day): `gh issue edit $today_num --body-file /tmp/radar-body.md` (regenerate body).
  - If `real_drift_count == 0`: auto-close all open `plan-claims-radar`-labelled issues: `for n in $(echo "$existing" | jq -r '.[].number'); do gh issue close $n --comment "auto-closed: no drift detected on $(date -u +%Y-%m-%d)"; done`.
  - Idempotency: per-day issue is unique by title; auto-close is bounded by `--label` query result; no duplicates possible.
- Workflow concurrency: `concurrency: { group: plan-claims-radar, cancel-in-progress: false }` queues `workflow_dispatch` invocations behind cron. Per-day title key means even raced runs converge on the same issue.
- Failure modes: driver canary check failure → exit 1 → workflow red. Any `gh` call failure → workflow red. SC1 measurement signal: `gh issue list --label plan-claims-radar --state all` count over 30 days (excluding canary-only days — the body's "Real drift" section count is the discriminator).

**Patterns to follow:**
- `.github/workflows/ci.yml` for `actions/checkout@v4` + `actions/setup-python@v5` shape and `pip install -e .` invocation.
- `.github/workflows/plan-claims-gate.yml` (Unit 4) for `fetch-depth: 0` + explicit `git fetch origin main` step.
- `reference_phase0_remote_routines` (memory) — the only prior art for "scheduled job that posts informational signal to the repo." Mirror its non-blocking, observable pattern; do NOT model after the (non-existent) issue-filing GHA workflow precedent. **This is the repo's first GHA-native issue filer.**
- For shell driver in workflow YAML: keep all path handling via Python-embed (`python -c "..."`) instead of `awk $field` / bash arrays — `feedback_awk-field-split-truncates-paths-with-spaces` style gotcha applies because the workspace path contains spaces.

**Test scenarios:**

*Driver (`scripts/plan_claims_radar.py`) pytest coverage in `tests/test_plan_claims_radar.py`:*
- *Happy path*: zero real drift, canary present → summary `{real_drift_count: 0, canary_drift_detected: true}`. Workflow's issue management step interprets as "no drift" → auto-close path.
- *Happy path*: one real drifting plan + canary → summary `{real_drift_count: 1, canary_drift_detected: true, plans: [{path: "docs/plans/X.md", exit_code: 7, ...}]}`. Body groups output into "Canary" and "Real drift" sections.
- *Happy path*: canary fixture has the deliberately-unreachable path AND post-cutoff date → drives `canary_drift_detected: true` deterministically.
- *Error path*: canary fixture is present in tree but its path resolves on origin/main (e.g., someone created `src/this-file-does-not-exist.py`) → `canary_drift_detected: false` → driver exits 1 → workflow fails (operator must fix the canary).
- *Error path*: driver itself errors (filesystem, plan-check binary missing) → exit non-zero → workflow fails.
- *Edge case*: zero post-cutoff plans (day 1 reality) + canary → summary `{real_drift_count: 0, canary_drift_detected: true}`. Auto-close path; SC1 sees no issue (correct: no plan to drift).
- *Edge case*: a plan-doc with malformed frontmatter that `plan-check` returns exit 2 on → driver classifies it as drift-of-kind-schema (separate from path/sha drift) in the body, increments `real_drift_count`.

*Workflow YAML smoke (operational, post-merge):*
- First scheduled run after merge: zero real drift, canary surfaces → auto-close path runs (no open issues to close on day 1; no-op). Workflow exits 0.
- Inject-drift rehearsal in scratch branch: add a plan-doc with deliberately-broken `claims.paths`; `workflow_dispatch` from that branch → issue created with today's date in title, label `plan-claims-radar`.
- Recovery rehearsal: fix the inject-drift plan, re-dispatch → driver detects only canary drift → issue auto-closes with comment "auto-closed: no drift detected".
- Idempotency rehearsal: two `workflow_dispatch` invocations 30s apart on same drift day → second run finds today's title already present, edits body in place (no duplicate).

**Verification:**
- After merge, the next 09:00 UTC cron run completes within ~3 minutes (single-job, base install) and exits 0.
- `gh run list --workflow=plan-claims-radar.yml --limit 5` shows recent successful runs.
- `pytest tests/test_plan_claims_radar.py` passes (driver is unit-tested independently of the workflow).
- SC1 measurement (revised per D19): count unique `plan-claims drift radar:` issues opened in 30 days where body lists at least one non-canary plan. Falsifiable because per-day issues + auto-close means count = real drift events count. Liveness: every successful run must have `canary_drift_detected: true` in summary — radar broken if not.
- Cron drift correctness: spot-check on day 2-3 that the radar surfaces a manually-introduced drift (introduce in a scratch PR, `workflow_dispatch` the radar on its branch to validate the find-and-file path).

## System-Wide Impact

- **Interaction graph:** `pyproject.toml` (deps + scripts), `.github/workflows/` (TWO new workflows alongside `ci.yml`: `plan-claims-gate.yml` PR gate + `plan-claims-radar.yml` nightly), `docs/plans/*.md` author convention, GitHub Issues (radar files rolling issue). No runtime publishing path affected. No CLI pipeline (`plan-backlinks | validate | publish`) changes.
- **Error propagation:** `plan-check` failures stop at the gate; no downstream side effects. Local CLI errors print to stderr and exit; CI errors fail the job and stop the merge. Operators see the same human-readable table in both surfaces (R13).
- **State lifecycle risks:** none — `plan-check` is stateless. The only mutation is an opportunistic `git fetch origin main`. No cache, no persistent state, no temp files.
- **API surface parity:** new console-script `plan-check` joins 7 existing entries in `pyproject.toml [project.scripts]`. Adopting the same `main(argv=None) -> None` shape means it composes the same way (callable in-process from tests, runnable as `python -m backlink_publisher.cli.plan_check`, and as `plan-check ...`).
- **Integration coverage:** Unit 2 and Unit 3 each carry one explicit integration test against a tmp git repo to prove path/sha resolution + CLI dispatch work end-to-end. Unit 4's workflow is verified by being run on the introducing PR (dogfood).
- **Unchanged invariants:** the 0-6 exit-code contract in `cli/*.py` is *extended* (7, 8) not violated. `_util/errors.py` exit-code mapping unchanged. Existing test isolation conftest fixtures (4 autouse) unchanged. No legacy import path changes. No monolith budget impact (new file, no growth on the budgeted 6).

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| CI false positive due to stale `origin/main` in shallow checkout | `fetch-depth: 0` + explicit `git fetch origin main` step (D8). Belt+suspenders. |
| New plan-doc author forgets `claims:` block on day 1 of cutoff | Exit 8 message is self-describing (`"date 2026-05-20 is post-cutoff; add a claims: block (use claims: {} to opt out)"`). AGENTS.md section provides copy-paste template. |
| Required-check gating breaks merges on first false positive | Ship as non-required (D9); promote to required after 14 days clean. Matches Phase 0 ship-seal pattern. |
| `git fetch` itself fails offline → operator can't run `plan-check` locally | Resolved by D16: skip-with-WARN + exit 0, always emit `fetch_head_age_seconds`. CI gate is the backstop. |
| Multi-worktree `git fetch` race (19 active `bp-*/` worktrees share one `.git/`; two concurrent `plan-check` invocations could race on `FETCH_HEAD` write) | Low likelihood; git fetch is mostly idempotent at the object level. The 300s staleness threshold also bounds concurrent fetch attempts. If observed, add `flock` around `_maybe_fetch_origin_main` — not in v1. |
| Grandfather + R15 interaction: when an implementing PR re-resolves `claims:` and flips `status: active → shipped`, does it bump `date:` past the cutoff? | **Resolved**: NO bump. `date:` is set at original write time and never moved. Plan-docs authored before 2026-05-20 stay grandfathered through their ship cycle. R15 discipline updates `status` and `claims`, not `date`. New plan-docs authored on 2026-05-20+ must include `claims:`. Document this rule in `AGENTS.md` (Unit 5). |
| CI's own `git fetch` could fail transiently (D16 makes the gate exit 0 on skip — could let drift through under network flake) | Acceptable in v1: the 14-day soak period (D9) is exactly where this would surface. If observed, add `--strict-fetch` flag and pass it in CI to fail on fetch failure. Not implemented in v1; tracked as documented follow-up. |
| Stack-PR sharp edge becomes more expensive once gate is required (forces `close && reopen` on every plan-doc-touching stack PR) | Track during 14-day soak: count `close && reopen` workarounds as a kill-switch metric alongside false-positive count. If stack-PR friction dominates, defer required-check promotion past 14 days or invest in a fix. |
| `pyyaml` adds transitive dep weight to base install | Single small wheel, widely vetted; user-confirmed in planning. Follow-up PR can clean up `phase0/validation.py` defensive import. |
| `argparse` exits 2 for invalid arg before our code runs; user sees mixed exit-code semantics | Mitigated by D4: no `choices=`, all validation post-parse, raise `UsageError` (exit 1). Exit 2 only when our schema-error path runs intentionally. |
| Stack-PR sharp edge (`pull_request: branches: [main]` doesn't fire on non-main bases) | Same as existing `ci.yml`; documented escape hatch is `gh pr close && gh pr reopen`. Don't try to fix in v1. |

## Documentation / Operational Notes

- New section "Plan-doc claims contract" in `backlink-publisher/AGENTS.md` (Unit 5). Cover both the PR gate (R12-R14) AND the nightly radar (R16-R17) so operators know where signal can surface.
- Promote `fetch-depth: 0 + merge-base` learning to `docs/solutions/best-practices/` after the gate has caught its first drift incident (SC1 measurement window — 30 days). Not in scope for this plan; tracked as follow-up.
- Promote "GHA-native issue filing pattern" to `docs/solutions/best-practices/` after the radar's first issue files cleanly. First GHA issue-filer in this repo (per repo-research-analyst); the pattern is reusable for future schedule-jobs.
- Required-check toggle: 14 days after merge, the operator with admin rights (org-level branch protection) adds `plan-claims-gate / check` to the required checks list for `main`. The radar is **NEVER** a required check — it's informational only. Reminder: Phase 0 ship-seal `reference_phase0_remote_routines` pattern.
- No new env vars introduced.

## Session Log

- 2026-05-19 (Session A, parallel): Initial plan written + deepened in same session. 5 units, D1-D16 decisions, 0/2/7/8 exit codes, FETCH_HEAD mtime staleness, offline skip-with-WARN.
- 2026-05-19 (Session B, this session): Reconciled with brainstorm-review additions (R11b filename-date lock, R16-R17 nightly radar, reframed SC1/SC3/SC4). Added D17-D19 + Unit 6. Kept D3/D5/--json (plan's grounding stronger than brainstorm's review simplification critique).
- 2026-05-19 (Session B, third pass — post document-review on plan-doc): Fixed 3 P0 findings. **(P0-1) Unit 1 ordering**: filename-date lock now runs FIRST, before grandfather check — without this, backdated plans escape the gate via grandfather route. **(P0-2) D19 + SC1 falsifiability**: replaced rolling-issue + manual-close model with per-drift-day issues + auto-close-on-clean + positive-control canary fixture (`tests/fixtures/radar-canary-drifted-plan.md`). SC1 reframed: count unique non-canary drift issues. Liveness signal: canary must surface every run. **(P0-3) Exit code 9 for stale-pass**: D16's offline-skip now emits exit 9 (not 0); consumers can distinguish "claims-clear" from "couldn't-check"; CI gate uses `--strict-fetch` to upgrade exit 9 → 1. D3 + Unit 3 dispatch table updated. Unit 6 also gained `scripts/plan_claims_radar.py` driver module + `tests/test_plan_claims_radar.py` pytest coverage (feasibility-F2 fix — the "small Python one-liner" was actually 60 LOC).

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-19-plan-doc-claims-and-head-drift-gate-requirements.md`
- Code patterns: `src/backlink_publisher/cli/report_anchors.py`, `src/backlink_publisher/cli/footprint.py`, `src/backlink_publisher/_util/errors.py`, `src/backlink_publisher/_util/cli_flags.py`, `src/backlink_publisher/phase0/validation.py`, `.github/workflows/ci.yml`, `tests/test_cli_footprint.py`
- Memory feedback: `feedback_argparse_choices_vs_usage_error_exit_code_clash`, `feedback_check_upstream_refactor_before_fixing_stale_branch`, `feedback_validate_main_before_planning_off_feat_branch`, `feedback_verify_repo_state_before_planning`, `feedback_scan_parallel_prs_before_blocker`
- Memory reference: `reference_footprint_gate_design`, `reference_ci_workflow_pr_filter`, `reference_phase0_remote_routines`
- Solutions: `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md`, `docs/solutions/best-practices/plan-time-url-validation-prevents-publish-404-2026-05-15.md`, `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md`
- Related PRs: #57 (footprint gate — design template)
