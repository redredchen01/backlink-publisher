---
title: "bind-channel diagnostic playbook — five rules learned from Medium 3-attempt failure"
date: 2026-05-20
category: docs/solutions/best-practices
module: cli/_bind + webui_app/bind_job
problem_type: best_practice
component: authentication
severity: high
applies_when:
  - "Diagnosing why a Playwright-driven bind run reports `bound_predicate_timeout` even though the operator says login completed"
  - "Reproducing a bind failure that the operator can no longer reproduce themselves"
  - "Deciding whether to keep iterating versus hand off to a different bind tool (chrome-devtool MCP, manual cookie paste)"
related_components:
  - background_job
  - tooling
tags:
  - bind
  - playwright
  - bound-predicate-timeout
  - operator-handoff
  - terminal-event
  - background-mode
  - medium
  - diagnostic-playbook
---

# bind-channel diagnostic playbook — five rules from the Medium 3-attempt failure

## Context

On 2026-05-20 a `medium-login` bind (post-PR #88 cookies-only path) failed three times in a row with `bound_predicate_timeout` at ~91s. Each retry felt like progress because new diagnostic data appeared, but the same root cause recurred. The session ended with a clean operator handoff to chrome-devtool MCP — no orphan processes, no profile corruption, no `channel-status.json` mutation.

The five rules below distill what worked and what wasted time, so the next session catches the pattern in one attempt instead of three.

## Guidance

### Rule 1 — Absence of `channel.bind.persisted` event ≠ success

Operators self-report "yes I'm logged in" because the Medium UI shows their avatar. That signal is **not** the bind contract. The bind contract is the `channel.bind.persisted` event in `webui_store/channel-status.json` (or the equivalent stdout event from `cli._bind`). If that event is missing, the bind failed regardless of UI state.

```bash
# Single source of truth
jq '.medium' webui_store/channel-status.json  # last_bound_at must be set
```

If `last_bound_at` is unset (or older than the bind attempt), the bind did not persist. The operator's screen state is irrelevant.

### Rule 2 — Use sync Bash + `timeout=600000` for failing-run reproduction

When a bind is repeatedly failing, **do not** run it with `run_in_background: true`. Background-mode flush will swallow the first terminal event (the `bound_predicate_timeout` JSONL line) and you end up diagnosing the wrong layer — typically chasing "why is the Playwright handle leaking" when the actual cause is the bind predicate never firing.

Sync Bash with an explicit 10-minute timeout captures the full event sequence:

```bash
# Right: full event sequence, predictable timeout
BACKLINK_PUBLISHER_CONFIG_DIR=~/.config/backlink-publisher \
    timeout 600 medium-login --verbose 2>&1 | tee /tmp/medium-bind-run.log
```

The 2026-05-20 session ran the first attempt in background mode, missed the terminal event, then re-ran in sync mode to capture the complete sequence — the diagnostic delta between the two runs is what isolated background-mode flushing as the obscuring layer.

### Rule 3 — Stuck on `bound_predicate_timeout`? Inspect the SQLite cookie store

The bound-predicate timeout means the predicate (URL/cookie pattern) never matched within the timeout window. The single most informative next step is to look at the actual cookie state captured by the Playwright profile:

```bash
sqlite3 ~/.cache/medium-spike/browser-profile/Default/Cookies \
  "SELECT name, length(value), is_httponly FROM cookies WHERE host_key LIKE '%medium.com';"
```

If `sid` / `rid` are 0-length placeholders, the auth handshake never completed — the operator clicked through SSO, but Medium never set the auth cookie. Common cause: Cloudflare anti-bot challenge or third-party-cookie blocking. This is detectable in <30 seconds and is decisively more useful than another Playwright run.

### Rule 4 — 91-second timeout = idle; 1200-second timeout = absolute

There are two distinct timeout shapes in the bind harness:

- **Idle timeout (~91s)** — fires when the page has been navigated but no further URL transition or DOM event for N seconds. Means: page loaded, but the next step (SSO redirect, cookie write, post-handshake nav) never happened.
- **Absolute timeout (~1200s)** — fires when the entire bind run exceeds the ceiling regardless of activity. Means: the bind is genuinely stuck, possibly in a CAPTCHA loop or operator-blocking modal.

The number on the timeout message **is the diagnosis**. 91s = "Medium loaded and then the auth flow stopped" (look at cookies + recent navigation). 1200s = "the operator never finished" (look at the visible browser window for modals or check whether the operator walked away).

### Rule 5 — Operator says "你下去吧" or switches MCP → stop immediately

When the operator explicitly hands off — "let me try this in chrome-devtool MCP", "你下去吧", "I'll do this manually" — the diagnostic session is over. Don't run "just one more attempt" in the background. Don't open another retry. The clean exit state is:

- `channel-status.json` untouched (no half-written placeholder)
- Browser profile preserved (don't wipe cookies — the operator may want them)
- No orphan Playwright processes (`pgrep -f playwright` returns empty)
- Final log line written explicitly to the bind attempt log

The 2026-05-20 session followed this rule and the handoff was clean: 14 cookies preserved in the profile (with `sid`/`rid` as 0-length placeholders — useful evidence for the next session), `channel-status.json` unchanged, no orphan procs.

## Why This Matters

Bind diagnostics are a high-cost loop: each attempt takes 60-300 seconds of operator attention (they have to drive the SSO flow), and a wrong diagnosis sends both the agent and operator chasing the wrong layer. The five rules collapse the diagnostic decision tree:

```
fail → check channel-status.json (Rule 1)
     → re-run sync if needed (Rule 2)
     → inspect cookie SQLite (Rule 3)
     → diagnose from timeout shape (Rule 4)
     → if operator hands off, stop clean (Rule 5)
```

In the 2026-05-20 session, applying these rules from the start would have saved two of the three attempts: Rule 2 catches the obscured terminal event on attempt 1, and Rule 3 isolates the Cloudflare cookie failure before attempt 3.

## When to Apply

- `medium-login` / `velog-login` / `blogger-login` / any future Playwright-driven bind reports timeout.
- Operator's self-report disagrees with `channel-status.json` (Rule 1).
- A bind that previously worked starts failing across multiple attempts (likely an upstream auth change — Cloudflare, OAuth scope, cookie policy).

This playbook does **not** apply to token-paste binds (ghpages, writeas, hashnode) — those don't have a Playwright harness, and the failure modes are different (invalid token, wrong API endpoint).

## Examples

**Right (post-playbook, hypothetical):**

```
Attempt 1: medium-login → timeout 91s
           → check channel-status.json → not persisted (Rule 1)
           → sqlite3 ... Cookies → sid: 0-len, rid: 0-len (Rule 3)
           → diagnosis: Cloudflare blocked auth cookie write
           → handoff to chrome-devtool MCP (Rule 5)
Total operator time: ~2 minutes
```

**Wrong (2026-05-20 actual):**

```
Attempt 1: medium-login --background → output shows "bind started"
           → wait for status → status never updates
           → assume Playwright handle leak, kill processes, retry
Attempt 2: medium-login sync → timeout 91s
           → assume operator didn't finish SSO, retry
Attempt 3: medium-login sync → timeout 91s
           → finally inspect SQLite → sid: 0-len → Cloudflare diagnosis
           → operator says "你下去吧" → handoff
Total operator time: ~10 minutes
```

## Related

- `docs/solutions/best-practices/medium-httponly-auth-cookies-spike-3a-2026-05-19.md` — cookie taxonomy (what `sid` / `rid` represent and how to distinguish from anti-bot cookies).
- `docs/solutions/best-practices/medium-liveness-probe-partial-spike-2-2026-05-19.md` — Cloudflare anti-bot context for the cookie-write failure.
- `docs/solutions/logic-errors/playwright-framenavigated-orphaned-during-cross-origin-sso-2026-05-19.md` — adjacent Playwright failure mode at the same handshake.
- PR #88 (`fdeaebc`) — `medium-login` cookies-only path that's the current production code path.
