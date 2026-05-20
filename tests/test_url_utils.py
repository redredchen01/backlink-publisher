"""Tests for backlink_publisher.url_utils — Plan 2026-05-13-004 Unit 1."""

from __future__ import annotations

import pytest

from backlink_publisher._util.url import (
    absolutize,
    is_same_host,
    strip_fragment_query,
    validate_https_url,
    validate_main_domain_url,
)


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
