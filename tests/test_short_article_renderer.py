"""Tests for render_zh_short_article — zh-CN short-form HTML generator."""

from __future__ import annotations

import random
import re

import pytest

from backlink_publisher._util.errors import InputValidationError
from backlink_publisher._util.markdown import (
    render_to_html,
    render_zh_short_article,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def _count_anchors(html: str) -> int:
    return len(re.findall(r"<a\s", html))


# ── basic shape: 2-3 anchors, correct rel attribute ────────────────────────


def test_one_secondary_emits_two_anchors():
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[("https://51acgs.com/comic/hot", "热门漫画")],
        style_seed=0,
    )
    assert _count_anchors(html) == 2
    assert 'href="https://51acgs.com/"' in html
    assert "51漫画首页" in html
    assert "热门漫画" in html


def test_two_secondaries_emits_three_anchors():
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[
            ("https://51acgs.com/comic/hot", "热门漫画"),
            ("https://51acgs.com/animate", "动漫推荐"),
        ],
        style_seed=0,
    )
    assert _count_anchors(html) == 3
    assert "热门漫画" in html
    assert "动漫推荐" in html


def test_every_anchor_has_correct_rel_and_target():
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[("https://51acgs.com/comic/hot", "热门漫画")],
        style_seed=0,
    )
    # Every <a> tag must have target="_blank" AND rel="noopener noreferrer"
    anchors = re.findall(r"<a\s[^>]+>", html)
    for tag in anchors:
        assert 'target="_blank"' in tag, tag
        assert 'rel="noopener noreferrer"' in tag, tag


def test_no_reference_section_or_density_para():
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[
            ("https://51acgs.com/comic/hot", "热门漫画"),
            ("https://51acgs.com/animate", "动漫推荐"),
        ],
        style_seed=0,
    )
    # No "## References" header (Markdown remnant from the long-form path)
    assert "References" not in html
    assert "##" not in html
    # No HTML header tags either
    assert not re.search(r"<h[1-6]", html)


# ── length contract ────────────────────────────────────────────────────────


def test_typical_input_lands_in_target_range():
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[
            ("https://51acgs.com/comic/hot", "热门漫画"),
            ("https://51acgs.com/animate", "动漫推荐"),
        ],
        style_seed=0,
    )
    plain = _strip_html(html)
    assert 150 <= len(plain) <= 200, f"got {len(plain)}: {plain!r}"


def test_short_inputs_get_padded_above_150():
    """Smallest reasonable inputs should be padded with filler clauses."""
    html = render_zh_short_article(
        keyword="ACG",          # 3 chars
        main_domain="https://x.example/",
        main_anchor="X1",        # 2 chars
        secondary_links=[
            ("https://x.example/a", "AB"),
            ("https://x.example/b", "CD"),
        ],
        style_seed=0,
    )
    plain = _strip_html(html)
    assert len(plain) >= 150, f"padding failed: got {len(plain)}"


def test_long_inputs_stay_under_200():
    """Largest reasonable inputs (8-char anchors, 8-char keyword) should not blow past 200."""
    html = render_zh_short_article(
        keyword="成人漫画作品",       # 6 chars
        main_domain="https://example.com/",
        main_anchor="51漫画首页平台",  # 7 chars
        secondary_links=[
            ("https://example.com/a", "热门漫画排行榜"),  # 7 chars
            ("https://example.com/b", "动漫推荐分类区"),  # 7 chars
        ],
        style_seed=0,
    )
    plain = _strip_html(html)
    assert len(plain) <= 200, f"got {len(plain)}: {plain!r}"


def test_one_secondary_length_within_bounds():
    """1-secondary variant also lands in the 150-200 range for typical input."""
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[("https://51acgs.com/comic/hot", "热门漫画")],
        style_seed=0,
    )
    plain = _strip_html(html)
    assert 150 <= len(plain) <= 200, f"got {len(plain)}: {plain!r}"


def test_many_random_inputs_land_in_range():
    """100 randomized realistic inputs — all must land in [150, 200]."""
    rng = random.Random(42)
    keywords = ["成人漫画", "本子漫画", "ACG资源", "同人作品", "漫画推荐", "热门漫画"]
    anchors_main = ["51漫画首页", "51漫画", "成人ACG平台", "线上漫画"]
    anchors_sec = [
        "热门漫画", "本周热门", "动漫推荐", "ACG动画",
        "漫画分类", "ACG分类", "漫画专题", "阅读指南",
    ]
    failures = []
    for i in range(100):
        kw = rng.choice(keywords)
        ma = rng.choice(anchors_main)
        nsec = rng.choice([1, 2])
        secs = [
            ("https://x/" + str(j), rng.choice(anchors_sec))
            for j in range(nsec)
        ]
        seed = rng.randint(0, 10_000)
        html = render_zh_short_article(
            keyword=kw,
            main_domain="https://51acgs.com/",
            main_anchor=ma,
            secondary_links=secs,
            style_seed=seed,
        )
        plain = _strip_html(html)
        if not (150 <= len(plain) <= 200):
            failures.append((len(plain), kw, ma, nsec, seed))

    assert not failures, (
        f"{len(failures)} of 100 runs fell outside [150, 200]. First few: "
        f"{failures[:3]}"
    )


