# Dofollow canary close-out runbook

Plan: [`2026-05-25-003-feat-dofollow-canary-closeout-plan.md`](../plans/2026-05-25-003-feat-dofollow-canary-closeout-plan.md).
Origin: [`2026-05-25-remaining-channel-viability-expansion-requirements.md`](../brainstorms/2026-05-25-remaining-channel-viability-expansion-requirements.md).
Probe evidence: [`docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md`](../spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md).

`livejournal` and `txtfyi` ship registered `dofollow="uncertain"`. This runbook is
the **operator** procedure that closes the R4 canary loop: publish a canary, read
the now-observable link-attribute verdict (added to publish output by plan-003
Unit 1), and either flip the registration to `dofollow=True` or retire the platform.
The flip is a separate operator PR — code does not do it automatically, by design.

## Why there is no CI gate

A committed-TOML + CI gate was considered and rejected (plan-003 Key Decisions): for
two platforms it is over-built, its "forcing" is illusory (a contradiction check only
fires *after* an unforced manual edit), and a canary verdict is not CI-measurable the
way SLOC is. The forcing mechanism is instead a **dated tracked issue with an owner**
(step 5) plus the now-observable verdict.

## Procedure (per uncertain platform)

1. **Throwaway-account gate — do this first, abort if unconfirmed.**
   Verify the bound account is a **dedicated throwaway**, not the operator's primary
   identity. This matters most for `livejournal`: its XML-RPC secret is
   password-equivalent and **un-revocable** — there is no token to rotate, so a leak
   can only be remediated by changing the account password. Inspect the bound
   credentials under `$BACKLINK_PUBLISHER_CONFIG_DIR` (default `~/.config/backlink-publisher/`).
   If you cannot confirm it is a throwaway, **stop** — do not publish a canary.

2. **Publish one fresh canary.** Run a single seed row through
   `plan-backlinks → validate-backlinks → publish-backlinks` for the platform in
   `--publish` mode. It must be a **fresh** publish, not a `--resume` — the checkpoint
   does not persist the verification verdict, so a resumed publish will not carry it.

3. **Read the verdict — but inspect the *target backlink*, not the page-wide flag.**
   The publish output row now contains a `link_attr_verification` object. Note that
   `verify_link_attributes` scans **every** `<a>` on the page, so `nofollow_detected`
   reflects nav/footer/related links too — it is page-wide noise, not the verdict for
   your backlink. Open the published canary URL and inspect the `rel` on the specific
   anchor pointing at your target. Record dofollow vs nofollow from **that** anchor.

4. **Act on the verdict (the flip PR).**
   - **Backlink is dofollow** → open a PR that, in one change:
     - edits the platform's `register(...)` in `publishing/adapters/__init__.py` to `dofollow=True`,
     - drops the now-unneeded `rationale=` / `referral_value=` args and **deletes the orphaned `_R["<platform>"]` entry** in `_nofollow_rationales.py`,
     - adds a regression test pinning `dofollow_status("<platform>") is True` (so it cannot silently regress to `uncertain`).
   - **Backlink is nofollow** → decide by referral value. `txtfyi` is `referral_value="low"`, so a nofollow result makes it a **retire** candidate. `livejournal` is `referral_value="high"`, so a nofollow result may still keep it as a `dofollow=False` referral channel (record the rationale).

5. **Deadline (the forcing mechanism).** Open a dated tracked issue per uncertain
   platform with an owner and a target date ("run canary for `<platform>` by `<date>`,
   else retire"). This replaces the inert `# R4 canary pending` comment. Consider
   `/schedule` to wake the owner near the date.

## Caveat: closing the loop ≠ backlinks

A platform flipped to `dofollow=True` is registered but **idle** — it produces zero
backlinks until quota/proportion wiring allocates publishing volume to it. That
quota-wiring follow-on is currently **unscoped** (no plan named). "Canary closed" is
not "in production rotation."

## Probe-driven decisions (auditable record — R4)

These verdicts come from the Phase 0 read-only probe (see `findings.md`, dated
2026-05-25). Recorded here so they are not re-litigated. Roster: the parent effort's
six candidate platforms degrade to five viable after bloglovin retires.

| Platform | Verdict | Evidence (findings.md) | Date |
|---|---|---|---|
| livejournal | GO — shipped `dofollow="uncertain"`, canary pending (this runbook) | XML-RPC alive, no Cloudflare; body links `rel="noopener noreferrer"` (no nofollow token) | 2026-05-25 |
| txt.fyi | GO — shipped `dofollow="uncertain"`, canary pending (this runbook) | Pure HTML form POST, nonce + form_time, no captcha | 2026-05-25 |
| bloglovin.com | **NO-GO — platform effectively dead** | Rebranded→Activate 2018, abandoned 2021; homepage 403 Cloudflare to bots; no blog-post service | 2026-05-25 |
| justpaste.it | **CONDITIONAL-deferred** (resolved, not open) | JS SPA, no native form; raw POST insufficient — needs XHR/API reverse-eng or browser recipe | 2026-05-25 |
| teletype.in | **CONDITIONAL-deferred** (resolved, not open) | JS SPA; account + JS editor; credential path (Unit 6-style), not the credential-less form path | 2026-05-25 |
| jkforum.net | **HOLD / likely NO-GO** (out of scope) | Bad-neighborhood reputation risk (operator implicated in pornographic ads); Discuz default-nofollow; promo-post deletion | 2026-05-25 |
| hashnode | Excluded (out of scope) | GraphQL API moved behind Pro paywall 2026-05-13; removed in PR #204 | 2026-05-22 |

SPA verification (headless render for justpaste.it / teletype.in if revived) is deferred
with those platforms; the regex verifier suffices for livejournal and txt.fyi.
