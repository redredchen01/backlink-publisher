---
title: "`argparse choices=` exits 2, repo `UsageError` exits 1 — closed-set args go in post-parse validation"
date: 2026-05-20
category: docs/solutions/logic-errors
module: cli/*.py argparse setup
problem_type: logic_error
component: tooling
symptoms:
  - "Invalid CLI value exits 2 instead of the documented 1 for usage errors"
  - "CI guard `[ $? -eq 1 ]` misses invalid-channel inputs entirely"
  - "Exit-code contract table in AGENTS.md silently wrong for one parameter family"
root_cause: wrong_api
resolution_type: code_fix
severity: medium
related_components:
  - testing_framework
tags:
  - argparse
  - exit-codes
  - usage-error
  - cli-contract
  - choices-vs-validation
  - closed-set-args
---

# `argparse choices=` exits 2, repo `UsageError` exits 1 — exit-code clash

## Problem

`argparse` exits with code `2` for argument-parsing failures, including invalid `choices=`. The repo's documented CLI contract uses exit `1` for `UsageError` (operator-visible "you gave me a bad input"). Mixing them means an invalid value to a parameter with `choices=[...]` exits `2` while every other usage error exits `1`. Operators reading the exit-code table get one answer; the CLI gives a different one for one specific kind of input.

`backlink-publisher` CLIs document exit codes 0-6 as a contract. `argparse`'s exit-2 path is not on that table, and there's no `sys.exit(2)` in the source — it comes from `argparse.ArgumentParser.error()`.

## Symptoms

- A wrapper script `if backlink-publisher plan --channel invalid 2>/dev/null; then ... fi; case $? in 1) handle_usage;; *) handle_unknown;; esac` mis-routes invalid-channel inputs to the unknown branch.
- AGENTS.md's exit-code section accurately describes `UsageError → 1` but invalid channel inputs exit 2.
- A regression test that asserts `assert proc.returncode == 1` for invalid CLI input passes for `--seed-file does_not_exist.jsonl` but fails for `--channel does_not_exist`.

## What Didn't Work

- **Overriding `ArgumentParser.error()` to exit 1**. Works for the specific parser but breaks `--help` and `--version` semantics (which also use `error()` internally on bad subcommand combinations). Fragile and easy to regress.
- **Adding `argparse choices=[...]` everywhere consistently**. Doesn't solve the problem — it propagates exit-2 to more places. The clash is structural, not stylistic.
- **Documenting `2` as a valid exit code in the contract**. Hides the underlying inconsistency and forces every consumer of the CLI to special-case the same parameter family.

## Solution

For any parameter whose valid value is a known closed set (channels, events, output formats, schema names), **omit `choices=` from argparse** and validate post-parse. Route the failure through the repo's `UsageError`.

```python
# cli/plan_backlinks.py — wrong (exits 2 on invalid)
parser.add_argument(
    "--channel",
    choices=CHANNELS,            # argparse error path = exit 2
    required=True,
)
args = parser.parse_args()
```

```python
# cli/plan_backlinks.py — right (exits 1 via UsageError)
parser.add_argument(
    "--channel",
    required=True,
    # no choices= → argparse just stores the string
)
args = parser.parse_args()

if args.channel not in CHANNELS:
    raise UsageError(
        f"--channel must be one of {sorted(CHANNELS)}; got {args.channel!r}"
    )
```

`UsageError` is the repo's canonical "operator-visible bad input" exception (defined in `errors.py`). The CLI entry point catches it, prints a clean error message to stderr, and exits `1`. This is the documented contract.

The closed-set parameter families to apply this to:

- `--channel` / `--channels` (registered_consumer set)
- `--event` (events.consumers set)
- `--schema` (schema validators)
- Any future parameter where the valid set is enumerable and the operator-error case matters for downstream tooling

## Why This Works

`argparse`'s `choices=` is convenient for `--help` rendering (it shows the valid set inline). The cost is that validation lives at parse-time and routes through `argparse.ArgumentParser.error()`, which calls `sys.exit(2)` unconditionally. There is no clean way to hook into that path without subclassing the parser and breaking other consumers.

Moving validation to post-parse keeps the failure path uniform with every other operator-input failure. The `--help` rendering loses the inline choices list, but the docstring/epilog can carry the same info:

```python
parser.add_argument(
    "--channel",
    required=True,
    help=f"Publishing channel (one of: {', '.join(sorted(CHANNELS))})",
)
```

The downside is real but small. The upside is contract consistency, which matters for every script and CI guard that reads exit codes.

## Prevention

- **Lint rule** at PR time: grep for `choices=` in `cli/*.py`. Each hit needs justification or refactor to post-parse validation.

```bash
grep -nE '^\s*"--[a-z][a-z_-]+",\s*$' cli/*.py -A 5 | grep -B5 'choices='
```

- **Reviewer checklist** for new CLI flags: "is the valid set closed?" → if yes, post-parse. Add to the standards-reviewer checklist for `cli/*.py` diffs.
- **Exit-code test in `tests/test_cli_contract.py`**: parameterize over `(invalid_arg, expected_message_fragment)` and assert exit code is always `1` for operator-input failures.
- **AGENTS.md exit-code section** lists the contract; cross-link this doc so the next reader understands why `choices=` is avoided.

## Related Issues

- `errors.py:UsageError` — canonical exception type that exits 1 through the CLI entry point.
- `docs/solutions/workflow-issues/late-plan-revisions-skip-code-2026-05-20.md` — adjacent: exit-code drift between plan-doc and code (separate cause, same family of symptom).
- AGENTS.md → "Exit-code table (0-6)" — current documented contract.
- `cli/plan_backlinks.py:main` — entry point that catches `UsageError`.
