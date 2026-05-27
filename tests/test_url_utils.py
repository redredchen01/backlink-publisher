"""Tests for backlink_publisher.url_utils — Plan 2026-05-13-004 Unit 1."""

from __future__ import annotations

import pytest

from urllib.request import Request

from backlink_publisher._util.url import (
    absolutize,
    is_same_host,
    normalize_url_for_fetch,
    safe_hostname,
    safe_urlparse,
    strip_fragment_query,
    validate_https_url,
    validate_main_domain_url,
)

# Inputs that make stdlib urlparse/urlsplit/urljoin raise (unterminated IPv6
# literal). Shared across the never-raises tests below.
_MALFORMED_URLS = ["http://[invalid", "http://[::1", "http://["]


# ── validate_main_domain_url ────────────────────────────────────────────────


class TestValidateMainDomainUrl:
    def test_https_root_with_trailing_slash_is_unchanged(self):
        assert validate_main_domain_url("https://site.com/") == "https://site.com/"

    def test_https_root_without_trailing_slash_is_normalized(self):
        assert validate_main_domain_url("https://site.com") == "https://site.com/"

    def test_https_with_path_is_rejected(self):
        # main_url context — non-root paths are rejected
        assert validate_main_domain_url("https://site.com/path/") is None
        assert validate_main_domain_url("https://site.com/foo") is None

    def test_http_scheme_is_rejected(self):
        assert validate_main_domain_url("http://site.com/") is None

    def test_scheme_missing_is_rejected(self):
        assert validate_main_domain_url("site.com") is None
        assert validate_main_domain_url("//site.com") is None

    def test_empty_and_none_are_rejected(self):
        assert validate_main_domain_url("") is None
        assert validate_main_domain_url(None) is None

    def test_fragment_is_rejected(self):
        assert validate_main_domain_url("https://site.com/#section") is None

    def test_query_is_rejected(self):
        assert validate_main_domain_url("https://site.com/?foo=bar") is None

    def test_whitespace_around_url_is_stripped(self):
        assert validate_main_domain_url("  https://site.com  ") == "https://site.com/"

    def test_subdomain_is_accepted(self):
        assert validate_main_domain_url("https://www.site.com") == "https://www.site.com/"

    def test_port_is_preserved(self):
        assert validate_main_domain_url("https://site.com:8443") == "https://site.com:8443/"


# ── validate_https_url ──────────────────────────────────────────────────────


class TestValidateHttpsUrl:
    def test_https_with_deep_path_is_accepted(self):
        assert validate_https_url("https://site.com/work/123") == "https://site.com/work/123"

    def test_https_with_query_is_preserved(self):
        assert (
            validate_https_url("https://site.com/list?page=2")
            == "https://site.com/list?page=2"
        )

    def test_https_fragment_is_dropped(self):
        assert (
            validate_https_url("https://site.com/work/1#comments")
            == "https://site.com/work/1"
        )

    def test_http_scheme_is_rejected(self):
        assert validate_https_url("http://site.com/work/1") is None

    def test_empty_and_none_are_rejected(self):
        assert validate_https_url("") is None
        assert validate_https_url(None) is None

    def test_no_host_is_rejected(self):
        assert validate_https_url("https:///path") is None

    def test_bare_root_gets_trailing_slash(self):
        assert validate_https_url("https://site.com") == "https://site.com/"


# ── is_same_host ────────────────────────────────────────────────────────────


class TestIsSameHost:
    def test_identical_hosts(self):
        assert is_same_host("https://site.com/a", "https://site.com/b")

    def test_www_prefix_ignored(self):
        assert is_same_host("https://www.site.com/", "https://site.com/")

    def test_case_insensitive(self):
        assert is_same_host("https://Site.COM/", "https://site.com/")

    def test_different_hosts(self):
        assert not is_same_host("https://site.com/", "https://other.com/")

    def test_different_subdomains_not_same_host(self):
        # `cdn.site.com` and `site.com` are different hosts (www is the only
        # prefix we strip).
        assert not is_same_host("https://cdn.site.com/", "https://site.com/")

    def test_strict_port_comparison(self):
        assert not is_same_host("https://site.com/", "https://site.com:8443/")

    def test_empty_inputs_return_false(self):
        assert not is_same_host("", "https://site.com/")
        assert not is_same_host("https://site.com/", "")

    def test_non_url_inputs_return_false(self):
        assert not is_same_host("not a url", "also not")


# ── absolutize ──────────────────────────────────────────────────────────────


