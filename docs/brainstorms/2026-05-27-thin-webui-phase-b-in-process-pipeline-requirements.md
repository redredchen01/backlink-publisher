---
date: 2026-05-27
topic: thin-webui-phase-b-in-process-pipeline
---

# Thin WebUI — Phase B: In-Process Pipeline Execution

## Problem Frame

The WebUI invokes the data-pipeline CLIs through a subprocess bridge:
`PipelineAPI` → `run_pipe(cmd, stdin)` → `subprocess.run([sys.executable, '-m', <module>, ...])`,
exchanging JSONL over stdin/stdout and diagnostics over stderr. `pipeline_api.py`
states this in its own docstring: *"Phase A: still delegates to `run_pipe`
(subprocess). Phase B will replace the subprocess bridge with in-process
`main(argv)` calls."* Phase B is the unfinished step.

The subprocess boundary carries ongoing cost:
- **Per-call process spawn** for every plan/validate/publish, plus the
  scheduler-thread `publish-backlinks` runs (`scheduler.py:59`).
- **Stringly-typed contract** — results cross as stdout text + stderr text +
  exit code. The `stderr[:200]` truncation has repeatedly hidden the real
  error behind the config-echo banner (see `strip_cli_diagnostic_banner`,
  built specifically to work around this).
- **No shared validation path** — CLI and WebUI cannot *provably* run the same
  contract because they only meet across a process boundary.

