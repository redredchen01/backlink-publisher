"""Shared corpus generators for the Footprint Regression Gate (Plan Unit 2 + 3).

Pure-function fixtures for the 3 renderer paths the gate exercises:

- ``work_themed``  — ``content.themed_gen.select_anchors`` + ``render_work_themed_article``
- ``zh_short``     — ``_util.markdown.render_zh_short_article``
- ``markdown_it``  — ``_util.markdown.render_to_html`` (singleton path used by ALL adapters)

Both the regen CLI (``footprint baseline regenerate``) and the gate test
(``tests/test_footprint_regression.py``) import from here, so they exercise
exactly the same fixture content. Implementer choice per Plan Unit 2.

Determinism contract: given a fixed ``PYTHONHASHSEED=0``, every call to
``make_corpus(name)`` produces byte-identical HTML payloads. The
``fixture_set_id`` (sha256 over canonical inputs, first 16 hex chars) is
recorded in baselines so a future fixture refresh is loud (mismatch
raises ``FootprintGateSchemaMismatch`` in the gate).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from backlink_publisher._util.markdown import (
    render_to_html,
    render_zh_short_article,
)
from backlink_publisher.config.types import ThreeUrlConfig
from backlink_publisher.content.scraper import WorkMetadata
from backlink_publisher.content.themed_gen import (
    render_work_themed_article,
    select_anchors,
)

__all__ = [
    "CORPUS_NAMES",
    "canonical_fixture_inputs",
    "compute_fixture_set_id",
    "make_corpus",
]


CORPUS_NAMES: tuple[str, ...] = ("work_themed", "zh_short", "markdown_it")


# ---------------------------------------------------------------------------
# Fixture pools — chosen for byte-level diversity in <a> tags while keeping
# totals small (CI runtime budget ~sub-second per corpus).
# ---------------------------------------------------------------------------


_WORK_THEMED_CFG_KWARGS: dict[str, Any] = {
    "main_url": "https://example-brand.com/",
    "list_url": "https://example-brand.com/list",
    "branded_pool": ["品牌站", "品牌首页", "Brand Home"],
    "partial_pool": ["品牌相关内容", "brand related"],
    "exact_pool": ["品牌关键词", "brand keyword"],
    "work_urls": [],
    "work_anchor_templates": ["看《{title}》", "推荐《{title}》", "《{title}》介绍"],
}

_WORK_THEMED_TITLES: tuple[str, ...] = (
    "深夜动漫推荐",
    "工业设计入门",
    "Tokyo Travel Notes",
    "厨房改造日志",
    "Long Distance Cycling",
)

_WORK_THEMED_URLS: tuple[str, ...] = (
    "https://example-brand.com/works/anime",
    "https://example-brand.com/works/industrial",
    "https://example-brand.com/works/tokyo",
    "https://example-brand.com/works/kitchen",
    "https://example-brand.com/works/cycling",
)

_WORK_THEMED_SEEDS: tuple[int, ...] = (0, 1, 2, 3, 4)


_ZH_SHORT_INPUTS: tuple[dict[str, Any], ...] = (
    {
        "keyword": "动漫资源",
        "main_domain": "https://anime-site.com/",
        "main_anchor": "动漫站",
        "secondary_links": [("https://anime-list.com/", "动漫列表")],
        "style_seed": 0,
    },
    {
        "keyword": "工业设计",
        "main_domain": "https://design-site.com/",
        "main_anchor": "设计站",
        "secondary_links": [
            ("https://design-list.com/", "设计列表"),
            ("https://design-blog.com/", "设计博客"),
        ],
        "style_seed": 1,
    },
    {
        "keyword": "travel notes",
        "main_domain": "https://travel-site.com/",
        "main_anchor": "Travel",
        "secondary_links": [("https://travel-blog.com/", "Travel Blog")],
        "style_seed": 2,
    },
    {
        "keyword": "厨房技巧",
        "main_domain": "https://kitchen-site.com/",
        "main_anchor": "厨房站",
        "secondary_links": [
            ("https://kitchen-tools.com/", "厨房工具"),
            ("https://kitchen-recipes.com/", "厨房食谱"),
        ],
        "style_seed": 3,
    },
    {
        "keyword": "cycling logs",
        "main_domain": "https://cycling-site.com/",
        "main_anchor": "Cycling",
        "secondary_links": [("https://cycling-list.com/", "Cycling List")],
        "style_seed": 4,
    },
)


_MARKDOWN_IT_INPUTS: tuple[str, ...] = (
    "Read more at [the brand site](https://example-brand.com/). "
    "Or browse [the list](https://example-brand.com/list).",

    "See [design blog](https://design-blog.com/) for ideas. "
    "Tools at [design tools](https://design-tools.com/).",

    "Visit [travel notes](https://travel-blog.com/) — and [tokyo guide](https://travel-site.com/tokyo).",

    "Recipes: [kitchen recipes](https://kitchen-recipes.com/). "
    "Tools: [kitchen tools](https://kitchen-tools.com/). "
    "Blog: [kitchen blog](https://kitchen-site.com/blog).",

    "Cycling logs: [long distance](https://cycling-site.com/long). "
    "List: [routes](https://cycling-list.com/).",
)


# ---------------------------------------------------------------------------
# Corpus generators
# ---------------------------------------------------------------------------


def _reset_mdit_singleton() -> None:
    """Reset ``_util.markdown._mdit_instance`` so the markdown_it corpus
    always starts from a cold cache (Gate R2 contract).
    Bare assignment intentional — no monkeypatch teardown."""
    from backlink_publisher._util import markdown as _md

    _md._mdit_instance = None


def _make_work_themed_corpus() -> list[str]:
    cfg = ThreeUrlConfig(**_WORK_THEMED_CFG_KWARGS)
    payloads: list[str] = []
    for i, (title, url, seed) in enumerate(
        zip(_WORK_THEMED_TITLES, _WORK_THEMED_URLS, _WORK_THEMED_SEEDS)
    ):
        meta = WorkMetadata(title=title, description=f"desc {i}", h1=title)
        anchors = select_anchors(cfg, meta, seed=seed, recent_texts=[])
        payload = render_work_themed_article(cfg, url, anchors, seed=seed)
        payloads.append(payload["content_markdown"])
    return payloads


def _make_zh_short_corpus() -> list[str]:
    return [render_zh_short_article(**inputs) for inputs in _ZH_SHORT_INPUTS]


def _make_markdown_it_corpus() -> list[str]:
    _reset_mdit_singleton()  # Gate-R2 contract; bare assignment, no teardown.
    return [render_to_html(md) for md in _MARKDOWN_IT_INPUTS]


_GENERATORS = {
    "work_themed": _make_work_themed_corpus,
    "zh_short": _make_zh_short_corpus,
    "markdown_it": _make_markdown_it_corpus,
}


def make_corpus(corpus_name: str) -> list[str]:
    """Return the rendered HTML payload list for ``corpus_name``.

    Raises ``KeyError`` if ``corpus_name`` is not in ``CORPUS_NAMES``.
    """
    if corpus_name not in _GENERATORS:
        raise KeyError(
            f"unknown corpus_name {corpus_name!r}; expected one of {CORPUS_NAMES}"
        )
    return _GENERATORS[corpus_name]()


# ---------------------------------------------------------------------------
# fixture_set_id: sha256 over canonical fixture inputs (first 16 hex chars)
# ---------------------------------------------------------------------------


def canonical_fixture_inputs(corpus_name: str) -> dict[str, Any]:
    """Return the JSON-serializable canonical input record for ``corpus_name``.

    Used both for ``fixture_set_id`` computation and for human inspection
    during baseline review.
    """
    if corpus_name == "work_themed":
        cfg = ThreeUrlConfig(**_WORK_THEMED_CFG_KWARGS)
        return {
            "cfg_dict": dataclasses.asdict(cfg),
            "items": [
                {
                    "title": t,
                    "url": u,
                    "seed": s,
                    "recent_texts": [],
                }
                for t, u, s in zip(
                    _WORK_THEMED_TITLES, _WORK_THEMED_URLS, _WORK_THEMED_SEEDS
                )
            ],
        }
    if corpus_name == "zh_short":
        return {
            "items": [
                {
                    "keyword": inp["keyword"],
                    "main_domain": inp["main_domain"],
                    "main_anchor": inp["main_anchor"],
                    "secondary_links": [list(t) for t in inp["secondary_links"]],
                    "style_seed": inp["style_seed"],
                }
                for inp in _ZH_SHORT_INPUTS
            ],
        }
    if corpus_name == "markdown_it":
        return {"md_inputs": list(_MARKDOWN_IT_INPUTS)}
    raise KeyError(
        f"unknown corpus_name {corpus_name!r}; expected one of {CORPUS_NAMES}"
    )


def compute_fixture_set_id(corpus_name: str) -> str:
    """Stable 16-hex sha256 prefix over canonical inputs for ``corpus_name``."""
    payload = json.dumps(
        canonical_fixture_inputs(corpus_name),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
