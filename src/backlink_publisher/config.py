"""User config loader for backlink-publisher.

Config file: ~/.config/backlink-publisher/config.toml
Token file:  ~/.config/backlink-publisher/blogger-token.json
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import DependencyError, InputValidationError
from .logger import plan_logger
from .url_utils import validate_https_url, validate_main_domain_url

_log = logging.getLogger(__name__)
_UNSAFE_IN_ANCHOR = re.compile(r'[\]\[()><"\'\n\r]')

# Anchor profile scheduler (zh-CN short-form) — type & proportion constants.
# These live module-level so other consumers (scheduler, resolver, validator)
# import a single source of truth.
ANCHOR_TYPES: tuple[str, ...] = ("branded", "partial", "exact", "lsi")
_SAFE_SEO_PROPORTIONS: dict[str, float] = {
    "branded": 0.55,
    "partial": 0.25,
    "exact": 0.10,
    "lsi": 0.10,
}
_LLM_API_KEY_ENV_VAR = "BACKLINK_LLM_API_KEY"
_PROPORTIONS_SUM_TOLERANCE = 1e-3

# Work-themed backlinks (Plan 2026-05-13-004 Unit 3).
# Templates for synthesising work_anchor text from scraped <title>. `{title}`
# is substituted at render time; templates without a `{title}` placeholder are
# rejected by the parser. Override per-target via
# ``[targets."<domain>"].work_anchor_templates``.
DEFAULT_WORK_TEMPLATES: tuple[str, ...] = (
    "{title}",
    "{title} 详情",
    "{title} 推荐",
    "{title} 介绍",
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


def _config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    return base / "backlink-publisher"


def _cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path.home() / ".cache"
    return base / "backlink-publisher"


@dataclass
class BloggerOAuthConfig:
    client_id: str
    client_secret: str


@dataclass
class MediumOAuthConfig:
    client_id: str
    client_secret: str


@dataclass
class ThreeUrlConfig:
    """Three-URL target config for the work-themed backlinks path.

    Required: ``main_url`` (https + host-root + trailing slash), ``list_url``,
    and three non-empty anchor pools (``branded_pool`` / ``partial_pool`` /
    ``exact_pool``).

    Optional: ``work_urls`` (when empty, Unit 2's ``work_scraper`` discovers
    them via sitemap / HTML fallback), ``work_anchor_templates`` (defaults to
    :data:`DEFAULT_WORK_TEMPLATES`), ``list_path_blocklist`` (overrides the
    scraper's default nav-path filter; ``None`` keeps the default),
    ``insecure_tls`` (opt-in TLS bypass for a target with broken certs).
    """

    main_url: str
    list_url: str
    branded_pool: list[str]
    partial_pool: list[str]
    exact_pool: list[str]
    work_urls: list[str] = field(default_factory=list)
    work_anchor_templates: list[str] = field(
        default_factory=lambda: list(DEFAULT_WORK_TEMPLATES)
    )
    list_path_blocklist: list[str] | None = None
    insecure_tls: bool = False


@dataclass
class LLMProviderConfig:
    """OpenAI-compatible LLM endpoint used to generate anchor-text candidates.

    The provider is optional — the anchor resolver falls back to config-pinned
    typed pools when this is unset. ``base_url`` MUST be ``https://`` (enforced
    at load time); ``api_key`` is preferentially loaded from the
    ``BACKLINK_LLM_API_KEY`` env var with the toml value as fallback.
    """

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 30.0


@dataclass(frozen=True)
class AnchorAlarmOverride:
    """One override row in ``[[anchor_alarm.override]]``.

    Matches a target by ``scope``: ``"url"`` matches the entry's full
    ``target_url``; ``"domain"`` matches its ``main_domain``. ``match`` is the
    exact string compared (no glob/regex — keep config behavior obvious).

    Each threshold field is optional; ``None`` means "fall through to the next
    precedence layer for this field". A row with all three fields ``None`` is
    rejected as a config error (it would have no effect — almost certainly a
    typo).
    """

    match: str
    scope: str  # "url" | "domain"
    entropy_floor: float | None = None
    exact_ratio_ceiling: float | None = None
    top3_concentration_ceiling: float | None = None


@dataclass
class AnchorAlarmConfig:
    """Operator-tunable thresholds for the anchor distribution alarm.

    Three global defaults plus an ordered list of overrides. Resolution
    precedence per target (highest wins): per-target-URL > per-`main_domain` >
    these globals > hardcoded constants in ``anchor_metrics``. Partial-field
    overrides fall through layer-by-layer.

    Defaults of ``None`` mean "use the hardcoded constants from
    ``anchor_metrics``". Setting any value here overrides only that field.
    """

    entropy_floor: float | None = None
    exact_ratio_ceiling: float | None = None
    top3_concentration_ceiling: float | None = None
    overrides: list[AnchorAlarmOverride] = field(default_factory=list)


@dataclass
class Config:
    blogger_blog_ids: dict[str, str] = field(default_factory=dict)
    blogger_oauth: BloggerOAuthConfig | None = None
    medium_oauth: MediumOAuthConfig | None = None
    medium_integration_token: str | None = None
    medium_user_data_dir: Path | None = None
    target_anchor_keywords: dict[str, list[str]] = field(default_factory=dict)
    """Per-target SEO anchor keyword pool, keyed by main_domain (trailing slash
    stripped). Populated from ``[targets."<main_domain>"].anchor_keywords`` in
    config.toml. Empty pool / missing entry triggers fallback to bare domain
    label at link-rendering time. Used by the en/ru long-form path. Must be
    edited by hand — ``save_config`` does not write this section back."""

    site_url_categories: dict[str, dict[str, str]] = field(default_factory=dict)
    """Per-site URL category → URL mapping for the zh-CN short-form path.

    Schema: ``[main_domain][category_name] → URL``. ``category_name`` is one of
    ``home`` / ``hot`` / ``animate`` / ``category`` / ``topic`` (the scheduler
    treats this set as opaque — any string is accepted, but the scheduler
    requires at least ``home`` plus one non-``home`` category to engage).

    Populated from ``[sites."<main_domain>".url_categories]`` in config.toml.
    Not round-tripped by ``save_config`` — manual edit only."""

    target_anchor_pools_v2: dict[str, dict[str, dict[str, list[str]]]] = field(
        default_factory=dict,
    )
    """Per-site, per-(url_category, anchor_type) anchor candidate pool.

    Schema: ``[main_domain][url_category][anchor_type] → list[anchor_text]``.
    ``anchor_type`` is one of ``branded`` / ``partial`` / ``exact`` / ``lsi``.

    Empty inner pools are valid and signal the anchor resolver to fall back to
    LLM-generated candidates. Populated from
    ``[sites."<main_domain>".anchor_pools.<url_category>.<anchor_type>]`` in
    config.toml. Not round-tripped by ``save_config``."""

    anchor_proportions: dict[str, float] = field(
        default_factory=lambda: dict(_SAFE_SEO_PROPORTIONS),
    )
    """Target distribution for the anchor profile scheduler.

    Defaults to Safe SEO (Branded 55% / Partial 25% / Exact 10% / LSI 10%).
    Sum must equal 1.0 ± 0.001 — validated at load time. Override by setting
    ``[anchor.proportions]`` in config.toml. Not round-tripped by
    ``save_config``."""

    llm_anchor_provider: LLMProviderConfig | None = None
    """Optional OpenAI-compatible LLM provider used to generate anchor candidates
    when typed pools are empty for a given (url_category, anchor_type).

    Populated from ``[llm.anchor_provider]`` in config.toml. ``api_key`` is
    loaded with priority ``BACKLINK_LLM_API_KEY`` env var > toml value.
    ``base_url`` is required to use ``https://`` — ``http://`` raises
    ``InputValidationError`` at load time. Not round-tripped by ``save_config``."""

    target_three_url: dict[str, ThreeUrlConfig] = field(default_factory=dict)
    """Three-URL target config for the work-themed backlinks path (Plan
    2026-05-13-004 Unit 3). Keyed by main_domain with trailing slash stripped.
    Populated from ``[targets."<main_domain>"]`` entries that carry the
    required three-URL schema (``main_url`` + ``list_url`` + three non-empty
    pools). Round-tripped by ``save_config(target_three_url=...)``."""

    anchor_alarm: AnchorAlarmConfig = field(default_factory=AnchorAlarmConfig)
    """Operator-tunable thresholds for ``report-anchors`` distribution alarm.

    Populated from ``[anchor_alarm]`` in config.toml. Globals + per-target
    overrides. Not round-tripped by ``save_config`` — manual edit only.
    See ``anchor_metrics.resolve_thresholds`` for precedence rules."""

    @property
    def config_dir(self) -> Path:
        return _config_dir()

    @property
    def cache_dir(self) -> Path:
        return _cache_dir()

    @property
    def blogger_token_path(self) -> Path:
        return _config_dir() / "blogger-token.json"

    @property
    def screenshot_dir(self) -> Path:
        return _cache_dir() / "screenshots"


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML file. Missing file → empty Config (not an error)."""
    config_path = path or (_config_dir() / "config.toml")
    if not config_path.exists():
        return Config()

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise DependencyError(
            f"Failed to parse config file {config_path}: {exc}"
        ) from exc

    blogger_section = data.get("blogger", {})
    oauth_section = blogger_section.pop("oauth", {})
    medium_section = data.get("medium", {})
    medium_browser_section = medium_section.get("browser", {})

    blogger_oauth: BloggerOAuthConfig | None = None
    if oauth_section.get("client_id") and oauth_section.get("client_secret"):
        blogger_oauth = BloggerOAuthConfig(
            client_id=oauth_section["client_id"],
            client_secret=oauth_section["client_secret"],
        )

    medium_oauth_section = medium_section.get("oauth", {})
    medium_oauth: MediumOAuthConfig | None = None
    if medium_oauth_section.get("client_id") and medium_oauth_section.get("client_secret"):
        medium_oauth = MediumOAuthConfig(
            client_id=medium_oauth_section["client_id"],
            client_secret=medium_oauth_section["client_secret"],
        )

    user_data_dir: Path | None = None
    if medium_browser_section.get("user_data_dir"):
        user_data_dir = Path(medium_browser_section["user_data_dir"])
    else:
        user_data_dir = _config_dir() / "chrome-profile-default"

    # blogger_section now contains only main_domain → blog_id mappings
    blog_ids = {k: str(v) for k, v in blogger_section.items() if isinstance(v, (str, int))}

    targets_section = data.get("targets", {})
    target_anchor_keywords = _parse_target_anchor_keywords(targets_section)
    target_three_url = _parse_target_three_url(targets_section)

    sites_section = data.get("sites", {})
    site_url_categories = _parse_site_url_categories(sites_section)
    target_anchor_pools_v2 = _parse_target_anchor_pools_v2(sites_section)

    # Maintenance-mode INFO: same domain has both legacy [sites."x"] and the
    # new three-URL [targets."x"] schema. Inform (not alarm) — both paths
    # continue to work; the dispatcher will prefer the work-themed flow.
    for domain_key in target_three_url:
        if domain_key in site_url_categories or domain_key in target_anchor_pools_v2:
            _log.info(
                "[sites.%r] is in maintenance mode; consider migrating to "
                "[targets.%r] three-URL form",
                domain_key, domain_key,
            )

    anchor_proportions = _parse_anchor_proportions(data.get("anchor", {}))

    llm_anchor_provider = _parse_llm_anchor_provider(
        data.get("llm", {}).get("anchor_provider", {}),
        config_path=config_path,
    )

    anchor_alarm = _parse_anchor_alarm(data.get("anchor_alarm"))

    return Config(
        blogger_blog_ids=blog_ids,
        blogger_oauth=blogger_oauth,
        medium_oauth=medium_oauth,
        medium_integration_token=medium_section.get("integration_token") or None,
        medium_user_data_dir=user_data_dir,
        target_anchor_keywords=target_anchor_keywords,
        site_url_categories=site_url_categories,
        target_anchor_pools_v2=target_anchor_pools_v2,
        anchor_proportions=anchor_proportions,
        llm_anchor_provider=llm_anchor_provider,
        target_three_url=target_three_url,
        anchor_alarm=anchor_alarm,
    )


