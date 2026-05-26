---
date: 2026-05-25
topic: codebase-optimization-backlog
type: ideation
status: draft
---

# Codebase Optimization Backlog — "全方位優化建議"

A grounded, prioritized improvement backlog for `backlink-publisher`, fulfilling the
original broad request ("全方面分析代碼狀況 + 優化建議讓服務更優質"). One direction
(reliability/observability) was already taken deep and is in flight (see below); this
doc covers the rest with file:line evidence, impact×effort, and a safe-to-start flag.

> **Evidence caveat:** line numbers were gathered while the working tree had heavy
> concurrent uncommitted WIP. Treat each `file:line` as a *lead to re-verify*, not a
> frozen coordinate. Re-grep before acting.

## Where the leverage actually is

The codebase is **mature and well-engineered (≈B+)**: 150 src modules / ~32.7k SLOC /
~3300 tests, with rare discipline — monolith-SLOC budget gate, dynamic adapter registry,
4-layer test isolation, CSRF guard, atomic secret writes. **Pure code cleanup is NOT the
high-leverage area.** The leverage is in *honesty of signals* (does the operator see the
truth?) and *closing silent-failure gaps*, not in restructuring.

## Already in flight (do not duplicate)

- **Reliability & observability → health dashboard.** Brainstorm
  `docs/brainstorms/2026-05-25-publishing-health-dashboard-requirements.md` →
  `docs/plans/2026-05-25-005-fix-events-projector-correctness-plan.md` (projector
  correctness, active, concurrent agent) + `2026-05-25-006-feat-publishing-health-dashboard-plan.md`
  (dashboard, blocked on 005-fix). Uncovered a verified P0 (projector silently drops every
  CLI success — `events/projector.py` `done`/`succeeded` mismatch).
- **Equity ledger** — `docs/plans/2026-05-25-004-feat-backlink-equity-ledger-plan.md` (active).

## Prioritized backlog

