# Bloglovin — Retired Platform

**Decision date:** 2026-05-25
**Verdict:** NO-GO — platform effectively dead
**Status:** Not registered, not planned. No adapter exists or should be created.

## Evidence

| Signal | Detail |
|---|---|
| Rebranded | Bloglovin renamed to Activate (influencer marketing SaaS) in 2018 |
| Abandoned | Last blog-related update December 2021; blog-post service discontinued |
| Bot-blocking | bloglovin.com homepage returns HTTP 403 via Cloudflare to non-browser clients |
| No blog service | No public endpoint exists to create or retrieve blog posts |

Source: Phase 0 read-only probe, 2026-05-25.
Full evidence: [`docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md`](../../spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md)
Runbook context: [`docs/runbooks/2026-05-25-dofollow-canary-closeout.md`](../../runbooks/2026-05-25-dofollow-canary-closeout.md)

## Why No Adapter

Bloglovin has no blog-post API, no stable HTML form, and blocks automated access via Cloudflare.
There is nothing to publish to. A browser-recipe approach would also fail because there is no
post-creation UI to drive.

## If You Are Reconsidering This

Re-probe only if there is concrete evidence the platform revived (new API docs, new blog
endpoint, removed Cloudflare gate). Do not re-probe on rumor. File a fresh `/ce:brainstorm`
session with the new evidence rather than reopening this file.
