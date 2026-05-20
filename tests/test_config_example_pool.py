"""Regression test: config.example.toml anchor pool stays LLM-free-viable.

The example block is sized to operate without LLM fallback. If someone tweaks
the candidate lists in a way that would push the scheduler into degrade under
realistic load, this test catches it. Two checks:

1. Every candidate passes the resolver's _passes_filters — same gate the
   resolver applies before returning an anchor to the renderer.
2. A 500-article × 3-seed runtime simulation with provider=None produces
   ZERO degrades. This is the load-bearing assertion — it proves the pool
   sizes are large enough that the scheduler never exhausts a cell against
   the 20-entry text-dedup window.

The expected pool lives in this file directly (not parsed from the .toml)
because:
- pytest doesn't need to parse the user's runtime config, and
- duplicating the data here means a candidate change in the example must
  also be reflected here, making the regression intent explicit.
"""

from __future__ import annotations

import random

import pytest

from backlink_publisher.anchor.profile import (
    ProfileEntry,
    ProfileState,
    now_iso,
    recent_texts,
    recent_type_counts,
)
from backlink_publisher.anchor.resolver import (
    FORBIDDEN_ANCHOR_TEXTS,
    _passes_filters,
    resolve_anchor,
)
from backlink_publisher.anchor.scheduler import schedule
from backlink_publisher.config import Config


EXAMPLE_POOLS = {
    "home": {
        "branded": [
            "51漫画", "51漫画首页", "51漫画平台", "51漫画推荐", "51漫画站",
            "51漫画网", "51漫画家", "51漫画馆", "51漫画社", "51漫画大全",
            "51同人漫画", "51成人漫画", "51漫画精选", "51漫画聚集", "51漫画基地",
        ],
        "exact": ["成人漫画", "漫画", "同人漫画", "本子漫画", "情色漫画"],
        "partial": [
            "成人漫画平台", "在线漫画", "漫画阅读", "同人漫画推荐", "漫画站",
            "成人漫画站", "成人漫画网", "漫画大全", "在线漫画站",
        ],
        "lsi": [
            "二次元资源", "同人作品", "同人推荐", "动漫资源", "ACG动漫平台",
            "同人作品集", "二次元站", "同好分享",
        ],
    },
    "hot": {
        "branded": ["51漫画热门", "51漫画排行", "51漫画榜单", "51热门作品", "51人气榜", "51漫画风云榜"],
        "exact": ["热门漫画", "漫画排行", "本周热门", "热门作品", "本周漫画"],
        "partial": ["热门漫画排行", "热门漫画推荐", "热门成人漫画", "本周热门漫画", "最新热门漫画", "成人漫画排行"],
        "lsi": ["人气榜单", "人气推荐", "人气作品", "周榜推荐", "人气精选", "周榜单"],
    },
    "animate": {
        "branded": ["51动漫", "51动漫推荐", "51动漫站", "51动漫网", "51动漫平台", "51动漫大全"],
        "exact": ["动漫", "成人动漫", "在线动漫", "动漫资源"],
        "partial": ["成人动漫推荐", "动漫推荐", "在线动漫资源", "动漫资源站", "在线动漫", "成人动漫站"],
        "lsi": ["二次元资源", "番剧推荐", "二次元站", "ACG动画推荐", "在线动画", "番剧资源"],
    },
    "category": {
        "branded": ["51漫画分类", "51动漫分类", "51分类导航", "51内容分类", "51作品分类", "51同人分类"],
        "exact": ["漫画分类", "动漫分类", "内容分类", "作品分类"],
        "partial": ["漫画分类导航", "漫画分类整理", "动漫分类列表", "漫画分类列表", "漫画作品分类", "成人漫画分类"],
        "lsi": ["内容分类导航", "资源分类", "作品分类", "同人分类", "二次元分类", "同好分类"],
    },
    "topic": {
        "branded": ["51漫画专题", "51漫画推荐", "51专题文章", "51专题精选", "51同人专题", "51漫画指南"],
        "exact": ["漫画专题", "专题文章", "漫画推荐", "专题精选"],
        "partial": ["漫画专题文章", "漫画推荐文章", "同人漫画精选", "漫画阅读指南", "漫画专题精选", "成人漫画专题"],
        "lsi": ["同人专题", "阅读指南", "资源精选", "二次元专题", "同好分享", "同好推荐"],
    },
}


EXAMPLE_URL_CATEGORIES = {
    "home": "https://51acgs.com/",
    "hot": "https://51acgs.com/comic/hot",
    "animate": "https://51acgs.com/animate",
    "category": "https://51acgs.com/category",
    "topic": "https://51acgs.com/topic/blog",
}


def _all_anchors():
    """Iterate every (cell, anchor_text) pair for individual-test verification."""
    for url_cat, type_pools in EXAMPLE_POOLS.items():
        for anchor_type, candidates in type_pools.items():
            for anchor in candidates:
                yield (url_cat, anchor_type, anchor)


# ── Structural checks ──────────────────────────────────────────────────────


def test_all_20_cells_present():
    assert len(EXAMPLE_POOLS) == 5
    for url_cat, types in EXAMPLE_POOLS.items():
        assert set(types.keys()) == {"branded", "partial", "exact", "lsi"}, (
            f"{url_cat} missing or extra types"
        )


def test_minimum_three_candidates_per_cell():
    for url_cat, type_pools in EXAMPLE_POOLS.items():
        for anchor_type, candidates in type_pools.items():
            assert len(candidates) >= 3, (
                f"{url_cat}/{anchor_type} has only {len(candidates)} candidates "
                "(plan v2 R23 requires ≥3)"
            )


