# In-process global-state audit — thin-WebUI Phase 2 Unit 5

**Date:** 2026-05-27 · **Plan:** `docs/plans/2026-05-27-004-refactor-thin-webui-in-process-pipeline-plan.md` (R6, R7) · **Gates:** Unit 6/7 in-process migration.

## Why this audit exists

Three read-only CLIs — `validate-backlinks` (`cli/validate_backlinks.py`), `plan-backlinks` (`cli/plan_backlinks/core.py`), `report-anchors` (`cli/report_anchors.py`) — are about to run **in-process** inside the long-lived Flask process behind `PipelineAPI`. Today each runs as a fresh subprocess, so every call starts with pristine process state. In-process, a Flask **request thread** and the **`BackgroundScheduler` thread** (`webui_app/scheduler.py`) will share every module-level / process-global surface these CLIs touch.

This document enumerates each process-lifetime side effect with a decision: **reset-per-call** / **lock** / **documented-acceptable** / **not-a-hazard**. Line numbers verified against the tree at `e8d15f0`.

## Summary

| # | Surface | Real shared mutable? | Decision |
|---|---------|----------------------|----------|
| 1 | `config.load_config` memoization | **No** — no cache; fresh `Config` per call (`config/loader.py:127`) | not-a-hazard |
| 2 | `content.fetch` `_STATS` / `reset_stats()` | **Yes** (plan only) — `fetch.py:134`, zeroed at `core.py:323` | **reset-per-call / lock** |
| 2b | `content.fetch` `_CACHE` / `_DEFAULT_MAX_AGE_S` | Yes — `fetch.py:101,129`; 900s TTL set at WebUI startup | documented-acceptable |
| 3 | platform `registry` | Yes but import-once idempotent (`publishing/registry.py:116`) | not-a-hazard |
| 4 | `set_log_level` logger singletons | **Yes** (validate `:77` + plan `:229`) — `_util/logger.py:160-166` | **reset-per-call / lock** |
| 5 | `config_echo` banner | No sentinel; shared stderr interleave only (`config_echo.py:178`) | documented-acceptable |
| 6 | `_check_row_reachability` pool | Out of scope (publish only); no stdout (`_publish_helpers.py:93`) | not-a-hazard |
| 7 | validate config-fail tolerance | Fail-soft, intentional (`validate_backlinks.py:93-102`) | documented-acceptable |
| + | `anchor/profile._locks` | Yes, **already** lock-guarded (`anchor/profile.py:62`) | not-a-hazard |
| + | shared `sys.stdout` / `sys.stderr` | **Yes** (all three write JSONL to stdout) | **per-call capture** |
| + | env / signals / stdout-rebind | None found | not-a-hazard |

**Three surfaces actually bite under request+scheduler concurrency: #4, #2-`_STATS`, and shared stdout/stderr.** Everything else is non-existent, idempotent, already locked, or cosmetic.

## The three load-bearing hazards (Unit 6 must handle)

### H1 — `set_log_level` flips verbosity for ALL loggers, globally

`_util/logger.py:166` mutates `.level` on **all four** singleton loggers (`plan_logger`, `validate_logger`, `publish_logger`, `opencli_logger`) in a loop. `validate_backlinks.py:77` and `plan_backlinks/core.py:229` both call it from inside `main()`. In-process, a request-thread validate at `--log-level DEBUG` and a scheduler-thread plan at `WARN` race last-writer-wins, flipping verbosity for both runs **and** for the publish logger the scheduler is actively using. `_emit` reads `self.level` at log time, so the level can change underneath a running thread.

