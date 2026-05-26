"""Playwright headed driver for ``bind-channel`` — Plan 2026-05-19-001 Unit 2.

Responsibilities (in order):

  1. Validate the storage_state target path resolves *inside* ``_config_dir()``
     (defense-in-depth against supply-chain adapters injecting paths).
  2. Launch a **headed** Chromium via Playwright (lazy-imported — the module
     itself does not need Playwright at import time, only at ``run_bind``
     time, so the webui can import this module to share ``BindResult``
     without a Playwright transitive cost).
  3. Open the recipe's ``login_url``; emit ``channel.bind.browser_ready``.
  4. Block on ``recipe.bound_predicate(page)`` until the operator finishes
     login. A Playwright timeout here surfaces as
     ``error_code="bound_predicate_timeout"``.
  5. Export Playwright's ``storage_state`` via the context — atomically:
     write to ``<target>.tmp`` then ``os.replace`` to the real path, then
     ``os.chmod`` to 0600. A failure during persist is reported as
     ``error_code="persist_io_error"`` and ``mark_bound`` is **not** called.
  6. Call ``mark_bound(channel, target)`` and emit
     ``channel.bind.persisted``.

The CLI in ``bind_channel.py`` is responsible for emitting
``channel.bind.start`` (before validation) and the terminal
``channel.bind.failed`` event (mapping our ``BindResult.error_code``).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind.channels import CHANNELS, EVENTS
from backlink_publisher.config.loader import _config_dir


# Default per-bind timeout. Playwright accepts ms; 5 minutes lets the operator
# finish a social-OAuth flow including 2FA without artificial pressure.
BIND_TIMEOUT_MS = 15 * 60 * 1000  # SPIKE PATCH plan-016 Unit 1 (was 5*60*1000)


# ───────── exception sentinels (caught by run_bind and CLI) ─────────


from ._driver_impl import PlaywrightLaunchError, BoundPredicateTimeout, PersistIOError, IdentityMismatch, ChromeLaunchError, BindResult, BrowserRunner, _emit, _browser_profile_dir, _validate_storage_state_path, _persist_storage_state, run_bind, _promote_last_account_if_pending, _PlaywrightBrowserRunner, _apply_host_filter, _origin_host










# ───────── public types ─────────



# ───────── re-exports from _driver_impl ─────────

__all__ = [
    "BIND_TIMEOUT_MS",
    "BindResult",
    "BoundPredicateTimeout",
    "BrowserRunner",
    "IdentityMismatch",
    "PersistIOError",
    "ChromeLaunchError",
    "PlaywrightLaunchError",
    "_browser_profile_dir",
    "_emit",
    "_persist_storage_state",
    "_promote_last_account_if_pending",
    "_validate_storage_state_path",
    "run_bind",
]
