---
title: "Typed-error envelope on stderr beats stderr[:N] truncation for CLI→UI error branching"
date: 2026-05-27
category: docs/solutions/best-practices
module: _util/error_envelope.py + _util/errors.py + webui_app bridge
problem_type: best_practice
component: cli_webui_bridge
severity: medium
applies_when:
  - "A CLI chained via stdin/stdout JSONL needs to communicate WHY it failed to a programmatic consumer (a WebUI, a scheduler, another CLI)"
  - "The consumer currently does `stderr[:200]` (or any fixed slice) to show or branch on the error — and the real cause sits past the cut, or behind a diagnostic banner"
  - "The consumer needs to branch on error CLASS (auth-expired → re-bind, rate-limited → backoff, validation → show inline) not just a boolean success"
  - "You control both the producer (CLI) and the consumer (bridge), so you can add a machine-readable channel"
related_components:
  - cli
  - tooling
  - development_workflow
tags:
  - typed-error
  - error-envelope
  - sentinel-json
  - stderr-contract
  - chokepoint
  - additive-channel
  - quarantine-fallback
---

# Typed-error envelope on stderr beats stderr[:N] truncation for CLI→UI error branching

## Context

The pipeline CLIs emit human diagnostics on stderr and data on stdout (exit 0–6 contract). The WebUI bridge surfaced failures by slicing `stderr[:200]`. Two failure modes followed:

1. **Truncation hid the cause.** Every CLI prints a ~210-char `effective config:` banner before any error line, so `[:200]` showed the operator the banner and nothing else (see `[[feedback_webui_stderr_preview_truncated]]`).
2. **No structured branching.** The WebUI wanted to react to *kinds* of failure — auth-expired credentials → prompt re-bind; rate-limit → backoff; validation → show inline — but a sliced string forces brittle substring sniffing.

The fix is an **additive, machine-readable typed-error envelope** on stderr, parsed by the bridge into structured fields. Shipped as Phase 1 of the thin-WebUI refactor (PR #270, layered on the banner-strip fidelity work in PR #269).

## Guidance

### The envelope is a sentinel-prefixed JSON line, additive to human text

A leaf stdlib-only module (`_util/error_envelope.py`) owns the contract:

```
__BLP_ERR__ {"error_class": "AuthExpiredError", "exit_code": 3, "message": "channel 'x' credentials expired"}
```

- **Additive**: the human-readable error line is still printed. The envelope is an *extra* line, so existing log-readers and `surface_cli_error` keep working unchanged.
- **Sentinel-prefixed** (`__BLP_ERR__`): lets the parser find it in noisy stderr (banner + traceback + envelope) without false positives.
- **stdlib-only / leaf module**: no imports into the CLI or adapter layers, so it can't create an import cycle. The `ErrorClass` enum that adapters branch on lives here too — the retry layer re-imports it, never the reverse.

### Emit at a chokepoint, not at every `raise SystemExit`

Route every fatal nonzero exit through one of a few functions in `_util/errors.py` so the envelope is emitted in exactly one place per pattern:

- `emit_error(message, exit_code=5, *, error_class=None)` — prints the human line, emits the envelope, raises `SystemExit`. The `error_class` override matters: without it the class is derived from the exit code, and a site that maps an `AuthExpiredError` onto exit 3 would be mislabeled (a code-review P1 on this PR). Pass `error_class=type(exc).__name__` at sites that already know the exception type.
- `emit_envelope_and_exit(error_class, exit_code, message)` — envelope-only + `SystemExit`, for sites that already printed their own human text.

A **static AST guard test** (`_bare_nonzero_systemexits` over the in-scope CLIs) is the tripwire: it fails if any CLI grows a bare `raise SystemExit(<nonzero>)` that bypasses the chokepoint. This is what keeps the contract from rotting as CLIs change — the missed-dispatch site is caught at test time, not in production.

### The consumer parses defensively, with a QUARANTINE fallback

`parse(stderr)` is **last-valid-wins, malformed-skip, banner-resilient**: it scans every sentinel line, ignores ones that aren't valid JSON, and returns the last well-formed envelope. When no valid envelope is present (an argparse usage error exits 2 with *no* envelope by design, or a crash, or a truncated line), the bridge does **not** invent structure — it QUARANTINEs: `error_class="unrecognized"` + the full banner-stripped text.

Two guards on the untrusted-content path:

- **Length-bound** the surfaced message (4000 chars) — `str(exc)` can fold in a target URL or a fetched-page snippet, and it flows into logs + history JSON. Cap it the same as `surface_cli_error`.
- **Strip the sentinel on the human path.** A *malformed* envelope that `parse` rejected still contains the raw `__BLP_ERR__ {...` text; `strip_cli_diagnostic_banner` must remove sentinel lines so the operator never sees the raw JSON (a code-review P2). The QUARANTINE path and the scheduler's failure path both clean through here.

### Keep `.error` a plain string

`PipeResult` gained `error_class: str | None` and `exit_code: int | None`, but `.error` stayed a `str` (the full message). Existing consumers that slice or format `.error` keep working — the structured fields are *additive*, mirroring the envelope's own additive design.

## Why This Matters

The `[:200]` slice is the kind of bug that looks fine in every demo (short errors fit) and fails exactly when an operator most needs the message (a long auth/validation error in production). The envelope fixes both halves at once: the human still gets full text (fidelity), and the machine gets a class to branch on (structure) — without a breaking change, because both channels are additive.

The chokepoint + AST guard is the durable part. Anyone can add an envelope to the three sites they remember; the guard is what catches the fourth site they forgot, six months later.

## When to Apply

- Adding error-kind branching to any CLI→programmatic-consumer boundary where you own both ends.
- Replacing a fixed `stderr[:N]` / `output[:N]` slice that an operator or another program reads.
- Any time a consumer is substring-sniffing stderr to guess what went wrong (`"expired" in stderr`) — that's the signal you need a typed channel.

Skip when:

- The consumer is a human reading a terminal — they get the full stderr already; no envelope needed.
- The CLI has exactly one failure mode the consumer treats uniformly (a boolean success/fail is enough).
- The boundary is a one-off script, not a contract two sides depend on.

## Examples

**Producer (chokepoint, with class override at a known-type site):**

```python
# _publish_helpers.py auth-expired handler — derive class from the exception,
# not from the exit code (exit 3 alone would mislabel it "DependencyError")
except AuthExpiredError as exc:
    emit_error(str(exc), exit_code=3, error_class=type(exc).__name__)
```

**Consumer (defensive parse + QUARANTINE + cap):**

```python
def _typed_error_result(stderr, fallback_label):
    env = parse(stderr or "")
    if env is not None:
        msg = env.message[:_MAX_SURFACED_ERROR]  # cap untrusted content
        return PipeResult(success=False, error=msg,
                          error_class=env.error_class, exit_code=env.exit_code)
    # no/malformed envelope → QUARANTINE: loud, full, banner+sentinel-stripped
    return PipeResult(success=False, error=surface_cli_error(stderr),
                      error_class="unrecognized", exit_code=None)
```

**The AST guard that keeps it honest** (`tests/test_cli_typed_error_emission.py`): walks each in-scope CLI's AST, flags any `raise SystemExit(<nonzero-constant>)` not routed through the chokepoint. A new uninstrumented exit fails the suite.

Related: `[[feedback_webui_stderr_preview_truncated]]`, `[[reference_webui_csrf_architecture]]`, `probe-then-pivot-when-api-unverifiable` (sibling pattern for fail-safe error classes).
