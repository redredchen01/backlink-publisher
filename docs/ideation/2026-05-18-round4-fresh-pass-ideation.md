---
date: 2026-05-18
topic: round4-fresh-pass
focus: open-ended (fourth pass; explicit exclusion of R1+R2+R3 survivors and 64 documented rejections)
---

# Ideation: Backlink-Publisher — Round 4 Fresh Pass (2026-05-18)

Fourth round of open-ended ideation. Builds on Rounds 1-3 (`2026-05-12`, two 2026-05-14 docs). This round explicitly **excludes 19 prior survivors (12 shipped + 5 in-flight + 2 demoted-not-killed) and 64 prior rejections — 83 explored ideas in total** — and pushes for ideas that compound on the shipped substrate, address the operator's bad day, embed institutional knowledge as runnable checks, or surface cross-module synergies.

## Codebase Context

### Project shape
- Python 3.11+ CLI pipeline: `plan-backlinks → validate-backlinks → publish-backlinks → footprint → report-anchors`, JSONL-pipeable, cron-safe, local-first.
- Two consumer surfaces: CLI entry points + `webui.py` shim → `webui_app/` package (active P0 split refactor — webui.py 4904 lines, mid-migration).
- Domain core: anchor system (`anchor_lang/metrics/profile/resolver/scheduler` — the moat), `content_fetch`, `linkcheck`, `language_check`, `footprint`, `checkpoint`, `verify_publish`, `markdown_utils`, `pipeline_logger`.
- Adapters: Blogger API, Medium API + Brave + Browser (Playwright). `retry_transient` decorator.
- TOML config + `.config-history/` snapshots; `config_echo` 4-line banner + canonical SHA stamped into every artifact's `metadata.config_sha`.

### Frame & guardrails for this round
- **Hard constraints baked into every ideation prompt**: no runtime LLM, no `webui.py` retrofit (active P0 refactor — `webui_app/` sibling pattern only), no `config.py` decomposition this window (P1 queued), no ToS violation, no footprint emission, solo-operator scale, cron-safe / non-interactive.
- **12 saturated themes excluded**: retry/OAuth, checkpoint/resume, config safety/wizard/echo, anchor distribution defense, post-publish verification, adapter expansion, pre-publish gates, logging/observability dashboards, selector drift, footprint emission defense, velocity limiting, campaign/profile primitives.
- **Four divergent frames**, deliberately distinct from Round 3's coverage:
  - A: force-multipliers on what already shipped (compound on substrate that didn't exist 30 days ago)
  - B: operator's bad day (graceful failure & 2am recovery, vs Round 3's "extreme cron-safe" which was prevention)
  - C: knowledge compounding from `docs/solutions/` corpus (turn prose recipes into deterministic runners)
  - D: cross-module synergy (publish↔verify, anchor↔log, fetch↔check pairs neither owner would propose)
- **Synthesis combos** by orchestrator after dedup: **S2 Footprint Loop Closure** (A2+D6), **S3 Pre-Commit Knowledge Embedding** (C2+C5+C7+C8).
- **Adversarial filter**: explicit "subsumed by R1/R2/R3" check, explicit "shell-script-in-disguise" check, explicit footprint-emission check, explicit LLM-free check, explicit ToS check.

### Past learnings consulted
- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — tautological-assertion class bigger than 2 incidents; audit recipe never run repo-wide.
- `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` — prevention rules #3 + #5 still un-automated.
- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — removed-tool subprocess pattern + sync subprocess in Flask.
- `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md` — platform-conditional test audit + CI-flag vs `[dev]`-extras drift.
- `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md` — plan-time checks only run inside `/ce:plan`.

## Ranked Ideas

### 1. Footprint Loop Closure (S2: A2 Regression Gate + D6 Renderer Variance)

**Description:** Two-arm wiring that converts shipped `bp footprint` from advisory tool into a feedback loop.

