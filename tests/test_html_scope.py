"""Tests for ``ArticleScopedCollector``.

Plan: ``docs/plans/2026-05-14-005-feat-v1-verifier-asset-extraction-plan.md``
Source: ``origin/pr/1:src/backlink_publisher/verifier.py``
"""

from __future__ import annotations

import pytest

from backlink_publisher.html_scope import ArticleScopedCollector, collect_article_text


# ── convenience wrapper ───────────────────────────────────────────────────


class TestCollectArticleText:
    def test_simple_article(self) -> None:
        assert collect_article_text("<article>Hello</article>") == "Hello"

    def test_no_article(self) -> None:
        assert collect_article_text("<p>No article</p>") == ""

    def test_only_first_article(self) -> None:
        html = "<article>First</article><article>Second</article>"
        assert collect_article_text(html) == "First"

    def test_nested_article_skips_inner(self) -> None:
        html = "<article>Outer <article>Inner</article> text</article>"
        result = collect_article_text(html)
        assert "Inner" not in result
        assert "Outer" in result

    def test_empty_html(self) -> None:
        assert collect_article_text("") == ""


# ── ArticleScopedCollector internals ──────────────────────────────────────


class TestArticleScopedCollectorBasics:
    def test_no_article_reached(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<p>Hello</p>")
        collector.close()
        assert collector.text == ""
        assert collector.reached_article is False

    def test_single_article(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<article>Hello world</article>")
        collector.close()
        assert collector.text == "Hello world"
        assert collector.reached_article is True

    def test_multiple_articles(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<article>First</article><article>Second</article>")
        collector.close()
        assert collector.text == "First"

    def test_text_before_article_ignored(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<p>Before</p><article>Inside</article>")
        collector.close()
        assert collector.text == "Inside"

    def test_nested_article_outer_only(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<article>A<article>B</article>C</article>")
        collector.close()
        assert "B" not in collector.text
        assert "A" in collector.text or "C" in collector.text

    def test_deeply_nested_article(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<article>Level1<article>Level2<article>Level3</article></article></article>")
        collector.close()
        assert "Level2" not in collector.text
        assert "Level3" not in collector.text
        assert "Level1" in collector.text


# ── sidebar exclusion ──────────────────────────────────────────────────────


class TestSidebarExclusion:
    def test_role_complementary_excluded(self) -> None:
        html = "<article>Main <div role='complementary'>Side</div> text</article>"
        result = collect_article_text(html)
        assert "Side" not in result
        assert "Main" in result
        assert "text" in result

    def test_role_navigation_excluded(self) -> None:
        html = "<article>Main <nav role='navigation'>Links</nav> post</article>"
        result = collect_article_text(html)
        assert "Links" not in result

    def test_role_banner_excluded(self) -> None:
        html = "<article>Main <header role='banner'>Banner</header> post</article>"
        result = collect_article_text(html)
        assert "Banner" not in result

    def test_hidden_element_excluded(self) -> None:
        html = "<article>Visible <div hidden='hidden'>Hidden</div> text</article>"
        result = collect_article_text(html)
        assert "Hidden" not in result

    def test_aria_hidden_excluded(self) -> None:
        html = "<article>Visible <div aria-hidden='true'>Hidden</div> text</article>"
        result = collect_article_text(html)
        assert "Hidden" not in result

    def test_excluded_subtree_nested(self) -> None:
        html = "<article>Main <div role='navigation'><ul><li>Link1</li><li>Link2</li></ul></div> post</article>"
        result = collect_article_text(html)
        assert "Link1" not in result
        assert "Link2" not in result
        assert "Main" in result

    def test_role_contentinfo_excluded(self) -> None:
        html = "<article>Content <footer role='contentinfo'>Footer</footer></article>"
        result = collect_article_text(html)
        assert "Footer" not in result

    def test_non_sidebar_role_included(self) -> None:
        html = "<article>Main <div role='main'>Content</div></article>"
        result = collect_article_text(html)
        assert "Content" in result or "Main" in result


# ── edge cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_unclosed_article(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<article>Unclosed text")
        collector.close()
        # ``reached_article`` is True because ``<article>`` was entered,
        # even though it was never closed.
        assert collector.reached_article is True
        assert collector.text == "Unclosed text"

    def test_unclosed_tag_inside_article(self) -> None:
        collector = ArticleScopedCollector()
        collector.feed("<article>Text <div>More")
        collector.close()
        # ``reached_article`` is True because the outer ``<article>``
        # was entered, even though it (and the inner ``<div>``) never closed.
        assert collector.reached_article is True
        assert collector.text == "Text More"

    def test_empty_article(self) -> None:
        result = collect_article_text("<article></article>")
        assert result == ""

    def test_article_with_whitespace(self) -> None:
        result = collect_article_text("<article>   </article>")
        assert result == ""

    def test_mixed_content(self) -> None:
        html = "<article>Hello <b>bold</b> world</article>"
        result = collect_article_text(html)
        assert "Hello" in result
        assert "world" in result
        # bold tag data captured
        assert "bold" in result

    def test_nested_sidebar_inside_nested_article(self) -> None:
        html = "<article>Outer<article><div role='complementary'>Skip</div></article></article>"
        result = collect_article_text(html)
        # outer article captures "Outer", inner article+depth handling
        # The outer article stays at depth 1, inner at depth 2
        # _inner_depth only tracked for depth==1, so inner article sidebar
        # is handled by nested article skip, not sidebar skip.
        # The key: no crash, no unexpected text
        assert "Skip" not in result

    def test_article_with_br(self) -> None:
        result = collect_article_text("<article>Line1<br>Line2</article>")
        assert "Line1" in result
        assert "Line2" in result

    def test_self_closing_tag_in_article(self) -> None:
        result = collect_article_text("<article>Text<hr/>More</article>")
        assert "Text" in result
        assert "More" in result

    def test_deep_article_nesting_beyond_max(self) -> None:
        collector = ArticleScopedCollector(max_article_depth=1)
        html = "<article>L1<article>L2</article></article>"
        collector.feed(html)
        collector.close()
        assert collector.text == "L1"
        # No crash from depth exceeding max.

    def test_multiple_sidebar_roles(self) -> None:
        html = (
            "<article>"
            "<div role='complementary'>Sidebar</div>"
            "<nav role='navigation'>Nav</nav>"
            "Main"
            "</article>"
        )
        result = collect_article_text(html)
        assert "Sidebar" not in result
        assert "Nav" not in result
        assert "Main" in result
