# Unit 1b stealth-premise runners

Throwaway operator-supervised scripts. Run in order; stop at first success.

## Setup (once)

```bash
cd /Users/dex/YDEX/INPORTANT\ WORK/外链/0511_backlink\ \ publisher/bp-hashnode-bind
git worktree add ../bp-stealth-spike -b spike/hashnode-stealth-re-test origin/main
cd ../bp-stealth-spike
python3 -m venv .venv-spike
source .venv-spike/bin/activate
```

## Run order

```bash
# 1. patchright (fastest to try; drop-in API)
pip install patchright
patchright install chromium
BACKLINK_PUBLISHER_SPIKE_OUT=/tmp/hn-stealth-patchright \
  python3 docs/spike-notes/2026-05-22-hashnode-stealth-runners/01_patchright_run.py
# If verdict.json shows hashnode_session_captured=true → STOP, fill matrix, declare PASS.

# 2. playwright-stealth (only if patchright failed)
pip install playwright playwright-stealth
playwright install chromium
BACKLINK_PUBLISHER_SPIKE_OUT=/tmp/hn-stealth-pwstealth \
  python3 docs/spike-notes/2026-05-22-hashnode-stealth-runners/02_playwright_stealth_run.py

# 3. undetected-playwright (only if 1-2 failed; may be stale)
pip install undetected-playwright
BACKLINK_PUBLISHER_SPIKE_OUT=/tmp/hn-stealth-undetected \
  python3 docs/spike-notes/2026-05-22-hashnode-stealth-runners/03_undetected_run.py
```

Each runner:
- Opens a headed browser at https://hashnode.com/onboard
- Operator manually logs in (max 5 min)
- Operator presses Enter in the launching terminal when done
- Script captures final URL + hashnode.com cookies → `$BACKLINK_PUBLISHER_SPIKE_OUT/verdict.json`
- Exit code: 0 = session captured, 1 = no session, 2 = import failed, 3 = aborted

## After verdict

1. Fill in the matrix at `../2026-05-22-hashnode-playwright-stealth-spike.md`.
2. Tick PASS / FAIL / ABORTED.
3. Follow the "Followup actions after verdict" section.
4. Delete `/tmp/hn-stealth-*` directories + the spike worktree + venv.
