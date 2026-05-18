"""Blogger / Medium token file I/O."""
from __future__ import annotations

import json
import logging
import math
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import DependencyError, InputValidationError
from ..logger import plan_logger
from ..url_utils import validate_https_url, validate_main_domain_url
from .types import (
    ANCHOR_TYPES,
    AnchorAlarmConfig,
    AnchorAlarmOverride,
    BloggerOAuthConfig,
    Config,
    DEFAULT_WORK_TEMPLATES,
    LLMProviderConfig,
    MediumOAuthConfig,
    ThreeUrlConfig,
    _LLM_API_KEY_ENV_VAR,
    _PROPORTIONS_SUM_TOLERANCE,
    _SAFE_SEO_PROPORTIONS,
    _UNSAFE_IN_ANCHOR,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from .loader import _config_dir


def _resolve_config_dir():
    """Indirect lookup of ``_config_dir`` via the package — restores
    monkeypatchability after the Unit 5 split (see ``writer.py``)."""
    from backlink_publisher import config as _cfg
    return _cfg._config_dir()


_log = logging.getLogger(__name__)

def load_blogger_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load OAuth token dict from JSON file. Returns None if file missing."""
    token_path = path or (_resolve_config_dir() / "blogger-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_blogger_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_resolve_config_dir() / "blogger-token.json")
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
    token_path = path or (_resolve_config_dir() / "medium-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_medium_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Medium OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_resolve_config_dir() / "medium-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
