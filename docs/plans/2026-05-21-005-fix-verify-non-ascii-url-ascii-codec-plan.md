---
title: "fix: Verify fetcher dies on non-ASCII published URLs (velog @username / CJK slug)"
type: fix
status: completed
date: 2026-05-21
claims: {}
---

# fix: Verify fetcher dies on non-ASCII published URLs

## Overview

`publish-backlinks` marks legitimately-published velog posts as `published_unverified` because the post-publish verifier's HTTP fetch crashes with `'ascii' codec can't encode characters in position 21-22: ordinal not in range(128)` whenever the published URL contains non-ASCII characters (Korean `@username`, CJK url_slug). The post is real on velog; only verification fails.

This plan adds a small URL-normalization helper, applies it at every `urllib.request.urlopen` call site in `linkcheck/`, and regression-tests with CJK fixtures so the same encoding error cannot return through a different fetch site.

## Problem Frame

**Observed (2026-05-21 06:23 UTC, run_id `20260521T062322-477c5be1`):**

```
verification failed: id=66a0ded05763427d reason=verification failed after 3 attempt(s):
  fetch failed: 'ascii' codec can't encode characters in position 21-22: ordinal not in range(128)
extra: {"adapter": "velog-graphql"}
status=published_unverified
```

**Root cause:** `src/backlink_publisher/linkcheck/verify.py:44` calls `urllib.request.Request(url)` with the raw `published_url` returned by the velog adapter. Per `velog_graphql.py:538`:

```python
published_url = f"https://velog.io/@{username}/{url_slug_returned}"
```

- `username` is whatever velog stored — Korean is legal.
- `url_slug_returned` comes from `_slugify(title)` at `velog_graphql.py:105-112`, whose regex `[^\w\s-]` uses Python's default `re.UNICODE` flag, so CJK word characters survive into the slug.

Position 21-22 of `https://velog.io/@<x>/...` lands inside the username (`h`+`t`+`t`+`p`+`s`+`:`+`/`+`/`+`v`+`e`+`l`+`o`+`g`+`.`+`i`+`o`+`/`+`@` = 18 chars, so position 21-22 = 4th-5th byte after `@`). `http.client._encode` enforces `latin-1`/`ascii` on the request line; non-ASCII path bytes crash before any network I/O.

**Why "minimum 3 attempts" makes it worse:** `verify_published` polls every 6s for up to 30s on the *same* unencoded URL — every retry hits the same client-side encoding error, then sleeps. That wastes ~30s per publish and marks the row `published_unverified` even though velog likely has the post.

**Same latent bug, different site:** `src/backlink_publisher/linkcheck/http.py:36-56` (`_check_url_once`) does the same `Request(url) + urlopen` without normalization. Pre-publish reachability of CJK-containing internal URLs would crash identically. The fix should cover both.

## Requirements Trace

- **R1.** Publishing to a platform that legitimately produces non-ASCII URLs (velog Korean `@username`, CJK slugs) MUST result in `status=published` when the post is reachable.
- **R2.** The verifier MUST NOT crash on URLs that browsers can fetch without manual percent-encoding.
- **R3.** Pre-publish reachability (`linkcheck.http.check_url`) MUST tolerate the same URL shapes without changing existing ASCII-URL behavior.
- **R4.** The fix MUST be additive: existing ASCII URLs must take an identical code path observable to the upstream service (same headers, same method, no extra round-trips).

## Scope Boundaries

**In scope:**
- Stdlib URL normalization helper in `_util/url.py`.
- Apply to `linkcheck/verify.py:_get_body` and `linkcheck/http.py:_check_url_once`.
- Unit + regression tests covering CJK in netloc path-segment (`@한글`), CJK in slug, and idempotency over already-percent-encoded URLs.

