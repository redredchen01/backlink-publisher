---
title: "Medium HttpOnly cookie taxonomy — distinguish auth from anti-bot/CSRF before trusting structural inference"
date: 2026-05-19
category: best-practices
module: backlink-publisher / cli._bind.recipes.medium
problem_type: best_practice
component: cookie_sanity_gate
severity: high
applies_when:
  - "Adding or updating a `_cookie_sanity_passes` chain for any channel that lives behind Cloudflare or other anti-bot vendors"
  - "Touching `MEDIUM_AUTH_COOKIE_WHITELIST` / `MEDIUM_ANONYMOUS_TRACKING_NAMES` in `src/backlink_publisher/cli/_bind/recipes/medium.py`"
  - "Designing a sanity gate for a new channel recipe (Velog, Blogger, future) that uses structural HttpOnly inference as a fallback"
  - "Investigating a false-positive bind on a logged-OUT profile that nonetheless passed cookie sanity"
tags:
  - cookie-sanity
  - httponly
  - cloudflare
  - csrf
  - medium
  - bind-gate
  - false-positive-risk
  - spike-output
---

# Medium HttpOnly cookie taxonomy — auth vs ambient

## Guidance

On medium.com apex, **not every long-lived HttpOnly cookie is an auth cookie**. The structural heuristic that Plan 003 R5 ships (`httpOnly == True` AND `expires - now > 7 days` AND `name NOT in tracking-blacklist`) is a safety net for the case where Medium rotates its auth cookie names — but it is *only* safe if the blacklist stays current with whatever ambient HttpOnly cookies Medium and its anti-bot vendor add.

Spike 3a (2026-05-19, against a logged-in test account) enumerated medium.com's HttpOnly cookies. The result is six entries split into three families. **Only two are auth**:

| Cookie          | Expires        | Family          | Counts as auth? | Rationale |
|-----------------|----------------|-----------------|-----------------|-----------|
| `sid`           | ~2027 (~1.5y)  | AUTH            | Yes             | Session id; canonical Medium login signal |
| `rid`           | ~2027 (~1.5y)  | AUTH            | Yes             | Refresh id; paired with `sid` |
| `uid`           | ~2027          | Analytics       | No              | Legacy anonymous user id; set pre-login too |
| `cf_clearance`  | ~2027          | Cloudflare      | No              | Anti-bot clearance; set on ANY visitor (incl. logged-out) that passes the JS challenge |
| `_cfuvid`       | session-only   | Cloudflare      | No              | Cloudflare visitor id; not bound to auth |
| `xsrf`          | ~2026          | CSRF            | No              | CSRF token, emitted independently of auth |

The decision encoded in the code:

- `MEDIUM_AUTH_COOKIE_WHITELIST = frozenset({"sid", "rid"})` — positive identification, beats structural inference (`recipes/medium.py:58`).
- `MEDIUM_ANONYMOUS_TRACKING_NAMES` extended with `cf_clearance`, `_cfuvid`, `xsrf` so the structural fallback does not false-positive a logged-out visitor that merely cleared Cloudflare (`recipes/medium.py:70-87`, lines 84-86 added by Spike 3a).

## When to Apply

**Apply when**:

- Adding any cookie name to `MEDIUM_AUTH_COOKIE_WHITELIST` — require positive evidence (network trace, manual logout/login diff) that the cookie is *only* set on authenticated sessions. "It looks long-lived and HttpOnly" is exactly the trap this spike was meant to surface.
- Designing the `_cookie_sanity_passes` analogue for a new channel that sits behind Cloudflare, Akamai, PerimeterX, or similar. The anti-bot vendor's cookies (`cf_clearance`, `_cfuvid`, `ak_bmsc`, `_px3`, …) will look structurally indistinguishable from auth cookies; pre-populate the blacklist before turning on the sanity gate.
- Reviewing a structural-only sanity gate (no positive whitelist): treat that as a P1 risk. Whitelist + blacklist + structural fallback is the three-layer pattern; dropping the whitelist makes the gate trust ambient cookies the moment the channel adds a new HttpOnly tracker.

**Do NOT apply when**:

- The channel does not use HttpOnly cookies at all (rare, but `localStorage`-only sites exist). The taxonomy is not relevant; the bind probe needs a different shape entirely.

## Why This Works

Three separate failure modes are blocked by splitting the cookie set this way:

