---
name: channel-probe
description: >
  Evaluate a candidate backlink channel BEFORE writing an adapter — a GO /
  NO-GO / NEEDS-CANARY triage. Runs an HTTP user-agent reachability matrix,
  a search-index check, and a real-browser backlink-surface inspection
  (outbound <a href> + rel), then maps the result to this project's
  dofollow / referral_value taxonomy and emits a ready-to-paste register()
  recommendation or retired-platforms note.
  Triggers: "渠道探针", "渠道分析", "评估渠道", "这个外链渠道能不能接",
  "新渠道 GO/NO-GO", "evaluate a backlink channel", "should we add this channel",
  "probe a channel", "is this platform worth an adapter".
metadata:
  version: "1.0.0"
  project: backlink-publisher
  precedes: "AGENTS.md → Adding a new publisher adapter"
---

# channel-probe

The standardized pre-flight for any new backlink channel. It exists because the
expensive way to learn a channel is dead is to build the adapter first. This
skill front-loads the cheap, decisive checks and produces an evidence-backed
verdict that feeds the registry decision (`register(..., dofollow=...,
referral_value=...)`) or a `docs/notes/retired-platforms/<channel>.md` note.

**Iron rule:** an HTTP 200 from a JS/SPA site proves NOTHING about whether a
public, linkable, indexable backlink surface exists. Never declare GO from HTTP
status alone — the browser tier is what confirms a real dofollow link. (This is
the exact trap a raw `curl` walk-through fell into for bloglovin: curl reported
all-403, the project UA reported 200-but-login-walled, and only the rendered
page revealed zero outbound links.)

## Inputs

- The channel **homepage** URL (required).
- One **content/post URL** on that channel if you can find a real example
  (strongly preferred — the homepage is often just a login screen; the content
  page is where the backlink would actually live).

If only a domain is given, find a representative content/post URL first
(WebSearch `site:<domain>` or the channel's own discovery pages).

## Phase 1 — HTTP reachability matrix (deterministic)

Run the repo's probe engine. It hits each URL with the project's REAL
`link_attr_verifier` preflight UA (imported live), a Googlebot UA, and a
desktop-browser UA:

```bash
python scripts/channel_probe.py "<homepage>" "<content_url>" --json
```

Read the `verdict` field:

| verdict | meaning | next |
|---|---|---|
| `no-go-unreachable` | nothing fetches it the way the pipeline would | go to Phase 4 → likely NO-GO |
| `needs-browser-tier` | reachable only by a JS browser and/or login-gated | Phase 2 + Phase 3 (mandatory) |
| `needs-canary` | cleanly HTTP-reachable by all UAs | Phase 2 + Phase 3 to confirm a real dofollow surface |

Surface the `signals` to the user verbatim — especially a Googlebot-403
(usually Cloudflare anti-spoofing by IP, NOT proof real Googlebot is blocked —
resolve it with the `site:` check, never assume) and any login-wall flag.

## Phase 2 — Search-index check

```
WebSearch: site:<domain>
```

Decisive question: are there **fresh, dated content/post pages** indexed, or
**only stale structural pages** (home / about / tos / signup / search landing)?
Only-structural-pages = Google is not crawling new content → backlinks placed
there carry no SEO value even if they exist. Note recency of what is indexed.

## Phase 3 — Browser tier (the verdict-maker)

Use a real, JS-capable browser. Prefer the Chrome MCP tools
(`mcp__claude-in-chrome__*`); fall back to `/connect-chrome`, `/browse`, or the
`web-access` skill. If multiple Chrome browsers are connected, ask the user
which one (do not pick).

Navigate to the **content/post URL** (not just the homepage), then:

1. Confirm what actually renders: real article content, a **login wall**, an
   empty SPA shell, a 404, or a redirect (watch the final URL — a bounce to
   `/login` is decisive).
2. Extract every outbound link and its `rel`. The decisive evidence is whether
   a real dofollow link to the source blog / target exists at all:

```js
(() => {
  const all = [...document.querySelectorAll('a[href]')];
  const outbound = all
    .filter(a => { try { return !new URL(a.href).hostname.includes(location.hostname.replace(/^www\./,'')); } catch { return false; } })
    .map(a => ({ href: a.href, rel: a.getAttribute('rel') || '(none)', text: (a.textContent||'').trim().slice(0,50) }));
  const seen = new Set(); const uniq = [];
  for (const o of outbound) if (!seen.has(o.href)) { seen.add(o.href); uniq.push(o); }
  const art = document.querySelector('article');
  return JSON.stringify({
    totalAnchors: all.length, outboundCount: uniq.length, outbound: uniq.slice(0,30),
    articleTextLen: art ? art.innerText.trim().length : null,
    bodyTextLen: document.body.innerText.trim().length
  }, null, 2);
})()
```

Interpret:
- **No outbound links except the site's own socials** → no backlink surface →
  NO-GO regardless of HTTP status. (This was bloglovin's killer.)
- Outbound link to source/target present → record its `rel`:
  - no `rel` / `rel` without `nofollow` → dofollow candidate.
  - `rel="nofollow"` (or `ugc`/`sponsored`) → nofollow; judge `referral_value`.
  - href routed through a redirect interstitial (e.g. `…/go?to=`, `link.<domain>`)
    → equity likely stripped → treat as nofollow dead-weight (see the `jianshu`/
    `csdn`/`juejin` rationales in `registry.py::_REJECTED_PLATFORMS`).
- `articleTextLen`/`bodyTextLen` near-zero on a content URL → gated/empty shell,
  not a usable page.

## Phase 4 — Synthesize the verdict

Map the evidence to a recommendation in this project's vocabulary:

| Verdict | When | Recommended action |
|---|---|---|
| **GO (dofollow)** | renders public content + dofollow link to target survives, bots can fetch, fresh pages indexed | proceed to AGENTS.md "Adding a new publisher adapter"; `register(..., dofollow="uncertain")` until a live canary flips it to `True` |
| **GO (nofollow, high referral)** | public + indexed but `rel=nofollow`, yet real DA / referral / entity value (cf. devto, mastodon, notion) | `register(..., dofollow=False, referral_value="high", rationale=…)` |
| **NEEDS-CANARY** | plausibly fine but dofollow/indexation unconfirmed | publish one real post + run the dofollow canary before committing |
| **NO-GO** | no public surface (login-walled / SPA shell), no outbound link to target, redirect-interstitial strips equity, or only structural pages indexed | write `docs/notes/retired-platforms/<channel>.md` (Decision date, Evidence, Decision, "If reconsidered" criteria) — and, if it was ever registered, add a `_REJECTED_PLATFORMS` entry in the same PR |

Always present: the HTTP matrix, the index finding, the browser link/rel
evidence (quoted), and the verdict with a one-line rationale. For a NO-GO,
draft the retired-platforms note ready to paste. For a GO, point at the adapter
SOP and the `register()` line to add.

## Boundaries

- Read-only reconnaissance. Never log in, create accounts, accept ToS, or
  publish during a probe.
- The script never writes config; the browser step is navigation + read only.
- Do not infer indexation from a faked-Googlebot HTTP status — verify via
  `site:`. Do not infer a backlink surface from HTTP 200 — verify in-browser.
