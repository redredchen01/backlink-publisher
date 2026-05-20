"""Tests for backlink_publisher.work_themed_generator — Plan 2026-05-13-004 Unit 4.

Covers:
- ``select_anchors`` happy path / fallback paths / determinism / dedup
- ``render_work_themed_article`` produces 3 paragraphs, 6 reachable position
  permutations across seeds, ``<a target="_blank" rel="noopener">`` (NO
  ``nofollow``), markdown-it idempotent round-trip
- XSS via scraped ``<title>`` is HTML-escaped; bidi/zero-width attack characters
  are rejected by ``_passes_work_anchor_filter``
- ``_passes_work_anchor_filter`` accepts pure-ASCII titles (no CJK requirement)
- ``InputValidationError`` when ``branded_pool`` is empty
"""

from __future__ import annotations

import pytest

from backlink_publisher.anchor.resolver import _passes_work_anchor_filter
from backlink_publisher.config import DEFAULT_WORK_TEMPLATES, ThreeUrlConfig
from backlink_publisher._util.errors import InputValidationError
from backlink_publisher._util.markdown import _format_anchor_html, render_to_html
from backlink_publisher.content.scraper import WorkMetadata
from backlink_publisher.content.themed_gen import (
    Anchors,
    render_work_themed_article,
    select_anchors,
)


# ── helpers ──────────────────────────────────────────────────────────────────


_DEFAULT_BRANDED = ["品牌站", "品牌首页", "Brand"]
_DEFAULT_PARTIAL = ["品牌部分关键词", "品牌相关"]
_DEFAULT_EXACT = ["关键词", "exact term"]


def _cfg(
    *,
    branded: list[str] | None = None,
    partial: list[str] | None = None,
    exact: list[str] | None = None,
    templates: list[str] | None = None,
) -> ThreeUrlConfig:
    # Use `is None` sentinel — `branded=[]` must reach the dataclass intact.
    return ThreeUrlConfig(
        main_url="https://site.com/",
        list_url="https://site.com/list",
        branded_pool=list(_DEFAULT_BRANDED) if branded is None else branded,
        partial_pool=list(_DEFAULT_PARTIAL) if partial is None else partial,
        exact_pool=list(_DEFAULT_EXACT) if exact is None else exact,
        work_urls=[],
        work_anchor_templates=(
            templates if templates is not None else list(DEFAULT_WORK_TEMPLATES)
        ),
    )


def _meta(title: str = "深夜动漫推荐") -> WorkMetadata:
    return WorkMetadata(title=title, description="一部值得一看的作品", h1=title)


# ═════════════════════════════════════════════════════════════════════════════
# select_anchors
# ═════════════════════════════════════════════════════════════════════════════


class TestSelectAnchorsHappyPath:
    def test_returns_three_anchors_from_correct_pools(self):
        cfg = _cfg()
        anchors = select_anchors(cfg, _meta(), seed=0, recent_texts=[])
        assert isinstance(anchors, Anchors)
        assert anchors.main_anchor in cfg.branded_pool
        assert anchors.list_anchor in (cfg.partial_pool + cfg.exact_pool)
        # work_anchor: derived from a template substitution on the scraped title
        assert any(
            anchors.work_anchor == tpl.format(title="深夜动漫推荐")
            for tpl in cfg.work_anchor_templates
        )

    def test_deterministic_for_same_seed_and_recents(self):
        cfg = _cfg()
        a1 = select_anchors(cfg, _meta(), seed=42, recent_texts=[])
        a2 = select_anchors(cfg, _meta(), seed=42, recent_texts=[])
        assert a1 == a2

    def test_partial_seventy_exact_thirty_distribution(self):
        # Smoke test: across 1000 different seeds, ~70% land in partial pool,
        # ~30% in exact. Tolerate ±10pp for the deterministic bucketing.
        cfg = _cfg(partial=["P"], exact=["E"])
        partial_count = sum(
            1 for s in range(1000)
            if select_anchors(cfg, _meta(), seed=s, recent_texts=[]).list_anchor == "P"
        )
        ratio = partial_count / 1000
        assert 0.6 <= ratio <= 0.8, f"partial ratio {ratio:.3f} outside [0.6, 0.8]"


class TestSelectAnchorsWorkFallback:
    def test_empty_title_falls_back_to_branded(self, caplog):
        cfg = _cfg()
        meta = WorkMetadata(title=None, description=None, h1=None)
        with caplog.at_level("WARNING"):
            anchors = select_anchors(cfg, meta, seed=0, recent_texts=[])
        assert anchors.work_anchor == cfg.branded_pool[0]
        assert any("fallback" in r.message.lower() for r in caplog.records)

    def test_none_meta_falls_back_to_branded(self):
        cfg = _cfg()
        anchors = select_anchors(cfg, None, seed=0, recent_texts=[])
        assert anchors.work_anchor == cfg.branded_pool[0]

    def test_all_templates_filtered_out_falls_back_to_branded(self, caplog):
        # Title with bidi override that fails the work filter
        cfg = _cfg()
        meta = WorkMetadata(title="‮title", description=None, h1=None)
        with caplog.at_level("WARNING"):
            anchors = select_anchors(cfg, meta, seed=0, recent_texts=[])
        assert anchors.work_anchor == cfg.branded_pool[0]

    def test_all_candidates_in_recent_falls_back_to_branded(self, caplog):
        cfg = _cfg()
        meta = _meta()
        # Pre-populate recent with every templated rendering of the title
        recent = [tpl.format(title=meta.title) for tpl in cfg.work_anchor_templates]
        with caplog.at_level("WARNING"):
            anchors = select_anchors(cfg, meta, seed=0, recent_texts=recent)
        assert anchors.work_anchor == cfg.branded_pool[0]

    def test_first_template_in_recent_skips_to_next(self):
        cfg = _cfg(templates=["{title}", "{title} 详情"])
        meta = _meta()
        # First template's output is "depleted" by recent_texts → second wins
        anchors = select_anchors(
            cfg, meta, seed=0, recent_texts=[meta.title]
        )
        assert anchors.work_anchor == f"{meta.title} 详情"

    def test_empty_branded_pool_raises(self):
        cfg = _cfg(branded=[])
        with pytest.raises(InputValidationError):
            select_anchors(cfg, _meta(), seed=0, recent_texts=[])


# ═════════════════════════════════════════════════════════════════════════════
# _passes_work_anchor_filter — character blacklist
# ═════════════════════════════════════════════════════════════════════════════


class TestWorkAnchorFilter:
    def test_pure_ascii_title_passes(self):
        # Critical: legacy _passes_filters rejects (no CJK); work filter accepts
        assert _passes_work_anchor_filter("Hot Anime Recommendation") is True

    def test_cjk_title_passes(self):
        assert _passes_work_anchor_filter("深夜动漫推荐") is True

    def test_length_below_two_rejected(self):
        assert _passes_work_anchor_filter("X") is False
        assert _passes_work_anchor_filter("") is False

    def test_length_above_thirty_rejected(self):
        assert _passes_work_anchor_filter("x" * 31) is False
        assert _passes_work_anchor_filter("x" * 30) is True

    def test_forbidden_anchor_text_rejected(self):
        assert _passes_work_anchor_filter("点击这里") is False

    def test_c0_control_chars_rejected(self):
        assert _passes_work_anchor_filter("\x00title") is False
        assert _passes_work_anchor_filter("ti\x1ftle") is False

    def test_c1_control_chars_rejected(self):
        assert _passes_work_anchor_filter("ti\x80tle") is False
        assert _passes_work_anchor_filter("ti\x9ftle") is False

    def test_zero_width_chars_rejected(self):
        assert _passes_work_anchor_filter("ti​tle") is False  # zwsp
        assert _passes_work_anchor_filter("ti﻿tle") is False  # bom

    def test_bidi_overrides_rejected(self):
        assert _passes_work_anchor_filter("ti‮tle") is False  # RLO
        assert _passes_work_anchor_filter("ti⁦tle") is False  # LRI

    def test_fullwidth_ascii_punctuation_rejected(self):
        assert _passes_work_anchor_filter("ti＜tle") is False  # ＜
        assert _passes_work_anchor_filter("ti＞tle") is False  # ＞
        assert _passes_work_anchor_filter("ti＆tle") is False  # ＆
        assert _passes_work_anchor_filter("ti＂tle") is False  # ＂
        assert _passes_work_anchor_filter("ti＇tle") is False  # ＇

    def test_ascii_structural_chars_rejected(self):
        for ch in ("<", ">", '"', "'", "`", "[", "]", "(", ")", "\\", "\n", "\r"):
            assert _passes_work_anchor_filter(f"ti{ch}tle") is False, ch

    def test_non_string_rejected(self):
        assert _passes_work_anchor_filter(None) is False  # type: ignore[arg-type]
        assert _passes_work_anchor_filter(42) is False  # type: ignore[arg-type]


# ═════════════════════════════════════════════════════════════════════════════
# render_work_themed_article
# ═════════════════════════════════════════════════════════════════════════════


class TestRenderHappyPath:
    def test_output_contains_three_anchors_with_correct_attrs(self):
        cfg = _cfg()
        anchors = select_anchors(cfg, _meta(), seed=1, recent_texts=[])
        result = render_work_themed_article(
            cfg, "https://site.com/work/123", anchors, seed=1
        )

        html = result["content_markdown"]
        # Three <a> tags
        assert html.count("<a ") == 3
        # All have target="_blank" rel="noopener" (NOT noreferrer; NOT nofollow)
        assert html.count('target="_blank"') == 3
        assert html.count('rel="noopener"') == 3
        assert "nofollow" not in html
        # All three URLs present
        assert 'href="https://site.com/"' in html
        assert 'href="https://site.com/list"' in html
        assert 'href="https://site.com/work/123"' in html

    def test_output_payload_shape(self):
        cfg = _cfg()
        anchors = select_anchors(cfg, _meta(), seed=1, recent_texts=[])
        result = render_work_themed_article(
            cfg, "https://site.com/work/123", anchors, seed=1
        )
        assert "content_markdown" in result
        assert "url" in result and result["url"] == "https://site.com/work/123"
        assert "main_domain" in result and result["main_domain"] == "https://site.com/"
        assert "links" in result and len(result["links"]) == 3
        kinds = {link["kind"] for link in result["links"]}
        assert kinds == {"main_domain", "list", "work"}

    def test_deterministic_for_same_seed(self):
        cfg = _cfg()
        anchors = select_anchors(cfg, _meta(), seed=7, recent_texts=[])
        a = render_work_themed_article(cfg, "https://site.com/work/1", anchors, seed=7)
        b = render_work_themed_article(cfg, "https://site.com/work/1", anchors, seed=7)
        assert a["content_markdown"] == b["content_markdown"]

    def test_renders_through_markdown_it_without_breaking_raw_html(self):
        cfg = _cfg()
        anchors = select_anchors(cfg, _meta(), seed=2, recent_texts=[])
        result = render_work_themed_article(
            cfg, "https://site.com/work/abc", anchors, seed=2
        )
        rendered = render_to_html(result["content_markdown"])
        # markdown-it must preserve all three raw <a> tags exactly
        assert rendered.count("<a ") == 3
        assert rendered.count('target="_blank"') == 3
        assert rendered.count('rel="noopener"') == 3
        assert "nofollow" not in rendered


class TestRenderPositionPermutation:
    def test_all_six_perms_reachable_across_100_seeds(self):
        cfg = _cfg()
        seen_perms: set[tuple[str, str, str]] = set()
        for seed in range(100):
            anchors = select_anchors(cfg, _meta(), seed=seed, recent_texts=[])
            result = render_work_themed_article(
                cfg, "https://site.com/work/x", anchors, seed=seed
            )
            html = result["content_markdown"]
            # Sequence of anchor_kind in the rendered output
            perm_url_order: list[str] = []
            import re as _re
            for href in _re.findall(r'href="([^"]+)"', html):
                if href == "https://site.com/":
                    perm_url_order.append("main")
                elif href == "https://site.com/list":
                    perm_url_order.append("list")
                elif href == "https://site.com/work/x":
                    perm_url_order.append("work")
            assert len(perm_url_order) == 3
            seen_perms.add(tuple(perm_url_order))
        assert len(seen_perms) == 6, (
            f"only {len(seen_perms)} of 6 permutations seen across 100 seeds: {seen_perms}"
        )


class TestRenderXSSAndUnicode:
    def test_xss_via_scraped_title_is_escaped(self):
        cfg = _cfg(branded=["safe-brand"])
        # Scraped title is full of XSS payloads. _passes_work_anchor_filter
        # rejects all template renderings (because of `<` `>` `"` etc) and
        # falls back to branded[0] — so the rendered HTML never contains the
        # raw payload.
        meta = WorkMetadata(
            title='<script>alert(1)</script>',
            description=None,
            h1=None,
        )
        anchors = select_anchors(cfg, meta, seed=0, recent_texts=[])
        result = render_work_themed_article(
            cfg, "https://site.com/work/y", anchors, seed=0
        )
        html = result["content_markdown"]
        assert "<script>" not in html
        assert "</script>" not in html
        # Sanity: branded fallback was used
        assert "safe-brand" in html

    def test_special_html_chars_in_safe_anchor_get_escaped(self):
        # If for any reason the anchor text reaches the renderer, it must be
        # HTML-escaped so `&` / `"` / `'` can't break attribute boundaries.
        # We simulate by passing a custom Anchors directly.
        cfg = _cfg(branded=["safe"])
        anchors = Anchors(
            main_anchor='A & B "test" \'1\'',
            list_anchor="list",
            work_anchor="work",
        )
        result = render_work_themed_article(
            cfg, "https://site.com/work/z", anchors, seed=0
        )
        html = result["content_markdown"]
        assert "&amp;" in html
        assert "&quot;" in html
        # html.escape(quote=True) emits &#x27; for apostrophe
        assert "&#x27;" in html or "&#39;" in html
        # Raw unescaped versions must NOT appear inside the anchor body
        # (they may appear inside href for & due to URL escape, hence we check
        # the angle brackets stayed safe overall)
        assert "<script" not in html


# ═════════════════════════════════════════════════════════════════════════════
# _format_anchor_html rel parameterisation — smoke (full coverage in test_markdown_render)
# ═════════════════════════════════════════════════════════════════════════════


class TestFormatAnchorHtmlRel:
    def test_default_rel_unchanged(self):
        out = _format_anchor_html("https://example.com", "anchor")
        assert 'rel="noopener noreferrer"' in out

    def test_explicit_rel_noopener_only(self):
        out = _format_anchor_html("https://example.com", "anchor", rel="noopener")
        assert 'rel="noopener"' in out
        assert "noreferrer" not in out
