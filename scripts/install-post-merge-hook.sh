#!/usr/bin/env bash
# Install a per-clone git post-merge hook that detects newly-merged bp-* worktrees
# and (by default) notifies the user. With BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1
# in the user's shell environment, the hook auto-removes clean candidates after
# the dirty-state guard.
#
# Run once per clone:  bash scripts/install-post-merge-hook.sh
# The hook is per-clone — git hooks are not committed. Re-run after fresh clone.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
HOOK_DIR="$(git -C "$REPO_ROOT" rev-parse --git-path hooks)"
HOOK_PATH="$HOOK_DIR/post-merge"

if [[ -e "$HOOK_PATH" ]] && ! grep -q "BACKLINK_PUBLISHER_WORKTREE_HOOK" "$HOOK_PATH" 2>/dev/null; then
  echo "error: a different post-merge hook already exists at $HOOK_PATH" >&2
  echo "       inspect it; merge manually or remove it and re-run this installer" >&2
  exit 1
fi

mkdir -p "$HOOK_DIR"
cat > "$HOOK_PATH" << 'HOOK_EOF'
#!/usr/bin/env bash
# BACKLINK_PUBLISHER_WORKTREE_HOOK — installed by scripts/install-post-merge-hook.sh
# Detects bp-* worktrees whose branch was just merged into origin/main and either
# notifies (default) or auto-removes (if BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1)
# after the dirty-state safety check.

set -euo pipefail

# Only act when post-merge fires in the main worktree on main branch (typical for
# `git pull` after a PR lands). Skip when running in a bp-* feature worktree or
# during a sub-branch merge — those don't indicate a PR landing on main.
current_branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || echo)"
[[ "$current_branch" != "main" ]] && exit 0

REPO_ROOT="$(git rev-parse --show-toplevel)"
SAFETY="$REPO_ROOT/scripts/_worktree_safety.sh"
[[ -f "$SAFETY" ]] || exit 0  # helpers not present, skip silently

# Re-use the prune script in dry-run mode to list candidates, then act based on env.
PRUNE="$REPO_ROOT/scripts/prune-stale-worktrees.sh"
[[ -x "$PRUNE" ]] || chmod +x "$PRUNE" 2>/dev/null || exit 0

# shellcheck disable=SC2155
local_out="$("$PRUNE" --dry-run 2>&1 || true)"
candidates=$(echo "$local_out" | grep -c '^would remove: ' || true)
[[ $candidates -eq 0 ]] && exit 0

echo ""
echo "[post-merge hook] $candidates stale worktree(s) detected after merge:"
echo "$local_out" | grep '^would remove: ' | sed 's/^/  /'

if [[ "${BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE:-0}" == "1" ]]; then
  echo "[post-merge hook] BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1 — auto-removing"
  "$PRUNE" --force
else
  echo "[post-merge hook] run \`bash scripts/prune-stale-worktrees.sh\` to clean up"
  echo "[post-merge hook] or set BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1 for auto-removal next time"
fi
HOOK_EOF

chmod +x "$HOOK_PATH"
echo "installed: $HOOK_PATH"
echo ""
echo "the hook runs after every \`git merge\` / \`git pull\` on the main branch."
echo "it only NOTIFIES by default. for auto-removal, add to your shell rc:"
echo "  export BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1"