1. **Cloudflare false-positive on logged-OUT.** A logged-out browser that solved a Turnstile/JS challenge has `cf_clearance` (HttpOnly, ~1-year expiry). Structural-only inference would accept it. Blacklisting `cf_clearance` + `_cfuvid` closes that path.
2. **CSRF token false-positive.** `xsrf` is HttpOnly with multi-month expiry but is emitted independently of session state. Blacklisting it forces the gate to find an actual auth cookie.
3. **Auth-name rotation resilience.** If Medium one day renames `sid` → `sessionid` (Velog already did this kind of rename), the whitelist will silently miss it — but the structural fallback (HttpOnly + long expiry + not in blacklist) will still accept the new cookie *provided* the blacklist has not gone stale. The whitelist gives a fast positive on the current names; the structural fallback gives forward-compat. Both are needed.

The cost of getting this wrong is a bind that "succeeds" against a logged-out profile, then the publish step blows up at the first authenticated action, with a stack trace that points at the publisher, not the bind probe — i.e. the failure is misattributed and hard to diagnose.

## Examples

The current `_cookie_sanity_passes` body (`recipes/medium.py:103-132`) shows the layered pattern in code:

```
1. if name in MEDIUM_AUTH_COOKIE_WHITELIST: return True   # positive hit, fast path
2. if not c.get("httpOnly"): continue                     # structural prerequisite
3. if expires < now + 7 days: continue                    # short-lived → not a real session
4. if name in MEDIUM_ANONYMOUS_TRACKING_NAMES: continue   # known ambient cookie
5. return True                                            # structural fallback accepts
```

Spike 3a's output is what populates step 1 (the whitelist) and what *adds three names* to step 4 (the blacklist). Both edits were necessary; either one alone would have left a known false-positive path open.

## Prevention

1. **For any new channel recipe**, run the equivalent of Spike 3a once before writing the sanity gate: dump the cookie set on a logged-in profile, then dump it again on a logged-out fresh profile, and **diff**. The cookies that are only present logged-in are whitelist candidates; the cookies present in both are blacklist candidates. Structural-only design without this diff is guessing.
2. **When the anti-bot vendor is known**, pre-seed the blacklist from the vendor's published cookie names (Cloudflare: `cf_clearance`, `_cfuvid`, `__cf_bm`; Akamai: `ak_bmsc`, `bm_sv`, `bm_sz`) before the spike, then let the spike confirm or extend.
3. **Re-run the spike when bind starts false-positiving in the wild**. Medium and similar SaaS rotate cookie names and add new ambient trackers without notice; the taxonomy here is one data point from 2026-05-19. If a future bind passes sanity but publish fails on 401, the first hypothesis is "ambient cookie crept into the structural fallback" — re-dump and update the blacklist before debugging deeper.
4. **Keep the whitelist narrow.** "Both `sid` and `rid` look like auth, let's add `uid` too" is the failure mode — `uid` is set pre-login. Whitelist additions require the logged-out-diff evidence, not pattern-matching on the name.

## Caveats

- This taxonomy is a single observation against one Medium account on 2026-05-19. Cookie sets can vary by region (GDPR opt-in flow alters which trackers ship), by A/B bucket, and over time. Treat the table as the *current* shape, not a permanent contract. The structural fallback exists precisely so the gate degrades gracefully when this table goes stale.
- The whitelist intentionally does not include `uid` even though it is HttpOnly + long-expiry on logged-in sessions — because it is *also* set on logged-out sessions. The auth signal is "presence of `sid` or `rid`", not "presence of any long-lived HttpOnly cookie".

## Related Issues

- Plan: `docs/plans/2026-05-19-003-feat-medium-bind-hardening-plan.md` — Plan 003, Spike 3a is the cookie-enumeration step that produced this table; R5 is the section that wires it into the recipe.
- Code: `src/backlink_publisher/cli/_bind/recipes/medium.py:58` (`MEDIUM_AUTH_COOKIE_WHITELIST`), `:70-87` (`MEDIUM_ANONYMOUS_TRACKING_NAMES`, with lines 84-86 added by Spike 3a), `:103-132` (`_cookie_sanity_passes` layered gate).
- Cross-channel risk surface: Velog and Blogger recipes face the same "anti-bot vs auth" disambiguation. Velog sits behind Cloudflare too (see `docs/phase0/2026-05-15-velog-spike-report.md`); when their bind recipes adopt sanity gates, the Spike 3a methodology — logged-in/logged-out cookie diff — should be repeated per channel rather than assuming Medium's blacklist transfers.