def _parse_target_anchor_keywords(targets_section: Any) -> dict[str, list[str]]:
    """Parse ``[targets."<main_domain>"].anchor_keywords`` entries.

    Tolerant of missing / malformed entries — invalid entries are skipped with a
    warning rather than aborting the whole config load. Keys are normalised by
    stripping trailing slashes so lookups work regardless of how the user wrote
    the domain.
    """
    if not isinstance(targets_section, dict):
        return {}
    result: dict[str, list[str]] = {}
    for raw_domain, entry in targets_section.items():
        if not isinstance(entry, dict):
            _log.warning(
                "[targets.%r] is not a table, skipping", raw_domain,
            )
            continue
        keywords = entry.get("anchor_keywords")
        if keywords is None:
            continue
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            _log.warning(
                "[targets.%r].anchor_keywords must be a list of strings, skipping",
                raw_domain,
            )
            continue
        # Strip characters that would break Markdown link syntax or inject HTML.
        # Brackets, parens, angle-brackets, newlines can corrupt [anchor](url) output.
        cleaned = [_UNSAFE_IN_ANCHOR.sub("", k).strip() for k in keywords]
        cleaned = [k for k in cleaned if k]  # drop any that became empty after cleaning
        key = raw_domain.rstrip("/")
        result[key] = cleaned
    return result