class TestAbsolutize:
    def test_relative_path_resolves_against_base(self):
        assert absolutize("https://site.com/list", "/work/1") == "https://site.com/work/1"

    def test_absolute_href_overrides_base(self):
        assert (
            absolutize("https://site.com/list", "https://other.com/x")
            == "https://other.com/x"
        )

    def test_relative_path_without_leading_slash(self):
        assert (
            absolutize("https://site.com/list/", "work/1")
            == "https://site.com/list/work/1"
        )

    def test_empty_href_returns_empty(self):
        assert absolutize("https://site.com/", "") == ""

    @pytest.mark.parametrize("href", _MALFORMED_URLS)
    def test_malformed_href_returns_empty_never_raises(self, href):
        # urljoin raises ValueError on malformed IPv6 just like urlparse.
        assert absolutize("https://site.com/", href) == ""

    def test_malformed_base_returns_empty_never_raises(self):
        assert absolutize("http://[invalid", "/page") == ""


# ── strip_fragment_query ────────────────────────────────────────────────────


class TestStripFragmentQuery:
    def test_strips_fragment(self):
        assert strip_fragment_query("https://site.com/a#frag") == "https://site.com/a"

    def test_strips_query(self):
        assert (
            strip_fragment_query("https://site.com/a?foo=bar")
            == "https://site.com/a"
        )

    def test_strips_both(self):
        assert (
            strip_fragment_query("https://site.com/a?foo=bar#frag")
            == "https://site.com/a"
        )

    def test_preserves_path(self):
        assert (
            strip_fragment_query("https://site.com/work/123/")
            == "https://site.com/work/123/"
        )

    def test_empty_returns_empty(self):
        assert strip_fragment_query("") == ""


# ── normalize_url_for_fetch ────────────────────────────────────────────────


class TestNormalizeUrlForFetch:
    """Regression coverage for Plan 2026-05-21-005.

    The bug: ``urllib.request.urlopen`` crashes with
    ``'ascii' codec can't encode characters`` whenever the URL handed to
    ``Request(...)`` carries non-ASCII bytes (Velog Korean ``@username``,
    CJK ``url_slug``). Verifier hits this at runtime and demotes legitimate
    posts to ``published_unverified``.
    """

    def test_ascii_url_is_returned_byte_identical(self):
        url = "https://example.com/api/v1?key=abc&id=1"
        assert normalize_url_for_fetch(url) == url

    def test_ascii_url_with_already_encoded_path_is_unchanged(self):
        url = "https://example.com/path%20with%20space?q=hello%20world"
        assert normalize_url_for_fetch(url) == url

    def test_cjk_in_path_segment_after_at_sign(self):
        # Real-world: Velog Korean @username
        out = normalize_url_for_fetch("https://velog.io/@한글/foo-bar")
        assert out == "https://velog.io/@%ED%95%9C%EA%B8%80/foo-bar"
        # The whole point: stdlib will now accept it.
        Request(out)

    def test_cjk_in_slug(self):
        out = normalize_url_for_fetch("https://velog.io/@user/한글-제목")
        assert out == "https://velog.io/@user/%ED%95%9C%EA%B8%80-%EC%A0%9C%EB%AA%A9"
        Request(out)

    def test_chinese_slug(self):
        out = normalize_url_for_fetch("https://velog.io/@user/英语口语训练")
        assert out.startswith("https://velog.io/@user/")
        # All non-ASCII bytes percent-encoded; hyphens / slashes preserved.
        Request(out)

    def test_idempotent_on_already_encoded(self):
        already = "https://velog.io/@%ED%95%9C%EA%B8%80/foo"
        assert normalize_url_for_fetch(already) == already

    def test_idempotent_under_double_application(self):
        once = normalize_url_for_fetch("https://velog.io/@한글/foo")
        twice = normalize_url_for_fetch(once)
        assert once == twice

    def test_empty_returns_empty(self):
        assert normalize_url_for_fetch("") == ""

    def test_non_http_scheme_passthrough(self):
        assert normalize_url_for_fetch("mailto:user@example.com") == "mailto:user@example.com"
        assert normalize_url_for_fetch("ftp://files.example.com/x") == "ftp://files.example.com/x"

    def test_query_with_non_ascii_value(self):
        out = normalize_url_for_fetch("https://x.io/p?q=한")
        assert out == "https://x.io/p?q=%ED%95%9C"
        Request(out)

    def test_query_delimiters_preserved(self):
        out = normalize_url_for_fetch("https://x.io/p?a=1&b=2&c=한")
        assert out == "https://x.io/p?a=1&b=2&c=%ED%95%9C"

    def test_port_preserved_with_cjk_path(self):
        out = normalize_url_for_fetch("https://velog.io:8443/@한/p")
        assert out == "https://velog.io:8443/@%ED%95%9C/p"

    def test_userinfo_preserved(self):
        # userinfo is rare but should pass through unchanged when host is ASCII.
        url = "https://user:pass@host.io/p"
        assert normalize_url_for_fetch(url) == url

    def test_idna_host(self):
        # IDNA-encoded host appears as xn-- punycode form.
        out = normalize_url_for_fetch("https://例え.テスト/path")
        assert out.startswith("https://xn--")
        assert out.endswith("/path")
        Request(out)

    def test_idna_failure_falls_back_to_original_host(self):
        # Non-ASCII host that fails IDNA: a label with 64 CJK chars expands
        # far beyond the 63-octet punycode limit, triggering UnicodeError in
        # host.encode("idna"). The function must not raise — it falls back to
        # the original host so callers get a structured network failure, not a
        # local exception.
        overlong_label = "あ" * 64  # each char → ~3 punycode bytes; 64×3 >> 63 limit
        url = f"https://{overlong_label}.example.com/path"
        result = normalize_url_for_fetch(url)
        # Must not raise; path/query still get percent-encoded
        assert "/path" in result

    def test_fragment_dropped_when_transformation_runs(self):
        # Fragment never reaches the wire; we drop it whenever we transform.
        out = normalize_url_for_fetch("https://velog.io/@한/p#section")
        assert "#" not in out

    def test_unicode_url_no_longer_crashes_request(self):
        """The exact production failure mode this plan fixes.

        ``urllib.request`` ultimately routes the URL through ``http.client``,
        which ASCII-encodes the request line. The non-ASCII URL therefore
        fails ``str.encode('ascii')``; the normalized URL must pass it.
        """
        url = "https://velog.io/@한글유저/some-slug"
        with pytest.raises(UnicodeEncodeError):
            url.encode("ascii")
        normalized = normalize_url_for_fetch(url)
        normalized.encode("ascii")  # would raise if not clean
        Request(normalized)


