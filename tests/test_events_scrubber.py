"""Tests for ``backlink_publisher.events.scrubber.scrub_text``.

Asserts positive shapes throughout (per institutional learning:
``docs/solutions/test-failures/inverted-negative-assertion-...``). Each
test names the expected pattern hit count rather than checking only
"output != input".
"""

from __future__ import annotations

from backlink_publisher.events.scrubber import scrub_text

#: Deterministic 64-char base64url token covering every symbol exactly once.
#: Shannon entropy = log2(64) = 6.0 (max), well above the 4.5 threshold —
#: replaces ``secrets.token_urlsafe(32)`` which produced ~43-char random
#: tokens that occasionally fell below threshold and flaked CI (#49 retry
#: 2026-05-18 hit this on both Python 3.11 and 3.12).
_HIGH_ENTROPY_64 = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)


def test_oauth_bearer_redacted_and_counted():
    text = "Authorization: Bearer abc123def456ghi789"
    cleaned, hits = scrub_text(text)
    # Assert the entire secret body is gone, not just the prefix — a
    # broken implementation that redacted only "Bearer abc123" would
    # still pass a substring-of-prefix check.
    assert "abc123def456ghi789" not in cleaned
    assert "<REDACTED>" in cleaned
    assert hits.get("oauth_bearer") == 1


def test_oauth_bearer_with_base64_padding_chars():
    # Standard-base64 tokens contain ``+``, ``/``, and ``=`` padding.
    # A char class missing any of these would leak the suffix.
    text = "Authorization: Bearer abcd+efgh/ijkl=="
    cleaned, hits = scrub_text(text)
    assert "abcd+efgh/ijkl" not in cleaned
    assert "==" not in cleaned
    assert hits.get("oauth_bearer") == 1


def test_jwt_redacted_and_counted():
    text = "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc"
    cleaned, hits = scrub_text(text)
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "<REDACTED>" in cleaned
    assert hits.get("jwt") == 1


def test_jwt_glued_to_identifier_prefix_still_redacted():
    # When a JWT is concatenated to an identifier (no separator), the
    # leading ``eyJ`` is preceded by a word char so neither ``\b`` nor
    # a negative-lookbehind on identifier chars can match it; the named
    # pattern claims only the payload+signature half. That residual
    # identifier+header prefix is ≥ 32 chars of base64-shaped material,
    # so the high-entropy fallback must redact the remainder. Net: the
    # secret bytes are gone, hit_counts shows jwt=1 (the segment we
    # could identify) plus high_entropy=1 (the segment we could only
    # tell was suspicious). Both signals are useful to downstream
    # routing — the assertion pins that both fire rather than letting
    # either silently regress.
    text = "access_tokeneyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature done"
    cleaned, hits = scrub_text(text)
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "eyJzdWIiOiJ4In0" not in cleaned
    assert "signature" not in cleaned
    assert hits.get("jwt") == 1
    assert hits.get("high_entropy") == 1


