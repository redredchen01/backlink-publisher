---
title: "Use a RECON log level for events the operator must always see — bypass the `--log-level` gate"
date: 2026-05-15
category: best-practices
module: backlink-publisher / logger
problem_type: best_practice
component: logging
severity: medium
applies_when:
  - "Designing a new event that the operator MUST see in every run (e.g. end-of-run reconciliation, silent-drop tripwire, data-integrity invariant trigger)"
  - "Reviewing a `logger.info(...)` call and asking 'will the operator actually see this?'"
  - "Adding a new always-on signal — must also audit tests for `assert stderr == ''` patterns that the new signal will trip"
tags:
  - logging
  - log-level
  - operator-visibility
  - cron-friendly
  - reconciliation
  - silent-drop
  - always-on-signals
---

# Use a RECON log level for events the operator must always see

## Guidance

When an event **must** be visible to every operator run, do not use `logger.info(...)`. The default `--log-level=WARN` (sensible for cron, prevents stderr spam) silently swallows INFO-level messages — the operator never sees the event no matter how important it is. Instead, add a method that bypasses the level gate entirely and emits at a custom `RECON` level.

The pattern (already shipped in `src/backlink_publisher/logger.py`):

```python
def recon(self, msg: str, **extra: Any) -> None:
    """Always-emit reconciliation event — bypasses the level gate."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": "RECON",
        "logger": self.name,
        "msg": msg,
    }
    if extra:
        record.update(extra)
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)
```

Calling convention: each CLI's `main()` invokes `recon` once at the end, **before** any `SystemExit` gate, summarizing what happened.

```python
plan_logger.recon("plan_reconciliation", input_rows=..., output_rows=..., delta=..., dropped={...})
```

Grep target for tests and audit: `"level": "RECON"`.

## When to Apply

| Use RECON | Use INFO | Use WARN/ERROR |
|---|---|---|
| End-of-run input/output delta | Debug context | Recoverable problem |
| Cron must know "the task actually completed" | Performance trace | Failed but continuing |
| Data-integrity invariant trigger | Startup banner | Aborting the run |
| Silent-drop tripwire | Verbose trace requested by operator | — |

The asymmetry is deliberate: default log levels should be conservative (cron does not want stderr spam) but a small set of events **must** punch through that filter. Custom levels are cheaper than forcing operators to raise `--log-level=INFO` (which then drowns out the signal in noise).

## Why This Works

The reconciliation pattern depends on visibility. A "silent-drop tripwire" that fires into a filtered log channel is no tripwire at all — it just makes the silent drop slightly more discoverable in postmortems, not preventable in the moment. RECON-level emission gives the operator the same channel for "the cron job ran and processed N rows, dropped M, here's the breakdown" that they'd otherwise have to extract from JSONL output, error logs, and exit codes combined.

The cost is intentionally narrow: only a handful of events per run, all structured JSON, all on stderr. Cron mail stays small; operators get the signal they need without flipping flags.

## Prevention / side-effect awareness

**Adding a new always-on signal forces a test-suite audit.** Any existing test asserting `stderr == ""` after a CLI run was green because the channel was empty. Introducing RECON output makes those assertions fail — they're inversion candidates per the negative-shape-assertion pattern.

When introducing a new RECON event:

1. **Grep first**: `rg -n 'assert\s+\w*stderr\s*==\s*""' tests/` — every match is a test that will turn red.
2. **Invert in the same commit** that introduces the signal. Don't ship the signal in PR-A and the test inversions in PR-B; the intermediate state has a red CI.
3. **Add a positive complement**: if the test was `assert stderr == ""`, replace with `assert '"level": "RECON"' in stderr` and `assert '"msg": "<expected_event>"' in stderr`. This catches the signal disappearing in the future as a real test failure, not a tautological pass.

A concrete example from the project: PR #13 added three RECON events (`plan_reconciliation`, `validate_reconciliation`, `publish_reconciliation`) and required inverting `test_validate_no_stderr_on_success`, `test_plan_no_stderr_on_success`, `test_plan_three_rows`. Each invert was 2 lines; doing all four in one commit kept CI green.

## Related Issues

- `docs/solutions/test-failures/negative-assertion-locks-in-bug-2026-05-15.md` — the test-side anti-pattern this signal's introduction triggers; pre-audit recipe.
- `docs/solutions/best-practices/plan-time-url-validation-prevents-publish-404-2026-05-15.md` — uses `plan_logger.recon` for the `category_link_skipped_no_config` downgrade event so the operator sees it without `--log-level=INFO`.
- Provenance: `feedback_recon-level-for-always-on-signals.md` (auto memory [claude], first encountered 2026-05-14).
