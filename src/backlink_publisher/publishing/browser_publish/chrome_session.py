"""Shared Chrome/CDP helpers + ChromeAttachSession context manager.

Plan 2026-05-21-001 Unit 1. Bridges bind (cli/_bind/chrome_backend.py)
and publish phases on a single Chrome lifecycle abstraction.

Design notes calibrated against Unit 0 spike
(`docs/spikes/2026-05-21-chrome-lifecycle-spike.md`):

- Probe 1: teardown uses ``proc.terminate()`` + ``proc.wait(timeout=5)``;
  ``os.killpg`` raises EPERM from outside the new session leader's lineage
  on macOS and is not necessary — Chrome reaps helpers on SIGTERM.
- Probe 2: attach-mode listener identity check uses ``lsof -iTCP:<port>
  -Fp`` + ``ps -o command=`` substring match against chrome_bin AND
  profile dir. ``ps -o comm=`` truncates to ~15 chars on macOS, unusable.
- Probe 3: ``os.chmod(profile, 0o700)`` works in ``$TMPDIR`` / user config
  dir — no SIP fail-soft fallback needed.
- Probe 4: ``_chrome_profile_dir()`` now honors
  ``BACKLINK_PUBLISHER_BIND_CHANNEL`` (net-new in Unit 1, not in main's
  PR #129 baseline). Channel name is whitelisted ``[a-z0-9_-]+`` to
  prevent path traversal.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.config.loader import _config_dir

if TYPE_CHECKING:
    from playwright.sync_api import Page


_DEFAULT_PORT = 9222
_CONNECT_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.25
_TERMINATE_TIMEOUT_S = 5.0
_CHANNEL_RE = re.compile(r"^[a-z0-9_-]+$")
_PID_FILE_NAME = "real-chrome-publish.pid"
_PROFILE_LOCK_NAME = "chrome-profile.lock"


from ._chrome_session_impl import (
    BrowserPublishRecipe,
    ChromeAttachSession,
    ChromeSessionError,
    _chrome_binary,
    _chrome_port,
    _chrome_profile_dir,
    _websocket_available,
    _cdp_available,
    _verify_listener_is_chrome,
    _ensure_profile_perms,
    reap_orphan_publish_chrome,
)


# ---------------------------------------------------------------------------
# Path / binary discovery helpers (single source of truth for bind + publish)
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# Listener identity verification (attach mode)
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Profile perms
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# PID file (orphan reap)
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# BrowserPublishRecipe
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ChromeAttachSession
# ---------------------------------------------------------------------------

# re-exported from _chrome_session_impl
__all__ = [
    "BrowserPublishRecipe",
    "ChromeAttachSession",
    "ChromeSessionError",
    "_chrome_binary",
    "_chrome_port",
    "_chrome_profile_dir",
    "_websocket_available",
    "_cdp_available",
    "_verify_listener_is_chrome",
    "_ensure_profile_perms",
    "reap_orphan_publish_chrome",
]
