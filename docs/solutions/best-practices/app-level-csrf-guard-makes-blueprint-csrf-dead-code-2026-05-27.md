---
title: App-level CSRF before_request makes per-blueprint CSRF dead code; shared-app config leaks cause false-green tests
date: 2026-05-27
category: docs/solutions/best-practices
module: webui_app (CSRF / route guards / test isolation)
problem_type: best_practice
component: authentication
severity: medium
applies_when:
  - Adding or auditing CSRF / origin guards on Flask blueprint routes
  - A webui route test passes in the full suite but 403s when run alone
  - Deciding whether a per-route security check is real protection or dead code
related_components:
  - testing_framework
tags: [csrf, flask, before-request, dead-code, test-isolation, false-green, webui, security]
---

# App-level CSRF before_request makes per-blueprint CSRF dead code; shared-app config leaks cause false-green tests

## Context

The WebUI enforces CSRF with a single app-level guard (`_global_csrf_guard`, an
app `before_request` registered in `create_app()`) that validates a canonical
`session['csrf_token']` against the form `csrf_token` / `X-CSRFToken` header and
`abort(403)`s on any failing POST/PUT/PATCH/DELETE.

One blueprint (`medium_login`) had *also* grown its own bespoke CSRF layer — a
separate session key, a separate form field, and its own `@bp.before_request`
that returned a friendly `302`+danger-flash redirect on failure. It looked like
defense-in-depth. It was dead code, and its tests were silently not testing
anything.

Two compounding traps surfaced together, both worth remembering.

## Guidance

**1. An app-level `before_request` guard runs before every blueprint
`before_request`. A per-blueprint guard checking the *same thing* is
unreachable.** Flask runs app-level `before_request` functions before
blueprint-level ones. If the app guard `abort(403)`s on bad CSRF, the
blueprint's own CSRF reject branch can never execute in production. Keeping it
is not defense-in-depth — it is dead code plus a copy-forward trap (the next
contributor mirrors the "two CSRF layers" shape into a new route).

Do **not** add a per-route/blueprint CSRF token layer when an app-level CSRF
guard already covers the method. Rely on the single global guard. If a route
needs *more* than CSRF (these routes spawn browsers and delete credential
profiles), add an **orthogonal** guard that the global guard does not provide —
origin/DNS-rebinding protection — matching whatever the codebase's other
sensitive routes already use (`_check_bind_origin_or_abort()` +
`_refuse_when_allow_network()`), not a second CSRF token.

**2. WebUI tests share one module-level app object. A sibling test that mutates
`app.config` and never restores it silently changes every later test.** The
test client is built from a module-level `webui.app` singleton. Many test files
do `webui.app.config["WTF_CSRF_ENABLED"] = False` (raw assignment, no restore).
Once any such file runs, the global guard is disabled for the rest of the
session. A route test that seeds the *wrong* CSRF key therefore **passes in the
full suite** (guard disabled by a sibling) but **403s when run alone** (guard
active). That is false-green coverage: the suite is green, the route logic was
never exercised.

The fix has two parts:
- Seed the **canonical** `session['csrf_token']` the global guard checks (copy
  the established helper, e.g. `_seed_csrf` in `test_webui_bind_routes.py`), and
  submit that token — not a bespoke per-route key.
- Make the test **force its own CSRF posture** with `monkeypatch.setitem` (which
  auto-restores), instead of inheriting whatever a sibling leaked:
  ```python
  monkeypatch.setitem(webui.app.config, "CSRF_ENABLED", True)
  monkeypatch.setitem(webui.app.config, "WTF_CSRF_ENABLED", True)
  ```

## Why This Matters

- **The dead-code layer hides risk.** A "CSRF check" that nobody tests and that
  never runs in production gives false confidence and teaches the wrong pattern.
