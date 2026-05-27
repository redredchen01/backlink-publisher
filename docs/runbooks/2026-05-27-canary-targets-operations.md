# Canary-targets operations runbook

Plan: [`2026-05-27-001-feat-adapter-contract-canary-plan.md`](../plans/2026-05-27-001-feat-adapter-contract-canary-plan.md).
Origin: [`2026-05-27-adapter-contract-canary-requirements.md`](../brainstorms/2026-05-27-adapter-contract-canary-requirements.md).
Prior art (read first): [`2026-05-25-dofollow-canary-closeout.md`](2026-05-25-dofollow-canary-closeout.md) — this runbook **automates** that manual dofollow-canary loop. The throwaway-account gate, the "inspect the *target* anchor not the page-wide flag" rule, and "canary closed ≠ in production" all carry over verbatim.

`canary-targets` is a **read-only** CLI verb that re-fetches one long-lived "canary post" per dofollow-tier platform and asserts your own backlink is still present and still dofollow. It is **advisory by default** — it surfaces drift loudly (alarm + dashboard red + publish-time WARNING) but does **not** silently halt publishing. Per-platform `hard_skip` opt-in is the only path to an actual skip.

This runbook covers the parts that are *not* in the verb: one-time canary-post **seeding**, **marker discipline**, **external scheduling**, the **operator action loop**, and the honest **threat-coverage** boundary.

---

## 1. Threat coverage — read this before trusting a green run

The verb's "green" verdict label is **`link-alive`**, deliberately **not** `healthy`. Here is exactly what v1 does and does not catch:

| Failure mode | evergreen (v1, all cohort) | L3 real-publish (Phase 2, blogger/ghpages only) |
|---|---|---|
| Existing post's `rel` retroactively flipped to `nofollow` / stripped | caught | caught |
| Existing post gets a `noindex` (meta / `X-Robots-Tag`) added | caught | caught |
| **Forward publish-path drift** (auth/schema/selector break — a *new* post publishes empty or fails) | **BLIND** | caught |
| Platform death / canary post rot | advisory (`canary-stale`) | caught |

**Be honest about this in every status report:** v1 evergreen re-fetches static pages that were published under the *old* contract, so it catches **retroactive rewrites** of existing posts. It is **structurally blind to forward publish-path drift** — the headline threat from the Problem Frame — because nothing publishes a fresh post to exercise the live publish path. Forward coverage is **Phase 2** (L3 publish-and-delete), and only for `blogger` + `ghpages` (the only two platforms with a verified clean delete primitive).

So: a `link-alive` cohort means "the existing dofollow backlinks are still alive," **never** "the publishing pipeline is working." Never let a green canary run be read as a publish-path all-clear.

---

## 2. The cohort

The cohort is computed dynamically: every registered platform with `registry.dofollow_status(name) is True`. **Do not** use the `referral_value` predicate (orthogonal axis; it is `None` for the dofollow tier, so it would yield an empty set).

As of this writing the True cohort is **blogger, medium, telegraph, velog, ghpages**. `livejournal` is registered `dofollow="uncertain"` and joins the cohort automatically the moment its closeout-runbook canary flip lands (`dofollow=True`) — no edit to `canary-targets` is needed. The verb asserts the cohort is non-empty at startup and fails loud if it is empty.

A cohort platform that has **no `[canary.<platform>]` config entry** is reported as a first-class **`not-configured`** verdict and listed loudly (a coverage assertion), never silently absent and never miscounted as advisory.

---

## 3. Seeding a canary post (one-time, per platform)

> Seeding is a one-time operator action. It is **out of the verb's automation scope** but is a hard prerequisite — there is nothing to re-fetch until a canary post exists. Seed **before** running `canary-targets` for real on that platform.

### 3.1 Seed via the REAL adapter publish path — not the platform UI

Each platform's canary post **MUST be created by running the real adapter publish path** (`plan-backlinks → validate-backlinks → publish-backlinks --publish` for that platform), **not** hand-pasted into the platform's web editor.

Why this matters: the whole point is to validate **real adapter output**. If you hand-craft the post in the UI, you are validating a hand-made artifact, not what the adapter actually emits — and any byte-level difference between the adapter's HTML and your hand-made HTML means the canary is measuring the wrong thing. Use the same pipeline the production publish would use.

This is also why the canary is **fresh-publish only, never `--resume`** (carryover from the closeout runbook): a resumed publish does not re-derive the link-attribute verdict.

### 3.2 Record the post into config

After the adapter publishes, record into `[canary.<platform>]` in `config.toml` (under `$BACKLINK_PUBLISHER_CONFIG_DIR`, default `~/.config/backlink-publisher/`):

