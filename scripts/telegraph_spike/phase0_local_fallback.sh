#!/usr/bin/env bash
# Telegraph Phase 0 — local fallback for cloud routines that lost GitHub OAuth
# on env_01SZV66MxSWAueB7niox9Mbn (2026-05-18). Designed for invocation by an
# hourly launchd watcher (~/Library/LaunchAgents/com.dex.bp-telegraph-phase0-watcher.plist).
#
# Modes:
#   bash phase0_local_fallback.sh watcher            — hourly entry; fires the
#                                                       per-day worker once on
#                                                       2026-05-25 / 06-01 / 06-08
#                                                       (any hour >= 09:00 local)
#   bash phase0_local_fallback.sh fire <t7|t14|t21>  — actually run recheck.py +
#                                                       post comment on PR #36
#
# Side effects (fire mode):
#   - Clones backlink-publisher fresh to /tmp/bp-tg-fire-<day>-<pid>
#   - Runs scripts/telegraph_spike/recheck.py against the 10-page manifest
#   - Posts a PR #36 comment with the full output table
#   - Does NOT edit docs/phase0/* or commit to docs/telegraph-phase0-report.
#     (Operator can copy the rel_t* values from the comment into the report later.
#     This keeps the local fallback cheap and conflict-free with the cloud routine
#     if it ever runs.)
#
# Self-disables after the T+21 fire (2026-06-08) by removing the launchd plist.

set -euo pipefail

# Make sure launchd-invoked runs find tools.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

readonly STATE_FILE="$HOME/.local/state/bp-tg-fallback-fired.log"
readonly PLIST_PATH="$HOME/Library/LaunchAgents/com.dex.bp-telegraph-phase0-watcher.plist"
readonly REPO_SLUG="redredchen01/backlink-publisher"
readonly TARGET_URL="https://51acgs.com"
readonly FIRE_HOUR_MIN=9   # do not fire before 09:00 local

mkdir -p "$(dirname "$STATE_FILE")"
touch "$STATE_FILE"

log() { printf '[bp-tg-fallback] %s\n' "$*"; }
err() { printf '[bp-tg-fallback ERROR] %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# fire <day>
# ---------------------------------------------------------------------------
fire() {
  local day="$1"
  case "$day" in
    t7|t14|t21) ;;
    *) err "fire: unknown day '$day'"; return 2 ;;
  esac

  local extra=""
  [[ "$day" == "t14" || "$day" == "t21" ]] && extra="--check-indexation"

  local workdir
  workdir="$(mktemp -d -t "bp-tg-fire-${day}-XXXXXX")"
  trap 'rm -rf "$workdir"' RETURN

  log "fire $day → clone $REPO_SLUG to $workdir"
  if ! gh repo clone "$REPO_SLUG" "$workdir" -- --depth=1 >&2; then
    err "gh repo clone failed — local gh CLI may have lost auth too"
    return 1
  fi

  cd "$workdir"

  # Ensure requests is importable.
  if ! python3 -c "import requests" 2>/dev/null; then
    log "installing requests --user"
    pip3 install --user requests >&2 || { err "pip install requests failed"; return 1; }
  fi

  log "running recheck.py --day $day $extra"
  if ! python3 scripts/telegraph_spike/recheck.py --day "$day" $extra --target-url "$TARGET_URL" >"$workdir/recheck.log" 2>&1; then
    err "recheck.py failed; tail:"
    tail -30 "$workdir/recheck.log" >&2
    return 1
  fi

  local output_md="scripts/telegraph_spike/run_output/recheck-${day}.md"
  if [[ ! -f "$output_md" ]]; then
    err "expected output $output_md not found"
    return 1
  fi

  local table summary
  table="$(cat "$output_md")"
  summary="$(awk '/^summary @/{flag=1} flag' "$workdir/recheck.log")"

  local body
  body="$(cat <<EOF
### Telegraph Phase 0 — ${day^^} recheck (local launchd fallback)

Cloud routine for ${day^^} could not run — GitHub OAuth on cloud env \`env_01SZV66MxSWAueB7niox9Mbn\` was denied on 2026-05-18 and never re-authorized. This comment was posted automatically by \`~/Library/LaunchAgents/com.dex.bp-telegraph-phase0-watcher.plist\` (hourly watcher → \`scripts/telegraph_spike/phase0_local_fallback.sh fire ${day}\`).

Posted at: $(date -u +%Y-%m-%dT%H:%M:%SZ) (UTC) / $(date +%Y-%m-%dT%H:%M:%S%z)

**recheck.py output (\`${output_md}\` in fresh clone):**

${table}

**Summary line:**

\`\`\`
${summary}
\`\`\`

**Next step for operator/agent:** copy the \`rel_${day}\` column values into the \`rel_${day}\` column of \`docs/phase0/2026-05-15-telegraph-indexation-report.md\` §3 table (match by idx). For ${day} == \`t14\`/\`t21\`, also fill the \`indexed_${day}\` column from the indexation results. If any row shows \`captcha\`, manually re-check in an incognito Google window.

If the cloud routine \`trig_*\` was eventually fixed (OAuth re-authorized) and also fired today, you may see two near-identical comments — pick the one that landed first, ignore the duplicate.
EOF
)"

  log "posting PR #36 comment ($(printf %s "$body" | wc -c) bytes)"
  if ! gh pr comment 36 --repo "$REPO_SLUG" --body "$body" >&2; then
    err "gh pr comment failed; printing body to stdout instead:"
    printf '%s\n' "$body"
    return 1
  fi

  log "fire $day complete; recording in $STATE_FILE"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%MZ)" "$day" >> "$STATE_FILE"
}

# ---------------------------------------------------------------------------
# watcher (hourly)
# ---------------------------------------------------------------------------
watcher() {
  local today hour
  today="$(date +%Y-%m-%d)"
  hour="$(date +%H)"

  if (( 10#$hour < FIRE_HOUR_MIN )); then
    log "watcher: hour $hour < $FIRE_HOUR_MIN, skip"
    return 0
  fi

  local day=""
  case "$today" in
    2026-05-25) day="t7" ;;
    2026-06-01) day="t14" ;;
    2026-06-08) day="t21" ;;
  esac

  if [[ -z "$day" ]]; then
    # Past T+21? Self-disable.
    if [[ "$today" > "2026-06-08" ]]; then
      log "past T+21 (2026-06-08); self-disabling"
      if [[ -f "$PLIST_PATH" ]]; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        rm -f "$PLIST_PATH"
      fi
    fi
    return 0
  fi

  if grep -qE " $day\$" "$STATE_FILE"; then
    log "watcher: $day already fired today (per $STATE_FILE), skip"
    return 0
  fi

  log "watcher: today=$today day=$day → fire"
  fire "$day"
}

case "${1:-}" in
  watcher) watcher ;;
  fire)
    [[ $# -ge 2 ]] || { err "usage: $0 fire <t7|t14|t21>"; exit 2; }
    fire "$2"
    ;;
  *)
    err "usage: $0 {watcher|fire <t7|t14|t21>}"
    exit 2
    ;;
esac