**Decision: reset-per-call (snapshot + restore).** The engine extracted in Unit 6 must NOT call `set_log_level` — it stays in the CLI *shell* (plan's Files note already says this). For the in-process `PipelineAPI` path, snapshot the four `.level` values, set the requested level, run, restore in a `finally`. Lowest-effort and correct. (A per-logger lock would serialize unrelated runs — reject.)

### H2 — `content.fetch.reset_stats()` zeroes shared `_STATS` mid-flight

`plan_backlinks/core.py:323` calls `content_fetch.reset_stats()` at plan start, then reads `content_fetch.stats_snapshot()` at `core.py:426` for the run report. `_STATS` (`fetch.py:134`) is a process-global dict; counter increments (`_STATS["cache_hits"] += 1`) are non-atomic read-modify-write. Two overlapping plan runs clobber each other's counters and the per-run `content_fetch_stats` report is interleaved/wrong.

**Decision: reset-per-call isolation.** Unit 6/7's plan engine must not rely on a process-global stats singleton for per-run reporting — give each invocation its own stats object/snapshot (capture-and-isolate), or accept process-aggregate stats and document it. The `reset_stats()` call is the load-bearing line. `_CACHE` itself (2b) stays shared: the 900s WebUI TTL (`webui._wire_content_fetch_ttl_from_env`) makes cross-run cache reuse **intended** daemon behavior — characterize the cross-run bleed but do not "fix" it.

### H3 — shared `sys.stdout` / `sys.stderr`

All three CLIs write structured JSONL **data** to `sys.stdout` (`validate write_jsonl:202`, report `print` `:94-135`, plan's writer) and diagnostics to `sys.stderr`. In-process, two threads sharing one `sys.stdout` would interleave JSONL and corrupt the data stream for any consumer.

**Decision: per-call capture.** The in-process harness must give each `PipelineAPI` call its own captured stdout/stderr buffer (redirect per-call), never the real shared fds. This is the architectural premise of the migration — a characterization test must pin it (two concurrent in-process calls produce non-interleaved output).

## Surfaces confirmed safe (no action)

- **`load_config` (1):** no module-level cache; `config/loader.py:127` reads disk + returns a fresh `Config` each call; config dir re-resolved from `BACKLINK_PUBLISHER_CONFIG_DIR` live. Nothing in scope writes `os.environ`, so per-call re-resolution is consistent across threads.
- **registry (3):** `publishing/registry.py:116` dicts are populated only by `register()` calls in the `adapters/__init__.py` module body — import-once, idempotent (module cache), import-lock-protected on first touch, read-only thereafter. *Best practice: trigger `import backlink_publisher.publishing.adapters` once at Flask app init so neither worker races the first import.*
- **config_echo banner (5):** no "already emitted" sentinel, no module-scope I/O; `emit_banner` resolves `sys.stderr` lazily and writes at call scope. Only hazard is cosmetic stderr interleaving (folds into H3's per-call capture).
- **`_check_row_reachability` (6):** only `publish-backlinks` / `_resume.py` use it; none of the three in-scope CLIs reach it, and it returns tuples (no stdout writes).
- **validate config-fail tolerance (7):** `validate_backlinks.py:93-102` swallows non-`InputValidationError` config-load failures (WARN + branded-pool fallback disabled), re-raises `InputValidationError` (cells fail-loud). In-process this means a bad `BACKLINK_PUBLISHER_CONFIG_DIR` degrades rather than crashes the Flask thread — intentional; the characterization corpus must include this branch.
- **`anchor/profile._locks` (`anchor/profile.py:62`):** genuine process-global, but already correctly guarded by `_locks_guard` + per-site lock. It is the model the other surfaces should follow; sharing it across in-process runs is correct.
- **Immutable-after-import constants:** `_MAX_CACHE_ENTRIES`, `_SSL_CTX`, `linkcheck/http.py` constants, `net_safety._SSRF_OPENER` (no `install_opener`) — read once at import, never reassigned at call scope.

## Characterization corpus (Unit 5 second artifact → `tests/test_pipeline_inprocess_characterization.py`)

Capture current **subprocess-path** behavior as the golden baseline Unit 6 asserts the in-process path against. Parity per R6 = typed result/error + byte-identical stdout **data** (banner-normalized stderr).

- Representative + error-path inputs per CLI, under the socket-block autouse conftest (URL checks pass by default):
  - `validate`: good payload (6 links + seo) → rows; malformed → `InputValidationError`/exit-2; **bad-config → fail-soft WARN + continue** (H? / surface 7).
  - `plan`: good seed → rows (`--no-check-urls` / `BACKLINK_NO_FETCH_VERIFY=1` to keep it network-free); error seed → typed error.
  - `report-anchors`: both structural paths — `--from-profile` (can exit-6 alarm) and stdin-aggregate (NOTE to stderr, no alarm); emits a markdown/JSON **document**, not JSONL rows.
- **Concurrency baseline:** two concurrent subprocess invocations + a scheduler-thread publish produce non-interleaved output **today** — this is the property Unit 6 must preserve in-process (asserts H3).