- **Arm A (CI regression gate):** New pytest fixture runs `footprint.analyze_corpus` against a frozen N=50 sample of `work_themed_generator` payloads on every PR. Fails the build if any byte-level dimension's `concentration_pct` moves >5pp vs the committed `tests/baselines/footprint_concentration.json` or crosses the 95% alarm line.
- **Arm B (renderer self-vary):** Footprint's "this attribute order/whitespace/`rel` spelling appears in 100% of outputs" findings get persisted to `~/.cache/backlink-publisher/footprint-invariants.json`. `markdown_utils` reads that file at render time and uses a seeded shuffle keyed off `(destination, anchor, row_id)` to rotate the flagged tokens — attribute order, `rel="noopener noreferrer"` vs `rel="noreferrer noopener"`, surrounding whitespace.

**Rationale:** R3-3 shipped `bp footprint` as a one-shot audit the operator runs manually — meaning it never runs. Arm A converts the audit into a permanent non-regression contract that survives any future renderer refactor. Arm B makes the auditor *teach the renderer* what to vary; cluster-key surface area shrinks over time without operator hand-tuning. The engine is already pure-functional, so both arms are wiring not invention. This is the highest-leverage compound on R3-3 — and it's the only Round-4 candidate where two frames (force-multiplier + cross-module) converged on the same target independently.

**Downsides:** Arm A's baseline file needs intentional updates on legitimate renderer changes → adds a "regenerate baseline" PR ritual. Arm B's seeded variance must remain deterministic (same `(dest, anchor, row_id)` → same output) so re-publishes don't churn — easy to get wrong if `row_id` derivation changes across runs. Both arms must be inert if `[footprint]` config is disabled.

**Confidence:** 90%
**Complexity:** Medium (Arm A ~80 LOC + baseline tooling; Arm B ~150 LOC + invariant-file contract + tests)
**Status:** Explored (brainstorm started 2026-05-18)

### 2. Config-History Bisect (`bp config bisect <broken_artifact_sha>`)

**Description:** Walk `.config-history/YYYYMMDD-HHMMSS.toml` snapshots backward, replay `config_echo.compute_config_sha` on each, and surface the exact snapshot pair whose canonical-SHA boundary brackets the bad artifact's `metadata.config_sha`. Output: unified diff of the two TOMLs + the timestamp range of suspicion. Operator question "which config edit broke verification on May 10?" becomes one command.

**Rationale:** R3-1 (Config Safety Net) ships `.config-history/` snapshots but only as forensic raw material — nobody reads them. R3-7 (Config Echo) stamps a canonical SHA into every artifact. Together they form `git bisect`-style root-causing for config-driven regressions — but no command wires them together. The substrate is <30 days old; this capability didn't exist before R3 shipped. Zero new persistence, pure read-side aggregation.

**Downsides:** SHA canonicalization between releases must stay stable, else historical snapshots show false drift. Snapshot rotation policy (rolling N=20) caps lookback — operator can't bisect beyond that window. If the breaking edit is in a section that `save_config` deliberately doesn't round-trip (`[sites.*]`, `[anchor.proportions]`, `[anchor_alarm]`, `[llm.anchor_provider]`), the snapshot won't capture it and bisect blame-points the wrong row.

**Confidence:** 88%
**Complexity:** Low (~120 LOC; pure file-walk + replay + unified diff)
**Status:** Unexplored

### 3. Pre-Commit Knowledge Embedding Bundle (S3: C2 + C5 + C7 + C8)

**Description:** A single `tools/precommit/` directory containing four small deterministic runners drawn directly from prevention recipes that today live as prose in `docs/solutions/`:

- **C2 (`detect_tautological_gates.py`):** AST scanner that walks every function in `src/backlink_publisher/**` whose return type is `bool` and reports those where every reachable return path produces a constant `True`/`False` literal — the `language_matches` failure shape.
- **C5 (`comment_contract_linker.py`):** Greps src/ for softening phrases (`allow flexibility`, `for now`, `be lenient`, `TODO sharpen`, `deliberately`, `intentionally`) and requires either a sibling test docstring referencing the file:line, or an explicit `# CONTRACT-EXEMPT: <reason>` marker.
- **C7 (`ci_flag_dependency_coverage.py`):** Parses `.github/workflows/ci.yml`, extracts every `pytest` flag, asserts each resolves to a plugin discoverable from `[project.optional-dependencies].dev` in `pyproject.toml`.
- **C8 (`audit_dead_subprocess_calls.py`):** Maintains `tools/known_removed_binaries.yml` (starts with `opencli`) and AST-walks every `subprocess.run([...])` / `Popen([...])` / `shell=True` string-literal call across `src/`, `webui.py`, `webui_app/`. Fails on any reference to a registered-removed binary.

