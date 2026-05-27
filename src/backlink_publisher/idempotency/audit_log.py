"""Append-only operator audit log for dedup mutations (Unit 5a).

Every operator-initiated dedup mutation (``--forget``, ``--adjudicate-uncertain``)
is recorded as one JSON line, **appended** under an exclusive ``flock`` with
``O_APPEND``.

Why not :func:`safe_write.atomic_write`? That is a whole-file ``os.replace``
(read-modify-rewrite). Under the concurrency this feature is tested against it
would **lose entries** (two writers each read the old file, each rewrite it, one
clobbers the other) and it is not append-only. ``O_APPEND`` + ``flock`` gives a
genuine, serialized append where every entry survives the race.

Honesty about guarantees: append-only here is **best-effort and
operator-defeatable** — the 0o600 owner can still truncate the file, and there is
no tamper-evidence. A per-entry ``prev_hash`` chain is a deferred option if
tamper-evidence is ever required; it is out of V1.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U5a).
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from ..config import _config_dir

_AUDIT_LOG_FILENAME = "dedup-audit.log"


def _audit_log_path() -> Path:
    return _config_dir() / _AUDIT_LOG_FILENAME


def _current_user() -> str:
    """Best-effort operator identity for the audit trail."""
    import getpass

    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - getuser can raise on odd envs
        return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"


def append_entry(
    *,
    action: str,
    platform: str,
    target_url: str,
    account: str = "default",
    from_state: str | None = None,
    to_state: str | None = None,
    reason: str = "",
    run_id: str | None = None,
) -> None:
    """Append one operator-mutation entry. Serialized via ``flock`` so concurrent
    appends do not interleave or drop. Creates the log 0o600 on first write."""
    entry: dict[str, Any] = {
        "ts": time.time(),
        "action": action,
        "platform": platform,
        "account": account,
        "target_url": target_url,
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "run_id": run_id,
        "user": _current_user(),
    }
    line = json.dumps(entry, sort_keys=True) + "\n"

    path = _audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # O_APPEND keeps each write atomic at the end of file; the exclusive flock
    # serializes whole-line appends so nothing is lost or interleaved.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_entries() -> list[dict[str, Any]]:
    """Return all log entries oldest-first. Tolerates an absent file (``[]``) and
    skips any malformed line rather than raising."""
    path = _audit_log_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def touched_keys() -> set[tuple[str, str, str]]:
    """Set of ``(platform, account, target_url)`` keys an operator has acted on.

    U6 backfill consults this to never re-seed a key the operator has already
    ``--forget``/``--adjudicate``-ed (decision-preserving / INSERT-only)."""
    keys: set[tuple[str, str, str]] = set()
    for e in read_entries():
        platform = e.get("platform")
        target_url = e.get("target_url")
        if platform and target_url:
            keys.add((platform, e.get("account", "default"), target_url))
    return keys
