"""Channel binding status singleton — Plan 2026-05-19-001 Unit 1.

Tracks each browser-binding channel's lifecycle in
``<config_dir>/channel-status.json``:

  {
    "velog":   {"status": "bound",   "bound_at": "ISO", "storage_state_path": "..."},
    "medium":  {"status": "expired", "bound_at": "ISO", "storage_state_path": "..."},
    "blogger": {"status": "unbound", "bound_at": null,  "storage_state_path": null}
  }

Public API:
  - ``mark_bound(channel, storage_state_path)`` — record successful bind
  - ``mark_expired(channel)`` — flip bound → expired (preserves bound_at)
  - ``get_status(channel)`` — read API; unknown channel returns unbound
  - ``list_all()`` — read API; entire store as dict
  - ``reconcile_on_load()`` — called by webui_app.create_app at startup:
        for each bound record, stat the storage_state_path; demote
        missing-file records to expired while preserving bound_at.

Channel whitelist enforced on every WRITE site against
``cli._bind.channels.CHANNELS``; ``UsageError`` raised on injection.
storage_state_path also validated to be inside ``_config_dir()`` to
prevent supply-chain adapters from writing arbitrary paths into the
store (defense in depth — Unit 2 CLI's ``--output`` is the first line).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind.channels import CHANNELS
from backlink_publisher.config.loader import _config_dir
from webui_store.base import JsonStore, Store


_UNBOUND_DEFAULT: dict[str, Any] = {
    "status": "unbound",
    "bound_at": None,
    "storage_state_path": None,
}


def _make_store() -> Store:
    """Construct the singleton with a path that honors
    BACKLINK_PUBLISHER_CONFIG_DIR. Resolved at import time; tests that
    set the env var earlier (conftest's session-scope fixture does this)
    get an isolated path."""
    return JsonStore(
        _config_dir() / "channel-status.json",
        default_factory=dict,
    )


channel_status_store: Store = _make_store()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_channel(channel: str) -> None:
    if not channel or channel not in CHANNELS:
        raise UsageError(
            f"channel_status: unknown channel {channel!r} "
            f"(allowed: {sorted(CHANNELS)})"
        )


def _validate_storage_state_path(path: Path | str) -> Path:
    """Ensure path resolves inside _config_dir(). Raises UsageError for
    traversal / arbitrary absolute paths."""
    resolved = Path(path).resolve()
    config_root = _config_dir().resolve()
    try:
        resolved.relative_to(config_root)
    except ValueError as exc:
        raise UsageError(
            f"channel_status: storage_state_path {str(path)!r} must resolve "
            f"inside {str(config_root)!r}"
        ) from exc
    return resolved


def mark_bound(channel: str, storage_state_path: Path | str) -> None:
    """Record a successful bind for ``channel``. Validates channel
    whitelist + path locality."""
    _validate_channel(channel)
    resolved_path = _validate_storage_state_path(storage_state_path)

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        current[channel] = {
            "status": "bound",
            "bound_at": _now_iso(),
            "storage_state_path": str(resolved_path),
        }
        return current

    channel_status_store.update(_apply)


def mark_expired(channel: str) -> None:
    """Flip ``channel`` to status=expired. Preserves bound_at +
    storage_state_path so the UI can render 'last bound at YYYY-MM-DD'."""
    _validate_channel(channel)

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        existing = current.get(channel, {})
        current[channel] = {
            "status": "expired",
            "bound_at": existing.get("bound_at"),
            "storage_state_path": existing.get("storage_state_path"),
        }
        return current

    channel_status_store.update(_apply)


def get_status(channel: str) -> dict[str, Any]:
    """Read API. Unknown channels return the unbound default (no
    KeyError) so UI rendering doesn't have to branch on membership."""
    data = channel_status_store.load() or {}
    rec = data.get(channel)
    if rec is None:
        return dict(_UNBOUND_DEFAULT)
    return rec


def list_all() -> dict[str, dict[str, Any]]:
    """Read API. Returns the full store as a dict."""
    return dict(channel_status_store.load() or {})


def reconcile_on_load() -> None:
    """Demote any bound record whose ``storage_state_path`` is missing
    on disk to status=expired (preserves bound_at + path for UX).

    Called by ``webui_app.create_app`` at startup (single-threaded
    path), not lazy on first access — avoids lazy-init thread races and
    makes the post-startup state strictly consistent with disk.
    """

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        for channel, rec in list(current.items()):
            if not isinstance(rec, dict):
                continue
            if rec.get("status") != "bound":
                continue
            path = rec.get("storage_state_path")
            if not path or not os.path.exists(path):
                current[channel] = {
                    "status": "expired",
                    "bound_at": rec.get("bound_at"),
                    "storage_state_path": rec.get("storage_state_path"),
                }
        return current

    channel_status_store.update(_apply)


__all__ = [
    "channel_status_store",
    "mark_bound",
    "mark_expired",
    "get_status",
    "list_all",
    "reconcile_on_load",
]
