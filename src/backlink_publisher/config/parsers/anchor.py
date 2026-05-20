"""Anchor proportions + V2 pool getters."""
from __future__ import annotations

import logging
from typing import Any

from ..._util.errors import InputValidationError
from ..types import (
    ANCHOR_TYPES,
    _PROPORTIONS_SUM_TOLERANCE,
    _SAFE_SEO_PROPORTIONS,
)

from .three_url import _normalize_domain_key

_log = logging.getLogger(__name__)


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
