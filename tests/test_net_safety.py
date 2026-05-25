"""Tests for the ``net_safety`` SSRF / TLS / credential-sanitizer helpers.

Plan: ``docs/plans/2026-05-14-005-feat-v1-verifier-asset-extraction-plan.md``
Source: ``origin/pr/1:src/backlink_publisher/verifier.py``
"""

from __future__ import annotations

import ssl
from unittest.mock import patch

import pytest

from backlink_publisher.net_safety import (
    RedirectRejected,
    SafeRedirectHandler,
    check_host_allowed,
    check_path_shape,
    check_resolved_ip_safe,
    normalize_host,
    safe_for_log,
    sanitize_exception,
    strict_ssl_context,
)


# ── strict_ssl_context ────────────────────────────────────────────────────


class TestStrictSslContext:
    def test_creates_default_context(self) -> None:
        ctx = strict_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        # Must NOT be CERT_NONE (that would bypass verification).
        assert ctx.verify_mode != ssl.CERT_NONE
        assert ctx.check_hostname is True

    def test_not_lax(self) -> None:
        """Ensure we aren't accidentally returning a permissive context."""
        ctx = strict_ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED


# ── normalize_host ────────────────────────────────────────────────────────


class TestNormalizeHost:
    def test_lowercases(self) -> None:
        assert normalize_host("EXAMPLE.COM") == "example.com"

    def test_strips_trailing_dot(self) -> None:
        assert normalize_host("example.com.") == "example.com"

    def test_none_for_empty(self) -> None:
        assert normalize_host("") is None

    def test_none_for_whitespace(self) -> None:
        assert normalize_host("  ") is None

    def test_none_for_invalid_encoding(self) -> None:
        # Unicode that fails IDNA encoding.
        assert normalize_host("\ud800") is None

    def test_idna_roundtrip(self) -> None:
        # IDNA validation check passes; function returns the original host.
        assert normalize_host("münchen.de") == "münchen.de"

    def test_none_for_none_input(self) -> None:
        assert normalize_host(None) is None

    def test_preserves_valid_host(self) -> None:
        assert normalize_host("medium.com") == "medium.com"


# ── check_host_allowed ────────────────────────────────────────────────────


class TestCheckHostAllowed:
    def test_exact_match(self) -> None:
        assert check_host_allowed("medium.com", ("medium.com",)) is True

    def test_wildcard_matches_base(self) -> None:
        assert check_host_allowed("medium.com", ("*.medium.com",)) is True

    def test_wildcard_matches_subdomain(self) -> None:
        assert check_host_allowed("sub.medium.com", ("*.medium.com",)) is True

    def test_wildcard_matches_multi_subdomain(self) -> None:
        assert check_host_allowed("deep.sub.medium.com", ("*.medium.com",)) is True

    def test_not_in_allowed(self) -> None:
        assert check_host_allowed("evil.com", ("medium.com",)) is False

    def test_wildcard_no_match_wrong_base(self) -> None:
        assert check_host_allowed("evil.com", ("*.medium.com",)) is False

    def test_wildcard_no_partial_suffix(self) -> None:
        # ``attacker.com.medium.com`` is a DNS subdomain of ``medium.com``,
        # so ``*.medium.com`` correctly matches it.
        assert (
            check_host_allowed("attacker.com.medium.com", ("*.medium.com",)) is True
        )

    def test_different_wildcard(self) -> None:
        assert check_host_allowed("foo.bar.com", ("*.bar.com",)) is True

    def test_empty_allowlist(self) -> None:
        assert check_host_allowed("anything.com", ()) is False


# ── check_path_shape ──────────────────────────────────────────────────────


class TestCheckPathShape:
    def test_no_patterns_returns_true(self) -> None:
        assert check_path_shape("/anything", ()) is True

    def test_matches_pattern(self) -> None:
        assert check_path_shape("/posts/123", (r"/posts/\d+",)) is True

    def test_no_match(self) -> None:
        assert check_path_shape("/about", (r"/posts/\d+",)) is False

    def test_multiple_patterns_one_matches(self) -> None:
        assert check_path_shape("/about", (r"/posts/\d+", r"/about")) is True


