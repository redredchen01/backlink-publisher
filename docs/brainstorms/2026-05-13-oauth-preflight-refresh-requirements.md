---
date: 2026-05-13
topic: oauth-preflight-refresh
---

# OAuth Pre-Flight Token Refresh

## Problem Frame

Blogger and Medium adapters currently check token validity only at the moment of the first API call. A token that is valid at call-start but expires mid-batch causes 401 errors after several articles have already been published, forcing the operator to restart the batch from scratch (risking duplicate publishes on Blogger, which has no server-side dedup). The documented failure window is 60 seconds: `creds.expired` flips True only after the expiry timestamp passes, not before.

Two platforms need different treatments:

- **Blogger**: Has a Google `refresh_token` → can silently pre-refresh.
- **Medium OAuth**: No documented refresh endpoint for API v1 → early detection + actionable error is the correct response.

---

## Requirements

**Blogger — Proactive Refresh**

- R1. Extract a helper `_near_expiry(creds, window_secs: int) -> bool` that returns True when `creds.expired` is True **or** when `creds.expiry` is a datetime within `window_secs` seconds of `datetime.utcnow()`.
- R2. In `_build_credentials()`, replace the condition `if creds and creds.expired and creds.refresh_token` with `if creds and _near_expiry(creds, 300) and creds.refresh_token`. All other logic (refresh → save → return) is unchanged.
- R3. If the pre-flight refresh itself fails (e.g., network error), fall through to full re-auth flow (existing behaviour).

**Medium — Near-Expiry Detection and `expires_at` Persistence**

- R4. In `settings_medium_oauth_callback()` (webui.py), after receiving `token_data` from Medium, compute `expires_at = time.time() + token_data["expires_in"]` when `expires_in` is present and `expires_at` is absent, then save the augmented dict via `save_medium_token()`.
- R5. In `MediumAPIAdapter.publish()`, after loading `medium_token_data`, check whether `expires_at` is present and `time.time() >= expires_at - 300`. If True, raise `ExternalServiceError("Medium OAuth token expires in < 5 minutes — re-authorize via Settings → Medium 授权")` before making any API call.
- R6. When `expires_at` is absent (integration token or old OAuth token saved before R4), skip the check entirely — do not error, do not warn.

---

## Success Criteria

- A Blogger batch that starts with 4m 30s remaining on the token completes without a 401 error.
- A Medium OAuth token with < 5 minutes to expiry produces an `ExternalServiceError` with a re-auth message before the first API call is made (no silent 401).
- Existing tests pass unchanged.
- New unit tests cover: `_near_expiry` with expired / within-window / outside-window inputs; Medium pre-flight skip when `expires_at` absent.

---

## Scope Boundaries

- Medium token refresh (exchanging a refresh token for a new access token) is out of scope — Medium API v1 does not document this endpoint.
- The webui `/api/token-status` badge endpoint is deferred to V2 (per raise-the-bar decision from ideation).
- No changes to CLI argument surface.

---

## Key Decisions

- **300-second window**: Covers batch runs up to ~5 articles at 60s throttle each. Chosen over 120s (too narrow for Medium throttle) and 600s (causes unnecessary refreshes for healthy tokens). Can be made configurable later via env var.
- **Medium: detect, not refresh**: No documented refresh path → early error is safer than silently retrying with an expired token (would just return 401 anyway).
- **Fail-open on `expires_at` absent**: Integration tokens are indefinite; old OAuth tokens lack the field. Checking only when field is present avoids false positives.

---

## Dependencies / Assumptions

- Google `google-auth` library's `creds.expiry` is a naive UTC datetime (confirmed in google-auth-library-python source).
- Medium `expires_in` field is in seconds (confirmed in Medium API docs).
- `time.time()` returns Unix timestamp (seconds).

---

## Outstanding Questions

### Deferred to Planning

- [Affects R1][Technical] Does `datetime.utcnow()` vs `datetime.now(timezone.utc)` matter for the expiry comparison? (google-auth uses naive UTC; confirm comparison is consistent.)
- [Affects R4][Technical] Does Medium ever omit `expires_in` from the token response? If so, R4 should skip augmentation gracefully.

## Next Steps

→ `/ce:plan` for structured implementation planning
