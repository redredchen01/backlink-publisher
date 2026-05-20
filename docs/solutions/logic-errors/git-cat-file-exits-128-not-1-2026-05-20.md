---
title: "`git cat-file -e` exits 128 not 1 for missing paths — discriminate via stderr + pin locale"
date: 2026-05-20
category: docs/solutions/logic-errors
module: scripts + cli helpers that call git plumbing
problem_type: logic_error
component: tooling
symptoms:
  - "Script returns wrong branch on `if git cat-file -e ...; then ...`"
  - "Test passes locally on English locale, fails on a German/Chinese CI runner"
  - "`git merge-base --is-ancestor` returns exit 128 with stderr `\"unknown commit\"` instead of the expected 1"
root_cause: wrong_api
resolution_type: code_fix
severity: medium
related_components:
  - testing_framework
  - documentation
tags:
  - git
  - exit-codes
  - cat-file
  - merge-base
  - locale-pinning
  - stderr-discrimination
  - lc-all-c
---

# `git cat-file -e` exits 128 not 1 for missing paths

## Problem

`git` documentation says certain plumbing commands exit `1` when an object does not exist. Reality: they exit `128` and write a human-readable error to stderr. Scripts that treat "exit != 0" as one undifferentiated failure (or that branch on `exit == 1`) misread the result and run the wrong code path.

The two most common offenders in this repo:

- `git cat-file -e <commit>:<path>` — documented as "exits 0 if blob exists, 1 if not." Actual: 128 if `<commit>` doesn't exist; 128 if `<path>` doesn't exist in `<commit>`; 0 if both exist.
- `git merge-base --is-ancestor <a> <b>` — documented as "0 = ancestor, 1 = not ancestor." Actual: 0 = ancestor, 1 = reachable but not ancestor, 128 = unknown object (e.g., `<a>` doesn't exist in this clone).

The 128-vs-1 distinction is load-bearing: `1` means "the question makes sense, answer is no"; `128` means "the question is malformed, I don't know how to answer." Scripts conflating them produce wrong answers, not just wrong error messages.

## Symptoms

- A check like `if git cat-file -e origin/main:src/x.py; then SHIP=1; fi` ships when it shouldn't, because every error path (missing branch, missing path, missing repo) takes the `else` branch identically.
- `git merge-base --is-ancestor` on a freshly-cloned shallow CI returns 128 (object unknown) where local returns 1 (not ancestor), and the script branches differently.
- Tests pass on English-locale dev machines, fail on a CI runner with `LANG=de_DE.UTF-8` because the script's `grep "does not exist"` doesn't match `existiert nicht`.

## What Didn't Work

- **Branching on exit code alone.** `[ $? -eq 1 ]` misses the 128 case; `[ $? -ne 0 ]` conflates legitimate "no" answers with malformed queries.
- **Suppressing stderr to avoid noise.** Now the stderr substring that would discriminate is gone, and the only signal left is the exit code, which doesn't carry enough information.
- **Trusting the man page.** The man pages document the success path correctly and the failure paths optimistically. Empirical testing across git versions (2.30 through 2.47) confirms 128 is the actual exit for the missing-object cases.

## Solution

Always discriminate via stderr substring **and** pin the locale to `C`:

```bash
# Right: distinguish 128 (malformed) from 1 (not ancestor)
LC_ALL=C LANG=C git merge-base --is-ancestor "$A" "$B" 2>/tmp/git_err
case $? in
  0)   echo "$A is ancestor of $B" ;;
  1)   echo "$A is reachable but not ancestor of $B" ;;
  128)
    if grep -q "Not a valid object name" /tmp/git_err; then
      echo "ERROR: $A or $B does not exist in this clone"
    else
      echo "ERROR: git failed with unexpected stderr:"
      cat /tmp/git_err
    fi
    ;;
  *)   echo "ERROR: unexpected exit $?" ;;
esac
```

```python
# Python equivalent
import subprocess

def commit_path_exists(commit: str, path: str, cwd: str | None = None) -> bool:
    """Return True iff `path` exists in `commit`, distinguishing 'missing' from 'unknown'."""
    env = {"LC_ALL": "C", "LANG": "C"}
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:{path}"],
        cwd=cwd, env={**os.environ, **env},
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 128:
        stderr = result.stderr.lower()
        if "does not exist" in stderr or "not a valid" in stderr:
            return False
        # Unexpected 128: re-raise so caller doesn't silently treat as "missing"
        raise RuntimeError(f"git cat-file failed: {result.stderr!r}")
    raise RuntimeError(f"unexpected git exit {result.returncode}: {result.stderr!r}")
```

The two non-negotiables:

1. **`LC_ALL=C LANG=C`** so the stderr substring is in English regardless of CI environment locale.
2. **Inspect stderr** for the discriminating substring, not just the exit code.

## Why This Works

Git's exit-code policy is documented but inconsistent across commands:

| Exit | Meaning (across most git commands) |
|------|------------------------------------|
| 0    | Success — the question has a positive answer |
| 1    | Success — the question has a negative answer (e.g., "not ancestor") |
| 128  | Failure — git couldn't process the request (malformed input, unknown ref, no repo, ...) |
| 129  | Failure — bad option/usage |

The `1` vs `128` split tracks "answerable vs unanswerable." Scripts that need to distinguish "object missing because the path doesn't exist in the commit" from "object missing because the commit itself isn't in this clone" need 128's stderr to tell them apart — exit `1` doesn't exist on the missing-object path.

`LC_ALL=C` is necessary because git localizes its stderr. The English substring `"does not exist"` is the most stable marker across versions, but only if locale is pinned. CI runners with non-English locales translate the message and silently break the discriminator.

## Prevention

- **Lint rule**: grep for unguarded git plumbing in scripts:

```bash
# Find `git cat-file -e`, `git merge-base --is-ancestor`, etc. used in conditionals
grep -rnE 'git (cat-file -e|merge-base --is-ancestor|rev-parse --verify)' scripts/ | \
    grep -v 'LC_ALL'
```

Any hit without `LC_ALL=C` or stderr inspection is a candidate for the same bug.

- **Reviewer checklist for git-plumbing PRs**:
  - Is the exit code handled per-value (0, 1, 128) or just `!= 0`?
  - Is stderr inspected for discrimination?
  - Is locale pinned?

- **Test with a missing ref**: regression tests for git-plumbing helpers should include a "commit does not exist in this clone" case, asserting the helper raises an explicit error instead of returning a false "missing" answer.

- **Document the contract in the helper docstring**, including the 128 case and the locale pin, so the next reader doesn't trust the git man page.

## Related Issues

- `docs/solutions/logic-errors/fetch-head-in-common-gitdir-2026-05-20.md` — adjacent git-plumbing portability bug.
- PR #98 doc-review pass — caught this pattern in plan-claims-gate code before it landed (one of the document-review save examples).
- `man git-cat-file`, `man git-merge-base` — official docs; treat as starting point, verify exit codes empirically.
- `LC_ALL=C` documentation under glibc — required for any English-stderr-substring match.
