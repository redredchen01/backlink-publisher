---
title: "Never smoke-test live WebUI `/save-*` endpoints — empty POSTs wipe operator config"
date: 2026-05-19
category: docs/solutions/best-practices
module: webui_app routes + operator config
problem_type: best_practice
component: rails_controller
severity: critical
applies_when:
  - "About to `curl -X POST http://localhost:8888/save-<anything>` against a running dev WebUI"
  - "Smoke-testing a new POST route end-to-end during development"
  - "Verifying that a save route's handler is wired correctly without writing a full pytest"
related_components:
  - testing_framework
  - tooling
tags:
  - webui
  - save-routes
  - data-loss
  - smoke-test
  - throwaway-config-dir
  - config-wipe
---

# Never smoke-test live WebUI `/save-*` endpoints

## Context

The WebUI's save routes (`/save-config`, `/save-llm-config`, `/save-channel-token`, `/save-anchor-config`, ...) accept a full form payload and persist it to `~/.config/backlink-publisher/*.json` (or `*.toml`). An empty POST is a valid payload — it persists an empty form, which **deletes every field the operator had configured**.

A single curl smoke test like `curl -X POST http://localhost:8888/save-llm-config` against a running dev WebUI wipes `llm-settings.json` clean. On 2026-05-19 this happened twice in a single session — the second occurrence after the first had been noted but the lesson hadn't formalized.

The save handlers are correct: they save what the form sends. The failure mode is operator-tooling shape, not handler bug.

## Guidance

### Default rule: never `curl` a save route against the running WebUI

Use one of these instead:

1. **Throwaway config dir** (preferred for any save-route smoke test):

```bash
mkdir -p /tmp/blp-smoke-$$
BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/blp-smoke-$$ \
    python webui.py &
WEBUI_PID=$!
sleep 1
curl -X POST http://localhost:8888/save-llm-config -d "provider=openai&model=gpt-4"
# inspect /tmp/blp-smoke-$$/llm-settings.json
kill $WEBUI_PID
rm -rf /tmp/blp-smoke-$$
```

`BACKLINK_PUBLISHER_CONFIG_DIR` redirects every config read/write to the throwaway path. The operator's real config in `~/.config/backlink-publisher/` is untouched.

2. **pytest route test with isolated config**:

The autouse conftest fixture sandboxes `BACKLINK_PUBLISHER_CONFIG_DIR` per-test. Adding a route test under `tests/webui/test_<route>.py` exercises the save handler end-to-end with no risk to the real config.

3. **Browser-driven smoke** with a non-empty form:

If you must smoke-test the running dev WebUI, drive it through the actual form in a browser (fill all required fields, submit). The form's client-side state guarantees the payload includes every configured field.

### Never run, even with `BACKLINK_NO_FETCH_VERIFY` or similar guards

```bash
# WRONG — these are not safe even with extra env vars
curl -X POST http://localhost:8888/save-config
curl -X POST http://localhost:8888/save-llm-config -d ""
curl -X POST http://localhost:8888/save-llm-config -d "provider=openai"   # partial → wipes other fields
```

The "partial form" case is the most insidious — providing one field looks safer than no fields, but the save handler treats absent fields as "operator wants these cleared." That's the contract for the UI (so operators can unset values by clearing them), and it's deadly for ad-hoc POSTs.

### Recovery if it happens

If the operator's config is wiped:

1. **Don't run more `/save-*` requests** — the in-memory state in the WebUI process now matches the wiped disk state, and a subsequent save would re-persist the empty values.
2. **Restart the WebUI** to clear in-memory state.
3. **Check `~/.config/backlink-publisher/.backup/`** — `save_config` writes an atomic-rename backup in some routes. Recover from there if present.
4. **Check `git stash` / shell history** for any prior config dump the operator might have captured.
5. **Tell the operator** — don't try to "rebuild silently from memory" or partial knowledge; the operator likely has the real values somewhere.

## Why This Matters

The data-loss cost is high and one-way:

- `llm-settings.json` includes API keys and provider config — wiping it means the operator pastes their API key again, possibly fishing it out of a password manager.
- `config.toml` includes `[targets.*]` blocks (publishing destinations) — wiping means rebuilding the entire publishing topology.
- `[anchor_alarm]`, `[anchor.proportions]`, `[llm.anchor_provider]` are not round-tripped by `save_config` (see CLAUDE.md "Config and environment" caveat) — wiping these silently drops sections the save route doesn't even know how to re-emit.

The smoke-test temptation is real because saving feels like a "should just work" verification. The cost asymmetry is severe: ~10 seconds to write a curl, ~30 minutes to recover wiped config.

## When to Apply

- Any session where you're tempted to `curl -X POST .../save-*` to verify a route handler.
- Reviewing a PR that adds a new `/save-*` route — the review checklist should include "how do we smoke-test this without risking config?"
- Writing operator playbooks — never include `curl POST /save-*` as a verification step.

Skip only when:

- You're explicitly running pytest (which sandboxes config dir via autouse fixture).
- You've confirmed `BACKLINK_PUBLISHER_CONFIG_DIR` is set to a throwaway path **for the WebUI process being curled**, not just the curl call's env.

## Examples

**Right (smoke-test a new `/save-channel-token` route):**

```bash
# In one shell:
mkdir -p /tmp/blp-smoke-$$
cp -r ~/.config/backlink-publisher/* /tmp/blp-smoke-$$/  # optional: seed with real shape
BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/blp-smoke-$$ python webui.py

# In another:
curl -X POST http://localhost:8888/save-channel-token \
     -d "channel=ghpages&token=test-token-value"
cat /tmp/blp-smoke-$$/config.toml | grep -A2 '\[targets.ghpages\]'

# Cleanup:
rm -rf /tmp/blp-smoke-$$
```

**Wrong (2026-05-19 actual, twice in one session):**

```
$ curl -X POST http://localhost:8888/save-llm-config
$ cat ~/.config/backlink-publisher/llm-settings.json
{}    # was: {"provider": "openai", "model": "gpt-4", "api_key": "sk-..."}

# Operator had to re-paste API key + reconfigure
```

## Related

- CLAUDE.md "Config and environment" → caveat that `save_config` does not round-trip several `[targets.*]` / `[anchor.*]` blocks. Wipes are silently worse than they look.
- `docs/solutions/best-practices/publish-history-helper-invariant-2026-05-20.md` — adjacent: webui_store invariant enforcement.
- `webui_app/helpers.py:save_config` — current implementation; partial-form behavior is by design for the UI.
- `BACKLINK_PUBLISHER_CONFIG_DIR` env var — universal isolation knob.
