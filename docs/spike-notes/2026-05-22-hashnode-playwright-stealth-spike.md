# Plan 2026-05-20-016 — Unit 1b Stealth-Premise Spike (Playwright Forks)

**Status**: PENDING (operator to run)
**Date opened**: 2026-05-21
**Budget**: 1 day time-boxed (8h). Abort if no progress at 4h.
**Branch**: `spike/hashnode-stealth-re-test` (throwaway, no merge)
**Origin**: Adversarial review A1 challenge to plan-016 KTD-2.

---

## Why this spike

Plan-016's load-bearing premise is "Cloudflare blocks Playwright Chromium on hashnode.com". That conclusion came from Unit 1 spike `d818662` with **3 attempts** using only `--disable-blink-features=AutomationControlled` — the weakest possible stealth flag.

If any modern stealth fork clears CF in 1 day of testing, the entire Tranche B chrome-CDP architecture (Unit 0 cookie/CDP machinery, Unit 3 chrome session wrapper, Sec1 attack-surface controls, BIND_TIMEOUT bump) becomes unnecessary for hashnode. Cookie host_filter fix (Sec2) still ships standalone for telegraph.

**Cost of finding out**: 1 day. **Cost of NOT finding out**: ~4-5 days of Tranche B engineering that could be redundant.

---

## Libraries to test (in order — stop at first success)

| # | Library | Install | API style | Maintenance signal |
|---|---|---|---|---|
| 1 | **patchright** | `pip install patchright` | Drop-in replacement: `from patchright.sync_api import sync_playwright` | rebrowser-playwright fork; actively maintained as of 2025 |
| 2 | **playwright-extra + stealth** | `pip install playwright-stealth` | Plain Playwright + `stealth_sync(page)` from playwright_stealth | Most popular; standalone plugin |
| 3 | **undetected-playwright-python** | `pip install undetected-playwright` | `from undetected_playwright import Tarnished` (may have shifted) | Possibly stale; verify last release date before testing |
| 4 | (stretch) **camoufox** | See https://github.com/daijro/camoufox | Firefox-based, anti-fingerprint browser | Only if 1-3 all fail and there's time left |

---

## Test protocol (per library)

1. `pip install <library>` into the spike worktree's venv (NOT the main venv).
2. Run the matching runner script under `2026-05-22-hashnode-stealth-runners/`.
3. A headed browser opens at `https://hashnode.com/onboard`.
4. Operator manually attempts login (Google SSO, GitHub SSO, or email — whichever).
5. Max 5 minutes per attempt. If still on CF challenge or redirect-loop, ABORT and record FAIL.
6. If logged in: confirm `hashnode-session` cookie present + non-empty on `hashnode.com` apex.
7. Record outcome in matrix below.

---

## Outcome matrix (operator fills in)

| # | Library | Version | CF cleared? | `hashnode-session` captured? | Time spent | Notes |
|---|---|---|---|---|---|---|
| 1 | patchright | ___ | ☐ | ☐ | ___ | |
| 2 | playwright-extra-stealth | ___ | ☐ | ☐ | ___ | |
| 3 | undetected-playwright | ___ | ☐ | ☐ | ___ | |
| 4 | camoufox (stretch) | ___ | ☐ | ☐ | ___ | |

---

## Verdict decision

After the matrix is filled, pick ONE:

- [ ] **PASS — at least one library works.** Library used: ________. Action: open plan-016 amendment block proposing Tranche B rewrite to use this library; skip Unit 0 Fix 2/3 (Chrome 148 IPv6, --remote-allow-origins, CDP security controls). Unit 0 Fix 1 (cookie host_filter fail-closed) still ships because it benefits telegraph today. Unit 3 becomes ~50% smaller (no CDP session machinery; uses Playwright `launch_persistent_context` with stealth applied).

- [ ] **FAIL — all libraries blocked by CF.** Action: confirm original premise; Tranche A + B proceed as written. Document per-library failure mode for future re-evaluation (memory entry `[[hashnode-stealth-libraries-2026-05-22]]`).

- [ ] **ABORTED — timeboxed out before verdict reached.** Action: record what was tested + remaining unknowns; treat as FAIL-with-uncertainty (proceed with Tranche B but log that the question is still open).

---

## Library failure modes (record details if applicable)

### patchright
- Final URL when blocked:
- Visible CF state (challenge page / challenge passed but loop / blank / other):
- Console errors (if devtools accessible):
- Observed fingerprint leak (if known):

### playwright-extra-stealth
- (as above)

### undetected-playwright
- (as above)

---

## Followup actions after verdict

**If PASS**:
1. Commit this notes file (filled in) on `spike/hashnode-stealth-re-test`.
2. Open amendment comment on plan-016 with proposed Tranche B rewrite.
3. Re-deepen the plan: `/ce:plan deepen docs/plans/2026-05-20-016-feat-hashnode-browser-bind-plan.md` with amendment context.
4. Drop the chrome-backend bind for hashnode (Unit 2 `required_backend` → `"playwright"`).
5. Adjust KTD-2 / KTD-5 rationale to reflect new evidence.

**If FAIL**:
1. Commit this notes file (filled in).
2. Update memory `[[feedback-chrome-devtools-cdp-traps]]` with "stealth-fork test 2026-05-22 result" so we don't re-test for at least 6 months.
3. Proceed with Tranche A + B as written.

**Always**:
- Delete the spike worktree + venv after verdict.
- The runner scripts under `2026-05-22-hashnode-stealth-runners/` are throwaway — keep for reproducibility but never imported by production code.

---

*Created 2026-05-21 as plan-016 Unit 1b. Pending operator execution.*