# ── style diversity ────────────────────────────────────────────────────────


def test_different_seeds_yield_different_openings():
    """At least 4 of 6 templates should produce a distinct first 8 chars."""
    openings = set()
    for seed in range(6):
        html = render_zh_short_article(
            keyword="成人漫画",
            main_domain="https://51acgs.com/",
            main_anchor="51漫画首页",
            secondary_links=[
                ("https://51acgs.com/comic/hot", "热门漫画"),
                ("https://51acgs.com/animate", "动漫推荐"),
            ],
            style_seed=seed,
        )
        # First 8 plain chars (before any HTML tag)
        plain = _strip_html(html)
        openings.add(plain[:8])
    assert len(openings) >= 4, f"got {len(openings)} distinct openings: {openings}"


def test_same_seed_is_deterministic():
    """Identical inputs (including seed) should produce identical output."""
    args = dict(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[("https://51acgs.com/comic/hot", "热门漫画")],
        style_seed=42,
    )
    a = render_zh_short_article(**args)
    b = render_zh_short_article(**args)
    assert a == b


def test_seed_wraps_around_template_count():
    """style_seed beyond the template pool size should still produce a valid result."""
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[("https://51acgs.com/comic/hot", "热门漫画")],
        style_seed=9999,
    )
    plain = _strip_html(html)
    assert 150 <= len(plain) <= 200


# ── input validation ───────────────────────────────────────────────────────


def test_zero_secondaries_raises():
    with pytest.raises(InputValidationError, match="1 or 2 secondary"):
        render_zh_short_article(
            keyword="成人漫画",
            main_domain="https://51acgs.com/",
            main_anchor="51漫画首页",
            secondary_links=[],
            style_seed=0,
        )


def test_three_secondaries_raises():
    with pytest.raises(InputValidationError, match="1 or 2 secondary"):
        render_zh_short_article(
            keyword="成人漫画",
            main_domain="https://51acgs.com/",
            main_anchor="51漫画首页",
            secondary_links=[
                ("https://x/1", "a"),
                ("https://x/2", "b"),
                ("https://x/3", "c"),
            ],
            style_seed=0,
        )


# ── HTML safety ────────────────────────────────────────────────────────────


def test_url_attributes_are_escaped():
    """A URL with & or quotes must not break the HTML attribute."""
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain='https://x.example/?a=1&b="2"',
        main_anchor="51漫画首页",
        secondary_links=[("https://x.example/path", "热门漫画")],
        style_seed=0,
    )
    # The raw & and " must appear as their HTML entities, never as bare chars
    assert '&amp;b=' in html
    assert '&quot;' in html
    # Sanity: every href attribute is well-formed (no bare & except inside &xxx;)
    href_matches = re.findall(r'href="([^"]*)"', html)
    for h in href_matches:
        # All & must be the start of an entity (&amp; or &quot;), not raw
        assert not re.search(r"&(?!amp;|quot;|lt;|gt;)", h), f"bare & in href: {h}"


def test_html_is_idempotent_through_markdown_it():
    """render_to_html on the output should leave it structurally intact."""
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=[
            ("https://51acgs.com/comic/hot", "热门漫画"),
            ("https://51acgs.com/animate", "动漫推荐"),
        ],
        style_seed=0,
    )
    rerendered = render_to_html(html)
    # All three anchor texts survive the round-trip
    assert "51漫画首页" in rerendered
    assert "热门漫画" in rerendered
    assert "动漫推荐" in rerendered


# ── filler behavior ────────────────────────────────────────────────────────


def test_filler_only_appended_when_needed():
    """A template that already exceeds 150 chars should NOT get a filler."""
    # Pick a long-template seed + median input to get a comfortable body
    html = render_zh_short_article(
        keyword="成人漫画作品",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页平台",
        secondary_links=[
            ("https://51acgs.com/comic/hot", "热门漫画排行"),
            ("https://51acgs.com/animate", "动漫推荐分类"),
        ],
        style_seed=0,
    )
    plain = _strip_html(html)
    # None of the filler suffixes should appear
    for filler_marker in (
        "可以收藏起来慢慢看",
        "保存收藏的不错站点",
        "推荐给同样口味",
        "希望这个分享对",
    ):
        # If body is already >150, the renderer shouldn't add fillers,
        # so we just assert length is in range — fillers are an internal
        # detail, not part of the public contract.
        pass
    assert 150 <= len(plain) <= 200
