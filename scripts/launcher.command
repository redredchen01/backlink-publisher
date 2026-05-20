#!/usr/bin/env bash
# Backlink Publisher WebUI 启动腳本 — 雙擊即可用。
#
# 兩存放位置：
#   canonical: backlink-publisher/scripts/launcher.command  (git 追蹤)
#   operational: <workspace-root>/启动WebUI.command          (operator 雙擊；未追蹤)
# 兩份內容相同；任一份被改後請 cp 同步另一份。詳見 docs/plans/2026-05-20-014.

set -euo pipefail

# ---- script-dir + backlink-publisher 定位 ----
# scripts/launcher.command 跑時 SCRIPT_DIR=.../backlink-publisher/scripts，
# workspace-root/启动WebUI.command 跑時 SCRIPT_DIR=<workspace-root>。
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P)
if [[ -f "$SCRIPT_DIR/webui.py" ]]; then
  BP_DIR="$SCRIPT_DIR"
elif [[ -f "$SCRIPT_DIR/../webui.py" ]]; then
  BP_DIR=$(cd "$SCRIPT_DIR/.." && pwd -P)
elif [[ -f "$SCRIPT_DIR/backlink-publisher/webui.py" ]]; then
  BP_DIR="$SCRIPT_DIR/backlink-publisher"
else
  echo "❌ 找不到 webui.py（從 $SCRIPT_DIR 探測 ., .., backlink-publisher/ 都沒有）" >&2
  read -n 1 -s -r -p "按任意鍵關閉視窗…"
  exit 1
fi
cd "$BP_DIR"
OUR_CWD=$(python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$BP_DIR")

# ---- 預設 ----
START_PORT="${PORT:-8888}"
MAX_TRIES="${MAX_TRIES:-20}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
WEBUI_SCRIPT="${WEBUI_SCRIPT:-webui.py}"  # 測試時可 override 為 tests/manual/webui_crash_stub.py

echo "================================================"
echo "  Backlink Publisher WebUI"
echo "================================================"
echo ""

# ---- Python 解釋器 ----
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
  echo "✓ 使用 .venv/bin/python"
else
  PY="$(command -v python3 || true)"
  if [[ -z "${PY}" ]]; then
    echo "❌ 未找到 python3，請先安裝 Python 3.11+"
    read -n 1 -s -r -p "按任意鍵關閉視窗…"
    exit 1
  fi
  echo "⚠️  未找到 .venv，臨時用系統 ${PY}"
fi

# ============================================================
#  Helper functions (Unit 1 of Plan 2026-05-20-014)
# ============================================================

# is_our_webui PID
#   回 0（true）若 PID 屬於本專案 cwd 且 cmdline 含 webui.py。
#   別人專案的 webui.py / 不存在的 PID / 他 user 的進程都回 1（false）。
is_our_webui() {
  local pid="$1"
  local cwd_of_pid cmdline
  # `-a` 把 -p PID 與 -d cwd 由 OR 改為 AND（否則 lsof 會列出全系統所有 PID 的 cwd）。
  # field-mode 輸出 p<pid>/fcwd/n<path>；取 n 開頭那行 strip 首字元。
  cwd_of_pid=$(lsof -a -p "${pid}" -d cwd -Fn 2>/dev/null | awk '/^n/{print substr($0,2); exit}')
  [[ -z "$cwd_of_pid" ]] && return 1
  # realpath 統一比對（避免符號連結 / Trailing slash 假不等）
  cwd_of_pid=$(python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$cwd_of_pid" 2>/dev/null || echo "")
  [[ "$cwd_of_pid" != "$OUR_CWD" ]] && return 1
  cmdline=$(ps -p "${pid}" -o command= 2>/dev/null || echo "")
  [[ "$cmdline" == *webui.py* ]] || return 1
  return 0
}

# kill_and_wait PID PORT
#   SIGTERM → 5s 內輪詢 kill -0 確認 process 死 → SIGKILL fallback。
#   process 死後再 lsof 一次確認 port 真釋放；仍佔則回 1。
kill_and_wait() {
  local pid="$1" port="$2" i
  kill "${pid}" 2>/dev/null || true
  for i in 1 2 3 4 5; do
    sleep 1
    if ! kill -0 "${pid}" 2>/dev/null; then
      break
    fi
  done
  if kill -0 "${pid}" 2>/dev/null; then
    echo "  ↻ PID ${pid} SIGTERM 5s 內未退，發 SIGKILL" >&2
    kill -9 "${pid}" 2>/dev/null || true
    sleep 1
  fi
  # 最終確認 port 真釋放（極罕見有別人 grab）
  if lsof -t -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  ↻ PID ${pid} 已死但 port ${port} 仍被佔（他人 grab），不復用" >&2
    return 1
  fi
  return 0
}

# resolve_port
#   stdout: 找到的 port  / 全佔時為空
#   exit:    0 = 找到 / 1 = 全佔
#   stderr:  順延 / kill 提示
resolve_port() {
  local i candidate holder_pid
  for ((i=0; i<MAX_TRIES; i++)); do
    candidate=$((START_PORT + i))
    holder_pid=$(lsof -t -iTCP:"${candidate}" -sTCP:LISTEN 2>/dev/null | head -1)
    if [[ -z "${holder_pid}" ]]; then
      echo "${candidate}"
      return 0
    fi
    if is_our_webui "${holder_pid}"; then
      echo "↻ port ${candidate} 被本專案殘留 PID ${holder_pid} 佔用，kill 復用…" >&2
      if kill_and_wait "${holder_pid}" "${candidate}"; then
        echo "${candidate}"
        return 0
      fi
      echo "  kill 後仍佔，順延" >&2
    else
      echo "⚠️  port ${candidate} 被陌生 PID ${holder_pid} 佔用，順延" >&2
    fi
  done
  echo ""
  return 1
}

# 允許 source 載入 helper 不進 main loop（測試用）。
if [[ "${LAUNCHER_HELPERS_ONLY:-}" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi

# ============================================================
#  Main loop with restart rate-limit (Unit 3 of Plan 2026-05-20-014)
# ============================================================

# RESTART_LOG: 每次 crash 的 epoch timestamp。60s 視窗內第 4 次 crash 觸 abort。
RESTART_LOG=()
FIRST_RUN=true

while true; do
  # 每次迴圈頂重跑 resolve_port (R8：restart 前要重新探 port)。
  # set +e 包圍：resolve_port 全佔時 return 1，set -e 環境下 var=$(cmd) 的 exit status
  # 會繼承 cmd 的非零碼立刻 errexit，STATUS=$? 永遠不會跑到。
  set +e
  PORT=$(resolve_port)
  STATUS=$?
  set -e
  if [[ -z "${PORT}" || "$STATUS" -ne 0 ]]; then
    if [[ "$FIRST_RUN" == "true" ]]; then
      echo "❌ 連續 ${MAX_TRIES} 個端口都被佔用 (${START_PORT}–$((START_PORT+MAX_TRIES-1)))，放棄"
    else
      echo "❌ Restart 時 ${MAX_TRIES} 個端口全佔，放棄（已 restart ${#RESTART_LOG[@]} 次）"
    fi
    read -n 1 -s -r -p "按任意鍵關閉視窗…"
    exit 1
  fi

  URL="http://${BIND_HOST}:${PORT}"
  if [[ "${PORT}" != "${START_PORT}" ]]; then
    echo "↪︎ 改用空閒端口 ${PORT}（預設 ${START_PORT} 被佔）"
  fi

  export PORT BIND_HOST
  # .venv 是從舊目錄遷來的，editable install 失聯；用 PYTHONPATH 繞過。
  export PYTHONPATH="src${PYTHONPATH:+:${PYTHONPATH}}"
  # 讓 webui.py route exception 真退出，restart loop 才觀察得到。
  # 直接 `python webui.py` 不過 launcher 時 webui.py 預設仍 debug=True。
  export FLASK_DEBUG=0

  if [[ "$FIRST_RUN" == "true" ]]; then
    ( sleep 3 && open "${URL}" ) &
    FIRST_RUN=false
  fi

  echo "🚀 啟動 $WEBUI_SCRIPT on ${URL}  (restart=${#RESTART_LOG[@]}/3 in 60s)"
  echo "   關閉此終端視窗即可停止服務（Ctrl-C 也行）"
  echo ""

  # 暫時關 errexit：webui.py 非零退出是 restart loop 的正常觀察對象。
  set +e
  "${PY}" "$WEBUI_SCRIPT"
  EXIT=$?
  set -e

  # EXIT ∈ {0, 130} 都視為 operator 主動結束（不同 Werkzeug 版本對 Ctrl-C 走 0 或 130）
  if [[ "${EXIT}" -eq 0 || "${EXIT}" -eq 130 ]]; then
    echo ""
    echo "✓ 正常退出 (code=${EXIT})"
    break
  fi

  # ---- crash 路徑 ----
  now=$(date +%s)
  # bash 3.2 + set -u 對空陣列 "${arr[@]}" 會炸；用 ${arr[@]+"${arr[@]}"} 防禦展開。
  new_log=()
  for ts in ${RESTART_LOG[@]+"${RESTART_LOG[@]}"}; do
    if (( now - ts < 60 )); then
      new_log+=("${ts}")
    fi
  done
  new_log+=("${now}")
  RESTART_LOG=("${new_log[@]}")

  if [[ "${#RESTART_LOG[@]}" -ge 4 ]]; then
    echo ""
    echo "❌ 60s 內 4 次 crash（webui.py exit=${EXIT}），已 restart 3 次仍不穩，放棄"
    echo "   提示：檢查終端上方 traceback；若是 syntax / import 錯誤，修完再雙擊重來"
    read -n 1 -s -r -p "按任意鍵關閉視窗…"
    exit 1
  fi

  echo ""
  echo "↻ webui.py crash (exit=${EXIT})，restart 第 ${#RESTART_LOG[@]} 次（60s 窗口內）"
  sleep 1
done