def _parse_target_three_url(targets_section: Any) -> dict[str, ThreeUrlConfig]:
    """Parse ``[targets."<main_domain>"]`` entries that carry the three-URL
    work-themed schema. Plan 2026-05-13-004 Unit 3.

    Tolerant of malformed entries — each error is logged at WARN level and the
    offending entry is skipped rather than aborting the load. Entries that
    only carry ``anchor_keywords`` (legacy schema) are silently ignored here;
    they belong to ``_parse_target_anchor_keywords``.

    Returns a dict keyed by ``main_url`` with trailing slash stripped (the
    canonical key used by ``get_three_url_config``).
    """
    if not isinstance(targets_section, dict):
        return {}
    result: dict[str, ThreeUrlConfig] = {}
    for raw_domain, entry in targets_section.items():
        if not isinstance(entry, dict):
            continue  # already logged by _parse_target_anchor_keywords
        # Detection: any of main_url/list_url/*_pool present → caller intends
        # the three-URL schema. anchor_keywords-only entries are silently ignored.
        if not any(k in entry for k in (
            "main_url", "list_url", "branded_pool", "partial_pool", "exact_pool",
        )):
            continue

        main_url = validate_main_domain_url(entry.get("main_url"))
        if not main_url:
            _log.warning(
                "[targets.%r].main_url must be https://<host>/ (host-root + "
                "trailing slash), skipping",
                raw_domain,
            )
            continue

        list_url = validate_https_url(entry.get("list_url"))
        if not list_url:
            _log.warning(
                "[targets.%r].list_url missing or not https://, skipping",
                raw_domain,
            )
            continue

        branded_pool = _clean_pool(entry.get("branded_pool"))
        partial_pool = _clean_pool(entry.get("partial_pool"))
        exact_pool = _clean_pool(entry.get("exact_pool"))
        if not branded_pool:
            _log.warning(
                "[targets.%r].branded_pool is empty or invalid; target is "
                "unusable without a branded pool, skipping",
                raw_domain,
            )
            continue
        if not partial_pool:
            _log.warning(
                "[targets.%r].partial_pool is empty or invalid, skipping",
                raw_domain,
            )
            continue
        if not exact_pool:
            _log.warning(
                "[targets.%r].exact_pool is empty or invalid, skipping",
                raw_domain,
            )
            continue

        # work_urls: drop non-https entries silently (already warned by url_utils
        # consumers — log a single line per target if any get dropped).
        raw_work_urls = entry.get("work_urls", []) or []
        if not isinstance(raw_work_urls, list):
            raw_work_urls = []
        work_urls: list[str] = []
        dropped_work = 0
        for u in raw_work_urls:
            if not isinstance(u, str):
                dropped_work += 1
                continue
            normalized = validate_https_url(u)
            if not normalized:
                dropped_work += 1
                continue
            work_urls.append(normalized)
        if dropped_work:
            _log.warning(
                "[targets.%r].work_urls: dropped %d non-https or invalid URL(s)",
                raw_domain, dropped_work,
            )

        templates_raw = entry.get("work_anchor_templates")
        if templates_raw is None:
            templates = list(DEFAULT_WORK_TEMPLATES)
        elif isinstance(templates_raw, list) and all(
            isinstance(t, str) for t in templates_raw
        ):
            templates = [t.strip() for t in templates_raw if t.strip()]
            if not templates:
                templates = list(DEFAULT_WORK_TEMPLATES)
        else:
            _log.warning(
                "[targets.%r].work_anchor_templates must be a list of strings, "
                "using defaults",
                raw_domain,
            )
            templates = list(DEFAULT_WORK_TEMPLATES)

        blocklist_raw = entry.get("list_path_blocklist")
        if blocklist_raw is None:
            blocklist: list[str] | None = None
        elif isinstance(blocklist_raw, list) and all(
            isinstance(p, str) for p in blocklist_raw
        ):
            blocklist = [p for p in blocklist_raw if p]
        else:
            _log.warning(
                "[targets.%r].list_path_blocklist must be a list of strings, "
                "ignoring (default blocklist applies)",
                raw_domain,
            )
            blocklist = None

        insecure_tls = bool(entry.get("insecure_tls", False))

        key = main_url.rstrip("/")
        result[key] = ThreeUrlConfig(
            main_url=main_url,
            list_url=list_url,
            branded_pool=branded_pool,
            partial_pool=partial_pool,
            exact_pool=exact_pool,
            work_urls=work_urls,
            work_anchor_templates=templates,
            list_path_blocklist=blocklist,
            insecure_tls=insecure_tls,
        )
    return result