**Out of scope:**
- Changing what velog adapter returns. `published_url` as constructed is correct — velog itself accepts and serves CJK slugs/usernames.
- Modifying `_slugify`. ASCII-fying slugs would change the URLs that get stored in artifacts and break round-tripping with velog's actual storage.
- Headers carrying non-ASCII values. Current code sets only `User-Agent: backlink-publisher/...`, both pure ASCII; no header carries dynamic non-ASCII data today.
- The `content/scraper.py` fetcher. It uses `requests` (not `urllib`); `requests` percent-encodes paths automatically. No bug there today, but flagged in Open Questions for follow-up audit.
- Webhook/notifier code paths and other adapters' internal fetches — out of scope; flagged in Open Questions.

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/linkcheck/verify.py:41-51` — `_get_body(url)`, the fetcher that exploded.
- `src/backlink_publisher/linkcheck/http.py:29-56` — `_check_url_once(url)`, same `Request(url)+urlopen` pattern; not yet observed crashing but identical exposure.
- `src/backlink_publisher/_util/url.py` — existing canonical URL utility module (`canonicalize_url`, `validate_*`, `absolutize`, `strip_fragment_query`). New helper belongs here.
- `src/backlink_publisher/publishing/adapters/velog_graphql.py:105-112, 538` — origin of CJK in URLs; not modified by this plan, only referenced to understand input shape.
- `tests/test_verify_publish.py` — patches `_get_body` for unit tests; pattern to extend.
- `tests/test_linkcheck.py` — patches `_check_url_once`; pattern to extend.

### Institutional Learnings

- `feedback_lsof_a_flag_required` / `feedback_bash_cjk_var_bracing` — repo has prior incidents where CJK characters in operational data tripped tooling. Reinforces that "must accept CJK input" is a recurring class.
- `feedback_grep_before_writing_brainstorm_plan_claims` — before claiming a call site is OK, grep all `urlopen`/`Request(` sites; done (only 2 in `linkcheck/`).

### External References

- Python stdlib `urllib.parse.quote(s, safe=...)` — percent-encodes non-ASCII bytes after UTF-8 encoding. Idempotent on already-percent-encoded inputs when `%` is in `safe`.
- Python stdlib `str.encode("idna")` — IDNA hostname encoding (`xn--…`). Required for non-ASCII hosts; not currently expected from any adapter but cheap to include for defense.
- RFC 3986 §3.3 (path) and §3.4 (query) — characters allowed unencoded in path vs query differ. Path keeps `:@`; query keeps `=&?+`.

## Key Technical Decisions

- **Helper lives in `_util/url.py`, not `linkcheck/`.** Rationale: `_util/url.py` is the canonical URL-utility home (already has `canonicalize_url`, `validate_*`, `absolutize`). A second URL helper inside `linkcheck/` would split conventions. Reviewers expect URL utilities in one place.
- **Implementation uses `urlsplit` + `quote` + IDNA encode**, not regex. Rationale: stdlib parsing handles `userinfo`, port, query, fragment correctly. Hand-rolled percent-encoding miscategorizes reserved chars between path and query.
- **Path `safe` includes `/:@%`; query `safe` includes `=&?/:@,+%`.** Rationale (`%` in safe): preserves already-encoded inputs (`%E1%84%82` stays as `%E1%84%82`, not double-encoded to `%25E1%2584%2582`). This makes the helper idempotent, matching `canonicalize_url` invariant in the same module.
- **Fail-soft on IDNA failures.** If `host.encode("idna")` raises (extreme edge case), fall back to the original host string. Rationale: verifier already returns a structured failure if the request itself fails; never want a normalization edge case to crash before the planned retry/timeout policy kicks in.
- **Apply at fetch boundary (`_get_body`, `_check_url_once`), not at adapter output.** Rationale: the URL stored in artifacts (`published_url`) must remain the human-meaningful form velog actually serves. Normalization is a transport concern between our code and `urlopen`, not a data-model concern.
- **No requests-library migration.** Rationale: scope creep; would touch SSL config, ProxyHandler, retry semantics. Stdlib fix is ~10 lines.

## Open Questions

### Resolved During Planning

- **Q: Should we also fix `_slugify` to ASCII-ify?** No — see Scope Boundaries. Velog stores the CJK slug; sending an ASCII-fied slug would not match the actual post URL.
- **Q: Could the failure be in a header rather than the URL?** No. Both call sites set only `User-Agent: backlink-publisher/...` (pure ASCII). Position 21-22 of the published URL (`https://velog.io/@` is 18 chars, so 21-22 = 4th-5th byte after `@`) matches a CJK username, not a header.
- **Q: Is the request-line encoding ASCII or latin-1?** `http.client.HTTPConnection.putrequest` calls `_encode(url, "url")` which encodes as `latin-1`. The crash message says `'ascii'` because `urllib.request` does its own ASCII check earlier (in `AbstractHTTPHandler.do_open` → `http.client.HTTPConnection.request` chain). Either way the fix is the same: hand `urlopen` a pre-encoded URL.

### Deferred to Implementation

- **Audit other fetch sites for non-ASCII vulnerability.** `content/scraper.py` uses `requests` which encodes automatically — not vulnerable. Adapter-internal HTTP (GraphQL clients, browser drivers) all use either `requests`/`httpx` or Playwright. To confirm at implementation time: `grep -rn "urlopen\|urllib.request" src/`.
- **Should the `published_url` artifact be stored as the normalized form or the human form?** Defer — current artifacts already store human-form CJK URLs and any downstream consumer (linkcheck on existing artifacts) will benefit from the same helper when it fetches them.

## Implementation Units

- [x] **Unit 1: Add `normalize_url_for_fetch` helper to `_util/url.py`**

**Goal:** Stdlib helper that takes any URL and returns an ASCII-safe form `urllib.request.urlopen` accepts, idempotent and round-trip-safe on already-ASCII URLs.

**Requirements:** R2, R4

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/_util/url.py`
- Test: `tests/test_util_url.py` (extend existing test module if present, otherwise create — grep first)

**Approach:**
- New function `normalize_url_for_fetch(url: str) -> str`.
- Use `urlsplit` to decompose. IDNA-encode the hostname (`parts.hostname.encode("idna").decode("ascii")`) with a try/except fallback to original host. Preserve userinfo and port byte-for-byte.
- `quote(parts.path, safe="/:@%")` for path; `quote(parts.query, safe="=&?/:@,+%")` for query. Fragment is dropped at request time anyway; pass `""` to `urlunsplit`.
- Empty-string and non-`http(s)` inputs pass through unchanged (mirrors `canonicalize_url` behavior in same module).
- Idempotent: `normalize_url_for_fetch(normalize_url_for_fetch(u)) == normalize_url_for_fetch(u)`. The `%` in `safe` makes this hold.

**Patterns to follow:**
- `canonicalize_url` (same file): docstring shape, idempotency contract, non-http passthrough.

**Test scenarios:**
- Happy path: ASCII URL `https://example.com/path?q=1` returns byte-identical input.
- Happy path: `https://velog.io/@한글/foo-bar` returns `https://velog.io/@%ED%95%9C%EA%B8%80/foo-bar`.
- Happy path: CJK in path-segment after `@`: `https://velog.io/@user/한글-제목` percent-encodes only the CJK runs, hyphens preserved.
- Happy path: Already-encoded `https://velog.io/@%ED%95%9C%EA%B8%80/foo` returns byte-identical input (idempotency).
- Edge case: Empty string returns empty string.
- Edge case: Non-http(s) scheme `mailto:user@example.com` returns input unchanged.
- Edge case: Query with non-ASCII value `https://x.io/p?q=한` encodes only the query bytes.
- Edge case: URL with port `https://velog.io:8443/@한/p` preserves `:8443`.
- Edge case: URL with userinfo `https://u:p@host.io/p` preserves `u:p@` verbatim.
- Edge case: IDNA-incompatible host (e.g. trailing dot, hyphen positions) — gracefully falls back to original host without raising.
- Edge case: Output passed to `urllib.request.Request(...)` does not raise `UnicodeEncodeError` (smoke test: construct the Request, don't open it).

**Verification:**
- `from backlink_publisher._util.url import normalize_url_for_fetch` exposes the function.
- `Request(normalize_url_for_fetch("https://velog.io/@한글/foo"))` constructs without raising.
- All listed test scenarios pass under `PYTHONHASHSEED=0 pytest tests/test_util_url.py`.

---

- [x] **Unit 2: Apply normalization at the two `linkcheck` fetch sites and regression-test verifier**

**Goal:** Stop the production crash: `verify_published` of a CJK-URL post returns `ok=True` when the post is reachable; pre-publish reachability gets the same protection.

**Requirements:** R1, R2, R3, R4

**Dependencies:** Unit 1

**Files:**
- Modify: `src/backlink_publisher/linkcheck/verify.py` (only `_get_body`, ~2-line change)
- Modify: `src/backlink_publisher/linkcheck/http.py` (only `_check_url_once`, ~2-line change at HEAD and GET)
- Test: `tests/test_verify_publish.py` (extend)
- Test: `tests/test_linkcheck.py` (extend)

**Approach:**
- `verify.py:_get_body`: import `normalize_url_for_fetch`, call `Request(normalize_url_for_fetch(url))` instead of `Request(url)`. No other change — `_VERIFY_TIMEOUT`, `_SSL_CTX`, `User-Agent`, retry policy in caller all stay identical.
- `http.py:_check_url_once`: same swap in both the HEAD branch (line 37) and the GET branch (line 48).
- Do NOT normalize at module boundaries higher up — keep transformation local to each `urlopen`-using function so the artifact stored in `published_url` remains the original form.
- Logging: the existing fail-path already prints `last_reason = f"fetch failed: {body}"` which on the new code path will only fire for real network/server failures (not local encoding). The ASCII-codec message will disappear from logs in success cases. No log-format change required.

**Patterns to follow:**
- `verify.py` test pattern: tests patch `_get_body` to return fixed `(status, body)` tuples (`tests/test_verify_publish.py:18-22`). New regression test patches `urlopen` (lower) to assert the URL handed to `Request` is the normalized form.

**Test scenarios:**
- Happy path (regression): `verify_published("https://velog.io/@한글/foo", title="제목", required_link_urls=["https://example.com"])` — patch `urlopen` to return a 200 body containing both `제목` and `https://example.com`; assert `result.ok is True` and the URL passed to `Request(...)` is the percent-encoded form.
- Edge case: `verify_published` with already-ASCII URL — assert the URL handed to `Request` is unchanged byte-for-byte (no behavioral drift for the common path).
- Error path: `_get_body` no longer raises `UnicodeEncodeError`; on real network failure it still returns `(0, error_str)` and the verifier reports `fetch failed: …` as before.
- Integration: `_check_url_once` with a CJK URL — patch `urlopen` to return 200 HEAD; assert reachable and that the Request URL is normalized. Mirror test for the GET fallback branch (HEAD fails, GET succeeds).
- Integration: `check_urls_strict(["https://velog.io/@한글/foo"])` with patched success — does not raise.
- Regression (the original bug): without the fix, the same test fails with `UnicodeEncodeError`; with the fix it passes. This must be a real assertion — `pytest.raises(UnicodeEncodeError)` on pre-fix code, assert pass post-fix. Achieved by running the same test against both `Request(url)` (xfail) and the new path.

**Verification:**
- `pytest tests/test_verify_publish.py tests/test_linkcheck.py tests/test_util_url.py` all green under `PYTHONHASHSEED=0`.
- `pytest tests/` full suite green (no test elsewhere relied on the old crash behavior — grep `UnicodeEncodeError` in `tests/` to confirm; expected: zero pre-existing matches).
- Manual replay of the failing run (the exact `66a0ded05763427d` payload, if recoverable from `~/.config/backlink-publisher/cache/` or by re-publishing) reports `status=published` instead of `published_unverified`. If the original payload is not recoverable, replay against a hand-constructed velog URL with the same shape.
- `py_compile` of both modified files: `python -m py_compile src/backlink_publisher/linkcheck/verify.py src/backlink_publisher/linkcheck/http.py`.

## System-Wide Impact

- **Interaction graph:** `publish_backlinks.py` → `linkcheck.verify.verify_published` → `_get_body` → (new) `_util.url.normalize_url_for_fetch` → `urllib.request`. Also `publish_backlinks.py` (per-row reachability) → `linkcheck.http.check_url` → `_check_url_once` → same helper.
- **Error propagation:** Encoding errors previously surfaced as `fetch failed: 'ascii' codec ...` (looked like network failure). Post-fix, those are eliminated; real network failures keep the same `fetch failed: <body>` shape. The `verification failed after N attempt(s)` reason text format does not change.
- **State lifecycle risks:** None. Artifacts (`published_url`) are NOT rewritten — the URL handed to `urlopen` is transformed locally inside the fetch function; the stored URL stays the human form. This preserves backward compatibility with `report-anchors` and `webui_app` consumers that render the URL.
- **API surface parity:** `verify.py` and `http.py` both fetch URLs with `urllib.request`. Both must use the new helper to avoid future selective regressions. Any new fetch site added must opt-in — flagged in Open Questions for a future grep audit.
- **Integration coverage:** Tests must drive at least one full path through `verify_published` (not just `_get_body`) to prove the integration is wired. Same for `check_url` (not just `_check_url_once`).
- **Unchanged invariants:**
  - `published_url` stored in publish artifacts: unchanged byte-for-byte from current adapter output.
  - `User-Agent` headers: unchanged.
  - SSL context (verification disabled): unchanged.
  - Retry/poll timing in `verify_published` (6s interval, 30s default `max_wait`): unchanged.
  - Exit codes from `publish-backlinks`: unchanged.
  - `dropped.unverified` reconciliation in `publish_reconciliation`: unchanged structure; the count for CJK-URL items should drop to zero.

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Helper double-encodes already-percent-encoded URLs (e.g., `%20` → `%2520`) | Include `%` in `safe` for both path and query; explicit idempotency test in Unit 1 scenarios. |
| Helper strips a meaningful fragment used by some downstream consumer | Audit: `grep -n "#" tests/fixtures/*velog* docs/` — verify no test fixture uses fragments in `published_url`. Velog URLs do not use fragments. If fragments matter elsewhere, they are dropped at fetch time only, not at artifact time. |
| Path component containing `?` (no query) gets misinterpreted | `urlsplit` correctly splits on the first `?`. The helper preserves `query` separately; no manual splitting. |
| Header values with non-ASCII (future change) hit the same crash class | Out of scope today (no dynamic-value headers exist). Add a comment in `verify.py` noting that headers must remain ASCII unless `latin-1` encoded explicitly. |
| The fix only papers over a velog username/slug we should have rejected upstream | Not the right tradeoff: velog legitimately accepts CJK; refusing them would block legitimate publishing. Document in Decisions. |

## Documentation / Operational Notes

- One-line `CHANGELOG.md` entry under "Fixed": `linkcheck: verify_published and check_url now tolerate non-ASCII URLs (velog Korean usernames, CJK slugs)`.
- No env-var, no config change, no migration. Operator action required: none. Existing rows already marked `published_unverified` can be manually re-verified by re-running `publish-backlinks` against the same `seeds.jsonl`; alternatively a one-off `report-anchors --recheck` if such a flag exists (defer; out of scope).
- No new dependency; stdlib only.

## Sources & References

- Failing run: `run_id=20260521T062322-477c5be1`, `id=66a0ded05763427d`, `adapter=velog-graphql`, 2026-05-21 06:23 UTC.
- Code: `src/backlink_publisher/linkcheck/verify.py:41-51`, `src/backlink_publisher/linkcheck/http.py:29-56`, `src/backlink_publisher/publishing/adapters/velog_graphql.py:105-112,538`, `src/backlink_publisher/_util/url.py:124-194`.
- Tests: `tests/test_verify_publish.py`, `tests/test_linkcheck.py`.
- RFC 3986 §3.3, §3.4 (URL component grammars).
- Python stdlib: `urllib.parse.quote`, `urllib.parse.urlsplit`, `str.encode("idna")`.