# ── safe_urlparse / safe_hostname (Plan 2026-05-27-006 Unit 1) ──────────────


class TestSafeUrlparse:
    def test_valid_url_parses(self):
        parsed = safe_urlparse("https://example.com/p?q=1")
        assert parsed is not None
        assert parsed.scheme == "https"
        assert parsed.netloc == "example.com"
        assert parsed.path == "/p"

    def test_valid_bracketed_ipv6_is_not_swallowed(self):
        # A *well-formed* IPv6 literal must parse, not be mistaken for malformed.
        parsed = safe_urlparse("http://[::1]:8080/")
        assert parsed is not None
        assert parsed.hostname == "::1"
        assert parsed.port == 8080

    def test_empty_string_returns_none(self):
        assert safe_urlparse("") is None

    @pytest.mark.parametrize("url", _MALFORMED_URLS)
    def test_malformed_ipv6_returns_none_never_raises(self, url):
        assert safe_urlparse(url) is None

    @pytest.mark.parametrize("bad", [123, ["x"], {}, 3.14, object()])
    def test_non_str_returns_none_never_raises(self, bad):
        # urlparse(123) raises AttributeError, not ValueError — the isinstance
        # guard is load-bearing for the never-raises contract.
        assert safe_urlparse(bad) is None

    def test_none_returns_none(self):
        assert safe_urlparse(None) is None


class TestSafeHostname:
    def test_valid_url_returns_host(self):
        assert safe_hostname("https://example.com/p") == "example.com"

    def test_valid_ipv6_returns_host(self):
        assert safe_hostname("http://[::1]:8080/") == "::1"

    @pytest.mark.parametrize("url", _MALFORMED_URLS)
    def test_malformed_returns_none_never_raises(self, url):
        assert safe_hostname(url) is None

    def test_non_str_returns_none(self):
        assert safe_hostname(123) is None

    def test_hostless_url_returns_none(self):
        # parseable but no authority → no hostname
        assert safe_hostname("mailto:x@y.com") is None


# ── scrape-path helpers never-raise on malformed input (Unit 2) ─────────────


class TestScrapePathHelpersNeverRaise:
    @pytest.mark.parametrize("url", _MALFORMED_URLS)
    def test_is_same_host_malformed_a_returns_false(self, url):
        assert is_same_host(url, "https://site.com") is False

    @pytest.mark.parametrize("url", _MALFORMED_URLS)
    def test_is_same_host_malformed_b_returns_false(self, url):
        assert is_same_host("https://site.com", url) is False

    def test_is_same_host_valid_pair_unchanged(self):
        assert is_same_host("https://site.com/a", "https://www.site.com/b") is True
        assert is_same_host("https://a.com", "https://b.com") is False

    @pytest.mark.parametrize("url", _MALFORMED_URLS)
    def test_strip_fragment_query_malformed_returns_empty(self, url):
        assert strip_fragment_query(url) == ""

    def test_strip_fragment_query_valid_still_strips(self):
        assert strip_fragment_query("https://s.com/p?q=1#frag") == "https://s.com/p"