def _clean_pool(value: Any) -> list[str]:
    """Strip unsafe chars + drop empties from a list-of-string pool entry."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return []
    cleaned = [_UNSAFE_IN_ANCHOR.sub("", v).strip() for v in value]
    return [v for v in cleaned if v]


def _parse_site_url_categories(sites_section: Any) -> dict[str, dict[str, str]]:
    """Parse ``[sites."<main_domain>".url_categories]`` entries.

    Each URL must be a string starting with ``http://`` or ``https://``;
    malformed entries are skipped with a warning rather than raising.
    """
    if not isinstance(sites_section, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for raw_domain, entry in sites_section.items():
        if not isinstance(entry, dict):
            continue
        categories = entry.get("url_categories")
        if categories is None:
            continue
        if not isinstance(categories, dict):
            _log.warning(
                "[sites.%r].url_categories must be a table, skipping", raw_domain,
            )
            continue
        cleaned: dict[str, str] = {}
        for cat_name, cat_url in categories.items():
            if not isinstance(cat_name, str) or not isinstance(cat_url, str):
                continue
            if not re.match(r"^https?://", cat_url):
                _log.warning(
                    "[sites.%r].url_categories.%r is not a valid URL, skipping",
                    raw_domain, cat_name,
                )
                continue
            cleaned[cat_name] = cat_url
        if cleaned:
            result[raw_domain.rstrip("/")] = cleaned
    return result


def _parse_target_anchor_pools_v2(
    sites_section: Any,
) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Parse ``[sites."<main_domain>".anchor_pools.<url_cat>.<anchor_type>]``.

    Schema-strict: ``anchor_type`` must be one of ``ANCHOR_TYPES``; lists must
    be ``list[str]``. Pool entries are run through ``_UNSAFE_IN_ANCHOR`` to
    strip characters that would break Markdown/HTML link syntax — same hygiene
    contract as the legacy ``target_anchor_keywords`` parser.
    """
    if not isinstance(sites_section, dict):
        return {}
    result: dict[str, dict[str, dict[str, list[str]]]] = {}
    for raw_domain, entry in sites_section.items():
        if not isinstance(entry, dict):
            continue
        pools = entry.get("anchor_pools")
        if pools is None:
            continue
        if not isinstance(pools, dict):
            _log.warning(
                "[sites.%r].anchor_pools must be a table, skipping", raw_domain,
            )
            continue
        site_pools: dict[str, dict[str, list[str]]] = {}
        for url_cat, type_table in pools.items():
            if not isinstance(type_table, dict):
                continue
            cat_pools: dict[str, list[str]] = {}
            for anchor_type, words in type_table.items():
                if anchor_type not in ANCHOR_TYPES:
                    _log.warning(
                        "[sites.%r].anchor_pools.%s.%s is not a known anchor "
                        "type (expected one of %s), skipping",
                        raw_domain, url_cat, anchor_type, ANCHOR_TYPES,
                    )
                    continue
                if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
                    _log.warning(
                        "[sites.%r].anchor_pools.%s.%s must be a list of strings, skipping",
                        raw_domain, url_cat, anchor_type,
                    )
                    continue
                cleaned = [_UNSAFE_IN_ANCHOR.sub("", w).strip() for w in words]
                cleaned = [w for w in cleaned if w]
                cat_pools[anchor_type] = cleaned
            if cat_pools:
                site_pools[url_cat] = cat_pools
        if site_pools:
            result[raw_domain.rstrip("/")] = site_pools
    return result