The architecture this refactor names ("thin WebUI / services / adapters /
schema-first") is already ~70% adopted across three prior plans: the `api/`,
`services/`, and `helpers/` layers exist; platform branching in routes is down
to two spots (`batch.py` blogger, `pipeline.py` velog); credential save already
dispatches by registry `auth_type`. **Phase B is the load-bearing remainder.**

**Sequencing decision (reversed after review):** the north star is still
in-process execution (the original "Phase B"), but the schema-first typed-error
contract goes **first**. It runs over the *existing* subprocess transport, so it
carries near-zero process-model risk while delivering the actual operator-facing
win — killing the `stderr[:200]` truncation pain now instead of deferring it.
The in-process transport migration goes second, and by then the typed contract
makes its "no behavior change" target far more robust (parity is defined by the
typed result/error object, not by byte-identical free-text stderr).

## Architecture (target boundary)

Current state is NOT a clean single seam (reviewers corrected the draft):
`PipelineAPI` today exposes only `plan()` / `validate()` / `publish()`.
`report-anchors` is invoked by `services/seo_viz.py` via a **raw `subprocess.run`**
(bypassing `run_pipe` *and* `PipelineAPI`). `footprint` has **no WebUI caller at
all**. `equity-ledger` **already runs in-process** (`routes/equity_ledger.py`
calls `build_ledger()` directly — no subprocess to remove). So the migration is
partly "funnel the leaks" and partly greenfield seam surface.

```
  Phase 1 (first): schema-first typed-error contract — subprocess UNCHANGED
  ─────────────────────────────────────────────────────────────────────────
  CLI emits structured/typed errors  ──►  PipelineAPI deserializes to
  (not free-text stderr)                   typed PipeResult.error
                                           (no more stderr[:200] slicing)

  Phase 2 (second): in-process transport behind PipelineAPI (the intended seam)
  ─────────────────────────────────────────────────────────────────────────
        │ go in-process                          │ stay subprocess
        ▼                                         ▼
  validate-backlinks (read-only — pilot)    publish-backlinks  ← KEEP subprocess
  plan-backlinks                              (0600 creds + side-effecting
  report-anchors  (today raw subprocess        network publish; SSRF/crash/
        │          in seo_viz.py — add to seam) credential isolation. Revisit
  equity-ledger   (already in-process —         only after per-call SSRF opener
        │          parity only)                 + thread-safe cred lock proven)
  [footprint]     EXCLUDED (no WebUI caller;  medium_login / velog_login
        │          PYTHONHASHSEED startup-only) bind_channel / frw_login
        ▼                                       └─ drive real browsers; keep
   stdout data byte-identical;                     process isolation
   parity defined by typed result/error
```

Routes never learn which path a call takes — `PipelineAPI` hides it.

## Requirements

**Phase 1 — Schema-First Error & Result Contract (subprocess unchanged; ships first; user-visible)**
- R1. Define a single result/error contract shared by the CLI and the WebUI.
  CLI entrypoints emit **structured, typed errors** (a machine-readable error
  object, not free-text stderr); `PipelineAPI` deserializes them into a typed
  `PipeResult.error` instead of slicing `stderr[:200]`. After R1, the operator
  sees the real error (`AuthExpiredError`, `ContentRejectedError`, etc.), not a
  banner-truncated preview. This runs over the **existing subprocess transport**
  — no process-model change, low risk.
- R2. CLI and WebUI **provably validate against one contract** — the same
  schema module gates both the CLI's emitted result/error and the WebUI's
  consumption. This phase is permitted to change error format (that is the
  point); stdout *data* JSONL stays as-is for shell/CI consumers.

**Phase 2 — In-Process Transport (the original "Phase B"; behavior-neutral; second)**
- R3. *(B0 — must fully complete before any in-process migration begins.)* Every
  caller of an in-scope CLI funnels through `PipelineAPI` — both `run_pipe`
  importers (`scheduler.py`, `routes/pipeline.py`, `routes/sites.py`,
  `api/pipeline_api.py`, `helpers/cli_runner.py`) **and direct `subprocess.run`
  callers** (`services/seo_viz.py` invokes `report-anchors` raw — a `run_pipe`
  grep misses it). `PipelineAPI` must also grow `report-anchors` (and other
  in-scope) methods — today it exposes only `plan()`/`validate()`/`publish()`,
  so this is partly greenfield seam surface, not pure funneling.
- R4. In-process scope (read-only / low-risk only): `validate-backlinks` (pilot),
  `plan-backlinks`, `report-anchors`. `equity-ledger` is **already in-process**
  (`build_ledger()`) — parity verification only. `footprint` is **excluded** (no
  WebUI caller; `PYTHONHASHSEED` is startup-only — see Scope Boundaries).
- R5. `publish-backlinks` **stays subprocess** in Phase 2. It is the only
  in-scope CLI that writes 0600 credential files and performs side-effecting
  network publishing; subprocess gives it SSRF containment, crash isolation, and
  credential-write isolation, while its spawn-latency payoff is the lowest
  (already network-bound). Revisit only after the security-isolation guarantees
  (per-call SSRF opener, thread-safe credential lock, per-call timeouts,
  crash-containment) are proven — see Outstanding Questions.
- R6. Browser-driving login CLIs (`medium_login`, `velog_login`, `bind_channel`,
  `frw_login`) stay subprocess — process isolation for browser lifecycles is a
  deliberate keep. The same isolation reasoning is what motivates R5.
- R7. Behavior-neutral parity for the in-process CLIs is defined by the **typed
  result/error contract from Phase 1** (more robust than byte-identical
  free-text stderr) plus byte-identical stdout *data*. Raw stderr already
  differs across calls today (publish once-per-machine gate banner via
  filesystem sentinel; `config_echo` recomputes per call), so any residual
  stderr comparison normalizes via `strip_cli_diagnostic_banner`. The
  `SystemExit`→`PipeResult` mapping mirrors CPython (int→that code, `None`→0,
  **string→1 with the string on stderr** — `equity_ledger.py` raises
  `SystemExit(<string>)`); the golden corpus includes error-path inputs.
- R8. Concurrent execution stays correct. The real hazard is **not**
  `routes/pipeline.py`'s `ThreadPoolExecutor(max_workers=3)` (that wraps only
  `fetch_url_metadata`) — it is the **`BackgroundScheduler` thread**
  (`scheduler.py:59`) racing **threaded Flask request threads** over
  process-global `sys.stdout`/`sys.stdin` and **shared mutable module state**
  (`set_log_level` singletons in `_util/logger.py`, the `content.fetch` cache,
  `config.load_config` memoization). CLIs also spawn their **own internal
  `ThreadPoolExecutor`s** (`_publish_helpers._check_row_reachability`), so per-call
  `redirect_stdout` won't reach CLI-spawned threads — a **pure-return core that
  never touches `sys.stdout` is likely mandatory**. This global-state audit must
  run **before** any byte-identical stdout lock is meaningful.