- **False-green is worse than red.** A route whose every POST 403s before
  reaching its handler has *zero effective coverage*, yet CI is green — so a real
  regression in that route ships unnoticed. The green only holds as long as some
  unrelated sibling keeps leaking `WTF_CSRF_ENABLED=False` in the right order.
  (Correction 2026-05-27: this repo does **not** run `pytest-randomly` — it is
  absent from dev-deps and `ci.yml`; order is deterministic under
  `PYTHONHASHSEED=0`, which makes the false-green *stably* wrong rather than
  flaky. The structural fix is the autouse containment net + AST gate added in
  Plan 2026-05-27-003.)
- **Retiring without compensation can downgrade security.** Removing the bespoke
  layer left three browser-spawning / credential-deleting POSTs protected by CSRF
  alone — *weaker* than the sibling `bind` routes, which also carry an origin
  guard. The retirement is only safe because the origin guard was added for true
  parity, turning a cleanup into a net security improvement.

## When to Apply

- Before adding any CSRF check to a blueprint route — confirm the app-level guard
  doesn't already cover it (it almost certainly does for state-mutating methods).
- Whenever a webui route test is green in the suite but fails in isolation — the
  isolation failure is the truth; suspect shared `webui.app.config` pollution.
- When retiring a redundant security layer — check what *orthogonal* protection
  the codebase's comparable sensitive routes carry, and preserve parity.

## Examples

Diagnostic that proves the false-green (run the suspect file two ways):

```bash
# Green only because a sibling disabled CSRF on the shared app first:
PYTHONPATH=src pytest tests/test_history_bulk_routes.py tests/test_medium_login_routes.py -q
# 37 passed

# The truth — guard active, the route tests never reached their handlers:
PYTHONPATH=src pytest tests/test_medium_login_routes.py -q
# 10 failed (403 != expected)
```

Before — bespoke per-route CSRF (dead reject branch, wrong-key tests):

```python
# webui_app/routes/medium_login.py
@bp.before_request
def _csrf_check():
    if request.method == "POST" and not _validate_csrf():   # never reached:
        return redirect("/settings?flash_type=danger&...")  # global guard 403s first

# test seeded the bespoke key, so it only passed when a sibling disabled CSRF:
sess["medium_csrf"] = "test-csrf-abc"
client.post(url, data={"_csrf_token": token})
```

After — single global CSRF source + orthogonal origin guard + honest tests:

```python
# medium_login.py — no bespoke CSRF; add the orthogonal guard the bind routes use
def medium_clear_browser_login():
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    ...

# test — canonical token, forced CSRF posture, loopback Origin
monkeypatch.setitem(webui.app.config, "WTF_CSRF_ENABLED", True)
sess["csrf_token"] = "test-csrf-abc"
client.post(url, data={"csrf_token": token},
            headers={"Origin": f"http://127.0.0.1:{_FLASK_PORT}"})
# no-token POST now deterministically 403s (global guard), regardless of order
```

Contract note: retiring the bespoke 302-redirect changes the CSRF-failure
response from `302` (friendly flash) to `403` (raw guard). That is consistent
with every other webui POST and acceptable — production forms always carry the
token; only tampered/stale-session requests hit it.

## Related
- `docs/solutions/best-practices/never-smoke-test-real-save-endpoints-2026-05-19.md` — sibling test-isolation hazard (config-dir leaking to the operator's real `~/.config`); different mechanism (env var vs `app.config` singleton), same lesson: webui tests must sandbox/force their own state, never inherit it.
- `reference_webui_csrf_architecture` (auto memory [claude]) — global guard is the single CSRF enforcement; JS uses `X-CSRFToken`; the canonical seed pattern is `test_webui_url_verify_routes`.
- `feedback_global_csrf_guard_makes_blueprint_csrf_dead_code` (auto memory [claude]) — origin of this learning.
- PR #261 (origin/main `7c012d6`) — the retirement + origin-guard parity + test hardening that surfaced this.
- Follow-up: the broader root cause (many webui test files leak `WTF_CSRF_ENABLED=False` onto the shared `webui.app`) is not yet fixed globally; per-test forcing is the local mitigation until a session-scoped config-restore fixture lands.