def _parse_anchor_proportions(anchor_section: Any) -> dict[str, float]:
    """Parse ``[anchor.proportions]``; default to Safe SEO if absent.

    Validates that the four anchor types are covered and their sum is ~1.0.
    Raises ``InputValidationError`` on schema or sum violations — anchor
    distribution is load-bearing for the scheduler, silent fall-through would
    mask configuration bugs.
    """
    if not isinstance(anchor_section, dict):
        return dict(_SAFE_SEO_PROPORTIONS)
    proportions_section = anchor_section.get("proportions")
    if proportions_section is None:
        return dict(_SAFE_SEO_PROPORTIONS)
    if not isinstance(proportions_section, dict):
        raise InputValidationError(
            "[anchor.proportions] must be a table mapping anchor type → float"
        )
    # Start from Safe SEO and let toml keys override individual values; that
    # lets users tweak one slot without restating the whole map.
    result: dict[str, float] = dict(_SAFE_SEO_PROPORTIONS)
    for key, value in proportions_section.items():
        if key == "preset":
            # Only "safe_seo" is implemented; reject unknown presets explicitly.
            if value != "safe_seo":
                raise InputValidationError(
                    f"[anchor.proportions].preset = {value!r} is unknown "
                    f'(supported: "safe_seo")'
                )
            continue
        if key not in ANCHOR_TYPES:
            raise InputValidationError(
                f"[anchor.proportions].{key} is not a known anchor type "
                f"(expected one of {ANCHOR_TYPES})"
            )
        if not isinstance(value, (int, float)):
            raise InputValidationError(
                f"[anchor.proportions].{key} must be a number, got {type(value).__name__}"
            )
        result[key] = float(value)
    total = sum(result.values())
    if abs(total - 1.0) > _PROPORTIONS_SUM_TOLERANCE:
        raise InputValidationError(
            f"[anchor.proportions] values must sum to 1.0 ± {_PROPORTIONS_SUM_TOLERANCE} "
            f"(got {total:.4f}). Values: {result!r}"
        )
    return result


_ANCHOR_ALARM_THRESHOLD_FIELDS: tuple[str, ...] = (
    "entropy_floor",
    "exact_ratio_ceiling",
    "top3_concentration_ceiling",
)


def _coerce_threshold(section_label: str, key: str, value: Any) -> float:
    """Coerce a threshold scalar; raise ``InputValidationError`` on bad input.

    Anchor-alarm thresholds are non-load-bearing for publish-flow correctness,
    but silent fall-through still masks operator typos — we mirror
    ``_parse_anchor_proportions``'s raise-loud posture. Better to surface a
    config bug at load time than to ship with the operator's intent silently
    ignored.
    """
    if isinstance(value, bool):
        # bool is a subclass of int — reject explicitly to catch obvious typos.
        raise InputValidationError(
            f"[{section_label}].{key} must be a number, got bool ({value!r})"
        )
    if not isinstance(value, (int, float)):
        raise InputValidationError(
            f"[{section_label}].{key} must be a number, got {type(value).__name__}"
        )
    f = float(value)
    if not math.isfinite(f):
        raise InputValidationError(
            f"[{section_label}].{key} must be finite, got {value!r}"
        )
    if key == "entropy_floor":
        if f < 0:
            raise InputValidationError(
                f"[{section_label}].entropy_floor must be ≥ 0, got {f!r}"
            )
    else:
        # Ratio / concentration fields are bounded to [0, 1].
        if not (0.0 <= f <= 1.0):
            raise InputValidationError(
                f"[{section_label}].{key} must be in [0.0, 1.0], got {f!r}"
            )
    return f


def _parse_anchor_alarm(section: Any) -> AnchorAlarmConfig:
    """Parse ``[anchor_alarm]`` section. Missing → defaults (no overrides).

    Raises ``InputValidationError`` on malformed input — typos in a threshold
    key, non-numeric values, unknown scope, or an override row whose every
    threshold field is absent (a row with no effect is almost certainly a
    config mistake).
    """
    if section is None:
        return AnchorAlarmConfig()
    if not isinstance(section, dict):
        raise InputValidationError(
            f"[anchor_alarm] must be a table, got {type(section).__name__}"
        )

    cfg = AnchorAlarmConfig()
    overrides_raw = section.get("override")

    # Pull globals out of the section.
    for key, value in section.items():
        if key == "override":
            continue
        if key not in _ANCHOR_ALARM_THRESHOLD_FIELDS:
            raise InputValidationError(
                f"[anchor_alarm].{key} is not a known threshold "
                f"(expected one of {_ANCHOR_ALARM_THRESHOLD_FIELDS} or 'override')"
            )
        coerced = _coerce_threshold("anchor_alarm", key, value)
        setattr(cfg, key, coerced)

    # Parse overrides. TOML maps [[anchor_alarm.override]] to a list of dicts.
    if overrides_raw is not None:
        if not isinstance(overrides_raw, list):
            raise InputValidationError(
                "[[anchor_alarm.override]] must be an array of tables"
            )
        parsed: list[AnchorAlarmOverride] = []
        for i, row in enumerate(overrides_raw):
            if not isinstance(row, dict):
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i} must be a table"
                )
            match = row.get("match")
            scope = row.get("scope")
            if not isinstance(match, str) or not match:
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i}: 'match' is required (non-empty string)"
                )
            if scope not in ("url", "domain"):
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i}: 'scope' must be 'url' or 'domain', got {scope!r}"
                )
            kwargs: dict[str, float | None] = {}
            for f_name in _ANCHOR_ALARM_THRESHOLD_FIELDS:
                if f_name in row:
                    kwargs[f_name] = _coerce_threshold(
                        f"anchor_alarm.override[{i}]", f_name, row[f_name]
                    )
            if not kwargs:
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i} sets no threshold fields — "
                    f"row would have no effect. Add at least one of "
                    f"{_ANCHOR_ALARM_THRESHOLD_FIELDS}, or delete the row."
                )
            for f_name in _ANCHOR_ALARM_THRESHOLD_FIELDS:
                kwargs.setdefault(f_name, None)
            parsed.append(
                AnchorAlarmOverride(match=match, scope=scope, **kwargs)
            )
        cfg.overrides = parsed

    return cfg