## Success Criteria

- **Phase 1 (ships first):** an operator who hits a pipeline failure in the
  WebUI sees the real typed error (`AuthExpiredError`, `ContentRejectedError`,
  etc.), not a `stderr[:200]` banner-truncated preview. CLI and WebUI demonstrably
  validate against the same contract module. Zero process-model change.
- **Phase 2:** subprocess spawn eliminated for `validate`/`plan`/`report-anchors`
  (publish + login stay subprocess); measurable drop in their latency.
  **Quantify the current spawn cost first** to confirm the gain justifies the
  process-model risk.
- **The boundary is the only place** that knows in-process vs subprocess; no
  route or service references `run_pipe`/`subprocess` for these CLIs directly
  (includes `seo_viz.py`).
- **Parity is golden-locked by the typed result/error + byte-identical stdout
  data** (not raw stderr), across representative **and error-path** inputs.
  Test migration: the **5 files mocking `run_pipe`** redirect cleanly; the
  **~24 files mocking `subprocess` directly** are coupled to the removed boundary
  and need rewriting — two populations. Golden tests run with the socket-block
  conftest fixture active; the `real_ssrf_check` path asserts in-process SSRF
  rejection still fires.
- Login CLIs and `publish-backlinks` unchanged and still subprocess-isolated.

## Scope Boundaries

- **Login / browser CLIs are NOT migrated** (R6) and **`publish-backlinks` is
  NOT migrated** (R5) — both stay subprocess for isolation. Explicit non-goals.
- **Phase 1 does not touch the process model**; Phase 2 does not redesign the
  data contract. The two are sequenced, not interleaved.
- **Routes are not rewritten** — they keep calling `PipelineAPI`. This is not a
  route-layer refactor.
- **The CLI's own output contract (bytes on stdout + exit codes) is unchanged**
  for human/shell/CI use. The *mechanism* that captures those bytes (subprocess
  vs in-process) is internal to `PipelineAPI` and invisible to routes.
- **`footprint` is excluded from the in-process set.** It has no WebUI caller,
  and `PYTHONHASHSEED` can only be set at interpreter startup — a running Flask
  process cannot reproduce the seed-stable iteration order the subprocess
  inherited. If footprint output is hash-seed-dependent, in-process reproduction
  is structurally impossible; it stays subprocess / test-fixture-only.
- **Not the `api/` vs `services/` boundary cleanup** — that confusion exists but
  is a separate concern, out of scope here.

## Key Decisions

- **Schema-first contract first, in-process second (reversed after review).**
  The reported pain is opaque errors, and that lives in the contract, not the
  transport. Fixing it over the existing subprocess delivers the user-visible
  win at near-zero risk, and the resulting typed contract makes the later
  in-process swap's "no behavior change" target robust (parity = typed object,
  not byte-identical stderr). The in-process migration carries all the
  process-model risk, so it goes second and behind the safety net.
- **`publish-backlinks` stays subprocess; only read-only/low-risk CLIs go
  in-process.** Highest isolation value (0600 creds, side-effecting network,
  SSRF surface) meets lowest latency payoff (already network-bound). Revisit
  only after isolation guarantees are proven.
- **`PipelineAPI` is the seam; B0 funnels stray callers first.** The seam exists
  but is incomplete (only `plan`/`validate`/`publish`; `scheduler.py` /
  `sites.py` / `seo_viz.py` bypass it). B0 both funnels the leaks and grows new
  seam methods (`report-anchors`). Must complete before any in-process migration.
- **Pure-return core, not stdout capture.** `redirect_stdout` mutates
  process-global `sys.stdout`; unsafe across request threads + the scheduler
  thread and within a single call (CLIs spawn their own worker threads). A
  callable that returns rows without touching `sys.stdout` is likely mandatory,
  which pushes real (not purely mechanical) CLI-main refactoring into Phase 2.
- **Global-state audit gates the stdout golden lock.** Byte-identicality is
  partly an artifact of fresh-process-per-call; enumerate the process-lifetime
  side effects (gate sentinel, fetch cache, logger singletons, config memo)
  before "identical" is well-defined.