# ── check_resolved_ip_safe ────────────────────────────────────────────────


class TestCheckResolvedIpSafe:
    @patch("socket.getaddrinfo")
    def test_public_ip_safe(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("93.184.216.34", 0))  # example.com
        ]
        safe, reason = check_resolved_ip_safe("example.com")
        assert safe is True
        assert reason is None

    @patch("socket.getaddrinfo")
    def test_private_ip_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("10.0.0.1", 0))
        ]
        safe, reason = check_resolved_ip_safe("internal.example.com")
        assert safe is False
        assert "private_ip" in reason

    @patch("socket.getaddrinfo")
    def test_loopback_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("127.0.0.1", 0))
        ]
        safe, reason = check_resolved_ip_safe("localhost")
        assert safe is False
        assert "private_ip" in reason

    @patch("socket.getaddrinfo")
    def test_metadata_ip_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("169.254.169.254", 0))
        ]
        safe, reason = check_resolved_ip_safe("metadata.service")
        assert safe is False
        assert "private_ip" in reason

    @patch("socket.getaddrinfo")
    def test_dns_failure_is_transient(self, mock_getaddrinfo):
        import socket
        mock_getaddrinfo.side_effect = socket.gaierror("[Errno 8] nodename nor servname provided")
        safe, reason = check_resolved_ip_safe("nonexistent.invalid")
        assert safe is False
        assert reason.startswith("dns_failure:")

    @patch("socket.getaddrinfo")
    def test_unsafe_network_cgnat(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("100.64.1.1", 0))
        ]
        safe, reason = check_resolved_ip_safe("cgnat.example.internal")
        assert safe is False
        assert "private_ip" in reason

    @patch("socket.getaddrinfo")
    def test_ipv6_loopback_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("::1", 0))
        ]
        safe, reason = check_resolved_ip_safe("ip6-localhost")
        assert safe is False
        assert "private_ip" in reason

    @patch("socket.getaddrinfo")
    def test_ipv6_private_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("fc00::1", 0))
        ]
        safe, reason = check_resolved_ip_safe("ula.internal")
        assert safe is False
        assert "private_ip" in reason

    @patch("socket.getaddrinfo")
    def test_link_local_rejected(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (0, 0, 0, "", ("169.254.1.1", 0))
        ]
        safe, reason = check_resolved_ip_safe("linklocal.service")
        assert safe is False
        assert "private_ip" in reason


# ── safe_for_log ──────────────────────────────────────────────────────────


class TestSafeForLog:
    def test_none_returns_empty(self) -> None:
        assert safe_for_log(None) == ""

    def test_empty_string(self) -> None:
        assert safe_for_log("") == ""

    def test_strips_non_printable(self) -> None:
        out = safe_for_log("valid\x00\x01more")
        assert "\x00" not in out
        assert "\x01" not in out
        assert "validmore" == out

    def test_strips_newlines(self) -> None:
        out = safe_for_log("line1\nline2")
        assert "line1line2" == out

    def test_truncates_long(self) -> None:
        long_str = "x" * 300
        out = safe_for_log(long_str, max_len=50)
        assert len(out) == 50
        assert out.endswith("...")

    def test_short_passthrough(self) -> None:
        out = safe_for_log("hello", max_len=256)
        assert out == "hello"


# ── sanitize_exception ────────────────────────────────────────────────────


