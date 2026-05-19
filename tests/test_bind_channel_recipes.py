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
