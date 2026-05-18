---
title: "awk $2 silently truncates worktree paths containing spaces"
date: 2026-05-18
category: docs/solutions/logic-errors
module: scripts / worktree tooling
problem_type: logic_error
component: tooling
severity: medium
symptoms:
  - "Shell helper reports 0 candidates and 0 skipped, when there are clearly worktrees to evaluate"
  - "`git worktree list --porcelain` parser produces truncated paths (e.g. `/Users/me/PROJECT` instead of `/Users/me/PROJECT NAME/sub`)"
  - "Every worktree path compares equal to every other in subsequent logic — masking the dispatch as a silent no-op"
  - "Fixture-based unit tests pass; the bug only manifests when running against a real workspace whose path contains spaces"
root_cause: logic_error
resolution_type: code_fix
related_components:
  - testing_framework
tags:
  - shell
  - bash
  - awk
  - field-splitting
  - path-with-spaces
  - git-worktree
  - dogfooding
  - fixture-tests-miss-environmental-bugs
---

# `awk $2` silently truncates worktree paths containing spaces

## Problem

A bash helper that parses `git worktree list --porcelain` looked superficially correct:

```bash
git worktree list --porcelain | awk '
  /^worktree / { path=$2; next }
  /^HEAD / { head=$2; next }
  /^branch / { branch=$2; sub(/^refs\/heads\//, "", branch); print path "|" head "|" branch; next }
  ...
'
```

Run from a workspace whose absolute path contained two spaces (`…/0511_backlink  publisher/…`), the helper produced 12 lines of output where every `path` column was the truncated string `/Users/dex/YDEX/INPORTANT`. The downstream loop then compared each truncated path to the main worktree's also-truncated path, matched on every iteration, and `continue`d before ever incrementing a counter. The summary printed `candidates: 0, skipped: 0`. **The script silently did nothing.**

## Symptoms

- `bash scripts/prune-stale-worktrees.sh --dry-run` returns "no stale worktrees" even though `git worktree list` clearly shows worktrees that should be evaluated.
- The summary line shows `candidates for removal: 0` AND `skipped (unmerged): 0` AND `skipped (dirty): 0` — that triple-zero is the diagnostic giveaway (a real run would show at least the skipped categories populated).
- The bug does NOT surface in the test suite. Pytest's `tmp_path` is rooted under `/tmp/pytest-of-<user>/` which contains no spaces.

## What Didn't Work

Initially the tests passed, the script's prose matched what the bash *seemed* to do, and the only way to notice the bug was to actually run the script against the live workspace. Adding more fixture-based tests with clean paths would not have caught it — the trigger (a space in the path) is environmental, not behavioral.

## Solution

Replace `awk $2` (which field-splits on whitespace and silently truncates everything past the first space) with `awk substr($0, N)` (which slices the full line by character offset, ignoring whitespace):

```bash
# BEFORE — broken on path-with-spaces
git worktree list --porcelain | awk '
  /^worktree / { path=$2; next }
  /^HEAD / { head=$2; next }
  /^branch / { branch=$2; sub(/^refs\/heads\//, "", branch); print path "|" head "|" branch; next }
'

# AFTER — uses character-offset slicing
git worktree list --porcelain | awk '
  /^worktree / { path=substr($0, 10); next }      # past "worktree " (9 chars + space)
  /^HEAD / { head=substr($0, 6); next }           # past "HEAD "
  /^branch / { branch=substr($0, 8); sub(/^refs\/heads\//, "", branch); print path "|" head "|" branch; next }
'
```

Apply the same fix to every place that reads a path out of the porcelain output. In our case there was a second site — a `main_wt` lookup using the same `awk $2` pattern — that needed the same fix.

## Why This Works

Git's `--porcelain` format uses single-space delimiters between the field tag (`worktree`, `HEAD`, `branch`, `detached`) and the value, and the value can contain any character including spaces. `awk $N` invokes field-splitting on whitespace by default, which splits on every space (not just the first) and silently discards everything past the first split. `substr($0, N)` slices the whole line by character offset and ignores field-splitting entirely, so embedded spaces in the value are preserved.

The downstream silent-skip was a separate symptom of the same root cause: once every parsed path equaled every other parsed path, the `[[ "$path" == "$main_path" ]] && continue` guard fired on every iteration. That guard's fragility under string truncation was the second-order amplifier.

## Prevention

1. **Add a regression test that exercises a path containing spaces.** Synthetic fixtures default to clean paths (`tmp_path`, `mktemp -d`) and so systematically miss this class of bug. Concretely, build the fixture under an intermediate directory whose name contains a space:

   ```python
   spaced_root = tmp_path / "has spaces in name"
   spaced_root.mkdir()
   # ...build the rest of the fixture under spaced_root...
   ```

2. **Dogfood shell scripts against the real environment before declaring them done.** Run the script from the actual workspace — not just the test harness — and confirm the output matches what `git worktree list` (or other ground-truth tools) would suggest. A `--dry-run` mode makes this safe.

3. **Treat triple-zero summaries as a smell, not a result.** When a tool reports "0 candidates AND 0 skipped AND 0 errors," verify the loop body fired at least once. A run that processes zero items is almost always a parser/filter bug, not a correctly-empty input.

4. **For new shell parsers of structured-text output, prefer single-line-prefix slicing (`substr`, `cut -c`, parameter expansion `${line#prefix }`) over field-splitting when the value can contain whitespace.** Field-splitting is fine when the values are guaranteed atomic (SHAs, integers, identifiers); it's not safe for filesystem paths, branch names with slashes, or any user-supplied free-form string.

## Related

- See `docs/solutions/logic-errors/git-hookspath-config-redirects-hook-installation-2026-05-18.md` — the sibling bug from the same dogfooding pass. Both bugs share the meta-pattern: **fixture-based unit tests pass while environmental coincidences in the real workspace break the script.** Test against synthetic-clean fixtures AND dogfood against the real environment before merge.
- Memory `[Worktree Concurrent Switching]` documents adjacent worktree-tooling failure modes worth bundling into any new worktree-related helper script.