def test_jwt_after_non_identifier_separator_routed_to_jwt():
    # Cases where ``\b`` already works (``:``, ``=``, whitespace, quotes,
    # ``,``) — the negative-lookbehind anchor must not regress them.
    for sep in (": ", "=", '"', "'", ",", " "):
        text = f"auth{sep}eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig done"
        cleaned, hits = scrub_text(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned, f"sep={sep!r} leaked"
        assert hits.get("jwt") == 1, f"sep={sep!r} wrong routing: {hits}"


def test_google_api_key_redacted():
    # AIza prefix + exactly 35 alphanumeric chars = real-shape key
    key = "AIza" + "a" * 35
    text = f"GOOGLE_API_KEY={key}"
    cleaned, hits = scrub_text(text)
    assert key not in cleaned
    assert hits.get("google_api_key") == 1


def test_google_api_key_ending_in_dash_redacted():
    # ``\b`` after a ``-`` would fail (both ``-`` and whitespace are
    # non-word chars, so there is no transition). Use a negative
    # lookahead instead. Regression for that fix.
    key = "AIza" + "a" * 34 + "-"
    text = f"key={key} done"
    cleaned, hits = scrub_text(text)
    assert key not in cleaned
    assert hits.get("google_api_key") == 1


def test_basic_auth_url_redacted():
    text = "fetched https://user:pa55word@example.com/path successfully"
    cleaned, hits = scrub_text(text)
    assert "user:pa55word@" not in cleaned
    assert hits.get("basic_auth_url") == 1


def test_basic_auth_url_terminates_at_quote():
    # JSON / quoted-string context. The redaction span must stop at the
    # closing quote, not extend through the trailing structured fields.
    text = '{"url":"https://user:pa55word@example.com/x","result":"200"}'
    cleaned, hits = scrub_text(text)
    assert "user:pa55word@" not in cleaned
    assert '"result":"200"' in cleaned, f"trailing JSON clobbered: {cleaned!r}"
    assert hits.get("basic_auth_url") == 1


def test_basic_auth_url_terminates_at_markdown_paren():
    # Markdown link: ``[txt](url)``. The redaction must stop at ``)`` so
    # the link wrapper survives.
    text = "see [docs](https://user:pa55word@example.com/x) for details"
    cleaned, hits = scrub_text(text)
    assert "user:pa55word@" not in cleaned
    assert ") for details" in cleaned, f"markdown structure clobbered: {cleaned!r}"
    assert hits.get("basic_auth_url") == 1


def test_basic_auth_url_terminates_at_angle_bracket():
    # HTML anchor href: ``<a href="...">``. The redaction must stop at
    # ``>`` (and at ``"`` which fires first). Pin both.
    text = '<a href="https://user:pa55word@example.com/x">link</a>'
    cleaned, hits = scrub_text(text)
    assert "user:pa55word@" not in cleaned
    assert ">link</a>" in cleaned
    assert hits.get("basic_auth_url") == 1


def test_sha256_hex_token_redacted():
    token = "a" * 64  # 64 lowercase-hex chars
    text = f"medium token: {token}"
    cleaned, hits = scrub_text(text)
    assert token not in cleaned
    assert hits.get("sha256_hex_token") == 1


# --- negative / false-positive guards -------------------------------------


def test_plain_english_unchanged_no_hits():
    text = "the cat sat on the mat and watched the rain"
    cleaned, hits = scrub_text(text)
    assert cleaned == text
    assert hits == {}


def test_aiza_prefix_short_run_not_redacted():
    # "AIza" prefix but only 19 chars follow → does not match the 35-char
    # Google key shape, and the surrounding token is too short for the
    # high-entropy fallback (length 23 < 32).
    text = "This is example AIzaShortNotARealKey done"
    cleaned, hits = scrub_text(text)
    assert "AIzaShort" in cleaned
    assert "google_api_key" not in hits


def test_repeating_low_entropy_pattern_not_high_entropy():
    # 32 chars of repeating "a1b2c3d4" — 8 unique chars uniform → Shannon
    # entropy is exactly log2(8) = 3.0, below the 4.5 threshold.
    pattern = "a1b2c3d4" * 4  # 32 chars
    text = f"data: {pattern} end"
    cleaned, hits = scrub_text(text)
    assert pattern in cleaned
    assert "high_entropy" not in hits


# --- high-entropy fallback -------------------------------------------------


def test_random_token_triggers_high_entropy():
    text = f"opaque blob: {_HIGH_ENTROPY_64} trailing"
    cleaned, hits = scrub_text(text)
    assert _HIGH_ENTROPY_64 not in cleaned
    assert hits.get("high_entropy") == 1


def test_named_pattern_runs_before_high_entropy():
    # JWT-shaped token: ``eyJ`` prefix + deterministic high-entropy body.
    # The named JWT regex should claim it first (more useful routing
    # signal); ``high_entropy`` must not double-count.
    jwt = "eyJ" + _HIGH_ENTROPY_64
    text = f"auth: {jwt} done"
    cleaned, hits = scrub_text(text)
    assert jwt not in cleaned
    assert hits.get("jwt") == 1
    assert "high_entropy" not in hits


# --- structural integrity --------------------------------------------------


def test_empty_string_returns_empty_no_hits():
    cleaned, hits = scrub_text("")
    assert cleaned == ""
    assert hits == {}


def test_cjk_long_text_not_high_entropy_redacted():
    # 32+ Chinese characters in a single whitespace-bounded run.
    # Per-codepoint Shannon entropy over distinct ideographs would exceed
    # the threshold; the ASCII-density guard must skip the token.
    cjk = "这是一段非常详细的中文错误说明用于测试不应当被高熵规则误判为机密"
    assert len(cjk) >= 32
    cleaned, hits = scrub_text(cjk)
    assert cleaned == cjk
    assert "high_entropy" not in hits


def test_large_input_truncated_with_marker():
    # Inputs over _MAX_SCRUB_LEN (64 KiB) must be capped so scrub_text
    # can't be DoS'd by feeding it a multi-MB string. The truncation
    # marker tells the caller the cap fired.
    from backlink_publisher.events.scrubber import (
        _MAX_SCRUB_LEN,
        _TRUNCATED_TEMPLATE,
    )

    huge = "x" * (_MAX_SCRUB_LEN + 5000)
    cleaned, _hits = scrub_text(huge)
    expected_marker = _TRUNCATED_TEMPLATE.format(n=5000)
    assert cleaned.endswith(expected_marker), (
        f"expected truncation marker, got tail: {cleaned[-100:]!r}"
    )
    # The kept prefix plus marker is the only thing in cleaned.
    assert len(cleaned) == _MAX_SCRUB_LEN + len(expected_marker)


def test_large_input_secret_after_cap_not_scrubbed():
    # Anything beyond _MAX_SCRUB_LEN is dropped before scanning — by
    # design, since the alternative is unbounded regex work. Pin the
    # contract so callers know not to rely on full-input scrubbing.
    from backlink_publisher.events.scrubber import _MAX_SCRUB_LEN

    padding = " " * _MAX_SCRUB_LEN
    hidden_secret = "Bearer secret-past-the-cap-token-12345"
    text = padding + hidden_secret
    cleaned, hits = scrub_text(text)
    # The secret was beyond the cap; it's not present in cleaned at all
    # (truncated away rather than redacted).
    assert "secret-past-the-cap" not in cleaned
    assert "oauth_bearer" not in hits


def test_small_input_not_truncated():
    # Sanity: inputs under the cap pass through untouched (modulo
    # redaction). The truncation marker must NOT appear.
    text = "the cat sat on the mat"
    cleaned, _hits = scrub_text(text)
    assert "<TRUNCATED" not in cleaned
    assert cleaned == text


def test_multiple_secrets_in_one_message_all_counted():
    text = (
        "GET /api Bearer tokenABCXYZ123 returned 401; "
        "retried via https://u:p@example.com/x"
    )
    cleaned, hits = scrub_text(text)
    assert "Bearer tokenABCXYZ123" not in cleaned
    assert "u:p@example.com" not in cleaned
    assert hits.get("oauth_bearer") == 1
    assert hits.get("basic_auth_url") == 1
