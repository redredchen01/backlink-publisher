"""Core payload generation, link building, and CLI entry point."""

from __future__ import annotations

import hashlib
import random
import re
import sys
from typing import Any
from urllib.parse import urlparse

from typing import Iterator

from ... import config_echo
from ...anchor import (
    profile as anchor_profile,
    resolver as anchor_resolver,
    scheduler as anchor_scheduler,
)
from ...content import (
    fetch as content_fetch,
    scraper as work_scraper,
    themed_gen as work_themed_generator,
)
from ..._util import markdown as markdown_utils
import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before argparse
from backlink_publisher.publishing.adapters.llm_anchor_provider import OpenAICompatibleProvider
from backlink_publisher.publishing.registry import registered_platforms
from backlink_publisher.anchor.profile import ProfileEntry
from backlink_publisher.anchor.scheduler import ScheduleDecision, SecondaryLink
from backlink_publisher.config import (
    Config,
    ThreeUrlConfig,
    get_anchor_keywords,
    get_anchor_pool_v2,
    get_three_url_config,
    load_config,
)
from backlink_publisher._util.errors import (
    ExternalServiceError,
    InputValidationError,
    emit_error,
)
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import plan_logger
from backlink_publisher._util.markdown import (
    links_to_markdown,
    select_anchor_keywords,
    slugify,
)
from ...schema import (
    validate_input_payload,
)

# Re-export symbols from extracted sub-modules so __init__.py and sibling
# modules (._zh_short, ._work_themed) find them at their old import paths.
from ._links import (                               # noqa: F401
    _ContentGateRowFailure,
    _ROW_REQUIRED_KINDS,
    _SUPPORTING_POOL,
    _SUPPORTING_URLS_FOR_PREFETCH,
    _TARGET_PADDED_LINK_COUNT,
    _build_links,
    _build_link_density_paragraph,
    _collect_candidate_urls_for_row,
)
from ._templates import (                           # noqa: F401
    _TEMPLATES,
    _TDK_TITLE_TMPL,
    _domain_label_of,
)
from ._banners import (                             # noqa: F401
    _build_banner_runtime,
    _generate_banner_for_payload,
)

ARTICLE_LENGTH_WORDS = (100, 200)


def _resolve_article_anchors(
    config: Config | None,
    main_domain: str,
    url_mode: str,
    fallback_label: str,
) -> list[str]:
    keywords = get_anchor_keywords(config, main_domain) if config is not None else []
    selected = select_anchor_keywords(keywords, url_mode, 2)
    if selected is None:
        plan_logger.warn(
            f"anchor_keywords missing for {main_domain}, falling back to bare domain label",
            main_domain=main_domain,
        )
        return [fallback_label, fallback_label]
    return selected


