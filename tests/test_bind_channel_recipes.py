"""Tests for ChannelRecipe shape + per-channel host filters — Plan 2026-05-19-001 Unit 2.

Locks the contract:
- ``RECIPES`` is a dict[str, ChannelRecipe] keyed by CHANNELS membership.
- Every member of ``CHANNELS`` has a recipe; no extras.
- ``ChannelRecipe`` is a frozen dataclass (immutable; safe as module-level singleton).
- ``cookie_host_filter`` enforces exact-apex host match per channel; rejects
  prefix-confusion (``evilvelog.io``), suffix-confusion (``velog.io.attacker.tld``)
  and accepts case-insensitive + leading-dot ``.velog.io`` forms.
"""

from __future__ import annotations

import dataclasses

import pytest

from backlink_publisher.cli._bind.channels import CHANNELS
from backlink_publisher.cli._bind.recipes import RECIPES, ChannelRecipe


class TestRecipeRegistry:
    def test_recipes_dict_covers_exactly_channels(self):
        assert set(RECIPES.keys()) == set(CHANNELS)

    def test_every_recipe_is_channelrecipe_instance(self):
        for name, recipe in RECIPES.items():
            assert isinstance(recipe, ChannelRecipe), f"{name} recipe wrong type"

    def test_channelrecipe_is_frozen(self):
        # Frozen dataclass: mutating an instance must raise.
        recipe = RECIPES["velog"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            recipe.login_url = "https://attacker.test/"  # type: ignore[misc]


class TestRecipeFields:
    @pytest.mark.parametrize("channel", sorted(CHANNELS))
    def test_login_url_is_https(self, channel: str):
        # All login URLs must be HTTPS — no plaintext login flows.
        recipe = RECIPES[channel]
        assert recipe.login_url.startswith("https://"), \
            f"{channel}.login_url must be https"

    @pytest.mark.parametrize("channel", sorted(CHANNELS))
    def test_bound_predicate_is_callable(self, channel: str):
        recipe = RECIPES[channel]
        assert callable(recipe.bound_predicate)

    @pytest.mark.parametrize("channel", sorted(CHANNELS))
    def test_cookie_host_filter_is_callable(self, channel: str):
        recipe = RECIPES[channel]
        assert callable(recipe.cookie_host_filter)


class TestVelogHostFilter:
    """Velog cookie host filter — exact-apex match against velog.io.
    Mirrors the spike's _velog_host_allowed primitive (plan-012 R16).
    """

    def setup_method(self):
        self.filter = RECIPES["velog"].cookie_host_filter

    def test_accepts_exact_apex(self):
        assert self.filter("velog.io") is True

    def test_accepts_leading_dot_form(self):
        # Cookie hosts often appear as ".velog.io" (RFC 6265 historical form)
        assert self.filter(".velog.io") is True

    def test_accepts_case_variant(self):
        assert self.filter("Velog.IO") is True

    def test_rejects_prefix_confusion(self):
        assert self.filter("evilvelog.io") is False

    def test_rejects_suffix_confusion(self):
        assert self.filter("velog.io.attacker.tld") is False

    def test_rejects_subdomain(self):
        # Subdomains are not the apex — explicit deny per R16 ("精确匹配")
        assert self.filter("api.velog.io") is False

    def test_rejects_empty(self):
        assert self.filter("") is False

    def test_rejects_none(self):
        assert self.filter(None) is False  # type: ignore[arg-type]


class TestMediumHostFilter:
    def setup_method(self):
        self.filter = RECIPES["medium"].cookie_host_filter

    def test_accepts_medium_com(self):
        assert self.filter("medium.com") is True

    def test_accepts_leading_dot(self):
        assert self.filter(".medium.com") is True

    def test_rejects_phishing_prefix(self):
        assert self.filter("evilmedium.com") is False

    def test_rejects_suffix_confusion(self):
        assert self.filter("medium.com.attacker.tld") is False


# ─── Plan 2026-05-19-003 Unit 1 — Medium recipe hardening ───


class TestMediumCookieSanity:
    """Cookie sanity = whitelist match OR (HttpOnly + long expires + not in
    anonymous-tracking name list). Defends against false-positive 'bound'
    state when Medium sets an anonymous HttpOnly tracking cookie on
    logged-out page loads."""

    def setup_method(self):
        from backlink_publisher.cli._bind.recipes.medium import (
            _cookie_sanity_passes,
        )
        self.check = _cookie_sanity_passes

    def _make(self, *, name, httpOnly=True, expires_in_days=90):
        import time as _t
        return {
            "name": name,
            "httpOnly": httpOnly,
            "expires": _t.time() + expires_in_days * 86400,
        }

    def test_whitelisted_cookie_passes(self):
        # Spike 3a populated the whitelist with {"sid", "rid"}. Either
        # name on its own — even with short expires + not HttpOnly —
        # passes the whitelist arm (the whitelist takes precedence over
        # the structural gates).
        from backlink_publisher.cli._bind.recipes.medium import (
            MEDIUM_AUTH_COOKIE_WHITELIST,
        )
        assert MEDIUM_AUTH_COOKIE_WHITELIST == frozenset({"sid", "rid"})
        assert self.check([self._make(name="sid")]) is True
        assert self.check([self._make(name="rid")]) is True

    def test_cf_clearance_rejected_even_when_long_lived(self):
        # Spike 3a found cf_clearance is HttpOnly + ~1-year expires on
        # logged-OUT visitors that pass Cloudflare's challenge. Must NOT
        # count as auth.
        cookies = [self._make(name="cf_clearance", expires_in_days=365)]
        assert self.check(cookies) is False

    def test_xsrf_rejected_even_when_long_lived(self):
        # Spike 3a found xsrf is HttpOnly + ~1-year expires. It's a CSRF
        # token, not auth — must not stand alone as proof.
        cookies = [self._make(name="xsrf", expires_in_days=365)]
        assert self.check(cookies) is False

    def test_structural_fallback_accepts_long_lived_httponly(self):
        # Unknown-name cookie that's HttpOnly + 90-day expires + not in
        # tracking list → passes structural fallback.
        cookies = [self._make(name="medium-session-v2", expires_in_days=90)]
        assert self.check(cookies) is True

    def test_rejects_anonymous_tracking_cookies(self):
        # Anonymous tracking cookie names must NOT pass even if HttpOnly +
        # long expires. Defeats the "any HttpOnly = bound" false positive.
        cookies = [
            self._make(name="uid", expires_in_days=365),
            self._make(name="_ga", expires_in_days=365),
            self._make(name="_dd_s", expires_in_days=365),
        ]
        assert self.check(cookies) is False

    def test_rejects_short_lived_cookies(self):
        # HttpOnly but session-scoped or <7 day expiry → not auth.
        cookies = [self._make(name="medium-temp", expires_in_days=3)]
        assert self.check(cookies) is False

    def test_rejects_non_httponly(self):
        # Non-HttpOnly cookies are visible to JS → not the auth path.
        cookies = [
            self._make(name="medium-session-v2", httpOnly=False, expires_in_days=90)
        ]
        assert self.check(cookies) is False

    def test_empty_cookie_list_fails(self):
        assert self.check([]) is False

    def test_mixed_pass_takes_precedence(self):
        # If one cookie passes, return True even if others fail.
        cookies = [
            self._make(name="_ga", expires_in_days=365),
            self._make(name="medium-session-v2", expires_in_days=90),
        ]
        assert self.check(cookies) is True


class TestMediumUsernameScrape:
    """_scrape_username uses 3-tier fallback: DOM data-testid → og:url meta
    → page.url parse. Returns None if all fail."""

    def setup_method(self):
        from backlink_publisher.cli._bind.recipes.medium import (
            _scrape_username,
        )
        self.scrape = _scrape_username

    def test_dom_data_testid_first(self):
        page = _FakePage(
            url="https://medium.com/@alice",
            dom_user_link="https://medium.com/@alice_via_dom",
        )
        assert self.scrape(page) == "alice_via_dom"

    def test_og_url_fallback(self):
        page = _FakePage(
            url="https://medium.com/some-path",
            dom_user_link=None,
            og_url="https://medium.com/@bob",
        )
        assert self.scrape(page) == "bob"

    def test_url_parse_last_resort(self):
        page = _FakePage(url="https://medium.com/@charlie", dom_user_link=None, og_url=None)
        assert self.scrape(page) == "charlie"

    def test_returns_none_when_all_fail(self):
        page = _FakePage(url="https://medium.com/", dom_user_link=None, og_url=None)
        assert self.scrape(page) is None


class TestMediumLastAccountFile:
    """_read_last_account + _write_last_account_tentative round-trip.

    Uses the autouse ``_isolate_user_dirs`` fixture's env var indirection
    via ``BACKLINK_PUBLISHER_CONFIG_DIR``. Cleans per-test artifacts to
    isolate from other tests in this file.
    """

    def setup_method(self):
        from backlink_publisher.cli._bind.recipes.medium import (
            _read_last_account,
            _write_last_account_tentative,
        )
        from backlink_publisher.config.loader import _config_dir
        self._read = _read_last_account
        self._write = _write_last_account_tentative
        cfg = _config_dir()
        cfg.mkdir(parents=True, exist_ok=True)
        for name in ("medium-last-account.txt", "medium-last-account.tentative"):
            p = cfg / name
            if p.exists():
                p.unlink()

    def test_read_returns_none_when_file_absent(self):
        assert self._read() is None

    def test_write_then_read_after_promote(self):
        from backlink_publisher.config.loader import _config_dir
        cfg = _config_dir()

        self._write("alice")
        # Simulate driver promoting tentative → final
        (cfg / "medium-last-account.tentative").rename(
            cfg / "medium-last-account.txt"
        )
        assert self._read() == "alice"

    def test_write_creates_tentative_with_mode_0600(self):
        from backlink_publisher.config.loader import _config_dir
        cfg = _config_dir()

        self._write("alice")
        tentative = cfg / "medium-last-account.tentative"
        assert tentative.exists()
        assert (tentative.stat().st_mode & 0o777) == 0o600


# ─── Helpers for predicate testing ───


class _FakePage:
    """Minimal fake page that mimics the Playwright Page API surface that
    the Medium predicate uses. Tests instantiate this with canned values
    for URL, DOM scrape result, and og:url meta."""

    def __init__(
        self,
        *,
        url: str,
        dom_user_link: str | None = None,
        og_url: str | None = None,
    ):
        self.url = url
        self._dom_user_link = dom_user_link
        self._og_url = og_url

    def query_selector(self, selector: str):
        if "headerUserIcon" in selector and self._dom_user_link:
            return _FakeElement(href=self._dom_user_link)
        if 'og:url' in selector and self._og_url:
            return _FakeElement(content=self._og_url)
        return None


class _FakeElement:
    def __init__(self, *, href: str | None = None, content: str | None = None):
        self._href = href
        self._content = content

    def get_attribute(self, name: str) -> str | None:
        if name == "href":
            return self._href
        if name == "content":
            return self._content
        return None


class TestBloggerHostFilter:
    def setup_method(self):
        self.filter = RECIPES["blogger"].cookie_host_filter

    def test_accepts_blogger_com(self):
        assert self.filter("blogger.com") is True

    def test_accepts_google_com(self):
        # Blogger login routes through accounts.google.com → blogger.com;
        # the filter must accept google.com for the OAuth cookies too.
        assert self.filter("google.com") is True

    def test_accepts_accounts_subdomain(self):
        # accounts.google.com is the OAuth host — must be allowed.
        assert self.filter("accounts.google.com") is True

    def test_rejects_unrelated_host(self):
        assert self.filter("evil.test") is False

    def test_rejects_google_suffix_confusion(self):
        assert self.filter("google.com.attacker.tld") is False
