---
title: "`del os.environ[CONFIG_DIR]` in a test poisons every later test under a session-scoped isolation fixture"
date: 2026-05-27
category: test-failures
module: backlink-publisher / testing-discipline
problem_type: test_failure
component: testing_framework
symptoms:
  - "`origin/main` is red — `test (3.11)` and `test (3.12)` both fail in CI — even though the merged PR was green on its own branch"
  - "7 tests fail in the full suite but the same tests pass when run in isolation (`pytest <file>` alone is green)"
  - "Failures cluster in unrelated areas: `test_generic_channel_api` (status/verify/csrf), `test_webui_store_isolation` ×2, `test_webui_store_pkg/test_channel_status` env-override"
  - "`assert body['bound'] is False` fails with `True` — an unbound channel reports as bound, as if real operator credentials leaked in"
  - "`test_singleton_paths_resolve_to_isolated_dir` fails on `assert cfg_env` — `BACKLINK_PUBLISHER_CONFIG_DIR` is unexpectedly empty mid-suite"
  - "A store-write isolation test reports the write landed in the operator's real `~/.config/backlink-publisher/`"
root_cause: test_isolation
resolution_type: test_fix
severity: medium
related_components:
  - "tests/test_channel_bind_save.py (`test_token_save_creates_0600_file`, `test_token_clear_unlinks_file` — the polluters)"
  - "tests/conftest.py (session-scoped `_isolate_user_dirs` fixture that sets `BACKLINK_PUBLISHER_CONFIG_DIR` once)"
  - "tests/test_generic_channel_api.py, tests/test_webui_store_isolation.py, tests/test_webui_store_pkg/test_channel_status.py (the 6 poisoned victims)"
  - "webui_store/__init__.py (`_LazyStore`, `_refresh_paths`) — resolves store paths from the env var"
related_prs:
  - "#259 — fix(tests): repair red main — env-leak pollution + stale cnblogs test"
  - "#257 — the PR that introduced the polluting test on a stale base and turned main red"
  - "#253 — removed the cnblogs adapter (left a stale test that #257 then re-referenced)"
tags:
  - test-isolation
  - pytest-fixtures
  - monkeypatch
  - env-var-teardown
  - passes-in-isolation
  - session-scope
  - stale-base-merge
---

# `del os.environ[CONFIG_DIR]` poisons later tests under a session-scoped fixture

## Problem

`origin/main` went red (both `test (3.11)` and `test (3.12)` failing in CI) immediately after a PR merged — even though that PR's own CI was green. Seven tests failed in the full suite; six of them passed when run in isolation. The root cause was a single test that mutated a global environment variable with a bare `del` to "restore" it, which silently broke a **session-scoped** isolation fixture for every test that ran afterward.

## Symptoms

- Full suite: `7 failed, 5005 passed`. Run any one failing file alone: green.
- Failures spread across unrelated modules (channel-status API, webui_store isolation, config-dir override) — no obvious common owner.
- An unbound channel's status endpoint returned `bound: true`, as if the operator's real credentials had bled into the test.
- `BACKLINK_PUBLISHER_CONFIG_DIR` was empty partway through the run, tripping `assert cfg_env` in the isolation contract test.
- GitHub CI confirmed main itself was red — not a local-only artifact.

## What Didn't Work

- **Reading the failing tests themselves.** All six victims are correct and pass in isolation; the bug is not in them. Time spent staring at `test_generic_channel_api` / `test_webui_store_isolation` was wasted — the failure manifests there but originates elsewhere.
- **Assuming the failures lived in the files where they appeared.** They are order-dependent pollution; the file that *fails* is not the file that's *broken*.
- **Suspecting a feature regression** (e.g. the channel-binding code wrongly reporting `bound`). Confirmed false: the endpoint returns `bound: false` correctly in isolation — it was reading leaked state, not computing wrong.

## Solution