```toml
[canary.blogger]
post_url        = "https://your-blog.example.com/2026/05/canary.html"   # the LIVE published URL
expected_target = "https://your-site.com/landing-page"                  # the backlink href you expect to find dofollow
marker          = "<see §4 — a per-post VARYING private sentinel>"       # private; gates drift-confirmed
hard_skip       = false                                                  # opt-in; leave false for advisory default
```

- `post_url` — the live published URL the verb re-fetches.
- `expected_target` — your backlink's canonical href; the verb asserts *this specific anchor* exists and is dofollow (page-wide `nofollow_detected` is **not** a valid verdict — closeout-runbook lesson).
- `marker` — a private sentinel that must be present in the seeded post (see §4). Its presence proves "this really is the canary page" and **gates `drift-confirmed`**: marker-present + anchor-gone is the strongest drift signal and is never downgraded to advisory; marker-absent is treated as "can't read it" → advisory.
- `hard_skip` — `false` (default) = advisory only. `true` = opt-in to actual publish-skip when the platform is drift-quarantined (see §6).

> Use placeholder domains in any committed doc or example (`your-site.com`, `*.example.com`). Never paste a real operator domain into `docs/solutions/` or anywhere outside `docs/plans/` / `docs/brainstorms/`.

### 3.3 Throwaway-account gate (carryover from closeout runbook)

The canary post is published from a bound account. Confirm that account is a **dedicated throwaway**, not the operator's primary identity — **before** publishing. This matters most for `livejournal`: its XML-RPC secret is password-equivalent and **un-revocable** (there is no token to rotate), so a leak can only be remediated by changing the password. If you cannot confirm the bound account is a throwaway, **stop** — do not seed.

**Forward-looking note:** v1 `canary-targets` is read-only — it never publishes, so this gate applies only to the one-time seeding step. Any future **Phase 2 L3 real-publish** path MUST use a throwaway account as a fail-closed gate, and `livejournal` is **hard-excluded from real-publish entirely** because its secret is un-revocable. v1 does not publish, so this is forward-looking.

---

## 4. Marker discipline

The marker is a private sentinel embedded in each seeded canary post. The verb requires it to be present on re-fetch before it will call anything `drift-confirmed`.

**The marker MUST vary across every footprint dimension — it must NOT be a constant string reused on every post.** A constant sentinel reused across all canary posts becomes a cross-page **byte-signature**: a stable feature that a platform's spam-cluster detection (footprint cluster key) can key on, which is exactly the footprint regression gate's threat model. A self-inflicted cluster key defeats the purpose.

Concretely, vary the marker across all the footprint cluster-key dimensions the `footprint` engine tracks — `attr_order`, `rel`, `target`, `preceding_char` — plus the surrounding structure, **not** just a single high-entropy token dropped into otherwise identical boilerplate.

### Verify with the footprint extractor

After seeding (or before, on the candidate corpus of canary posts), run the `footprint` CLI / extractor across the set of canary posts and confirm **no dimension is pushed to ≥95% prevalence**. 95% is the footprint gate's alarm threshold; crossing it means the marker has become a concentration signature.

If a dimension legitimately must sit high (small cohort makes some dimensions naturally concentrated), use the footprint gate's **`OVERRIDE.md` break-glass** pattern: drop a minimum-viable override file carrying a `reason:` line. The footprint gate then warns-and-passes and prints the override's age (`git log`-derived) so a forgotten override surfaces in every PR. Treat an override as a temporary, visible exception — not a silent suppression.

---

## 5. Scheduling is EXTERNAL

The `canary-targets` verb is **pure on-demand CLI**: exit 0, JSONL on stdout, recon on stderr, advisory posture. **No cron is baked into the verb.** Scheduling is layered on top by a harness Cron / RemoteTrigger routine — the same precedent as the **Telegraph Phase 0 remote routines** (one-time remote agents that `git clone` and run a read-only recheck on a fixed schedule, writing results back).

Set up a routine that periodically invokes `canary-targets` and surfaces the JSONL receipts to the operator action loop (§6).

### Cadence and detection latency

Detection latency ≈ **cadence × debounce-N** (the verb debounces: a single bad read does not flip a verdict; it takes N consecutive confirmations).

- **Target max detection latency: ≤48h** (operator-tunable default).
- **Starting cadence: weekly** (operator-tunable default).

These are **starting defaults to calibrate after observing the real advisory distribution** — run advisory-only for a while, watch how often each platform produces ambiguous reads, then tighten cadence and debounce-N so that `cadence × N` stays under the chosen detection-latency ceiling. The ceiling and the cadence/N must be set **together**: e.g. if you want ≤48h detection and N=2 debounce, cadence must be ≤24h, not weekly. The latency ceiling is a real operator decision (it bounds how long a silently-rotted backlink can sit before you notice), not a purely technical knob.