def _generate_payload(
    row: dict[str, Any],
    config: Config | None = None,
    *,
    fetch_verify_enabled: bool = True,
) -> dict[str, Any]:
    main_domain = row["main_domain"].rstrip("/")
    target_url = row["target_url"].rstrip("/")
    url_mode = row.get("url_mode", "A")
    platform = row["platform"]
    language = row["language"]
    target_language = row.get("target_language", language)
    publish_mode = row.get("publish_mode", "draft")
    topic = row.get("topic", "")
    
    # Plan-time URL Validation: verify target_url health
    if fetch_verify_enabled:
        from backlink_publisher.content.fetch import verify_url_has_content
        ok, reason, _ = verify_url_has_content(target_url)
        if not ok:
            # Plan-time URL Validation: ensure we fail early on unreachable target URLs
            raise InputValidationError(f"Target URL {target_url} is unreachable ({reason}).")
    
    extra_urls = row.get("extra_urls", [])
    custom_tags = row.get("custom_tags", "")
    system_prompt = row.get("system_prompt", "")
    tdk_title = row.get('tdk_title', '')
    tdk_description = row.get('tdk_description', '')
    tdk_keywords = row.get('tdk_keywords', '')

    domain_label = _domain_label_of(main_domain)

    anchors = _resolve_article_anchors(config, main_domain, url_mode, domain_label)

    tmpl = _TEMPLATES.get(target_language, _TEMPLATES.get(language, _TEMPLATES["en"]))
    title_tmpl = tmpl["title"].get(url_mode, tmpl["title"]["A"])
    topic_val = topic or tmpl.get("topic_fallback", "Resources")

    title = row.get("custom_title", "")
    if not title:
        if tdk_title and url_mode == 'C':
            lang_key = target_language if target_language in _TDK_TITLE_TMPL else "en"
            title = _TDK_TITLE_TMPL[lang_key].format(tdk=tdk_title, domain=domain_label)
        else:
            title = title_tmpl.format(domain=domain_label, topic=topic_val)

    slug = slugify(title)

    if tdk_description and url_mode in ('B', 'C'):
        excerpt = tdk_description[:200]
    else:
        excerpt = tmpl["excerpt"].get(url_mode, tmpl["excerpt"]["A"]).format(
            main_domain=main_domain, domain=domain_label, topic=topic_val,
            anchor=anchors[0],
        )

    tags_raw = tmpl.get("tags", ["backlink"])
    tags = [t.format(domain_label=domain_label) for t in tags_raw]

    if custom_tags:
        custom_tags_list = [t.strip() for t in custom_tags.split(",") if t.strip()]
        tags.extend(custom_tags_list)

    if tdk_keywords:
        kw_list = [k.strip() for k in tdk_keywords.split(",") if k.strip()]
        for kw in kw_list[:3]:
            if kw not in tags:
                tags.append(kw)

    body_tmpl = tmpl["body_paragraphs"].get(url_mode, tmpl["body_paragraphs"]["A"])

    if config and config.llm_anchor_provider and config.llm_anchor_provider.use_article_gen:
        try:
            llm_p = OpenAICompatibleProvider(
                base_url=config.llm_anchor_provider.base_url,
                api_key=config.llm_anchor_provider.api_key,
                model=config.llm_anchor_provider.model,
                temperature=config.llm_anchor_provider.temperature,
                system_prompt=config.llm_anchor_provider.system_prompt,
                article_system_prompt=config.llm_anchor_provider.article_system_prompt,
            )
            body = llm_p.generate_article_body(
                domain_label=domain_label,
                main_domain=main_domain,
                anchors=anchors,
                topic=topic_val,
                language=target_language,
            )
            plan_logger.info(f"LLM article body generated for {main_domain}")
        except Exception as e:
            plan_logger.warn(f"LLM article generation failed, falling back to template: {e}")
            body = body_tmpl(domain=domain_label, main_domain=main_domain, anchors=anchors)
    else:
        body = body_tmpl(domain=domain_label, main_domain=main_domain, anchors=anchors)

    if tdk_title or tdk_description:
        tdk_section = f"\n\n---\n**关于 {domain_label}**\n"
        if tdk_title:
            tdk_section += f"- 标题: {tdk_title}\n"
        if tdk_description:
            tdk_section += f"- 描述: {tdk_description[:150]}...\n"
        body = body + tdk_section

    cover_image_url = None
    cover_image_warning = None

    if extra_urls:
        extra_intro = f"\n\n除了主要的{domain_label}资源外，我们还整理了以下相关页面供您参考：\n"
        body = body + extra_intro

        for i, ex_url in enumerate(extra_urls[:3]):
            parsed = urlparse(ex_url)
            path = parsed.path

            if "/page/" in path or "?page=" in ex_url:
                anchor = f"第{path.split('/page/')[-1] if '/page/' in path else '其他'}页"
                context = f"更多内容请查看{anchor}。"
            elif "/category/" in path or "/tag/" in path:
                cat_name = path.split("/")[-2] if len(path.split("/")) > 2 else path.split("/")[-1]
                anchor = cat_name
                context = f"探索{anchor}分类了解更多相关内容。"
            elif "/archive/" in path:
                anchor = "历史归档"
                context = "查看历史文章归档。"
            else:
                anchor = f"相关页面 {i+1}"
                context = "这些相关页面也值得一读。"

            body = body + f"- [{anchor}]({ex_url}) - {context}\n"

        extra_section = "\n## 更多相关资源\n\n"
        for ex_url in extra_urls[:5]:
            parsed = urlparse(ex_url)
            path = parsed.path.split("/")[-1] or parsed.path.split("/")[-2] or "页面"

            if "/category/" in path:
                anchor = f"分类: {path.split('/')[-1]}"
            elif "/tag/" in path:
                anchor = f"标签: {path.split('/')[-1]}"
            elif "/page/" in path:
                anchor = f"分页 {path.split('/page/')[-1]}"
            elif "/archive/" in path:
                anchor = "归档页面"
            else:
                anchor = path if path else "相关链接"

            extra_section += f"- [{anchor}]({ex_url})\n"

        body = body + extra_section

    links, dropped_kinds = _build_links(
        main_domain,
        target_url,
        url_mode,
        extra_urls,
        anchors=anchors,
        site_url_categories=config.site_url_categories if config else None,
        fetch_verify_enabled=fetch_verify_enabled,
    )

    density_para = _build_link_density_paragraph(
        domain=domain_label,
        main_domain=main_domain,
        target_url=target_url,
        language=language,
        url_mode=url_mode,
        extra_url_count=len(extra_urls) if extra_urls else 0,
        anchors=anchors,
        site_url_categories=config.site_url_categories if config else None,
        dropped_kinds=dropped_kinds,
    )
    if density_para:
        body = body + density_para

    content_parts: list[str] = []
    content_parts.append(f"# {title}\n")
    content_parts.append(f"\n{excerpt}\n")
    content_parts.append(f"\n{body}\n")
    content_parts.append("\n## References\n")
    content_parts.append(links_to_markdown(links))
    content_markdown = "\n".join(content_parts)

    seo_title = tmpl.get("seo_title", "{title}").format(title=title)
    seo_desc = tmpl.get("seo_desc", "").format(main_domain=main_domain)

    seed_str = f"{target_url}:{main_domain}:{url_mode}:{platform}"
    article_id = hashlib.sha256(seed_str.encode()).hexdigest()[:16]

    return {
        "id": article_id,
        "platform": platform,
        "language": target_language,
        "source_language": language,
        "publish_mode": publish_mode,
        "target_url": target_url + ("/" if not target_url.endswith("/") else ""),
        "main_domain": main_domain + ("/" if not main_domain.endswith("/") else ""),
        "url_mode": url_mode,
        "title": title,
        "slug": slug,
        "excerpt": excerpt,
        "tags": tags,
        "content_markdown": content_markdown,
        "links": links,
        "cover_image_url": cover_image_url,
        "cover_image_warning": cover_image_warning,
        "seo": {
            "title": seo_title,
            "description": seo_desc,
            "canonical_url": target_url,
        },
    }


