"""Tests for browser_publish.recipes.velog — Plan 2026-05-21-001 Unit 4a.

Covers:
  - velog_publish_flow happy path with stubbed Playwright Page
  - missing title / body → ValueError (recipe-internal contract)
  - missing DOM element → RuntimeError (gets wrapped by dispatcher)
  - selector module-level constants exist
  - velog dispatch chain contains both VelogGraphQLAdapter AND
    BrowserPublishDispatcher.for_channel("velog")
  - DependencyError from VelogGraphQL falls through to browser; happy path

A ``real_browser_publish_smoke`` marker test is provided but skipped by
default — operator runs it manually to re-verify selectors against live
velog.io/write.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.publishing.browser_publish import RECIPES
from backlink_publisher.publishing.browser_publish import (
    dispatcher as disp_mod,
)
from backlink_publisher.publishing.browser_publish.recipes import (
    velog as velog_recipe,
)
from backlink_publisher.publishing.browser_publish.recipes import (
    _velog_selectors as sel,
)


# ---------------------------------------------------------------------------
# Recipe registration
# ---------------------------------------------------------------------------


class TestRecipeRegistered:
    def test_velog_in_recipes(self):
        assert "velog" in RECIPES
        assert RECIPES["velog"].channel == "velog"
        assert RECIPES["velog"].compose_url == sel.COMPOSE_URL

    def test_recipe_publish_flow_is_module_callable(self):
        assert RECIPES["velog"].publish_flow is velog_recipe.velog_publish_flow


# ---------------------------------------------------------------------------
# Selector constants present
# ---------------------------------------------------------------------------


class TestSelectorsModule:
    @pytest.mark.parametrize(
        "name",
        [
            "COMPOSE_URL",
            "TITLE_INPUT",
            "BODY_EDITOR_FOCUSABLE",
            "OPEN_PUBLISH_DIALOG_BUTTON",
            "CONFIRM_PUBLISH_BUTTON_IN_DIALOG",
            "POST_PUBLISHED_URL_RE",
        ],
    )
    def test_selector_constant_defined(self, name):
        value = getattr(sel, name, None)
        assert value, f"{name} must be a non-empty selector constant"


# ---------------------------------------------------------------------------
# velog_publish_flow
# ---------------------------------------------------------------------------


def _make_page(*, final_url: str = "https://velog.io/@operator/my-slug"):
    page = MagicMock(name="velog_page")
    # Default body element exists.
    body_handle = MagicMock(name="body_handle")
    page.query_selector.return_value = body_handle
    # After wait_for_url the recipe reads page.url.
    page.url = final_url
    return page, body_handle


class TestVelogPublishFlow:
    def test_happy_path_returns_post_url(self):
        page, body = _make_page()
        url = velog_recipe.velog_publish_flow(
            page,
            {
                "title": "Hello velog",
                "content_markdown": "# Body\nText with link",
            },
        )
        assert url == "https://velog.io/@operator/my-slug"
        page.goto.assert_called_once_with(sel.COMPOSE_URL)
        page.fill.assert_called_once_with(sel.TITLE_INPUT, "Hello velog")
        body.fill.assert_called_once_with("# Body\nText with link")
        # Open + confirm publish both clicked exactly once.
        assert page.click.call_count == 2
        page.wait_for_url.assert_called_once_with(
            sel.POST_PUBLISHED_URL_RE,
            timeout=sel.POST_PUBLISH_REDIRECT_TIMEOUT_MS,
        )

    def test_accepts_body_alias(self):
        page, body = _make_page()
        velog_recipe.velog_publish_flow(
            page, {"title": "T", "body": "alt body field"}
        )
        body.fill.assert_called_once_with("alt body field")

    def test_missing_title_raises_value_error(self):
        page, _ = _make_page()
        with pytest.raises(ValueError, match="title or content_markdown"):
            velog_recipe.velog_publish_flow(page, {"content_markdown": "b"})

    def test_missing_body_raises_value_error(self):
        page, _ = _make_page()
        with pytest.raises(ValueError, match="title or content_markdown"):
            velog_recipe.velog_publish_flow(page, {"title": "t"})

    def test_missing_body_element_raises_runtime_error(self):
        page, _ = _make_page()
        page.query_selector.return_value = None
        with pytest.raises(RuntimeError, match="body element not found"):
            velog_recipe.velog_publish_flow(
                page, {"title": "t", "content_markdown": "b"}
            )


# ---------------------------------------------------------------------------
# Dispatch chain integration
# ---------------------------------------------------------------------------


class TestVelogChainFallthrough:
    """VelogGraphQLAdapter DependencyError → fall through to BrowserPublishDispatcher."""

    @pytest.fixture
    def fake_config(self):
        return MagicMock(name="fake_config")

    def test_velog_chain_has_both_adapters(self):
        """Chain shape verifies plan §Unit 4a registration."""
        # Import-side-effect loads adapters/__init__.py which registers velog.
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import _REGISTRY
        from backlink_publisher.publishing.adapters.velog_graphql import (
            VelogGraphQLAdapter,
        )
        from backlink_publisher.publishing.browser_publish import (
            BrowserPublishDispatcher,
        )

        chain = _REGISTRY["velog"].publishers
        assert len(chain) == 2
        assert chain[0] is VelogGraphQLAdapter  # class entry (legacy)
        assert isinstance(chain[1], BrowserPublishDispatcher)  # instance entry
        assert chain[1].channel == "velog"

    def test_dependency_error_falls_through_to_browser(
        self, monkeypatch, fake_config
    ):
        """When VelogGraphQLAdapter raises DependencyError, browser kicks in."""
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import dispatch
        from backlink_publisher.publishing.adapters.base import AdapterResult
        from backlink_publisher.publishing.adapters.velog_graphql import (
            VelogGraphQLAdapter,
        )

        # Patch VelogGraphQLAdapter.publish to raise DependencyError.
        def fake_publish(self, payload, mode, config):
            raise DependencyError("velog cookies missing")

        monkeypatch.setattr(VelogGraphQLAdapter, "publish", fake_publish)
        monkeypatch.setattr(VelogGraphQLAdapter, "available", classmethod(lambda cls, cfg: True))

        # Stub ChromeAttachSession to yield a fake page.
        fake_page, fake_body = _make_page(
            final_url="https://velog.io/@op/fallback-slug"
        )

        class FakeSession:
            def __init__(self, channel, **kwargs):
                self.channel = channel

            def __enter__(self):
                return fake_page

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(disp_mod, "ChromeAttachSession", FakeSession)
        # Stub link verifier so we don't try real network.
        monkeypatch.setattr(
            disp_mod, "verify_link_attributes", lambda url: {"verification": "ok"}
        )

        # Force BrowserPublishDispatcher.available True so it doesn't bail
        # on missing playwright/chrome bin in CI.
        from backlink_publisher.publishing.browser_publish import (
            BrowserPublishDispatcher,
        )
        monkeypatch.setattr(
            BrowserPublishDispatcher,
            "available",
            classmethod(lambda cls, cfg: True),
        )

        result = dispatch(
            {
                "platform": "velog",
                "title": "x",
                "content_markdown": "y",
                "target_url": "https://t.example",
            },
            "publish",
            fake_config,
        )
        assert isinstance(result, AdapterResult)
        assert result.adapter == "velog-browser-attach"
        assert result.published_url == "https://velog.io/@op/fallback-slug"


# ---------------------------------------------------------------------------
# Live smoke (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.real_browser_publish_smoke
@pytest.mark.skip(
    reason="Opt-in live smoke: open velog.io/write in attached Chrome and "
    "verify _velog_selectors still match the DOM. Run with "
    "`pytest -m real_browser_publish_smoke` and "
    "`BACKLINK_PUBLISHER_REAL_CHROME_ATTACH=1` after logging into velog in "
    "your real Chrome on the configured profile dir."
)
def test_velog_selectors_match_live_dom():
    """Operator-runs-this-by-hand smoke test."""
    raise AssertionError(
        "Spike to be performed manually — see skip reason"
    )
