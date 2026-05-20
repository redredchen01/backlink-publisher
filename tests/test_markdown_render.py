"""Tests for render_to_html + _format_anchor_html rel parameterisation."""

from backlink_publisher._util.markdown import (
    _format_anchor_html,
    render_to_html,
    select_anchor_keywords,
)


def test_heading_and_paragraph():
    html = render_to_html("# Title\n\nBody text.")
    assert "<h1>Title</h1>" in html
    assert "<p>Body text.</p>" in html


def test_link_no_nofollow():
    html = render_to_html("[anchor](https://example.com)")
    assert 'href="https://example.com"' in html
    assert "nofollow" not in html


def test_link_has_blank_target_and_noopener():
    html = render_to_html("[anchor](https://example.com)")
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html


def test_multiple_links_each_get_blank_target():
    md = "[a](https://a.com) and [b](https://b.com) and [c](https://c.com)"
    html = render_to_html(md)
    assert html.count('target="_blank"') == 3
    assert html.count('rel="noopener"') == 3


def test_bold():
    html = render_to_html("**bold**")
    assert "<strong>bold</strong>" in html


def test_empty_string():
    assert render_to_html("") == ""


def test_chinese_characters():
    html = render_to_html("**你好**，世界。")
    assert "你好" in html
    assert "世界" in html


def test_russian_characters():
    html = render_to_html("Привет **мир**.")
    assert "Привет" in html
    assert "<strong>мир</strong>" in html


def test_backlink_survives_rendering():
    md = "Visit [example.com](https://example.com) for more."
    html = render_to_html(md)
    assert "https://example.com" in html


def test_raw_html_preserved():
    md = "Text <br/> more text."
    html = render_to_html(md)
    # markdown-it by default allows inline HTML
    assert "more text" in html


# ---------------------------------------------------------------------------
# _format_anchor_html — rel parameterisation (Plan 2026-05-13-004 Unit 4)
# ---------------------------------------------------------------------------


def test_format_anchor_html_default_rel_unchanged():
    """Backwards-compatible default — long-form callers see noopener+noreferrer."""
    out = _format_anchor_html("https://example.com", "anchor")
    assert 'rel="noopener noreferrer"' in out
    assert 'target="_blank"' in out
    assert 'href="https://example.com"' in out


def test_format_anchor_html_explicit_rel_noopener_only():
    """Work-themed path opts into bare noopener so dofollow weight is preserved."""
    out = _format_anchor_html("https://example.com", "anchor", rel="noopener")
    assert 'rel="noopener"' in out
    assert "noreferrer" not in out


def test_format_anchor_html_url_attribute_escapes_apply():
    out = _format_anchor_html(
        'https://example.com/?q="x"&y=<z>', "anchor", rel="noopener"
    )
    assert "&amp;" in out
    assert "&quot;" in out
    assert "&lt;" in out
    assert "&gt;" in out


# ---------------------------------------------------------------------------
# select_anchor_keywords
# ---------------------------------------------------------------------------


def test_select_anchor_keywords_mode_a_no_offset():
    assert select_anchor_keywords(["a", "b", "c"], "A", 2) == ["a", "b"]


def test_select_anchor_keywords_mode_b_offset_one():
    assert select_anchor_keywords(["a", "b", "c"], "B", 2) == ["b", "c"]


def test_select_anchor_keywords_mode_c_offset_wraps_around():
    assert select_anchor_keywords(["a", "b", "c"], "C", 2) == ["c", "a"]


def test_select_anchor_keywords_count_exceeds_pool_wraps():
    # count=4 with pool of 2: wraps without crashing
    assert select_anchor_keywords(["x", "y"], "A", 4) == ["x", "y", "x", "y"]


def test_select_anchor_keywords_empty_pool_returns_none():
    assert select_anchor_keywords([], "A", 2) is None


def test_select_anchor_keywords_unknown_url_mode_treated_as_offset_zero():
    assert select_anchor_keywords(["a", "b", "c"], "Z", 2) == ["a", "b"]


def test_select_anchor_keywords_count_zero():
    assert select_anchor_keywords(["a", "b"], "A", 0) == []


def test_select_anchor_keywords_single_keyword_repeats():
    assert select_anchor_keywords(["only"], "A", 2) == ["only", "only"]


def test_select_anchor_keywords_deterministic():
    pool = ["k1", "k2", "k3", "k4"]
    # Same input → identical output, every call
    a = select_anchor_keywords(pool, "B", 3)
    b = select_anchor_keywords(pool, "B", 3)
    assert a == b == ["k2", "k3", "k4"]