Wire all four into `.pre-commit-config.yaml` + a weekly CI sweep.

**Rationale:** The learnings agent flagged that none of the `docs/solutions/` prevention recipes are automated — they all read like "grep for X, then ask Y", which is exactly the shape that decays to zero use. This bundle converts four of them simultaneously, in one bounded PR, into deterministic runners. Each is small (50-150 LOC), each addresses a *recurring* failure class (not a single incident), and together they form the seed of a project-local prevention plugin set. The pattern itself is the most important deliverable: future solutions docs add a 50-LOC runner under `tools/precommit/` instead of a paragraph of grep recipes.

**Downsides:** Pre-commit hooks slow `git commit`; over-eager rules become bypass-with-`--no-verify` muscle memory. C5's `# CONTRACT-EXEMPT:` escape hatch can become a junk drawer if not policed. C8's `known_removed_binaries.yml` needs to be appended on every dependency-removal PR or it goes stale. C2 may have false positives on functions whose `bool` return is intentionally constant (boolean feature flags); needs an opt-out decorator or path-exclusion list. Bundle complexity > sum of parts if any one fights the others.

**Confidence:** 82%
**Complexity:** Medium (one PR with 4 runners + pre-commit config + 4 small test files; ~500 LOC total)
**Status:** Unexplored

### 4. Reconciliation-Driven Cron Exit Codes (`--strict-recon`)

