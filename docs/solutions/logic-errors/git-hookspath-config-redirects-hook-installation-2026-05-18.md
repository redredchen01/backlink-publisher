---
title: "Git core.hooksPath silently redirects hook installation to an unexpected directory"
date: 2026-05-18
category: docs/solutions/logic-errors
module: scripts / git-hook installer
problem_type: logic_error
component: tooling
severity: medium
symptoms:
  - "Hook installer reports success and prints an install path that does NOT live inside the current repo's `.git/hooks/`"
  - "The reported path may point inside a sibling repo, a global hooks directory, or a stale path whose surrounding `.git/config` no longer exists"
  - "`git rev-parse --git-path hooks` resolves to a different location than `git rev-parse --git-common-dir`/hooks"
  - "The hook may still fire correctly for the original repo (git follows the redirect on read too) — the surprise is the LOCATION, not the behavior"
root_cause: config_error
resolution_type: code_fix
related_components:
  - development_workflow
tags:
  - git
  - git-hooks
  - core-hookspath
  - installer
  - git-config
  - dogfooding
  - fixture-tests-miss-environmental-bugs
---

# Git `core.hooksPath` silently redirects hook installation to an unexpected directory

## Problem

A hook installer that uses `git rev-parse --git-path hooks` to locate the install destination will silently follow any `core.hooksPath` config override (per-repo, global, or system) without warning the user. The hook lands wherever the resolved path says — which can be a different repo's `.git/hooks/`, a shared hooks directory used by multiple checkouts, or a stale path whose surrounding `.git/config` no longer exists.

In our specific case, this repo's *local* `.git/config` had:

```ini
[core]
    hooksPath = /Users/dex/.../0511_opencli_backlink by opencode/backlink-publisher/.git/hooks
```

That path is a sibling project's `.git/hooks/` directory. The sibling's `.git/config` no longer existed (only the `.git/hooks/` subdirectory survived a partial cleanup), so on first glance the target looked "not a real git repo" — but git happily wrote the hook there anyway, because git only consults `core.hooksPath` as a directory path, not a fully-formed repo identity.

## Symptoms

- `bash scripts/install-post-merge-hook.sh` prints `installed: <path>` where `<path>` is outside the repo you ran it in.
- Verification via `ls "$(git rev-parse --git-path hooks)/post-merge"` succeeds, but the path is suspicious-looking.
- `git rev-parse --git-path hooks` does NOT equal `$(git rev-parse --git-common-dir)/hooks`.
- The hook is shared across every git operation in any repo whose `core.hooksPath` resolves to the same location.

## What Didn't Work

The installer's test suite never caught this. Test fixtures use freshly-initialized repos via `git init`, which never have `core.hooksPath` set. The bug only surfaces when the installer is run against a repo with non-default git config — exactly the kind of environmental coincidence fixture tests systematically miss.

Manually inspecting the installer code doesn't catch it either, because the code looks correct: `git rev-parse --git-path hooks` is the documented way to locate a repo's hooks directory. The surprise is that this command honors `core.hooksPath` overrides.

## Solution

