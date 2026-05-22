"""Centralized debug artifact saving for publisher adapters.

All adapters should call ``save_debug_artifacts()`` when they catch a
failure, passing a dict with zero or more of the known keys.  Artifacts
are written to ``<cache_dir>/debug/<run_id>/<platform>.<seq>.{json,png}``.

Known keys:

- ``screenshot_path`` (str) — path to a screenshot PNG the adapter took
- ``page_html`` (str) — snapshot of the page HTML at failure time
- ``final_url`` (str) — the page URL at failure time
- ``payload_hash`` (str) — ``sha256.hexdigest()`` of the publish payload
- ``attempt_count`` (int) — which attempt this was (1-based)
- ``adapter`` (str) — adapter name (e.g. ``"medium-browser"``)
- ``error_type`` (str) — the exception type name
- ``error_message`` (str) — the exception message

Plan 2026-05-21-001 Unit 2.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def save_debug_artifacts(
    artifacts: dict[str, Any],
    *,
    cache_dir: Path | str,
    run_id: str = "",
) -> str | None:
    """Write debug artifacts to disk, returning the directory path.

    Returns ``None`` if the artifacts dict is empty (nothing to save).
    Creates ``<cache_dir>/debug/<run_id>/`` (default ``run_id`` is the
    ISO-8601 UTC timestamp).  Each non-None artifact is written as
    ``<key>_<seq>.<ext>`` with a sidecar ``metadata.json`` capturing
    all keys.

    Never raises: I/O errors are swallowed so a debug write can never
    break the publish path.
    """
    if not artifacts:
        return None

    cache = Path(cache_dir)
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    debug_dir = cache / "debug" / run_id
    platform = artifacts.get("platform", "unknown")
    seq = artifacts.get("attempt_count", 1)

    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        _write_artifact(debug_dir, platform, seq, artifacts)
    except OSError:
        pass

    return str(debug_dir)


def _write_artifact(
    debug_dir: Path,
    platform: str,
    seq: int,
    artifacts: dict[str, Any],
) -> None:
    """Write individual artifacts and a metadata sidecar."""
    prefix = f"{platform}.{seq:03d}"
    meta: dict[str, Any] = {
        "adapter": artifacts.get("adapter", ""),
        "platform": platform,
        "attempt_count": seq,
        "error_type": artifacts.get("error_type", ""),
        "error_message": artifacts.get("error_message", ""),
        "payload_hash": artifacts.get("payload_hash", ""),
        "final_url": artifacts.get("final_url", ""),
    }

    screenshot = artifacts.get("screenshot_path")
    if screenshot:
        src = Path(screenshot)
        if src.exists():
            dst = debug_dir / f"{prefix}.png"
            import shutil
            shutil.copy2(str(src), str(dst))
            meta["screenshot"] = dst.name

    page_html = artifacts.get("page_html")
    if page_html:
        html_path = debug_dir / f"{prefix}.html"
        html_path.write_text(page_html, encoding="utf-8")
        meta["page_html"] = html_path.name

    payload_raw = artifacts.get("payload_raw")
    if payload_raw and not meta.get("payload_hash"):
        meta["payload_hash"] = hashlib.sha256(
            payload_raw.encode("utf-8")
        ).hexdigest()[:16]

    meta_path = debug_dir / f"{prefix}.metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def make_debug_artifacts(
    *,
    adapter: str = "",
    platform: str = "",
    error_type: str = "",
    error_message: str = "",
    attempt_count: int = 1,
    screenshot_path: str | None = None,
    final_url: str | None = None,
    payload_raw: str | None = None,
    page_html: str | None = None,
) -> dict[str, Any]:
    """Build a debug_artifacts dict from keyword arguments.

    Adapters should call this at failure sites and pass the result to
    ``AdapterResult(debug_artifacts=..., ...)``.
    """
    d: dict[str, Any] = {
        "adapter": adapter,
        "platform": platform,
        "error_type": error_type,
        "error_message": error_message,
        "attempt_count": attempt_count,
    }
    if screenshot_path:
        d["screenshot_path"] = screenshot_path
    if final_url:
        d["final_url"] = final_url
    if payload_raw:
        d["payload_raw"] = payload_raw
    if page_html:
        d["page_html"] = page_html
    return d