def _emit_link_count_recon(payload: dict[str, Any], *, branch: str) -> None:
    links = payload.get("links") or []
    kinds = sorted({lk.get("kind", "?") for lk in links})
    plan_logger.recon(
        "link_count_at_plan",
        branch=branch,
        count=len(links),
        kinds=kinds,
        main_domain=payload.get("main_domain", ""),
        article_id=payload.get("id", ""),
    )


def _dispatch_row(
    row: dict[str, Any],
    config: Config,
    *,
    llm_provider: OpenAICompatibleProvider | None,
    rng: random.Random | None,
    work_count: int,
    fetch_verify_enabled: bool = True,
) -> Iterator[dict[str, Any]]:
    three_url_cfg = get_three_url_config(config, row["main_domain"])
    if three_url_cfg is not None:
        from backlink_publisher.cli.plan_backlinks import _plan_work_themed_row
        for payload in _plan_work_themed_row(row, three_url_cfg, count=work_count):
            _emit_link_count_recon(payload, branch="work_themed")
            yield payload
        return

    payload: dict[str, Any] | None = None
    if row["language"] == "zh-CN" and _scheduler_enabled_for(
        config, row["main_domain"]
    ):
        from backlink_publisher.cli.plan_backlinks import _plan_zh_short_row
        payload = _plan_zh_short_row(row, config, llm_provider, rng=rng)
        if payload is not None:
            _emit_link_count_recon(payload, branch="zh_short")
            yield payload
            return
    if payload is None:
        from backlink_publisher.cli.plan_backlinks import _generate_payload
        payload = _generate_payload(
            row, config=config, fetch_verify_enabled=fetch_verify_enabled,
        )
    _emit_link_count_recon(payload, branch="long_form")
    yield payload