Before writing the hook, compare the resolved hook directory against the default (the repo's own `.git/hooks/`). If they differ, print a clear warning naming the override path AND the config scope (local/global/system) that's setting it:

```bash
# Detect non-default hooksPath: if `git rev-parse --git-path hooks` resolves
# outside the repo's own --git-common-dir, the user has a core.hooksPath
# override active. The hook is shared with any other repos that resolve to
# the same hooksPath — warn so the user understands the blast radius before
# we write the file.
COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir)"
COMMON_DIR_ABS="$(cd "$COMMON_DIR" && pwd -P)"
HOOK_DIR_ABS="$(mkdir -p "$HOOK_DIR" && cd "$HOOK_DIR" && pwd -P)"
EXPECTED_HOOK_DIR_ABS="$COMMON_DIR_ABS/hooks"
if [[ "$HOOK_DIR_ABS" != "$EXPECTED_HOOK_DIR_ABS" ]]; then
  echo "warn: git core.hooksPath is overridden — hook will be written to a shared location" >&2
  echo "      installing to: $HOOK_DIR_ABS" >&2
  echo "      (this repo's own .git/hooks would normally be: $EXPECTED_HOOK_DIR_ABS)" >&2
  for scope in local global system; do
    val=$(git -C "$REPO_ROOT" config --$scope --get core.hooksPath 2>/dev/null || true)
    [[ -n "$val" ]] && echo "      core.hooksPath ($scope): $val" >&2
  done
  echo "      this hook will fire for every git operation in any repo that resolves to" >&2
  echo "      the same hooksPath." >&2
fi
```

Do NOT silently override the user's config and force the hook into the default `.git/hooks/`. Users sometimes intentionally point `core.hooksPath` at a shared directory (e.g., to share hooks across all their repos via the global config). The right behavior is to surface the situation, not to second-guess it.

If the hook script itself is sensitive to where it runs (e.g., it sources scripts at `$REPO_ROOT/scripts/...`), make sure the script is safe-by-default for any repo that might resolve to the same hooksPath. Concretely: gate the work on a marker file that only exists in repos meant to use the hook:

```bash
SAFETY="$REPO_ROOT/scripts/_worktree_safety.sh"
[[ -f "$SAFETY" ]] || exit 0  # not our repo, no-op silently
```

That way, the hook fires harmlessly in unrelated repos that happen to share the same `core.hooksPath`.

## Why This Works

`git rev-parse --git-path <relpath>` returns a path that git itself would use to find the file. For files inside `.git/`, that resolution honors any config that redirects the lookup — including `core.hooksPath`, which is precisely a "redirect hooks elsewhere" knob. So an installer using `--git-path hooks` to write a hook is asking git "where would you LOOK for the hook," and getting back "wherever the user told me to look." Faithfully writing there is correct behavior; the missing piece is just letting the user know that the answer might surprise them.

Comparing against `--git-common-dir/hooks` (the un-redirected default) gives a stable reference point: any divergence means a config override is active. Iterating `git config --<scope> --get core.hooksPath` for each of `local`, `global`, `system` then tells the user *which* config layer is setting the override — useful for both diagnosis and undo.

## Prevention

1. **Always compare `--git-path hooks` against `--git-common-dir/hooks` before writing.** If they differ, warn loudly. The warning should name the install location, the default location, and the config scope responsible. Anything less than this trio buries the surprise.

2. **Make hooks safe-by-default for cross-repo sharing.** Gate the hook's real work on a marker file that only exists in repos that want it. The minimal pattern:

   ```bash
   MARKER="$REPO_ROOT/scripts/_my_project_marker.sh"
   [[ -f "$MARKER" ]] || exit 0
   ```

   Without this, a hook installed via `core.hooksPath` will fire in every unrelated repo sharing the path and produce confusing errors.

3. **Test installers against fixtures with non-default `core.hooksPath` set.** Mirror the dogfooding-vs-fixture-coverage pattern from sibling solution `awk-field-split-truncates-paths-with-spaces-2026-05-18.md`:

   ```python
   _git(repo, "config", "core.hooksPath", str(some_other_dir / "hooks"))
   # ... then assert the installer prints the warning and exits 0
   ```

4. **When debugging "where did my git hook go," check `git config --get core.hooksPath` first.** Most "hook didn't fire" or "hook fired in the wrong repo" mysteries trace back to this single config key being set somewhere unexpected. The local, global, and system scopes are all valid places to look.

## Related

- Sibling solution `docs/solutions/logic-errors/awk-field-split-truncates-paths-with-spaces-2026-05-18.md` — caught during the same dogfooding pass. Both share the **fixture tests pass, real environment breaks** meta-pattern; both fixes ship in the same PR.
- For background on the dogfooding pattern, see `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md` — that one is about plan-time review catching what unit tests miss; this pair is about run-time dogfooding catching what fixture tests miss. Both are forms of "exercise the artifact in a context the test harness can't synthesize."
