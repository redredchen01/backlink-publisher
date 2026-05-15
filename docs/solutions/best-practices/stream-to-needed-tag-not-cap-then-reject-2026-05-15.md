---
title: "Stream to the tag you need, don't cap-then-reject — title extraction is a 256KB head-window problem, not a 1MB whole-body problem"
date: 2026-05-15
category: best-practices
module: backlink-publisher / content_fetch
problem_type: best_practice
component: html_fetch_pipeline
severity: medium
applies_when:
  - "Writing or modifying any fetcher whose actual need is a single tag / region of an HTML document (title, meta, og:*, canonical, first heading)"
  - "Hitting a 'body too large' / 'response oversize' rejection on a real HTML page and being tempted to raise the cap"
  - "Reading the full response body into memory before parsing — even though only the prefix carries the data you need"
  - "Choosing between (a) raise the cap, (b) per-host override, (c) decompress-aware fetch, (d) streaming-with-early-exit — for a single-tag use case"
tags:
  - http-fetch
  - streaming
  - title-extraction
  - body-cap
  - early-exit
  - root-cause
  - band-aid
  - resource-bounding
---

# Stream to the needed tag — read-all-then-cap is the wrong abstraction for single-tag use cases

## Guidance

When the only thing a fetcher needs from a response is a **bounded prefix** (HTML `<head>` for title/og:title/canonical, the first N rows of a CSV, the JSON envelope of a paginated API), **stream until the prefix is satisfied and stop reading** — do not read the full body into memory and then reject on size.

The "read entire body, cap at N MB, reject as `body_too_large` if exceeded" pattern looks like a safety control but is structurally wrong for this class of use case: real well-formed inputs will exceed the cap, the cap-bump becomes a recurring band-aid, and the supposed safety property ("we never read more than N MB") is achievable far more cheaply by stopping early.

The first question to ask of any "we hit the cap" complaint is:

> What did we actually need from this response, and where in the byte stream did it appear?

If the answer is "the head" or "the first record" or "the envelope", streaming with an early-exit predicate is the right shape. Raising the cap is the wrong shape.

## When to Apply

**Apply when**:

- The fetcher's success contract is "extract a specific element / region", not "process the whole document".
- The required region is reliably located in a prefix (e.g. HTML `<head>` always precedes `<body>`; JSON envelope fields precede paginated arrays).
- The cap was set as a defense against "accidental binary download" or runaway responses — i.e. the intent was *resource bounding*, not *content validation*. Streaming-with-early-exit gives a *tighter* bound while letting real content through.

**Exception (keep the read-all-then-cap pattern) when**:

- The fetcher genuinely needs the whole document (linkcheck on every `<a href>`, full-page text search, byte-exact mirroring).
- The required element appears at the tail (e.g. RSS feed footer, file checksum trailer). Streaming-to-tail offers no early exit; a true cap is the right tool.

## Why This Works

`read-all-then-cap` couples two things that should be independent: **what we extract** and **how much we tolerate reading**. When real-world inputs grow (modern HTML pages with inlined CSS/JS / large nav structures routinely exceed 1MB), the cap has to grow with them — but the cap was set to defend against a different threat (accidental binary download). The two pressures pull in opposite directions and the cap drifts upward without bounding the original threat any better.

`stream-until-tag-then-stop` decouples them. The streaming reader is bounded by the tag's actual position, not by some abstract megabyte budget. A 50KB head closes the reader at 50KB regardless of whether the body that follows is 100KB or 10MB. The defense against binary downloads is delivered by the streaming cap itself (e.g. 256KB), which is much *tighter* than the old cap (1MB → 3MB), AND lets all real HTML pages through because real HTML heads fit comfortably below it.

The bonus property: once streaming-with-early-exit is in place, the `body_too_large` error class becomes unreachable for the use case it was designed to cover. That's the structural correctness signal — the failure mode the cap was guarding against can no longer occur, and not because we made the cap looser.

## Examples

A `verify_url_has_content(url)` function existed to extract one HTML title for backlink target validation. It read the full response body with a `read(MAX_BODY_BYTES + 1)` call, then rejected the response as `body_too_large` if it exceeded 1MB. Title extraction (via BeautifulSoup over `<head>`'s `og:title` then `<title>`) ran only on bodies that passed the cap.

**Symptom**: real HTML target pages with inlined CSS/JS and a large nav menu hit ~1.2MB on the wire. Every such page rejected as `body_too_large`. Operator sees an unhelpful error for a page that loads fine in a browser.

**Band-aid path** (rejected): raise `MAX_BODY_BYTES` to 3MB. Works for this page; will recur on the next page that exceeds 3MB; the cap drifts upward, the original "defense against binary download" intent silently erodes.

**Root-fix path** (taken): introduce `HEAD_SCAN_BYTES = 256_000` and a streaming helper `_read_html_head_window(resp, max_bytes)` that:

1. Reads in 16KB chunks via `resp.read(chunk_size)`.
2. After each chunk, scans the trailing 32KB window for `</head>` (case-insensitive).
3. Returns as soon as the close-tag is found, OR when the stream ends, OR when `max_bytes` is reached.

The hot path replaces `body = resp.read(MAX_BODY_BYTES + 1)` with `body = _read_html_head_window(resp, HEAD_SCAN_BYTES)`. The `body_too_large` branch is deleted.

Net effect:

- A 1.2MB page with a 30KB head reads ~30KB and returns success. Was previously rejected.
- A pathological 10MB binary stream reads at most 256KB before stopping. Was previously read up to 1MB+1 bytes.
- A genuine HTML head that has no `<title>` resolves to `http_200_no_title` as before — the failure mode for missing title is preserved.

The defensive bound got *tighter* (256KB vs 1MB), the success rate on real pages went *up*, and a recurring band-aid (raise the cap, again) was eliminated.

## Anti-pattern signals

Three signals say "you are in this pattern, not the resource-bounded one":

1. **The cap is documented as a defense against a threat that the use case doesn't actually require defending against at full-body granularity.** ("Protects against accidental binary downloads" — a 256KB streaming cap defends against that just as well.)
2. **The error class fires on real, well-formed inputs.** A defensive cap that rejects valid content has the wrong shape — the cap is in the wrong layer.
3. **The fix discussion centers on "what's the right cap value".** That framing assumes cap-then-reject is the structure; it isn't. The right discussion is "what's the prefix we need and how do we stop reading after we have it".

If any of those signal, the fix is structural (stream until the predicate), not parametric (raise the constant).

## Related

- `feedback_api-idempotency-lesson` (private memory) — another instance of the same "defensive cap in the wrong layer" anti-pattern, manifested as "5xx removed from retryable" because retry safety was solved at the wrong abstraction.
- `recon-log-level-for-always-on-signals` — pairs well: when the streaming reader closes early on a malformed input, a RECON-level breadcrumb lets the operator see "stopped at byte N, no `</head>` found" without `--log-level` gating.
