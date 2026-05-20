"""zh-CN short-form scheduler for the backlink pipeline."""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from backlink_publisher.anchor.profile import ProfileEntry
from backlink_publisher.anchor.scheduler import ScheduleDecision, SecondaryLink
from backlink_publisher.anchor import profile as anchor_profile
from backlink_publisher.anchor import resolver as anchor_resolver
from backlink_publisher.anchor import scheduler as anchor_scheduler
from backlink_publisher._util import markdown as markdown_utils
from backlink_publisher.config import Config, get_anchor_keywords, get_anchor_pool_v2
from backlink_publisher._util.errors import InputValidationError
from backlink_publisher._util.logger import plan_logger
from backlink_publisher.publishing.adapters.llm_anchor_provider import OpenAICompatibleProvider

from .core import (
    _domain_label_of,
    _SUPPORTING_POOL,
    _TARGET_PADDED_LINK_COUNT,
)


def _scheduler_enabled_for(config: Config, main_domain: str) -> bool:
    key = main_domain.rstrip("/")
    cats = config.site_url_categories.get(key, {})
    has_home = "home" in cats
    has_non_home = any(c != "home" for c in cats)
    has_pools = bool(config.target_anchor_pools_v2.get(key))
    return has_home and has_non_home and has_pools


def _extract_zh_keyword(row: dict[str, Any], main_domain: str) -> str:
    seeds = row.get("seed_keywords")
    if isinstance(seeds, list) and seeds and isinstance(seeds[0], str) and seeds[0]:
        return seeds[0]
    topic = row.get("topic", "")
    if isinstance(topic, str) and topic:
        return topic
    return _domain_label_of(main_domain)


def _build_profile_entries(
    decision: ScheduleDecision,
    main_anchor: str,
    main_target_url: str,
    sec_records: list[tuple[str, str, str, str]],
    *,
    degraded: bool,
) -> list[ProfileEntry]:
    ts = anchor_profile.now_iso()
    entries = [
        ProfileEntry(
            ts=ts,
            link_role="main",
            url_category="home",
            anchor_type=decision.main_link_anchor_type,
            anchor_text=main_anchor,
            degraded=degraded,
            target_url=main_target_url,
        )
    ]
    for url_cat, anchor_type, anchor_text, target_url in sec_records:
        entries.append(
            ProfileEntry(
                ts=ts,
                link_role="secondary",
                url_category=url_cat,
                anchor_type=anchor_type,
                anchor_text=anchor_text,
                degraded=degraded,
                target_url=target_url,
            )
        )
    return entries


def _build_zh_short_payload(
    row: dict[str, Any],
    html: str,
    main_domain: str,
    main_anchor: str,
    sec_pairs: list[tuple[str, str]],
) -> dict[str, Any]:
    target_url = row["target_url"].rstrip("/")
    platform = row["platform"]
    publish_mode = row.get("publish_mode", "draft")
    language = row["language"]
    url_mode = row.get("url_mode", "A")

    domain_label = _domain_label_of(main_domain)
    home_url = main_domain.rstrip("/") + "/"

    links: list[dict[str, Any]] = [
        {
            "url": home_url,
            "anchor": main_anchor,
            "kind": "main_domain",
            "required": True,
        },
    ]
    existing_urls: set[str] = {home_url}
    for sec_url, sec_anchor in sec_pairs:
        links.append({
            "url": sec_url,
            "anchor": sec_anchor,
            "kind": "supporting",
            "required": False,
        })
        existing_urls.add(sec_url)

    pad_count = _TARGET_PADDED_LINK_COUNT - len(links)
    added_supporting: list[dict[str, Any]] = []
    if pad_count > 0:
        for surl, sanchor in _SUPPORTING_POOL:
            if len(added_supporting) >= pad_count:
                break
            if surl in existing_urls:
                continue
            sup = {
                "url": surl,
                "anchor": sanchor,
                "kind": "supporting",
                "required": False,
            }
            added_supporting.append(sup)
            links.append(sup)
            existing_urls.add(surl)

    custom_title = row.get("custom_title", "")
    title = custom_title or f"{domain_label} 内容推荐"
    slug = markdown_utils.slugify(title) or hashlib.sha256(title.encode()).hexdigest()[:12]
    excerpt = re.sub(r"<[^>]+>", "", html)[:100]

    custom_tags = row.get("custom_tags", "")
    tags = ["backlink", domain_label]
    if custom_tags:
        tags.extend(t.strip() for t in custom_tags.split(",") if t.strip())

    seed_str = f"{target_url}:{main_domain}:zh-short:{platform}"
    article_id = hashlib.sha256(seed_str.encode()).hexdigest()[:16]

    return {
        "id": article_id,
        "platform": platform,
        "language": language,
        "source_language": language,
        "publish_mode": publish_mode,
        "target_url": target_url + "/",
        "main_domain": home_url,
        "url_mode": url_mode,
        "title": title,
        "slug": slug,
        "excerpt": excerpt,
        "tags": tags,
        "content_markdown": html,
        "links": links,
        "seo": {
            "title": title,
            "description": excerpt,
            "canonical_url": target_url,
        },
    }


def _plan_zh_short_row(
    row: dict[str, Any],
    config: Config,
    llm_provider: OpenAICompatibleProvider | None,
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    main_domain = row["main_domain"].rstrip("/")
    cats_map = config.site_url_categories.get(main_domain, {})
    available_cats = list(cats_map.keys())
    if "home" not in cats_map or not any(c != "home" for c in cats_map):
        return None

    rng = rng or random.Random()
    style_seed = abs(hash(row.get("target_url", main_domain))) % 10_000
    keyword = _extract_zh_keyword(row, main_domain)
    home_url = cats_map["home"]
    topic = row.get("topic")

    last_errors: list[str] = []

    for attempt in range(2):
        profile = anchor_profile.load_profile(main_domain)
        recent = anchor_profile.recent_texts(profile, n=20)
        try:
            decision = anchor_scheduler.schedule(
                profile, config.anchor_proportions, available_cats,
            )
        except InputValidationError:
            return None

        main_anchor = anchor_resolver.resolve_anchor(
            url_category="home",
            anchor_type=decision.main_link_anchor_type,
            keyword=keyword,
            target_url=home_url,
            url_subject=topic,
            config=config,
            main_domain=main_domain,
            recent_texts=recent,
            provider=llm_provider,
            rng=rng,
            language=row["language"],
        )
        if main_anchor is None:
            last_errors = ["main_anchor_resolution_failed"]
            continue

        running_recent = list(recent) + [main_anchor]
        sec_pairs: list[tuple[str, str]] = []
        sec_records: list[tuple[str, str, str, str]] = []
        for sec in decision.secondary_links:
            sec_url = cats_map.get(sec.url_category)
            if not sec_url:
                last_errors = [f"missing_url_for_category:{sec.url_category}"]
                break
            sec_anchor = anchor_resolver.resolve_anchor(
                url_category=sec.url_category,
                anchor_type=sec.anchor_type,
                keyword=keyword,
                target_url=sec_url,
                url_subject=topic,
                config=config,
                main_domain=main_domain,
                recent_texts=running_recent,
                provider=llm_provider,
                rng=rng,
                language=row["language"],
            )
            if sec_anchor is None:
                last_errors = ["secondary_anchor_resolution_failed"]
                break
            sec_pairs.append((sec_url, sec_anchor))
            sec_records.append((sec.url_category, sec.anchor_type, sec_anchor, sec_url))
            running_recent.append(sec_anchor)

        if len(sec_pairs) != len(decision.secondary_links):
            continue

        html = markdown_utils.render_zh_short_article(
            keyword=keyword,
            main_domain=home_url,
            main_anchor=main_anchor,
            secondary_links=sec_pairs,
            style_seed=style_seed + attempt,
        )
        expected = [main_anchor] + [a for _, a in sec_pairs]
        ok, errors_out = markdown_utils.validate_zh_short_payload(html, expected)
        if ok:
            entries = _build_profile_entries(
                decision, main_anchor, home_url, sec_records, degraded=False,
            )
            anchor_profile.record_article(main_domain, entries)
            return _build_zh_short_payload(
                row, html, main_domain, main_anchor, sec_pairs,
            )
        last_errors = errors_out

    recent_for_dedup = anchor_profile.recent_texts(
        anchor_profile.load_profile(main_domain), n=20,
    )
    branded_pool = get_anchor_pool_v2(config, main_domain, "home", "branded")
    branded_clean_all = [
        w for w in branded_pool
        if anchor_resolver._passes_filters(w, row["language"])
    ]
    branded_clean = [w for w in branded_clean_all if w not in recent_for_dedup]
    if not branded_clean:
        branded_clean = branded_clean_all or [_domain_label_of(main_domain)]

    main_anchor = rng.choice(branded_clean)
    sec_candidates = [w for w in branded_clean if w != main_anchor]
    if not sec_candidates:
        sec_candidates = [w for w in branded_clean_all if w != main_anchor]
    sec_anchor = (
        rng.choice(sec_candidates) if sec_candidates else _domain_label_of(main_domain)
    )
    sec_pairs = [(home_url, sec_anchor)]

    html = markdown_utils.render_zh_short_article(
        keyword=keyword,
        main_domain=home_url,
        main_anchor=main_anchor,
        secondary_links=sec_pairs,
        style_seed=style_seed + 999,
    )

    degrade_decision = ScheduleDecision(
        main_link_anchor_type="branded",
        secondary_links=(
            SecondaryLink(url_category="home", anchor_type="branded"),
        ),
    )

    plan_logger.warn(
        "anchor_resolver_degraded",
        main_domain=main_domain,
        errors=last_errors,
    )

    entries = _build_profile_entries(
        degrade_decision,
        main_anchor,
        home_url,
        [("home", "branded", sec_anchor, home_url)],
        degraded=True,
    )
    anchor_profile.record_article(main_domain, entries)
    return _build_zh_short_payload(row, html, main_domain, main_anchor, sec_pairs)
