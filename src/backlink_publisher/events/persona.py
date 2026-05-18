"""Salted persona identifier for the event substrate (T4).

The event log records *who* published *what* without leaking PII. The
strategy: hash ``(provider, account_label)`` with a per-installation salt
to a 16-char hex digest. The salt lives at
``~/.config/backlink-publisher/persona.salt`` (32 random bytes, mode 0600)
and is created lazily on first use.

The salt is **independent** of ``events.db`` — deleting the database to
trigger a rebuild must not regenerate the salt (otherwise every persona_id
in re-emitted events would drift, breaking historical correlation).
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

from backlink_publisher.config import _config_dir

#: Length of the random salt in bytes. 32 bytes (256 bits) matches modern
#: HMAC-style salt sizing and is what ``os.urandom(32)`` produces.
_SALT_BYTES: int = 32

#: Length of the returned persona id in hex characters. 16 hex chars = 64
#: bits of digest. Plan §D8 sizes the fleet at ≤ a few hundred operator
#: accounts; birthday-bound collision probability at n=300 is ~10^-15.
#: Bump only after running the §D8 numbers for the new fleet size.
_PERSONA_ID_HEX_LEN: int = 16


class CorruptSaltError(RuntimeError):
    """Raised when ``persona.salt`` exists but has the wrong byte length.

    A truncated or zero-byte salt file would silently degrade ``persona_id``
    to an unsalted SHA-256 over ``provider`` + ``account_label``, which is
    trivially reversible. We refuse to load such a file and let the caller
    decide whether to delete + regenerate (which rotates all persona_ids)
    or restore from backup.
    """


def _salt_path() -> Path:
    """Resolve the salt file path lazily so env-var overrides apply.

    Tests rely on ``BACKLINK_PUBLISHER_CONFIG_DIR`` to redirect this; the
    path is recomputed on every call rather than cached at import time.
    """
    return _config_dir() / "persona.salt"


@lru_cache(maxsize=8)
def _load_salt(path: Path) -> bytes:
    """Return the salt bytes for ``path``; create the file on first use.

    Cached by path so callers invoking ``persona_id`` thousands of times
    don't repeatedly hit the filesystem. Cache is keyed on the resolved
    Path object, so each tmp directory in tests gets its own entry.

    Operator note: rotating ``persona.salt`` requires a process restart —
    the cache is not invalidated by external file changes. This matches
    plan §D5 (events.db deletion must NOT rotate salt) since the typical
    rotation event is intentional and operators are expected to restart
    long-running processes after one.
    """
    if path.exists():
        return _read_validated_salt(path)
    # First use — provision the parent directory (0700) and salt file
    # (0600). ``O_EXCL`` guards against a race with another process: only
    # one writer creates the file; everyone else falls through to the
    # read-back path so two concurrent first-use invocations don't both
    # write their own salt.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    salt = os.urandom(_SALT_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the race between exists() and os.open. Read
        # back the salt that process wrote and return it; both processes
        # converge on the same salt for the rest of their lifetimes.
        return _read_validated_salt(path)
    try:
        os.write(fd, salt)
    finally:
        os.close(fd)
    return salt


def _read_validated_salt(path: Path) -> bytes:
    """Read ``path`` and require it to be exactly ``_SALT_BYTES`` long.

    A truncated salt file would silently downgrade ``persona_id`` to an
    unsalted SHA-256; we refuse to load it and surface ``CorruptSaltError``
    so the operator notices.
    """
    data = path.read_bytes()
    if len(data) != _SALT_BYTES:
        raise CorruptSaltError(
            f"{path} has {len(data)} bytes, expected {_SALT_BYTES}; "
            "delete to regenerate (rotates all persona_ids) or restore "
            "from backup."
        )
    return data


def persona_id(provider: str, account_label: str) -> str:
    """Return a stable 16-char hex digest for ``(provider, account_label)``.

    The same input pair always returns the same output for the lifetime of
    the salt file. Different providers or different account labels return
    different digests with overwhelming probability.

    Inputs are encoded as UTF-8 and joined with a NUL separator so
    ``("a", "bc")`` and ``("ab", "c")`` cannot collide.
    """
    salt = _load_salt(_salt_path())
    h = hashlib.sha256()
    h.update(salt)
    h.update(provider.encode("utf-8"))
    h.update(b"\x00")
    h.update(account_label.encode("utf-8"))
    return h.hexdigest()[:_PERSONA_ID_HEX_LEN]