class TestSanitizeException:
    def test_plain_exception(self) -> None:
        exc = ValueError("simple message")
        assert sanitize_exception(exc) == "ValueError: simple message"

    def test_redacts_google_oauth_token(self) -> None:
        exc = ValueError("token ya29.abc123.def456 revealed")
        result = sanitize_exception(exc)
        assert "ya29" not in result
        assert "<redacted>" in result

    def test_redacts_jwt(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.dGVzdA.signature"
        exc = ValueError(f"jwt={jwt}")
        result = sanitize_exception(exc)
        assert "eyJhbGci" not in result
        assert "<redacted>" in result

    def test_redacts_sk_key(self) -> None:
        exc = ValueError("sk-abcdef1234567890abcdef1234567890")
        result = sanitize_exception(exc)
        assert "sk-abcdef" not in result
        assert "<redacted>" in result

    def test_redacts_google_api_key(self) -> None:
        api_key = "AIza" + "x" * 30
        exc = ValueError(f"key={api_key}")
        result = sanitize_exception(exc)
        assert "AIza" not in result
        assert "<redacted>" in result

    def test_redacts_bearer_needle(self) -> None:
        exc = ValueError("Authorization: Bearer some-token")
        result = sanitize_exception(exc)
        assert "Bearer" not in result
        assert "<redacted>" in result  # from case-insensitive needle match

    def test_strips_control_chars(self) -> None:
        exc = ValueError("line1\n\tline2")
        result = sanitize_exception(exc)
        assert "\n" not in result
        assert "\t" not in result

    def test_truncates_long(self) -> None:
        exc = ValueError("x" * 300)
        result = sanitize_exception(exc, max_len=50)
        # "ValueError: " = 12 chars + 47 chopped "x" + "..." = 62
        assert len(result) == 62
        assert result.endswith("...")

    def test_redacts_access_token_needle(self) -> None:
        exc = ValueError("access_token=supersecret")
        result = sanitize_exception(exc)
        assert "access_token" not in result

    def test_redacts_refresh_token_needle(self) -> None:
        exc = ValueError("refresh_token=superdupersecret")
        result = sanitize_exception(exc)
        assert "refresh_token" not in result


# ── SafeRedirectHandler ────────────────────────────────────────────────────


class TestSafeRedirectHandler:
    def test_hops_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Stub IP check so it doesn't reject localhost on every hop.
        monkeypatch.setattr(
            "backlink_publisher.net_safety.check_resolved_ip_safe",
            lambda host: (True, None),
        )
        handler = SafeRedirectHandler(allowed_hosts=("localhost",))

        from urllib.request import Request

        req = Request("http://localhost/start")

        # exceed hop limit; the 6th redirect triggers RedirectRejected
        with pytest.raises(RedirectRejected, match="redirect_cap_exceeded"):
            for i in range(6):
                handler.redirect_request(
                    req,
                    None,
                    302,
                    "Found",
                    {},
                    f"http://localhost/step{i}",
                )

    def test_scheme_downgrade_blocked(self) -> None:
        handler = SafeRedirectHandler(allowed_hosts=("example.com",))

        class FakeRequest:
            full_url = "https://example.com/start"

        with pytest.raises(RedirectRejected, match="scheme_downgrade"):
            handler.redirect_request(
                FakeRequest(),
                None,
                302,
                "Found",
                {},
                "http://example.com/other",
            )

    def test_invalid_scheme_blocked(self) -> None:
        handler = SafeRedirectHandler(allowed_hosts=("example.com",))

        class FakeRequest:
            full_url = "https://example.com/start"

        with pytest.raises(RedirectRejected, match="invalid_scheme"):
            handler.redirect_request(
                FakeRequest(),
                None,
                302,
                "Found",
                {},
                "ftp://example.com/file",
            )

    def test_host_not_allowed_blocked(self) -> None:
        handler = SafeRedirectHandler(allowed_hosts=("medium.com",))

        class FakeRequest:
            full_url = "https://medium.com/start"

        with pytest.raises(RedirectRejected, match="host_not_allowed"):
            handler.redirect_request(
                FakeRequest(),
                None,
                302,
                "Found",
                {},
                "https://evil.com/payload",
            )
