"""Template registry for article generation.

Extracted from ``core.py`` in the Unit 3 monolith decomposition.
"""

from __future__ import annotations

from backlink_publisher._util.markdown import (
    _en_body_a,
    _en_body_b,
    _en_body_c,
    _ru_body_a,
    _ru_body_b,
    _ru_body_c,
    _zh_body_a,
    _zh_body_b,
    _zh_body_c,
)

_TDK_TITLE_TMPL: dict[str, str] = {
    "zh-CN": "深入了解{tdk}: {domain} 完整指南",
    "ru": "Подробнее о {tdk}: полный гид по {domain}",
    "en": "Deep Dive into {tdk}: The Complete {domain} Guide",
}


def _domain_label_of(main_domain: str) -> str:
    return (
        main_domain.rstrip("/")
        .replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
    )


_TEMPLATES: dict[str, dict[str, object]] = {
    "en": {
        "title": {
            "A": "Exploring {domain}: A Comprehensive Guide",
            "B": "Navigating {domain} \u2014 Categories and Resources",
            "C": "Deep Dive into {domain}: {topic}",
        },
        "excerpt": {
            "A": "This article explores the resources and value offered by [{anchor}]({main_domain}), "
                  "providing context and curated links for readers.",
            "B": "A curated overview of [{anchor}]({main_domain})'s sections and key pages, "
                  "helping you navigate the site effectively.",
            "C": "A detailed look at {topic} as covered by [{anchor}]({main_domain}), with "
                  "additional references for further reading.",
        },
        "seo_title": "{title} | Backlink Article",
        "seo_desc": "A well-researched backlink article referencing {main_domain} "
                    "with curated external links and resources.",
        "topic_fallback": "Latest Resources and Insights",
        "tags": ["backlink", "reference", "web resources", "{domain_label}", "content curation"],
        "body_paragraphs": {
            "A": _en_body_a,
            "B": _en_body_b,
            "C": _en_body_c,
        },
    },
    "zh-CN": {
        "title": {
            "A": "深入探索{domain}：全面指南",
            "B": "浏览{domain}—分类与资源概览",
            "C": "深度解析{domain}：{topic}",
        },
        "excerpt": {
            "A": "本文探讨[{anchor}]({main_domain})提供的资源和价值，为读者提供背景和精选链接。",
            "B": "对[{anchor}]({main_domain})各板块和关键页面的精选概览，帮助您高效浏览该网站。",
            "C": "详细解读[{anchor}]({main_domain})上的{topic}内容，并提供延伸参考资料。",
        },
        "seo_title": "{title} | 反向链接文章",
        "seo_desc": "一篇精心撰写的反向链接文章，引用{main_domain}并提供精选外部链接和资源。",
        "topic_fallback": "最新资源与见解",
        "tags": ["反向链接", "参考", "网络资源", "{domain_label}", "内容策展"],
        "body_paragraphs": {
            "A": _zh_body_a,
            "B": _zh_body_b,
            "C": _zh_body_c,
        },
    },
    "ru": {
        "title": {
            "A": "Изучение {domain}: Полное руководство",
            "B": "Навигация по {domain} — Категории и ресурсы",
            "C": "Подробный анализ {domain}: {topic}",
        },
        "excerpt": {
            "A": "Эта статья исследует ресурсы и ценность [{anchor}]({main_domain}), "
                  "предоставляя контекст и курированные ссылки для читателей.",
            "B": "Подбор разделов и ключевых страниц [{anchor}]({main_domain}), "
                  "который поможет вам эффективно ориентироваться на сайте.",
            "C": "Подробный анализ темы {topic} на [{anchor}]({main_domain}) "
                  "с дополнительными ссылками для дальнейшего чтения.",
        },
        "seo_title": "{title} | Обратная ссылка статья",
        "seo_desc": "Качественная обратная ссылка статья со ссылками на {main_domain} "
                      "и дополнительными ресурсами.",
        "topic_fallback": "Последние ресурсы и инсайты",
        "tags": ["обратная-ссылка", "ссылка",
                 "веб-ресурс", "{domain_label}", "курирование"],
        "body_paragraphs": {
            "A": _ru_body_a,
            "B": _ru_body_b,
            "C": _ru_body_c,
        },
    },
}