Bisect to find the **polluter**, not the victim. Tests run in collection order (alphabetical here), so a polluter sorts before its victims. Running the suspect file together with the victims reproduced all six failures:

```bash
# isolation: green
PYTHONHASHSEED=0 PYTHONPATH=src pytest tests/test_generic_channel_api.py -q   # 9 passed

# polluter + victims: red — confirms test_channel_bind_save.py is the source
PYTHONHASHSEED=0 PYTHONPATH=src pytest \
  tests/test_channel_bind_save.py tests/test_generic_channel_api.py -q        # 3 failed
```

The polluting tests set the config-dir env var directly and "restored" it with `del` in a `finally`:

```python
# BEFORE — poisons every later test
def test_token_save_creates_0600_file(client, tmp_path):
    ...
    import os as _os
    _os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(config_dir)
    try:
        ...
    finally:
        # "Restore env" — but del UNSETS it, it doesn't restore the prior value
        del _os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"]
```

Fix: use the `monkeypatch` fixture, which records the prior value and restores it on function teardown:

```python
# AFTER — auto-restores the session fixture's isolated dir on teardown
def test_token_save_creates_0600_file(client, tmp_path, monkeypatch):
    ...
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
    ...
    # no finally / no del needed
```

(The 7th failure was unrelated: `test_userpass_cnblogs_stores_plaintext` patched `adapters.cnblogs_api`, an adapter removed in #253 — deleted the stale test.)

Result: `7 failed → 0 failed` (`5011 passed`).

## Why This Works

The repo's `conftest.py` isolation fixture is **session-scoped** — it sets `BACKLINK_PUBLISHER_CONFIG_DIR` to a sandbox dir **once** at session start and restores it **once** at session end:

```python
@pytest.fixture(scope="session", autouse=True)
def _isolate_user_dirs(tmp_path_factory):
    previous = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(config_dir)
    yield
    # restore happens here — at the very END of the whole run
```

A test that does `del os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"]` removes the var entirely. The session fixture won't reset it until the run ends, so **for the rest of the session** the var is unset and `config` resolution falls back to the operator's real `~/.config/backlink-publisher/`. Downstream tests then read/write real state → `bound: true` leaks, isolation asserts fail, writes hit prod paths.

`monkeypatch.setenv` is function-scoped and stores the *prior* value; its teardown puts that exact value back — which is the session fixture's sandbox dir, not "unset." So the next test sees the isolated dir again, as intended.

## Prevention

- **Never mutate `os.environ` directly in a test, and never "restore" with bare `del`.** Always use `monkeypatch.setenv` / `monkeypatch.delenv` — they restore the *prior value*, not "absent." A bare `del` is especially dangerous when a broader-scoped fixture owns that variable.
- **Triage "passes in isolation, fails in the full suite" as state pollution, not a bug in the failing test.** Find the *polluter*: it sorts before the victims in collection order. Pair the suspected file with a victim file and run them together to confirm.
- **Watch for stale-base merges.** This PR was green on its own branch because it was branched before the conflicting change landed; merging onto current main exposed the pollution. CI tests "merged into latest main," so a green feature branch is not proof — rebase onto current main before trusting green. (auto memory [claude])
- Optional guardrail: a lightweight autouse check that asserts `BACKLINK_PUBLISHER_CONFIG_DIR` still points inside the session sandbox at test teardown would surface a polluter at its source instead of at a random downstream victim.

## Related Issues

- [`tests-coupled-to-operator-config-state-2026-05-18.md`](tests-coupled-to-operator-config-state-2026-05-18.md) — **sibling.** That doc *introduced* the session-scoped isolation fixture (PR #43) to stop tests reading the operator's real config; this bug is the inverse — test code that *breaks* that fixture's contract via bare `del`. Same env var, same fallback-to-`~/.config` failure mode, same `monkeypatch.setenv` remedy.
- [`ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md`](ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md) — same `test_isolation` family (tests coupled to machine/operator state), different mechanism.