Priority = impact × (1/effort), risk-adjusted. **Safe-now** = touches only new files or
cold areas; **Blocked** = touches files in the current concurrent-WIP zone (publishing/
adapters, cli, config, events, ledger/*) — defer until the tree settles to avoid collisions.

| # | Theme | Item | Impact | Effort | Safe now? |
|---|---|---|---|---|---|
| O1 | UX honesty | WebUI routes swallow errors then redirect "success" | High | S | Blocked (webui routes lightly touched) |
| O2 | Correctness | Silent-swallow `except Exception` in adapters | High | M | Blocked (adapters hot) |
| O3 | UX honesty | `fetch().then(r=>r.json())` without `r.ok`/content-type guard | Med | S | Blocked (templates) |
| O4 | Test/risk | OAuth routes have no dedicated test file | High | M | Mostly safe (new test file) |
| O5 | Test hygiene | Stale feature-gate skips (velog "PR #75") | Low | S | Safe (test edits, cold) |
| O6 | Extensibility | New adapter still needs manual UI/bind manifest wiring, fails silently if forgotten | Med | M | Blocked (registry in migration) |
| O7 | Quick win | Documented exit-code contract (0–6) unenforced by tests | Med | S | Safe (new param test) |
| O8 | Quick win | Pre-#140 `llm-settings.json`/cookie files may be 0644 | Med (sec) | S | Blocked (recipes hot) |
| O9 | Hygiene | Uncommitted 49-file formatter sweep sitting in tree | Low | S | Blocked (it IS the tree state) |

### High-impact (do when tree settles)

- **O1 — WebUI "false success" routes (UX honesty, High).** Several routes catch broad
  `Exception`, fall back to a safe default, and redirect as if it worked — the operator
  sees success when the action silently failed. Leads: `routes/checkpoint.py:~89-93`
  (delete failure swallowed → success redirect), `routes/drafts.py:~85-110` (scheduler
  job-removal failures swallowed; UI says "已取消排程" but job may still be queued),
  `routes/url_verify.py:~181-185` (ANY exception reported as `network_error`),
  `routes/pipeline.py:~137` (corrupt JSON → stale fallback, no feedback). Pattern mirrors
  the already-fixed PR #156 false-success bug — same class, different routes. **Fix:** surface
  the real failure to the UI instead of redirecting success.

- **O2 — Silent-swallow exceptions in adapters (Correctness, High).** Of ~30 risky broad
  catches, the genuinely silent ones (no log/re-raise/pragma): `adapters/medium_browser.py:~419`
  (screenshot/stderr-write errors on the failure path), `adapters/linkedin_api.py:~140`
  (`resp.json()` decode failure → `{}`, masks corrupt API responses). Most others are
  documented cleanup paths (`content/fetch.py` `# noqa: BLE001` with reasons, chrome
  teardown). **Fix:** add a one-line log + context to the truly-silent ones; leave the
  documented-fallback ones. Cross-reference `feedback_dead_code_audit_blind_spots` discipline.

- **O4 — OAuth routes untested (Test/risk, High).** `webui_app/routes/oauth.py` (~180 lines,
  Blogger OAuth + loopback-URI security gate) has no dedicated test file — only contract-level
  coverage in `tests/test_webui_route_contract.py`. The `_is_loopback_uri()` / insecure-transport
  helpers are security-adjacent and deserve direct tests. **Mostly safe now** (new test file
  `tests/test_webui_routes_oauth.py`); verify it doesn't import a hot module mid-rewrite.

### Safe to start now (new files / cold areas, zero collision)

- **O7 — Enforce the exit-code contract (Quick win, Med).** The 0–6 exit-code table is a
  documented contract but never asserted (`cli/*` `sys.exit(main())`). Add a parametrized
  test over the CLI entrypoints asserting each documented code — mirrors the existing
  `tests/test_cli_python_m_entrypoints.py` style. New test file → safe.

- **O5 — Prune stale feature-gate skips (Test hygiene, Low).** `tests/test_webui_platforms_context.py`
  skips gated on "velog not in registry / PR #75 not landed" — velog IS registered now;
  the skips are dead. Also re-check `tests/test_anchor_profile.py` for an explicitly-disabled
  skip. Edits are confined to cold test files. (Re-verify velog registration before deleting.)

### Deferred / coordinate

- **O3** content-type guard on WebUI `fetch` (template edit; coordinate — known trap
  `feedback_fetch_json_must_guard_content_type`).
- **O6** adapter manifest auto-wiring — the registry migration (`registry.py` visibility
  manifest, `HIDDEN_FROM_UI` legacy set in `binding_status.py`) is itself in progress; a
  new adapter that forgets `ui=`/`bind=` silently drops from the UI with no compile error.
  Add a registry-completeness assertion test once the migration settles.
- **O8** one-time `chmod 0600` upgrade for pre-#140 secret files on load (`recipes/medium.py`,
  `medium_browser.py`) — touches hot files; defer.
- **O9** the uncommitted 49-file docstring formatter sweep — decide commit-or-discard once
  the concurrent agents land their work (it IS the current tree contamination).

## Highest-leverage recommendation

After the in-flight reliability work lands, the single highest-leverage theme is **UX honesty
(O1 + O3)**: the tool's recurring pain class (PR #156 false-success, velog null-after-retry,
the projector bug) is all "the operator was told something succeeded when it didn't." Closing
the remaining false-success routes compounds with the health dashboard — together they make
the operator's view of "did it work?" trustworthy end-to-end. That is the truest reading of
"讓這個服務更優質".

## Sequencing note (important)

**Do not start any src/ code item now.** The working tree is mid-flight with two concurrent
agents (005-fix projector + 004 equity-ledger) and a 49-file formatter sweep — almost every
src/ dir has uncommitted changes. Start code items only after the tree settles, each from a
clean isolated worktree. The two "safe now" items (O7, O5) touch only new/cold test files and
can proceed in isolation if desired.