---

## 6. Operator action loop

An advisory that no one reads is a dead dashboard. This is the loop that keeps it alive.

- **Who:** the platform/SEO operator on rotation reviews the `canary-targets` advisory output (the harness routine's receipts + the health dashboard's canary section + any TG/alert surface that read-side-joins `canary-health.json`).
- **Cadence:** at minimum once per scheduled run (§5). Advisories have **dedup / cooldown** so a persistently-degraded platform does not re-spam the same WARNING every run — this is deliberate alert-fatigue defense (the project has prior L1/L2/L3 alert-dedup precedent). Cooldown means "don't re-fire," not "resolved" — a degraded platform stays degraded until acted on.

### Decision tree (per platform showing a non-`link-alive` verdict)

```
verdict?
├─ drift-confirmed  (200 + readable body + MARKER PRESENT + target anchor nofollow / href gone)
│     → (a) INVESTIGATE THE ADAPTER. The platform retroactively changed an existing
│           post's rel/noindex. Confirm by opening post_url, inspect the anchor at
│           expected_target. Decide: is this a platform policy change (lower the
│           platform's referral value / consider retiring) or an adapter contract break?
│     → if a high-value platform is now reliably nofollow and you must keep publishing
│           there in the meantime: (c) flip [canary.<platform>] hard_skip = true (opt-in
│           quarantine) so publish removes it from the payload. Default stays advisory;
│           hard_skip is a deliberate, per-platform operator choice — never automatic.
│
├─ canary-stale / needs-reseed  (K consecutive advisory rounds — the post likely rotted)
│     → (b) RE-SEED. The canary post is gone/unreadable (deleted, account expired,
│           platform dead). Re-run the §3 seeding flow to publish a fresh canary post
│           and update post_url/expected_target/marker in config. Do NOT leave it as
│           permanent advisory noise.
│
├─ advisory  (soft-404 / null / ssrf-blocked / auth-expired / throttled / marker missing /
│             interstitial unprovable)
│     → "can't read it" ≠ "drift." Do NOT quarantine. If auth-expired, re-bind the
│           channel (closeout runbook / bind-channel). If transient (throttle/null),
│           let the next scheduled run reconfirm. Only escalate if it persists into
│           canary-stale.
│
└─ not-configured  (platform in cohort but no [canary.<platform>] entry)
      → coverage gap. Seed it (§3) or accept the gap explicitly. This is loud on purpose.
```

The three terminal actions are exactly: **(a) investigate the adapter**, **(b) re-seed a rotted canary post**, **(c) flip the platform to opt-in `hard_skip=true`**.

> `hard_skip` re-arm: once a `hard_skip=true` platform recovers (M≥2 consecutive `link-alive`, or a cooldown window elapses), quarantine lifts automatically; rapid flapping escalates to a manual alert rather than silently re-arming. This machinery only has a live consumer once at least one platform is opted into `hard_skip`; with zero opt-ins, the gate is advisory-WARNING-only.

---

## 7. Honest limitations (state these plainly)

- **Not a publish-path health check** — see §1. `link-alive` ≠ pipeline healthy.
- **Not an indexability oracle** — the verb only checks page-level `noindex` (meta / `X-Robots-Tag`), which is necessary-not-sufficient for actual indexing.
- **Independent-UA caveat (UA cloaking)** — the canary fetch uses an independent UA (so the target can rate-limit it separately). A platform *could* UA-cloak: serve dofollow to the canary UA while serving nofollow to real traffic/crawlers. So the dofollow verdict is a **contract-drift signal**, not a guarantee of what a real visitor or search crawler sees — same disclaimer level as "not an indexability oracle."
- **Evergreen generalization caveat** — a platform may apply a *different* link policy to old posts / dormant accounts than to fresh campaign posts. The evergreen signal therefore may not generalize to what a new publish would get. This compounds the §1 forward-drift blindness.

---

## Cross-references

- [`docs/runbooks/2026-05-25-dofollow-canary-closeout.md`](2026-05-25-dofollow-canary-closeout.md) — the manual loop this verb automates; throwaway gate, target-anchor inspection rule, "closed ≠ in production."
- [`docs/plans/2026-05-18-007-feat-footprint-regression-gate-plan.md`](../plans/2026-05-18-007-feat-footprint-regression-gate-plan.md) — footprint dimensions (`attr_order/rel/target/preceding_char`), 95% alarm threshold, `OVERRIDE.md` break-glass.
- Telegraph Phase 0 remote routines — the external-scheduling precedent (read-only recheck on a fixed cron, results written back).
- [`docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`](../solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md) — "inspect the target anchor's rel, not the page-wide flag."
