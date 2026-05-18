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
import sys
from functools import lru_cache
from pathlib import Path

from ..config import _config_dir

#: Length of the random salt in bytes. 32 bytes (256 bits) matches modern
#: HMAC-style salt sizing and is what ``os.urandom(32)`` produces.
_SALT_BYTES: int = 32

#: Length of the returned persona id in hex characters. 16 hex chars = 64
#: bits of digest. Plan §D8 sizes the fleet at ≤ a few hundred operator
#: accounts; birthday-bound collision probability at n=300 is ~10^-15.
#: Bump only after running the §D8 numbers for the new fleet size.
_PERSONA_ID_HEX_LEN: int = 16

#: Minimum distinct-byte count required of a loaded salt. ``os.urandom(32)``
#: over a 256-symbol alphabet produces ≈ 28 distinct bytes with
#: overwhelming probability — P(distinct ≤ 15) is roughly 10⁻¹³. 16 is a
#: generous floor that rejects placeholder patterns (all-0x00, all-0xFF,
#: short repeating sequences from a failed-write or tarball template)
#: without flagging any realistic real-entropy salt.
_SALT_DISTINCT_BYTES_MIN: int = 16


class CorruptSaltError(RuntimeError):
    """Raised when ``persona.salt`` exists but fails integrity checks.

    Triggered by either: wrong byte length (a truncated salt would silently
    degrade ``persona_id`` to an unsalted SHA-256 over the inputs, which is
    trivially reversible), or too few distinct bytes (a placeholder salt
    like ``\\x00`` × 32 written by an ansible template / tarball-restore /
    failed-write is publicly pre-imageable for the same reason). We refuse
    to load such a file and let the caller decide whether to delete +
    regenerate (which rotates all persona_ids) or restore from backup.
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
    return _ensure_salt(path)


def _ensure_salt(final_path: Path) -> bytes:
    """Provision ``final_path`` with a validated random salt and return it.

    Durability + concurrent first-use convergence are both handled here:

    1. If the file already exists, validate-and-return it (length + entropy
       floor; see :class:`CorruptSaltError`).
    2. Otherwise, write a fresh ``os.urandom`` salt into a per-pid tmpfile
       in the same directory, ``fsync`` it, then atomically ``os.link`` the
       tmpfile to ``final_path``. The tmpfile is unlinked unconditionally in
       ``finally``.
    3. On ``os.link`` ``FileExistsError`` another process won the race —
       fall through to read the winner's salt. Because ``os.link`` is
       atomic and only ever links a fully-written + fsynced file,
       ``final_path`` is never observed in a torn or zero-byte state.

    Properties:
      * The salt on disk is either fully populated or absent — never
        partial. Crash mid-write leaves only the orphaned tmpfile, which
        is harmless (next call will not touch it; restart re-runs cleanly).
      * Concurrent first-use across processes converges on the link
        winner's salt; the loser reads it back rather than writing its own.
      * In-process callers of the race-loser still observe the same bytes
        the race-winner observed (the loser returns the read-back value),
        so historical persona_ids correlate across processes.
    """
    if final_path.exists():
        return _read_validated_salt(final_path)
    # First-use provisioning. ``mkdir(mode=0o700)`` only applies the mode
    # to a freshly-created directory; an already-existing parent (the
    # common case, since blogger/medium token writers create the same
    # ``_config_dir`` first under the operator's umask = 0o755) keeps
    # whatever permissions it had. The salt file's own 0o600 still blocks
    # cross-user reads, but follow up with an unconditional chmod on
    # POSIX so the documented 0o700 directory invariant holds. Windows
    # POSIX mode bits are not meaningful; skip the chmod there.
    final_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.platform != "win32":
        os.chmod(final_path.parent, 0o700)
    tmp_path = final_path.with_name(f"{final_path.name}.tmp.{os.getpid()}")
    salt = os.urandom(_SALT_BYTES)
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            # Loop to defend against POSIX short writes. 32 bytes to a
            # regular file is essentially never short, but treating the
            # write as a stream is the only way to actually be safe.
            written = 0
            while written < _SALT_BYTES:
                n = os.write(fd, salt[written:])
                if n == 0:
                    raise OSError(
                        f"os.write to {tmp_path} returned 0; disk full?"
                    )
                written += n
            # fsync before close so a power-loss / OOM-kill between the
            # link below and the next process's read can't leave a
            # zero-length file on disk.
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            # Atomic publish. Either ``final_path`` does not exist (we win,
            # link succeeds) or another writer already linked theirs (we
            # lose, link raises FileExistsError). No torn-file window.
            os.link(tmp_path, final_path)
            return salt
        except FileExistsError:
            # Loser path — the winner has already linked their fully-written
            # salt. Read it back so both processes observe the same bytes.
            return _read_validated_salt(final_path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def _read_validated_salt(path: Path) -> bytes:
    """Read ``path`` and require ``_SALT_BYTES`` length + distinct-byte floor.

    A truncated salt file would silently downgrade ``persona_id`` to an
    unsalted SHA-256; a constant-bytes salt (placeholder ``\\x00``-padding,
    failed-write zero-fill) would be public knowledge and equally
    pre-imageable. Both shapes raise ``CorruptSaltError`` so the operator
    notices rather than silently emitting broken persona_ids.

    Requires a regular file. A symlink to ``/dev/zero`` or another
    character device (possible on misconfigured shared CI runners or
    template fixtures) would make an unbounded ``read_bytes()`` hang the
    process; we refuse anything that is not a regular file outright.
    """
    if not path.is_file():
        raise CorruptSaltError(
            f"{path} is not a regular file (symlink to a device, fifo, "
            "or missing target?); refusing to load. Delete and regenerate "
            "or restore from backup."
        )
    data = path.read_bytes()
    if len(data) != _SALT_BYTES:
        raise CorruptSaltError(
            f"{path} has {len(data)} bytes, expected {_SALT_BYTES}; "
            "delete to regenerate (rotates all persona_ids) or restore "
            "from backup."
        )
    distinct = len(set(data))
    if distinct < _SALT_DISTINCT_BYTES_MIN:
        raise CorruptSaltError(
            f"{path} has only {distinct} distinct bytes (need "
            f"≥ {_SALT_DISTINCT_BYTES_MIN}); the salt looks like a "
            "placeholder (all-zero, all-0xFF, repeating fill). Delete to "
            "regenerate (rotates all persona_ids) or restore from backup."
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
