"""Work-themed article generator — Plan 2026-05-13-004 Unit 4.

Pure functions that turn a (target_cfg, scraped_meta, work_url, seed) tuple
into a rendered three-paragraph HTML body with three ``rel="noopener"``
backlinks (main_url, list_url, work_url). Anchor selection is deterministic
on the seed; link position is permuted across six possible orderings to avoid
a fixed "main first / work last" fingerprint.

Public:
    Anchors                    — frozen dataclass holding the three picks
    select_anchors(...)        — pick (main, list, work) anchor texts
    render_work_themed_article — produce a payload dict matching the
                                 short-form planner contract

Failure semantics:
- Empty ``branded_pool`` → :class:`InputValidationError` (config is broken,
  the dispatcher should surface this; the row cannot be processed).
- Empty / unfilterable scraped title, or every templated rendering already in
  ``recent_texts`` → fall back to ``branded_pool[0]`` and emit a WARN log.
- Anchor text always passes through ``html.escape(quote=True)`` before reaching
  ``_format_anchor_html``: defence-in-depth against scraped titles, even though
  ``_passes_work_anchor_filter`` already blocks structural HTML chars upstream.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from .anchor_resolver import _passes_work_anchor_filter
from .config import ThreeUrlConfig
from .errors import InputValidationError
from .markdown_utils import _format_anchor_html
from .work_scraper import WorkMetadata

__all__ = ["Anchors", "select_anchors", "render_work_themed_article"]

_log = logging.getLogger(__name__)

# Position permutations — each tuple maps paragraph index (0,1,2) to the
# anchor slot index (0=main, 1=list, 2=work) that gets rendered there.
# Six permutations give every possible ordering, so fingerprint detection
# can't lock onto "main first / work last".
_POSITION_PERMS: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2), (0, 2, 1),
    (1, 0, 2), (1, 2, 0),
    (2, 0, 1), (2, 1, 0),
)

# Three-paragraph templates: each tuple is (para1, para2, para3) with a single
# `{a}` placeholder per paragraph. Plain-text length per paragraph aims for
# 35–45 chars so the total + three anchor texts lands in the 150–200 range
# (in line with the zh-CN short-form contract). Templates intentionally vary
# their opening framing (sharing / forum / personal experience / discovery)
# so a batch of 50+ articles to one site doesn't look programmatic.
_WORK_TEMPLATES: tuple[tuple[str, str, str], ...] = (
    (
        "今天分享一个最近常用的站点 {a}，资源更新算稳定，分类整理也算用心。",
        "日常我会顺手刷一下 {a}，能挖到一些之前没注意过的精选作品。",
        "其中 {a} 这一篇值得花时间看看，整体阅读体验也算顺畅。",
    ),
    (
        "最近圈子里讨论比较多的是 {a}，整体使用感受比想象中要好不少。",
        "想找类似内容的朋友可以试试 {a}，更新节奏不算慢，分类导航也清楚。",
        "另外 {a} 也值得收藏一下慢慢翻看，里头能挖到不少有意思的内容。",
    ),
    (
        "在论坛上看到有人推荐 {a}，自己跟着看了一阵子下来感觉不错。",
        "{a} 是日常会扫一眼的页面，更新频率比较稳定，整理也算细致。",
        "顺手再看了一下 {a}，体验比一般聚合站要好不少，挺值得收藏。",
    ),
    (
        "用 {a} 看相关内容已经有一段时间了，整体感受比预期要好。",
        "想顺手翻一翻最近更新的朋友可以看看 {a}，分类入口都很顺手好找。",
        "其中 {a} 是值得花时间慢慢看的一篇，能发现一些之前忽略的细节。",
    ),
    (
        "分享一个最近经常在用的站点 {a}，作品比较全更新也算勤快。",
        "{a} 那一块的更新值得花点时间慢慢翻，能挖到一些冷门优质作品。",
        "另外 {a} 这一篇也挺有意思，整体阅读起来体验顺畅好读。",
    ),
    (
        "想找一些值得长期跟进的资源，{a} 是个不错的入口选择。",
        "日常顺手刷 {a}，能跟到一些最近的新进展和热门更新。",
        "其中 {a} 那一篇推荐给同好朋友，整理用心读下来挺有收获。",
    ),
)


@dataclass(frozen=True)
class Anchors:
    """The three anchor texts chosen for one work-themed article."""

    main_anchor: str
    list_anchor: str
    work_anchor: str


# ── select_anchors ───────────────────────────────────────────────────────────


def select_anchors(
    cfg: ThreeUrlConfig,
    meta: WorkMetadata | None,
    *,
    seed: int,
    recent_texts: list[str],
) -> Anchors:
    """Pick three anchor texts deterministically from ``cfg`` + ``meta``.

    Selection rules:
    - **main**  → ``cfg.branded_pool[seed % N]`` (deterministic).
    - **list**  → 70% from ``partial_pool``, 30% from ``exact_pool`` (the split
      uses ``(seed * 7 + 3) % 10`` so consecutive seeds spread evenly). Pool
      index is ``(seed // len(perms)) % len(pool)`` to decorrelate from main.
    - **work**  → first template substitution on the scraped title that (a)
      passes :func:`_passes_work_anchor_filter` and (b) is not already in
      ``recent_texts``. If no candidate qualifies — empty title, all filtered,
      or all-recent — fall back to ``branded_pool[0]`` with a WARN log.

    Raises :class:`InputValidationError` if ``branded_pool`` is empty (no
    fallback possible — target config is unusable).
    """
    if not cfg.branded_pool:
        raise InputValidationError(
            "ThreeUrlConfig.branded_pool is empty — cannot generate any anchor"
        )

    main_anchor = cfg.branded_pool[seed % len(cfg.branded_pool)]
    list_anchor = _pick_list_anchor(cfg, seed)
    work_anchor = _pick_work_anchor(cfg, meta, recent_texts)

    return Anchors(
        main_anchor=main_anchor,
        list_anchor=list_anchor,
        work_anchor=work_anchor,
    )


def _pick_list_anchor(cfg: ThreeUrlConfig, seed: int) -> str:
    """70% partial / 30% exact split, deterministic on ``seed``."""
    use_partial = (seed * 7 + 3) % 10 < 7
    if use_partial and cfg.partial_pool:
        pool = cfg.partial_pool
    elif (not use_partial) and cfg.exact_pool:
        pool = cfg.exact_pool
    else:
        pool = cfg.partial_pool or cfg.exact_pool or cfg.branded_pool
    # `// 6` decorrelates from the position-permutation seed bucket
    return pool[(seed // 6) % len(pool)]


def _pick_work_anchor(
    cfg: ThreeUrlConfig,
    meta: WorkMetadata | None,
    recent_texts: list[str],
) -> str:
    """Try each template against the scraped title; first survivor wins."""
    title = (meta.title if meta else None) or ""
    if title:
        recent_set = set(recent_texts)
        for template in cfg.work_anchor_templates:
            try:
                candidate = template.format(title=title).strip()
            except (KeyError, IndexError, ValueError):
                continue
            if not _passes_work_anchor_filter(candidate):
                continue
            if candidate in recent_set:
                continue
            return candidate
    _log.warning(
        "work-themed: no template anchor passed filters/dedup, fallback to "
        "branded_pool[0] (title=%r, templates=%d, recent=%d)",
        title, len(cfg.work_anchor_templates), len(recent_texts),
    )
    return cfg.branded_pool[0]


# ── render_work_themed_article ───────────────────────────────────────────────


def render_work_themed_article(
    cfg: ThreeUrlConfig,
    work_url: str,
    anchors: Anchors,
    *,
    seed: int,
) -> dict:
    """Render a 3-paragraph HTML body containing the three anchors.

    The position of each anchor across the three paragraphs is permuted by
    ``seed % 6``; the prose template family is chosen by ``seed // 6``.
    Anchor text is HTML-escaped via ``html.escape(quote=True)`` before being
    passed to :func:`_format_anchor_html`, defence-in-depth against any
    scraped content that slipped past the upstream filter.

    Returns a dict with the same shape Unit 5a's planner expects:
    ``content_markdown`` (HTML body — markdown-it is idempotent on raw HTML),
    ``url`` (the per-article work_url), ``main_domain``, ``title``, and
    ``links`` (three records keyed by ``kind``: ``main_domain`` / ``list`` /
    ``work``).
    """
    main_html = _format_anchor_html(
        cfg.main_url, _safe_anchor(anchors.main_anchor), rel="noopener"
    )
    list_html = _format_anchor_html(
        cfg.list_url, _safe_anchor(anchors.list_anchor), rel="noopener"
    )
    work_html = _format_anchor_html(
        work_url, _safe_anchor(anchors.work_anchor), rel="noopener"
    )

    perm = _POSITION_PERMS[seed % len(_POSITION_PERMS)]
    template = _WORK_TEMPLATES[
        (seed // len(_POSITION_PERMS)) % len(_WORK_TEMPLATES)
    ]
    slot_html = (main_html, list_html, work_html)
    paragraphs = [template[i].format(a=slot_html[perm[i]]) for i in range(3)]
    content = "\n\n".join(paragraphs)

    return {
        "content_markdown": content,
        "url": work_url,
        "main_domain": cfg.main_url,
        "title": anchors.work_anchor,
        "links": [
            {"url": cfg.main_url, "anchor": anchors.main_anchor, "kind": "main_domain"},
            {"url": cfg.list_url, "anchor": anchors.list_anchor, "kind": "list"},
            {"url": work_url, "anchor": anchors.work_anchor, "kind": "work"},
        ],
    }


def _safe_anchor(text: str) -> str:
    """HTML-escape anchor text so ``&`` ``<`` ``>`` ``"`` ``'`` can't break
    out of the surrounding ``<a>`` element. ``html.escape(quote=True)``
    matches the convention the rest of this codebase uses.
    """
    return html.escape(text, quote=True)