## Dependencies / Assumptions

- **Editable-install staleness is sidestepped.** `_rewrite_cli_cmd`'s
  `PYTHONPATH=./src` dance existed to dodge stale entry-point shims; in-process
  calls avoid the shim entirely. Assumes canonical imports resolve cleanly
  (post-#124, no `_LegacyPathFinder`).
- **`PYTHONHASHSEED=0` is interpreter-startup-only and cannot be honored
  in-process.** This is why `footprint` is excluded (Scope Boundaries), not a
  "verify later" item. Note: the production WebUI is not guaranteed to launch
  with `PYTHONHASHSEED=0` (that is a pytest-env setting), so even today's
  subprocess-from-WebUI may differ from subprocess-from-pytest.
- Assumes the existing `PipeResult` shape is close enough that Phase 1 mainly
  enriches its `error` channel (typed) rather than reshaping `rows`/`success`.

## Outstanding Questions

### Resolve Before Planning
- (none — driving pain, scope, sequencing, success criteria, and boundaries are
  decided: schema-first contract first, in-process second, publish + login stay
  subprocess)

### Deferred to Planning — Phase 1 (schema-first contract)
- [Affects R1/R2][Technical] Where the typed error contract lives and how the
  CLI emits it over subprocess without breaking shell/CI stdout consumers — a
  structured error object on a side channel (stderr JSON, or an error envelope
  line) vs reusing the existing `schema.validate_*` modules. Must handle the
  scattered `SystemExit` raises in `_publish_helpers.py`/`_resume.py` and
  `report-anchors`' **exit-6-with-populated-stdout** contract (`seo_viz.py` keeps
  the stdout on that non-zero exit; `run_pipe` discards it).

### Deferred to Planning — Phase 2 (in-process transport)
- [Affects R8][Needs research] Audit every global/process-lifetime side effect
  the core mutates and decide per item (reset-per-call vs documented change):
  `config.load_config` memoization, `content.fetch` cache, the platform
  `registry`, the `set_log_level` logger singletons, and the publish gate-banner
  sentinel. Include the scheduler-thread vs request-thread race.
- [Affects R4/R7][Technical] Concurrency-safe capture: `redirect_stdout` fails
  across threads and within a call (CLI-spawned worker threads). Decide the
  injection contract — `main(argv)` takes no stream params today, so either
  thread stdin/stdout through all in-scope mains (touches their subprocess tests)
  or extract pure-return cores. Blocks the `validate-backlinks` pilot.
- [Affects R7][Needs research] Two test populations: 5 files mock `run_pipe`
  (easy redirect) vs 24 mock `subprocess` directly (coupled to the removed
  boundary, need rewrite). Decide the new mock seam and golden-corpus generation
  under the socket-block fixture.
- [Affects R5][Security][Needs research] **Pre-condition for ever revisiting
  publish in-process** (and a guard for the read-only CLIs): the subprocess
  boundary is also a security boundary. Confirm (a) the SSRF opener
  (`_util/net_safety`) is constructed **per call**, not shared/weakened across
  threads; (b) no security-affecting process-global env var bleeds across
  threads — `OAUTHLIB_INSECURE_TRANSPORT` (set on `os.environ` by
  `routes/oauth.py`) could be observed by a concurrent in-process call and
  downgrade TLS; use thread-local/lock-scoped state; (c) concurrent in-process
  `publish` cannot race the 0600 credential-write `flock` (thread-safe, not only
  process-safe) or leave a torn token file on `SystemExit`/crash; (d) crash
  blast radius — a fetch hang/OOM/segfault a subprocess contained now risks the
  long-lived WebUI holding live sessions (add per-call timeouts).

## Next Steps
→ `/ce:plan` for structured implementation planning. Sequence: **Phase 1**
defines the schema-first typed-error contract over the existing subprocess
(ship the operator-visible win first), then **Phase 2** does B0 seam
consolidation and migrates one read-only entrypoint — `validate-backlinks`, the
purest — in-process end-to-end to prove the pattern before `plan`/`report-anchors`.
`publish` and login CLIs stay subprocess.