def _parse_llm_anchor_provider(
    section: Any,
    *,
    config_path: Path | None = None,
) -> LLMProviderConfig | None:
    """Parse ``[llm.anchor_provider]`` and resolve ``api_key`` from env.

    Returns ``None`` when the section is empty or missing required fields —
    LLM is optional; absence simply means the anchor resolver will only use
    config-pinned typed pools.

    Enforces ``https://`` on ``base_url`` and warns if config.toml contains
    ``api_key`` but its file permissions are not 0600.
    """
    if not isinstance(section, dict):
        return None

    env_api_key = os.environ.get(_LLM_API_KEY_ENV_VAR)
    toml_api_key_raw = section.get("api_key")
    toml_has_api_key = isinstance(toml_api_key_raw, str) and bool(toml_api_key_raw)

    if toml_has_api_key and config_path is not None and config_path.exists():
        _warn_if_loose_config_permissions(config_path)

    base_url = section.get("base_url")
    model = section.get("model")
    timeout_s = section.get("timeout_s", 30.0)

    api_key = env_api_key or (toml_api_key_raw if toml_has_api_key else None)

    if not base_url and not model and not api_key:
        # Section absent or fully empty — silent no-op.
        return None

    # Beyond this point we treat a section with ANY content as an explicit
    # intent to configure the provider, so missing fields become errors.
    if not isinstance(base_url, str) or not base_url:
        raise InputValidationError(
            "[llm.anchor_provider].base_url is required when the section is present"
        )
    if not base_url.startswith("https://"):
        raise InputValidationError(
            f"[llm.anchor_provider].base_url must use https:// "
            f"(got {base_url!r}). Insecure endpoints are rejected to prevent "
            f"prompt-injection and credential exfiltration via a hostile host."
        )
    if not isinstance(model, str) or not model:
        raise InputValidationError(
            "[llm.anchor_provider].model is required when the section is present"
        )
    if not api_key:
        raise InputValidationError(
            f"LLM provider is configured but no api_key is available — set "
            f"the {_LLM_API_KEY_ENV_VAR} env var or [llm.anchor_provider].api_key"
        )
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise InputValidationError(
            f"[llm.anchor_provider].timeout_s must be a positive number, got {timeout_s!r}"
        )

    return LLMProviderConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_s=float(timeout_s),
    )


def _warn_if_loose_config_permissions(config_path: Path) -> None:
    """Emit a warning if config.toml contains api_key but isn't 0600.

    No-op on Windows where POSIX permission bits aren't meaningful.
    """
    if os.name == "nt":
        return
    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except OSError:
        return
    if mode != 0o600:
        _log.warning(
            "config file %s contains an LLM api_key but has mode %s; "
            "set permissions to 0600 (chmod 600) to prevent credential leakage",
            config_path, oct(mode),
        )


def _normalize_domain_key(domain: str) -> str:
    """Strip scheme and trailing slashes for config key comparison."""
    return domain.rstrip("/").removeprefix("https://").removeprefix("http://")


def get_anchor_pool_v2(
    config: Config,
    main_domain: str,
    url_category: str,
    anchor_type: str,
) -> list[str]:
    """Return the configured typed-pool anchor candidates for one slot.

    Returns ``[]`` when any layer of the (main_domain, url_category,
    anchor_type) lookup is missing — callers should interpret an empty pool
    as the cue to fall back to LLM-generated candidates.

    Like ``get_anchor_keywords``, tolerates trailing-slash variants in the
    main_domain key.
    """
    for candidate in (
        main_domain.rstrip("/"),
        main_domain.rstrip("/") + "/",
    ):
        if candidate in config.target_anchor_pools_v2:
            return (
                config.target_anchor_pools_v2[candidate]
                .get(url_category, {})
                .get(anchor_type, [])
            )
    return []


def get_anchor_keywords(config: Config, main_domain: str) -> list[str]:
    """Return the configured anchor keyword pool for ``main_domain``.

    Tolerates scheme mismatches between config keys and seed rows — both
    ``https://example.com`` and ``http://example.com`` will match a config
    entry for either form, as well as a bare ``example.com`` key.

    Returns an empty list when no pool is configured — callers are expected to
    detect that condition and fall back to bare-domain anchor text.
    """
    bare = _normalize_domain_key(main_domain)
    for candidate in (
        main_domain.rstrip("/"),          # exact match first (most common)
        "https://" + bare,
        "http://" + bare,
        bare,                              # bare domain (no scheme)
    ):
        if candidate in config.target_anchor_keywords:
            return config.target_anchor_keywords[candidate]
    return []


def get_three_url_config(
    config: Config, main_domain: str
) -> ThreeUrlConfig | None:
    """Return the work-themed ``ThreeUrlConfig`` for ``main_domain`` if any.

    Tolerates trailing-slash variants in the lookup key — matches
    ``get_anchor_keywords``'s scheme-tolerance contract.
    """
    bare = _normalize_domain_key(main_domain)
    for candidate in (
        main_domain.rstrip("/"),
        "https://" + bare,
        "http://" + bare,
        bare,
    ):
        if candidate in config.target_three_url:
            return config.target_three_url[candidate]
    return None


def resolve_blog_id(config: Config, main_domain: str) -> str:
    """Return Blogger blog_id for main_domain. Raises DependencyError if not mapped."""
    # Normalise: strip trailing slash for lookup
    key = main_domain.rstrip("/")
    # Try exact match, then with/without trailing slash
    for candidate in (key, key + "/"):
        if candidate in config.blogger_blog_ids:
            return config.blogger_blog_ids[candidate]
    raise DependencyError(
        f"No Blogger blog_id configured for domain '{main_domain}'. "
        f"Add it to ~/.config/backlink-publisher/config.toml under [blogger]:\n"
        f'  "{main_domain}" = "<your-blog-id>"'
    )


