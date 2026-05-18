"""LLM anchor provider parser."""
from __future__ import annotations

import logging
import math
import os
import re
from typing import Any

from ...errors import InputValidationError
from ...url_utils import validate_https_url, validate_main_domain_url
from ..types import (
    ANCHOR_TYPES,
    AnchorAlarmConfig,
    AnchorAlarmOverride,
    DEFAULT_WORK_TEMPLATES,
    LLMProviderConfig,
    ThreeUrlConfig,
    _LLM_API_KEY_ENV_VAR,
    _PROPORTIONS_SUM_TOLERANCE,
    _SAFE_SEO_PROPORTIONS,
    _UNSAFE_IN_ANCHOR,
)

_log = logging.getLogger(__name__)

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
        from ..loader import _warn_if_loose_config_permissions
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