**Description:** The pipeline already emits structured `RECON plan_reconciliation` / `validate_reconciliation` / `publish_reconciliation` lines via `PipelineLogger.recon()`. Add a `--strict-recon` flag (defaults on for non-TTY) that converts any non-zero `dropped_at.*` count into a non-zero exit code, with **per-stage exit-code namespacing**: e.g., `40` = silent-drop in `plan_backlinks`, `42` = silent-drop in `language_check`, `43` = adapter-side drop, `44` = config-drift refusal (idea #5). Cron and `launchd` get an actionable signal instead of always-zero exits.

**Rationale:** R3-4 (Silent-Drop Tripwire) ships UUID per plan row + reconciliation lines, but the data lives in stderr JSONL — cron can't grep it, can only see exit codes. This is the missing one-line bridge between visibility and automation. Without the tripwire substrate this would have been guesswork; with it, the mapping is mechanical. Tiny diff, large blast radius: any cron / launchd / GitHub Actions workflow watching the exit code automatically gains stage-resolution failure signal.

**Downsides:** Backward-incompatible: any existing cron entry that relies on the always-zero contract gets new alarms. Exit-code namespace policing — once `42` means "language_check drop", it must never mean anything else; documentation + a test enforcing the mapping table is mandatory. Non-TTY default may bite interactive `--dry-run` calls in pipes; needs careful detection. Should not conflict with Python's reserved exit codes (1, 2, signal-derived).

**Confidence:** 86%
**Complexity:** Low (~60 LOC + an exit-code table + a regression test)
**Status:** Unexplored

### 5. Pre-Publish Config-Drift Refusal in `publish-backlinks`

**Description:** When `publish-backlinks` ingests a plan JSONL from stdin, compare each row's `metadata.config_sha` against the **current resolved config's SHA**. If they differ, refuse to publish that row by default (recon line `dropped_at.config_drift += 1`, exit code 44 per idea #4 if any row drops). Require `--accept-config-drift` flag for explicit override. Mid-batch drift detection: if config SHA changes between rows of the same run (config edited mid-publish), abort the remainder with a `RECON config_drift_mid_run` event.

**Rationale:** R3-7 (Config Echo) put the SHA into `metadata.config_sha` for forensic reverse-mapping. This idea repurposes it as a *forward-safety gate*. Plan and publish are often hours/days apart for solo operators batching weekly — "I planned with the old `[targets.*]` list and published with the new one" is an entire incident class. ~20 LOC defending against it. Plus the mid-batch case (operator edits config while a long publish is running) — currently a silent footgun.

**Downsides:** Operator who legitimately edits config between plan and publish hits a stop sign; the `--accept-config-drift` ergonomics need to be discoverable, else they'll be muttered about. SHA canonicalization sensitivity: any non-semantic change (TOML reformatting on disk) trips drift; must hash the canonical form, not source bytes. If `metadata.config_sha` is missing from older plan rows (pre-R3-7 plans), need a `--allow-unstamped` fallback for transition period.

**Confidence:** 88%
**Complexity:** Low (~30 LOC + the recon plumbing from idea #4)
**Status:** Unexplored

### 6. Soft-Kill Sentinel (`STOP` file)

**Description:** Before each per-item publish, the dispatcher `stat()`s a `STOP` file at `~/.cache/backlink-publisher/STOP`. If present, the remaining items in the batch are marked `skipped_soft_kill` (recon line `dropped_at.soft_kill += 1`), the run exits with a clean code (cron-safe), and `STOP`'s `mtime` is recorded in the event log. Operator drops the file from a phone SSH session — no signal-handling fragility, no need to find the right PID, works even mid-retry. Optional companion: `bp stop` / `bp resume` CLI for the same effect from the same machine.

**Rationale:** Round-3 explicitly rejected the "in-process circuit breaker" (D7) because in-process state is useless for cron-spawned children. This sentinel sidesteps that critique — disk-based state survives process boundaries by design. The recovery story is concrete: 2am, targeting bug ships, operator on phone, one-line `ssh vps 'touch ~/.cache/backlink-publisher/STOP'` halts everything *that hasn't been committed yet* without corrupting anything in-flight. Pairs naturally with R3-4 Tripwire reconciliation since soft-kills become a first-class drop reason.

**Downsides:** No way to scope the kill — affects every run, every target, until cleared. Operator must remember to `rm` the file before the next legitimate run (could be solved with a `--ttl` flag or `STOP-until-<timestamp>` filename variant). Disk-based polling adds a `stat()` per item which is negligible. Race condition: file dropped *after* the per-item check completes but *before* the API call still sends; documented as expected behavior, not a bug.

**Confidence:** 84%
**Complexity:** Low (~40 LOC + recon plumbing + one test)
**Status:** Unexplored

### 7. Config-SHA Run Cohort Diff (`bp runs cohort`)

**Description:** Every JSONL artifact already carries `metadata.config_sha` (16-char prefix stamped by `config_echo.compute_config_sha`). Add a CLI that scans `~/.cache/backlink-publisher/` JSONL outputs + checkpoint files, groups runs by `config_sha`, and produces a side-by-side delta table — verification pass-rate, breach counts (per-target Shannon entropy / exact-match ratio / top-3 concentration), drop reconciliation, adapter success-rate — bucketed by config cohort. Sub-flag `--baseline <sha>` to anchor the comparison; `--since <duration>` to scope the window. Output is operator-readable table + `--json` for piping.

**Rationale:** First time the operator can do controlled A/B on a config edit without spreadsheets. The substrate (deterministic SHA over canonicalized config + RECON-tagged structured logs + anchor metrics + verifier output) makes this purely a `glob + groupby + aggregate` script — but no individual shipped feature owner would build it because the value emerges from the *combination*. Question "did the config I changed three weeks ago actually move any KPI?" becomes one command instead of a half-day of jq-ing.

**Downsides:** Output design matters more than the implementation — a bad table is worse than no table. Comparing cohorts of unequal sample size needs care (don't put `n=2` next to `n=200` without flagging). Requires that `metadata.config_sha` stays canonical-stable over time; rolling out a SHA algorithm change would invalidate historical buckets. Pure read-side, but disk-walk over `~/.cache/` could be slow on large histories — needs an index or a `--limit` cap.

**Confidence:** 80%
**Complexity:** Medium (~200 LOC for aggregation + table renderer + tests)
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| A4 | Verification-Failure Replay Corpus (`bp verify replay`) | Post-publish verification saturated; capability folds naturally into in-flight R2-2 Health Monitor as a recovery loop |
| A6 | Per-Adapter Reliability Card (cold-read) | Shell-script-in-disguise (R3-C2 reject pattern); `jq` + 30 lines of awk delivers same value without subcommand |
| A7 | Anchor-Entropy Forecast Hook for Scheduler | Anchor-distribution defense theme saturated; likely already in R3-2 brainstorm's follow-up scope |
| B1 | Quarantine Lane (per-pair circuit) | Close to R3-D7 reject (in-process circuit breaker); even with disk persistence, cooldown UX adds operator friction without clear win over R2-4 Velocity Governor |
| B2 | Last-Known-Good Snapshot Bundle | Overlaps Idea #7 cohort diff; the "what did green look like" question is the cohort baseline |
| B3 | `bp incident bundle` Tarball | Useful but mundane packaging utility; not novel enough to clear the bar against #1-#7 |
| B4 | Pre-Publish Auth Probe + Negative Cache | R1-2 OAuth pre-flight already does this; marginal delta |
| B5 | Anchor Drift Replay Tool | Subsumed by R3-2 + future cohort diff (#7); replay data already exists |
| B7 | Publish Verdict Triage Index (`bp triage`) | Same shape as R2-A4 rejected "error catalog + bp explain" ("solo-operator ceremony") |
| B8 | Per-Target Rate Pause on Verifier Anomaly | Close to R3-D7 (rejected) and R2-4 Velocity Governor (in-flight); decision-policy ambiguity ("when do I clear?") makes this fragile |
| C1 | Inverted-Assertion Stress Mutator (pytest plugin) | Most ambitious of the C-set but mutation-testing infra is heavy; defer until S3 Pre-Commit Bundle ships and proves value |
| C3 | PipelineLogger API Surface Self-Test | Single-method check; not worth its own runner — subsumed naturally by C2 once AST scanner is in place |
| C4 | Adapter Fallback-Chain Mock Witness (conftest) | Narrow — Medium-specific; pattern doesn't generalize until a second adapter triplet exists |
| C6 | Plan-File Path-Claim Auditor (pre-commit) | Useful but per-document — better implemented as part of `document-review` skill, not a project-local hook |
| D1 | Footprint-Probe Adapter Gate (inline) | Subsumed by Idea #1 Arm A (CI gate); doing it inline adds latency and a webui-async-architecture dependency not in scope |
| D2 | Verifier-Sourced Anchor Reputation | Cross-module synergy is real but implementation cost high (new field, scheduler change, DOM-context heuristics); speculative without an incident class |
| D3 | Content-Fetch as Language-Check Oracle | Clever but speculative; no incident class motivates "register drift" today |
| D4 | Linkcheck-Driven Anchor Resolver Quarantine | Marginal — operator already sees linkcheck breakage in current output; saturated anchor theme |
| D5 | Checkpoint-as-Event-Replay (`bp resume --from-log`) | Depends on R2-3 Event Log substrate which is still in-flight; timing risk; better as follow-up after R2-3 lands |
| D7 | Work-Scraper-Informed Anchor Lang Detection | Narrow accuracy win on short anchors; sub-agent self-rated boldness 2 |
| D8 | Config-Check + Footprint Pre-Flight Pair | Forced unification — config check is one-time, footprint is per-run; pattern detection idea swallowed by Idea #1 |

## Session Log
- 2026-05-18: Initial Round-4 ideation — 31 raw candidates generated across 4 frames (A force-multipliers, B operator's bad day, C knowledge compounding, D cross-module synergy), 21 dedupe-survived, 2 cross-frame combos synthesized (S2 Footprint Loop, S3 Pre-Commit Bundle), 7 survivors after rubric pass.
- 2026-05-18: Handing off Idea #1 (Footprint Loop Closure, S2 = A2 Regression Gate + D6 Renderer Variance) to `ce:brainstorm` for detailed scoping.
