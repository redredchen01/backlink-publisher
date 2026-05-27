"""Canary health store + ``[canary.<platform>]`` config reader (Plan 2026-05-27-001 Unit 1).

Persists per-platform canary health in ``<config_dir>/canary-health.json``,
keyed by registry platform name. Mirrors ``webui_store/channel_status.py``:
a ``_LazyStore`` proxy defers backing-file path resolution to first access,
so ``BACKLINK_PUBLISHER_CONFIG_DIR`` changes (tests / CI) re-resolve cleanly.

**Import-path decision:** ``JsonStore`` / ``_LazyStore`` live in the
repo-root ``webui_store/`` package, NOT under ``src/``. Verified that
``from webui_store.base import JsonStore, _LazyStore`` resolves from within
``src/backlink_publisher/`` (the package is on the path the same way it is
for ``webui_store/channel_status.py``), so this module imports the
root-level package directly rather than re-implementing the store. Writes
go through ``JsonStore.save`` → ``persistence.safe_write.atomic_write``
(0o600), and ``update(fn)`` serialises read-modify-write under a
per-instance ``threading.Lock``.

**v1 MINIMAL fields only** (advisory-default debounce):
``{status, consecutive_failures, last_ok_at, last_drift_at}``.
``quarantined`` / ``consecutive_oks`` / re-arm machinery land in Unit 4
alongside their consumers — deliberately NOT created here before the
threshold-calibration step.

Status values are the verdicts Unit 3 will write:
``link-alive`` / ``drift-confirmed`` / ``advisory`` / ``not-configured``.

NOT ``channel_status_store`` — that store is bind-scoped to
{velog, medium, blogger} and raises ``UsageError`` for other platforms.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback mirrors config/loader.py
    import tomli as tomllib  # type: ignore[no-redef]

from backlink_publisher.config.loader import _config_dir
from webui_store.base import JsonStore, _LazyStore

# Health status values written by the canary verb (Unit 3).
STATUS_LINK_ALIVE = "link-alive"
STATUS_DRIFT_CONFIRMED = "drift-confirmed"
STATUS_ADVISORY = "advisory"
STATUS_NOT_CONFIGURED = "not-configured"

_HEALTH_DEFAULT: dict[str, Any] = {
    "status": STATUS_NOT_CONFIGURED,
    "consecutive_failures": 0,
    "last_ok_at": None,
    "last_drift_at": None,
}


canary_health_store: _LazyStore = _LazyStore(
    lambda: JsonStore(
        _config_dir() / "canary-health.json",
        default_factory=dict,
    )
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_health(platform: str) -> dict[str, Any]:
    """Read API. Unknown platforms return the minimal default record so
    callers don't have to branch on membership."""
    data = canary_health_store.load() or {}
    rec = data.get(platform)
    if rec is None:
        return dict(_HEALTH_DEFAULT)
    return rec


def list_all() -> dict[str, dict[str, Any]]:
    """Read API. Returns the full health store as a dict."""
    return dict(canary_health_store.load() or {})


def record_verdict(platform: str, status: str) -> dict[str, Any]:
    """Record a canary verdict for ``platform`` and return the new record.

    Debounce semantics (v1 minimal):

    - ``link-alive``      → ``consecutive_failures`` resets to 0,
      ``last_ok_at`` stamped.
    - ``drift-confirmed`` → ``consecutive_failures`` increments,
      ``last_drift_at`` stamped.
    - ``advisory`` / ``not-configured`` → counters preserved, timestamps
      unchanged (a read failure is neither an OK nor a confirmed drift).

    Read-modify-write runs through ``update(fn)`` under the store's
    per-instance lock; the write is atomic 0o600 via ``JsonStore.save``.
    """

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        existing = current.get(platform) or {}
        failures = int(existing.get("consecutive_failures", 0) or 0)
        last_ok_at = existing.get("last_ok_at")
        last_drift_at = existing.get("last_drift_at")

        if status == STATUS_LINK_ALIVE:
            failures = 0
            last_ok_at = _now_iso()
        elif status == STATUS_DRIFT_CONFIRMED:
            failures += 1
            last_drift_at = _now_iso()
        # advisory / not-configured: preserve counters + timestamps.

        current[platform] = {
            "status": status,
            "consecutive_failures": failures,
            "last_ok_at": last_ok_at,
            "last_drift_at": last_drift_at,
        }
        return current

    return canary_health_store.update(_apply)[platform]


def _load_canary_section(config_path: Path | None = None) -> dict[str, Any]:
    """Read the raw ``[canary]`` table straight off the parsed TOML.

    Mirrors ``loader.py``'s ``data.get("targets", {})`` style rather than
    threading a new field through the ``Config`` dataclass (which
    ``save_config`` does not round-trip). Missing file / missing section →
    empty dict (never raises)."""
    path = config_path or (_config_dir() / "config.toml")
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    section = data.get("canary", {})
    return section if isinstance(section, dict) else {}


def read_canary_config(
    platform: str, *, config_path: Path | None = None
) -> dict[str, Any] | None:
    """Read the ``[canary.<platform>]`` post config for ``platform``.

    Returns ``{"post_url", "expected_target", "marker", "hard_skip"}`` when
    the section exists, else ``None`` (platform not configured → Unit 3
    surfaces it as ``not-configured``).

    ``marker`` is the private, per-seeded-post sentinel the canary asserts is
    present before it will ever classify ``drift-confirmed`` (a missing marker
    means the page is not proven to be the canary post, so a missing anchor
    stays ``advisory`` rather than a false drift). ``None`` when unset.
    ``hard_skip`` defaults to ``False`` when absent. The opt-in hard-skip
    machinery that consumes it lands in Unit 4.
    """
    entry = _load_canary_section(config_path).get(platform)
    if not isinstance(entry, dict):
        return None
    marker = entry.get("marker")
    return {
        "post_url": str(entry.get("post_url", "")),
        "expected_target": str(entry.get("expected_target", "")),
        "marker": str(marker) if marker else None,
        "hard_skip": bool(entry.get("hard_skip", False)),
    }


__all__ = [
    "canary_health_store",
    "STATUS_LINK_ALIVE",
    "STATUS_DRIFT_CONFIRMED",
    "STATUS_ADVISORY",
    "STATUS_NOT_CONFIGURED",
    "get_health",
    "list_all",
    "record_verdict",
    "read_canary_config",
    "_load_canary_section",
]