def _scheduler_enabled_for(config: Config, main_domain: str) -> bool:
    from backlink_publisher.cli.plan_backlinks import _scheduler_enabled_for as _inner
    return _inner(config, main_domain)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="plan-backlinks",
        description="Generate backlink article payloads from seed URLs.",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--from-csv",
        default=None,
        metavar="FILE",
        help="Read target URLs from a CSV/text file (one URL per line). Use '-' for stdin.",
    )
    parser.add_argument(
        "--from-sitemap",
        default=None,
        metavar="URL",
        help="Fetch target URLs from a sitemap XML URL.",
    )
    parser.add_argument(
        "--default-platform",
        default="blogger",
        choices=registered_platforms(),
        help="Platform for --from-csv / --from-sitemap rows (default: blogger)",
    )
    parser.add_argument(
        "--default-language",
        default="zh-CN",
        choices=["zh-CN", "en", "ru", "ko"],
        help="Language for --from-csv / --from-sitemap rows (default: zh-CN)",
    )
    parser.add_argument(
        "--default-url-mode",
        default="A",
        choices=["A", "B", "C"],
        help="URL mode for --from-csv / --from-sitemap rows (default: A)",
    )
    parser.add_argument(
        "--default-publish-mode",
        default="draft",
        choices=["draft", "publish"],
        help="Publish mode for --from-csv / --from-sitemap rows (default: draft)",
    )
    parser.add_argument(
        "--work-count",
        type=int,
        default=10,
        metavar="N",
        help=(
            "Per-row article count for the work-themed dispatcher path "
            "(default: 10). Ignored for legacy zh-short / long-form rows."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    parser.add_argument(
        "--no-fetch-verify",
        action="store_true",
        default=False,
        help=(
            "Skip the plan-time URL content gate (default: enabled). Each row's "
            "URLs are normally fetched via content_fetch.verify_url_has_content "
            "and required to return HTTP 200 with a non-empty <title> or "
            "og:title before being added to the article. Use this flag in "
            "dev / replay / staging when target sites are intentionally offline. "
            "Plan ref: docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md"
        ),
    )
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    if args.no_fetch_verify:
        plan_logger.recon("fetch_verify_disabled", reason="cli_flag")

    bulk_sources = [args.from_csv, args.from_sitemap]
    if sum(bool(x) for x in bulk_sources) > 1:
        emit_error("--from-csv and --from-sitemap are mutually exclusive", exit_code=2)
    if (args.from_csv or args.from_sitemap) and args.input:
        emit_error("--from-csv / --from-sitemap cannot be combined with --input", exit_code=2)

    plan_logger.info("plan-backlinks started", extra={"mode": "generate"})

    if args.from_csv or args.from_sitemap:
        from ...bulk_input import parse_csv, parse_sitemap, urls_to_seed_rows

        if args.from_csv:
            try:
                urls = parse_csv(args.from_csv)
            except Exception as exc:
                emit_error(f"failed to read CSV: {exc}", exit_code=2)
                return
        else:
            try:
                urls = parse_sitemap(args.from_sitemap)
            except RuntimeError as exc:
                emit_error(str(exc), exit_code=2)
                return

        if not urls:
            emit_error("no URLs found in input source", exit_code=2)
            return

        rows = urls_to_seed_rows(
            urls,
            platform=args.default_platform,
            language=args.default_language,
            url_mode=args.default_url_mode,
            publish_mode=args.default_publish_mode,
        )
        plan_logger.info(f"read {len(rows)} seed rows from bulk input")
    else:
        try:
            rows = list(read_jsonl(args.input))
        except SystemExit as exc:
            raise SystemExit(exc.code)

    plan_logger.info(f"read {len(rows)} seed rows")

    cfg = load_config()
    config_sha = config_echo.emit_banner(cfg, "plan-backlinks")

    llm_provider: OpenAICompatibleProvider | None = None
    if cfg.llm_anchor_provider is not None:
        llm_provider = OpenAICompatibleProvider(
            base_url=cfg.llm_anchor_provider.base_url,
            api_key=cfg.llm_anchor_provider.api_key,
            model=cfg.llm_anchor_provider.model,
            timeout_s=cfg.llm_anchor_provider.timeout_s,
            temperature=cfg.llm_anchor_provider.temperature,
            system_prompt=cfg.llm_anchor_provider.system_prompt,
        )

    image_gen_runtime = _build_banner_runtime(cfg)

    rng = random.Random()

    outputs: list[dict[str, Any]] = []
    all_errors: list[str] = []
    validation_drops: list[int] = []
    generation_drops: list[int] = []
    content_gate_drops: list[int] = []

    fetch_verify_enabled = not args.no_fetch_verify

    content_fetch.reset_stats()

    if fetch_verify_enabled:
        validated_rows: list[dict[str, Any]] = []
        for row in rows:
            if not validate_input_payload(row, 0):
                validated_rows.append(row)
        prefetch_set: set[str] = set()
        for row in validated_rows:
            prefetch_set.update(_collect_candidate_urls_for_row(row, cfg))
        prefetch_set.update(_SUPPORTING_URLS_FOR_PREFETCH)
        if prefetch_set:
            content_fetch.verify_urls_batch(
                list(prefetch_set), max_workers=10,
            )
            plan_logger.recon(
                "content_fetch_prefetch",
                n_urls_prefetched=len(prefetch_set),
                n_rows=len(validated_rows),
            )

    for line_num, row in enumerate(rows, start=1):
        errs = validate_input_payload(row, line_num)
        if errs:
            all_errors.extend(errs)
            validation_drops.append(line_num)
            continue
        try:
            for payload in _dispatch_row(
                row, cfg,
                llm_provider=llm_provider,
                rng=rng,
                work_count=args.work_count,
                fetch_verify_enabled=fetch_verify_enabled,
            ):
                branded_pool = get_anchor_pool_v2(
                    cfg, payload["main_domain"], "home", "branded"
                )
                metadata = dict(payload.get("metadata") or {})
                metadata["branded_pool"] = list(branded_pool)
                metadata["config_sha"] = config_sha
                payload["metadata"] = metadata

                if image_gen_runtime is not None:
                    payload["banner"] = _generate_banner_for_payload(
                        payload,
                        runtime=image_gen_runtime,
                        llm_provider=llm_provider,
                    )
                else:
                    payload["banner"] = None

                plan_logger.debug(
                    f"generated payload: id={payload['id']} platform={payload['platform']}",
                    extra={"id": payload["id"], "platform": payload["platform"]},
                )
                outputs.append(payload)
        except _ContentGateRowFailure as exc:
            all_errors.append(
                f"line {line_num}: content-gate failure: kind={exc.kind} "
                f"url={exc.url} reason={exc.reason}"
            )
            content_gate_drops.append(line_num)
        except Exception as exc:
            all_errors.append(f"line {line_num}: generation error: {exc}")
            generation_drops.append(line_num)

    plan_logger.recon(
        "plan_reconciliation",
        input_rows=len(rows),
        output_rows=len(outputs),
        delta=len(rows) - len(outputs),
        dropped={
            "validation": len(validation_drops),
            "generation": len(generation_drops),
            "content_gate": len(content_gate_drops),
        },
        dropped_line_numbers={
            "validation": validation_drops,
            "generation": generation_drops,
            "content_gate": content_gate_drops,
        },
    )

    plan_logger.recon(
        "content_fetch_stats",
        **content_fetch.stats_snapshot(),
    )

    if all_errors:
        for err in all_errors:
            print(err, file=sys.stderr)
        plan_logger.error(f"generation failed: {len(all_errors)} errors")
        raise SystemExit(2)

    plan_logger.info(f"generated {len(outputs)} payloads")
    write_jsonl(outputs)
