"""Tests for validate_zh_short_payload — Unit 8 validator gate."""

from __future__ import annotations

import pytest

from backlink_publisher.markdown_utils import (
    render_zh_short_article,
    validate_zh_short_payload,
)


def _good_payload(seed: int = 0, n_sec: int = 2):
    """Build a known-good payload and its expected anchor list."""
    secs = [("https://x/hot", "热门漫画")]
    if n_sec == 2:
        secs.append(("https://x/animate", "动漫推荐"))
    html = render_zh_short_article(
        keyword="成人漫画",
        main_domain="https://51acgs.com/",
        main_anchor="51漫画首页",
        secondary_links=secs,
        style_seed=seed,
    )
    expected = ["51漫画首页"] + [a for _, a in secs]
    return html, expected


# ── happy path ──────────────────────────────────────────────────────────────


def test_happy_path_2_secondaries():
    html, expected = _good_payload(n_sec=2)
    ok, errors = validate_zh_short_payload(html, expected)
    assert ok, f"expected ok, got errors: {errors}"
    assert errors == []


def test_happy_path_1_secondary():
    html, expected = _good_payload(n_sec=1)
    ok, errors = validate_zh_short_payload(html, expected)
    assert ok, f"expected ok, got errors: {errors}"


def test_happy_path_all_seeds():
    for seed in range(6):
        html, expected = _good_payload(seed=seed, n_sec=2)
        ok, errors = validate_zh_short_payload(html, expected)
        assert ok, f"seed {seed}: {errors}"


# ── length checks ───────────────────────────────────────────────────────────


def test_short_body_rejected():
    html = '<a href="https://x/" target="_blank" rel="noopener noreferrer">x1</a>太短了。'
    ok, errors = validate_zh_short_payload(html, ["x1"])
    assert not ok
    assert any("length_below" in e for e in errors)


def test_long_body_rejected():
    # 220-char filler + 2 anchors → exceeds 200
    filler = "甲" * 220
    html = (
        f'<a href="https://x/" target="_blank" rel="noopener noreferrer">主页</a>'
        f"{filler}"
        f'<a href="https://x/a" target="_blank" rel="noopener noreferrer">热门</a>'
    )
    ok, errors = validate_zh_short_payload(html, ["主页", "热门"])
    assert not ok
    assert any("length_above" in e for e in errors)


# ── anchor count ────────────────────────────────────────────────────────────


def test_one_anchor_rejected():
    # Only 1 <a> tag, padded to ~165 chars
    body = (
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">主页</a>'
        + "甲" * 165
    )
    ok, errors = validate_zh_short_payload(body, ["主页"])
    assert not ok
    assert any("anchor_count_out_of_range:1" in e for e in errors)


def test_four_anchors_rejected():
    body = (
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">主页</a>'
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">a</a>'
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">b</a>'
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">c</a>'
        + "甲" * 165
    )
    ok, errors = validate_zh_short_payload(body, ["主页", "a", "b", "c"])
    assert not ok
    assert any("anchor_count_out_of_range:4" in e for e in errors)


# ── attribute checks ────────────────────────────────────────────────────────


def test_missing_target_blank_rejected():
    body = (
        '<a href="https://x/" rel="noopener noreferrer">主页</a>'
        + "甲" * 80
        + '<a href="https://x/a" target="_blank" rel="noopener noreferrer">热门</a>'
        + "甲" * 80
    )
    ok, errors = validate_zh_short_payload(body, ["主页", "热门"])
    assert not ok
    assert any("missing_target_blank" in e for e in errors)


def test_missing_noreferrer_rejected():
    """Strict rel match — 'noopener' alone is not enough."""
    body = (
        '<a href="https://x/" target="_blank" rel="noopener">主页</a>'
        + "甲" * 80
        + '<a href="https://x/a" target="_blank" rel="noopener noreferrer">热门</a>'
        + "甲" * 80
    )
    ok, errors = validate_zh_short_payload(body, ["主页", "热门"])
    assert not ok
    assert any("missing_rel_noopener_noreferrer" in e for e in errors)


# ── anchor text filter ──────────────────────────────────────────────────────


def test_forbidden_anchor_text_rejected():
    body = (
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">点击这里</a>'
        + "甲" * 80
        + '<a href="https://x/a" target="_blank" rel="noopener noreferrer">热门漫画</a>'
        + "甲" * 80
    )
    ok, errors = validate_zh_short_payload(body, ["点击这里", "热门漫画"])
    assert not ok
    assert any("anchor_failed_filters:点击这里" in e for e in errors)


def test_anchor_text_too_long_rejected():
    body = (
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">这是太长了的锚文本九个字</a>'
        + "甲" * 80
        + '<a href="https://x/a" target="_blank" rel="noopener noreferrer">热门漫画</a>'
        + "甲" * 80
    )
    ok, errors = validate_zh_short_payload(body, ["这是太长了的锚文本九个字", "热门漫画"])
    assert not ok
    assert any("anchor_failed_filters" in e for e in errors)


def test_unexpected_anchor_text_rejected():
    """Generator emitted text the resolver didn't decide."""
    body = (
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">出错文本</a>'
        + "甲" * 80
        + '<a href="https://x/a" target="_blank" rel="noopener noreferrer">热门漫画</a>'
        + "甲" * 80
    )
    ok, errors = validate_zh_short_payload(body, ["51漫画首页", "热门漫画"])
    assert not ok
    assert any("unexpected_anchor_text:出错文本" in e for e in errors)


# ── bare URL ────────────────────────────────────────────────────────────────


def test_bare_url_in_body_rejected():
    body = (
        '<a href="https://x/" target="_blank" rel="noopener noreferrer">主页</a>'
        + "甲" * 70
        + " 也可以访问 https://51acgs.com 这是裸链接 "
        + "甲" * 30
        + '<a href="https://x/a" target="_blank" rel="noopener noreferrer">热门漫画</a>'
        + "甲" * 30
    )
    ok, errors = validate_zh_short_payload(body, ["主页", "热门漫画"])
    assert not ok
    assert "bare_url_outside_anchor" in errors


def test_urls_inside_anchor_href_are_fine():
    """URLs in href= attributes don't count as bare URLs."""
    html, expected = _good_payload(n_sec=2)
    # The renderer puts https:// inside every <a href="..."> — those should NOT trigger
    ok, errors = validate_zh_short_payload(html, expected)
    assert ok, errors


# ── multi-failure reporting ────────────────────────────────────────────────


def test_multiple_errors_all_reported():
    """The validator should collect every distinct failure, not bail at #1."""
    body = (
        '<a href="https://x/" rel="noopener">点击这里</a>'  # missing target_blank, wrong rel, forbidden anchor
        + "甲" * 50
    )
    ok, errors = validate_zh_short_payload(body, ["点击这里"])
    assert not ok
    # At least 4 distinct error types: length_below_150, missing_target_blank,
    # missing_rel_noopener_noreferrer, anchor_failed_filters
    error_prefixes = {e.split(":")[0] for e in errors}
    assert len(error_prefixes) >= 4