def load_blogger_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load OAuth token dict from JSON file. Returns None if file missing."""
    token_path = path or (_config_dir() / "blogger-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# Top-level TOML section roots that save_config writes from the Config
# dataclass. Any other section on disk is preserved byte-for-byte by
# _preserve_unknown_sections. Adding a new "known" section here means
# save_config must also know how to write it back.
_SAVE_CONFIG_KNOWN_ROOTS: frozenset[str] = frozenset(
    {"blogger", "medium", "targets"}
)

# Cap on rolling config.toml snapshots kept under .config-history/.
_CONFIG_HISTORY_MAX: int = 20

# Matches a TOML top-level heading: `[section]`, `[[array.of.tables]]`,
# `[quoted."dotted"]`. Captures the root (first dotted segment) so the caller
# can decide whether to copy or skip. The lexer is intentionally not a full
# TOML parser — it only needs to find section boundaries.
_TOML_HEADING_RE = re.compile(
    r"""
    ^\s*\[\[?           # opening [ or [[
    \s*
    (?:
        "([^"]+)"       # quoted root
        |
        ([^.\]\s"]+)    # bare root (no dots, brackets, whitespace)
    )
    """,
    re.VERBOSE,
)


def _toml_heading_root(line: str) -> str | None:
    """Extract the root segment of a TOML heading line, or None if not a heading."""
    m = _TOML_HEADING_RE.match(line)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _preserve_unknown_sections(raw_text: str, known_roots: frozenset[str]) -> str:
    """Return verbatim text of top-level sections whose root is not in ``known_roots``.

    Walks the input line-by-line. When a TOML heading is encountered, the
    section-membership state flips based on whether the root is known. Lines
    inside an "unknown" section are appended verbatim — preserving comments,
    key order, and whitespace. Lines before the first heading (file-level
    comments) are dropped because save_config rewrites the file's preamble.

    Edge cases:
    - Empty input → empty output.
    - Input with only known sections → empty output.
    - Heading inside a string literal would fool the regex; we accept that
      risk because TOML values rarely span lines or contain `[` at column 0,
      and load_config would have rejected such a file at parse time anyway.
    """
    out: list[str] = []
    keep_current = False  # before the first heading, drop preamble
    for line in raw_text.splitlines():
        root = _toml_heading_root(line)
        if root is not None:
            keep_current = root not in known_roots
            if keep_current:
                out.append(line)
        elif keep_current:
            out.append(line)
    # Trailing newline keeps output well-formed when concatenated.
    return ("\n".join(out) + "\n") if out else ""


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write ``text`` to ``path`` atomically via a sibling .new + replace.

    Mirrors :func:`io_utils.atomic_write_json` for plain text. Readers see
    either the old file or the fully written new one — never a torn write.
    chmod best-effort; the rename is load-bearing.
    """
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    tmp.replace(path)


def _snapshot_config(path: Path, max_history: int = _CONFIG_HISTORY_MAX) -> None:
    """Best-effort: copy current ``path`` to ``.config-history/<UTC-ts>.toml``.

    Pre-save snapshot for time-travel recovery. Failures (missing source,
    unwritable dir, full disk) are logged but never raise — operator data
    safety on the main save path dominates. Rotates oldest snapshots so the
    directory does not grow unbounded.
    """
    if not path.exists():
        return
    snapshot_dir = path.parent / ".config-history"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(snapshot_dir, stat.S_IRWXU)  # 0700
        except OSError:
            pass
    except OSError as exc:
        plan_logger.warn(
            "config_snapshot_dir_failed",
            path=str(snapshot_dir),
            reason=type(exc).__name__,
        )
        return

    # UTC ISO timestamp with colons replaced (Windows-safe).
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    snap_path = snapshot_dir / f"{ts}.toml"
    try:
        snap_path.write_bytes(path.read_bytes())
        try:
            os.chmod(snap_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    except OSError as exc:
        plan_logger.warn(
            "config_snapshot_write_failed",
            path=str(snap_path),
            reason=type(exc).__name__,
        )
        return

    # Rotate: keep the newest `max_history` files by mtime.
    try:
        snapshots = sorted(
            (p for p in snapshot_dir.glob("*.toml") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(snapshots) - max_history
        for old in snapshots[:max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        # Rotation failure is benign — operator will see one extra file.
        pass


def save_config(
    config: "Config",
    path: Path | None = None,
    extra_blogger_ids: dict[str, str] | None = None,
    medium_token: str | None = None,
    blogger_client_id: str | None = None,
    blogger_client_secret: str | None = None,
    target_anchor_keywords: dict[str, list[str]] | None = None,
    target_three_url: dict[str, ThreeUrlConfig] | None = None,
) -> None:
    """Write (or update) config.toml with the supplied values.

    Merges new values with any existing config so that calling this
    function never silently drops keys that were already there.

    All round-trippable kwargs follow the same three-state semantics:
    - ``None`` (default) — preserve whatever is already on disk
    - ``{}`` — explicitly clear the corresponding section
    - non-empty dict — write exactly the provided contents (overrides disk)

    Sections this function manages (and therefore rewrites):
    ``[blogger]``, ``[blogger.oauth]``, ``[medium]``, ``[targets."<domain>"]``.

    All other sections present in the existing file (``[sites.*]``,
    ``[anchor.*]``, ``[llm.*]``, ``[medium.oauth]``, ``[medium.browser]``,
    user-added tables, comments interleaved between unmanaged tables) are
    preserved verbatim. This closes the P0 data-loss footgun documented in
    feedback_config-save-overwrite-pattern.md (Plan 2026-05-13-004 Unit 3).

    The write is atomic: contents go to ``<path>.tmp`` first, ``fsync``'d, then
    ``os.replace``'d onto the target path. A mid-write crash leaves the
    original file intact.
    """
    config_path = path or (_config_dir() / "config.toml")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Load parsed config (for merge logic on managed fields).
    existing = load_config(config_path)

    # Build blog_ids: start from config.blogger_blog_ids (may already be pre-set by caller),
    # then overlay extra_blogger_ids on top. If extra_blogger_ids is None, merge from existing.
    blog_ids: dict[str, str] = dict(config.blogger_blog_ids)
    if extra_blogger_ids is None:
        for k, v in existing.blogger_blog_ids.items():
            if k not in blog_ids:
                blog_ids[k] = v
    elif extra_blogger_ids:
        blog_ids.update(extra_blogger_ids)

    # OAuth credentials
    client_id = blogger_client_id or (
        existing.blogger_oauth.client_id if existing.blogger_oauth else ""
    )
    client_secret = blogger_client_secret or (
        existing.blogger_oauth.client_secret if existing.blogger_oauth else ""
    )

    # Medium token
    token = medium_token if medium_token is not None else (
        existing.medium_integration_token or ""
    )

    # Resolve three-state for target_anchor_keywords and target_three_url.
    if target_anchor_keywords is None:
        kws_by_domain = dict(existing.target_anchor_keywords)
    else:
        kws_by_domain = dict(target_anchor_keywords)

    if target_three_url is None:
        three_url_by_domain = dict(existing.target_three_url)
    else:
        three_url_by_domain = dict(target_three_url)

    lines: list[str] = []

    # [blogger] — domain → blog_id pairs first
    lines.append("[blogger]")
    for domain, blog_id in blog_ids.items():
        lines.append(f"{_toml_str(domain)} = {_toml_str(blog_id)}")
    lines.append("")

    # [blogger.oauth]
    if client_id or client_secret:
        lines.append("[blogger.oauth]")
        lines.append(f"client_id     = {_toml_str(client_id)}")
        lines.append(f"client_secret = {_toml_str(client_secret)}")
        lines.append("")

    # [medium]
    lines.append("[medium]")
    if token:
        lines.append(f"integration_token = {_toml_str(token)}")
    else:
        lines.append('# integration_token = "your-medium-integration-token"')
    lines.append("")

    # [targets."<domain>"] — merge anchor_keywords + three_url into one block
    # per domain so they share a single header (TOML disallows duplicate
    # table headers).
    all_target_domains = sorted(
        set(kws_by_domain) | set(three_url_by_domain)
    )
    for domain in all_target_domains:
        lines.append(f"[targets.{_toml_str(domain)}]")
        if domain in kws_by_domain:
            kws = kws_by_domain[domain]
            lines.append(f"anchor_keywords = {_toml_list(kws)}")
        if domain in three_url_by_domain:
            tu = three_url_by_domain[domain]
            lines.append(f"main_url = {_toml_str(tu.main_url)}")
            lines.append(f"list_url = {_toml_str(tu.list_url)}")
            lines.append(f"work_urls = {_toml_list(tu.work_urls)}")
            lines.append(f"branded_pool = {_toml_list(tu.branded_pool)}")
            lines.append(f"partial_pool = {_toml_list(tu.partial_pool)}")
            lines.append(f"exact_pool = {_toml_list(tu.exact_pool)}")
            if tu.work_anchor_templates != list(DEFAULT_WORK_TEMPLATES):
                lines.append(
                    f"work_anchor_templates = {_toml_list(tu.work_anchor_templates)}"
                )
            if tu.list_path_blocklist is not None:
                lines.append(
                    f"list_path_blocklist = {_toml_list(tu.list_path_blocklist)}"
                )
            if tu.insecure_tls:
                lines.append("insecure_tls = true")
        lines.append("")

    # Preserve every top-level section save_config does not know how to write
    # (e.g. [anchor.proportions], [anchor_alarm], [anchor_alarm.override],
    # [llm.anchor_provider], [sites.*], [medium.browser], [medium.oauth]).
    # This is the structural fix for the documented save_config data-loss bug —
    # see feedback_config-save-overwrite-pattern.md.
    preserved = ""
    if config_path.exists():
        try:
            existing_raw = config_path.read_text(encoding="utf-8")
            preserved = _preserve_unknown_sections(
                existing_raw, _SAVE_CONFIG_KNOWN_ROOTS,
            )
        except OSError as exc:
            plan_logger.warn(
                "config_preserve_read_failed",
                path=str(config_path),
                reason=type(exc).__name__,
            )

    payload = "\n".join(lines)
    if preserved:
        # Single blank line separator between known sections and preserved bytes.
        if not payload.endswith("\n"):
            payload += "\n"
        payload += "\n" + preserved

    # Snapshot before overwrite — opportunistic, never blocks the main save.
    _snapshot_config(config_path)

    # Atomic write: .new + replace. Crash mid-write leaves original intact.
    _atomic_write_text(config_path, payload)


# ── TOML emission helpers used by save_config's [targets.<domain>] writer ──


def _toml_str(value: str) -> str:
    """Quote ``value`` as a TOML basic string. Escapes ``\\`` and ``"``."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_list(values: list[str]) -> str:
    """Emit a TOML list-of-strings. Empty list → ``[]``."""
    if not values:
        return "[]"
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def save_blogger_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_config_dir() / "blogger-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    # Restrict permissions (no-op on Windows)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_medium_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Medium OAuth token dict from JSON file. Returns None if file missing."""
    token_path = path or (_config_dir() / "medium-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_medium_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Medium OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_config_dir() / "medium-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
