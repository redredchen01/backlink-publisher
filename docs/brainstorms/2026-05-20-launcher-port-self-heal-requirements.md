---
date: 2026-05-20
topic: launcher-port-self-heal
---

# 启动WebUI.command Port Self-Heal & Crash Auto-Restart

## Problem Frame

Operator 雙擊 `启动WebUI.command` 啟動 WebUI。當前腳本（workspace-root，70 行 bash）在 8888 被佔時**直接順延到 8889/8890/…**，最多試 20 個 port。三個體感問題：

1. 重複雙擊（殘留進程沒清乾淨）會讓 URL 一直漂移到 8889+，operator 書籤失效、找不到自己的服務。
2. webui.py 跑挂（網絡閃斷、unhandled exception、Flask reloader 卡死）後終端直接關閉，operator 要重新雙擊。
3. 兩個問題疊加時體驗最差：上一次留下的 zombie 佔著 8888，這次又起在 8889，operator 開了 8889 結果發現空的（zombie 還活著但無法服務）。

## Requirements

**Port 自愈（啟動階段）**
- R1. 啟動時若 8888 被佔，先識別**佔用者是否為本專案的 webui.py 殘留進程**（cwd 等於 `backlink-publisher/` 且 cmdline 含 `webui.py`），是則 `kill` 並等 port 釋放後復用 8888。
- R2. 若佔用者**不是**本專案 webui.py（IDE、其他 dev server、系統服務），保持當前行為——順延到 8889/8890/…，**不得** kill 陌生進程。
- R3. kill 後須驗證 port 真的釋放（`lsof` 輪詢，最多等 5 秒）；超時則 fallback 到 R2 順延邏輯。
- R4. R1/R2/R3 任一分支都要在終端明確告知 operator 發生了什麼（"kill 殘留 PID X 後復用 8888" / "8888 被陌生進程 PID Y 佔用，改用 8889"）。

**運行時自愈（webui.py 崩潰後）**
- R5. webui.py 非正常退出（exit code ≠ 0 且非 SIGINT/SIGTERM）時自動 restart。
- R6. Restart 速率限制：**60 秒滾動窗口內最多 3 次** restart；第 4 次直接 abort，顯示錯誤摘要 + log 提示 + `read -n 1` 等按鍵後關窗。
- R7. Operator 按 Ctrl-C（SIGINT）→ 正常退出，**不** restart。
- R8. 每次 restart 要重新跑 R1-R4 的 port 自愈流程（port 可能在崩潰瞬間被搶）。

## Success Criteria

- 連續雙擊兩次 `启动WebUI.command`：第二次自動 kill 第一次的殘留，URL 仍是 8888。
- 在另一個終端 `python3 -m http.server 8888` 佔住 → 雙擊腳本，腳本識別非自家進程，順延到 8889 啟動，**不** kill 陌生進程。
- 在 webui.py 裡 `raise RuntimeError` 觸發 crash → 腳本自動 restart 第二次。連續 crash 4 次 → abort 並顯示錯誤窗。
- Ctrl-C 一次乾淨退出，不會 restart。

## Scope Boundaries

- 不引入 launchd/systemd/supervisord 等外部 process manager——保持單檔 `.command` 雙擊體驗。
- 不改 webui.py 本身（不加 healthcheck endpoint、不加 pidfile 寫入；識別純靠 `lsof` + `ps`）。
- 不處理多 operator 多 instance 並存場景（單用戶單機假設）。
- 不寫 systemd-style status 命令（沒有 `./启动WebUI.command status`，純啟動腳本）。

## Key Decisions

- **「自己的進程」識別法**：`lsof -nP -iTCP:$PORT -sTCP:LISTEN -t` 拿 PID → `ps -p $PID -o command=` + `lsof -p $PID -d cwd` 雙重比對 cwd 和 cmdline。寫進可測試的 shell function `is_our_webui()`，方便手動驗。
- **Kill 策略**：先 `kill $PID`（SIGTERM）→ 輪詢 1s × 5 等 port 釋放 → 還在就 `kill -9 $PID`。避免直接 `-9` 砸掉 Flask 還沒寫完的 session state。
- **Restart 計數**：bash array 存最近 3 次 restart timestamp，每次 crash 比對 `now - timestamps[0] < 60` 判定速率限制。純 bash 內存，腳本退出即重置（簡單，符合 lightweight scope）。
- **port 自愈在 restart loop 內**：R8 要求 — 把 port 探測+kill 邏輯抽成 function，啟動時跑一次，每次 restart 前也跑一次。
- **不寫 pidfile**：lsof+ps 已經夠識別「自己的」進程，pidfile 引入新的 stale-file 問題（雙擊 crash 後 pidfile 殘留）反而複雜化。

## Dependencies / Assumptions

- macOS 環境（`.command` 雙擊體驗、`lsof -nP -iTCP`、`ps -p ... -o command=` 都是 macOS/BSD 語法）。Linux 不目標支援。
- `lsof` 與 `ps` 可用且不需要 sudo（macOS 預設可查自己 user 的進程）。
- webui.py 啟動失敗（exit 非 0 且 < 5 秒）也走 R5/R6 restart 路徑——同一速率限制覆蓋。

## Next Steps

→ `/ce:plan` for structured implementation planning（單檔 bash 重構，但有 4 個 function 抽取 + restart loop + 速率限制 + 多分支告知文案，值得先 plan 一輪定 function 邊界與測試手法）