def test_home_branded_oversized_for_dedup():
    """home/branded is the heavy-use cell — must be padded enough to survive
    the 20-entry text-dedup window."""
    assert len(EXAMPLE_POOLS["home"]["branded"]) >= 12, (
        "home/branded sees ~5-6 of 20 sliding-window entries; "
        "thinner than 12 risks degrade-to-fallback churn"
    )


def test_no_duplicates_within_cell():
    for url_cat, type_pools in EXAMPLE_POOLS.items():
        for anchor_type, candidates in type_pools.items():
            assert len(set(candidates)) == len(candidates), (
                f"{url_cat}/{anchor_type} has duplicate entries"
            )


# ── Per-anchor filter checks ───────────────────────────────────────────────


@pytest.mark.parametrize("url_cat,anchor_type,anchor", list(_all_anchors()))
def test_every_anchor_passes_filters(url_cat, anchor_type, anchor):
    """If this test ever fires it means a candidate would be rejected at
    runtime — fix the candidate before merging."""
    assert _passes_filters(anchor), (
        f"{url_cat}/{anchor_type}: {anchor!r} fails _passes_filters "
        "(length 2-8, no forbidden words, no unsafe chars, ≥50% CJK)"
    )


@pytest.mark.parametrize("url_cat,anchor_type,anchor", list(_all_anchors()))
def test_no_forbidden_phrases(url_cat, anchor_type, anchor):
    assert anchor not in FORBIDDEN_ANCHOR_TEXTS, (
        f"{url_cat}/{anchor_type}: {anchor!r} is in FORBIDDEN_ANCHOR_TEXTS"
    )


# ── Runtime simulation — the load-bearing test ─────────────────────────────


def _make_config() -> Config:
    return Config(
        site_url_categories={"https://51acgs.com": dict(EXAMPLE_URL_CATEGORIES)},
        target_anchor_pools_v2={"https://51acgs.com": EXAMPLE_POOLS},
    )


def _simulate(n_articles: int, seed: int) -> tuple[int, dict[str, float]]:
    """Run ``n_articles`` end-to-end without an LLM provider.

    Returns ``(degrade_count, final_deviation_pp_by_type)``. A degrade happens
    when ``resolve_anchor`` returns ``None`` for any slot in an article.
    """
    config = _make_config()
    cats = list(EXAMPLE_URL_CATEGORIES.keys())
    rng = random.Random(seed)
    entries: list[ProfileEntry] = []
    degrades = 0

    for _ in range(n_articles):
        profile = ProfileState(main_domain="https://51acgs.com", entries=entries[-100:])
        recent = recent_texts(profile, n=20)
        decision = schedule(profile, config.anchor_proportions, cats)

        main_anchor = resolve_anchor(
            url_category="home",
            anchor_type=decision.main_link_anchor_type,
            keyword="漫画",
            target_url=EXAMPLE_URL_CATEGORIES["home"],
            url_subject="漫画",
            config=config,
            main_domain="https://51acgs.com",
            recent_texts=recent,
            provider=None,
            rng=rng,
        )
        if main_anchor is None:
            degrades += 1
            continue

        running = list(recent) + [main_anchor]
        sec_records: list[tuple[str, str, str]] = []
        ok = True
        for sec in decision.secondary_links:
            sa = resolve_anchor(
                url_category=sec.url_category,
                anchor_type=sec.anchor_type,
                keyword="漫画",
                target_url=EXAMPLE_URL_CATEGORIES[sec.url_category],
                url_subject="漫画",
                config=config,
                main_domain="https://51acgs.com",
                recent_texts=running,
                provider=None,
                rng=rng,
            )
            if sa is None:
                ok = False
                break
            sec_records.append((sec.url_category, sec.anchor_type, sa))
            running.append(sa)

        if not ok:
            degrades += 1
            continue

        ts = now_iso()
        entries.append(ProfileEntry(
            ts=ts, link_role="main", url_category="home",
            anchor_type=decision.main_link_anchor_type,
            anchor_text=main_anchor, degraded=False,
        ))
        for cat, ty, text in sec_records:
            entries.append(ProfileEntry(
                ts=ts, link_role="secondary", url_category=cat,
                anchor_type=ty, anchor_text=text, degraded=False,
            ))

    final = ProfileState(main_domain="https://51acgs.com", entries=entries[-100:])
    counts = recent_type_counts(final)
    total = sum(counts.values()) or 1
    deviations = {
        t: counts[t] / total * 100 - config.anchor_proportions[t] * 100
        for t in ("branded", "partial", "exact", "lsi")
    }
    return degrades, deviations


@pytest.mark.parametrize("seed", [0, 42, 99])
def test_zero_degrades_over_500_articles(seed):
    """The load-bearing assertion: with provider=None and the example pool,
    500 articles produce zero degrades. If this test fires, either a cell
    shrank too much or the dedup math drifted — investigate before merging."""
    degrades, _ = _simulate(n_articles=500, seed=seed)
    assert degrades == 0, f"seed={seed}: {degrades} degrades in 500 articles"


@pytest.mark.parametrize("seed", [0, 42, 99])
def test_distribution_within_5pp_of_target(seed):
    """Sanity-check the scheduler converges with this pool — distribution
    should land near Safe SEO within 5pp on the rolling 100-entry window."""
    _, deviations = _simulate(n_articles=500, seed=seed)
    for t, dev in deviations.items():
        assert abs(dev) <= 5.0, f"seed={seed}: {t} deviation {dev:.2f}pp exceeds 5pp"
