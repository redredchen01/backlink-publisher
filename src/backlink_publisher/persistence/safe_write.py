from __future__ import annotations

import os
import stat
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """Write text to path atomically via a unique temp file and replace.

    Uses ``tempfile.mkstemp`` for a unique sibling filename so concurrent
    callers do not collide on a shared ``.new`` temporary.  Readers see
    either the old file or the fully written new one — never a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=path.parent,
        prefix=path.name + ".",
        text=False,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rotate_snapshots(
    path: Path,
    snapshot_dir: Path,
    file_suffix: str = ".toml",
    max_history: int = 20,
) -> None:
    """Best-effort: copy current file to snapshot_dir with UTC timestamp.

    Rotates oldest snapshots so that snapshot_dir does not grow unbounded.
    Failure to snapshot does not raise an exception, to ensure the main write path
    remains operational.
    """
    if not path.exists():
        return
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(snapshot_dir, stat.S_IRWXU)  # 0700
        except OSError:
            pass
    except OSError as exc:
        _log.warning(
            f"Failed to create snapshot directory {snapshot_dir}: {exc}"
        )
        return

    # UTC ISO timestamp with colons replaced (Windows-safe).
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    snap_path = snapshot_dir / f"{ts}{file_suffix}"
    try:
        snap_path.write_bytes(path.read_bytes())
        try:
            os.chmod(snap_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    except OSError as exc:
        _log.warning(
            f"Failed to write snapshot {snap_path}: {exc}"
        )
        return

    # Rotate: keep the newest max_history files by mtime.
    try:
        snapshots = sorted(
            (p for p in snapshot_dir.glob(f"*{file_suffix}") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(snapshots) - max_history
        for old in snapshots[:max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass
