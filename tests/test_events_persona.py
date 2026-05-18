"""Tests for ``backlink_publisher.events.persona``.

Test isolation: all tests redirect ``BACKLINK_PUBLISHER_CONFIG_DIR`` to
``tmp_path`` so the salt file goes into the per-test scratch tree rather
than the operator's ``~/.config/backlink-publisher/``. The ``_load_salt``
lru_cache is cleared in the autouse fixture so tests don't see each
other's salts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from backlink_publisher.events import persona


@pytest.fixture(autouse=True)
def _isolate_persona(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    persona._load_salt.cache_clear()
    yield
    persona._load_salt.cache_clear()


def test_persona_id_is_deterministic_for_same_inputs(tmp_path):
    first = persona.persona_id("blogger", "alice@example.com")
    second = persona.persona_id("blogger", "alice@example.com")
    assert first == second


def test_persona_id_is_16_hex_chars(tmp_path):
    pid = persona.persona_id("blogger", "alice@example.com")
    assert len(pid) == 16
    assert all(c in "0123456789abcdef" for c in pid)


def test_different_provider_gives_different_id(tmp_path):
    a = persona.persona_id("blogger", "alice@example.com")
    b = persona.persona_id("medium", "alice@example.com")
    assert a != b


def test_different_account_label_gives_different_id(tmp_path):
    a = persona.persona_id("blogger", "alice@example.com")
    b = persona.persona_id("blogger", "bob@example.com")
    assert a != b


def test_separator_prevents_join_collision(tmp_path):
    # ("ab", "c") and ("a", "bc") must not collide even though their
    # naive concatenation is identical. Implementation joins with NUL.
    a = persona.persona_id("ab", "c")
    b = persona.persona_id("a", "bc")
    assert a != b


def test_salt_file_created_with_mode_0600(tmp_path):
    salt_path = tmp_path / "persona.salt"
    assert not salt_path.exists()
    persona.persona_id("blogger", "alice@example.com")
    assert salt_path.exists()
    assert salt_path.stat().st_size == persona._SALT_BYTES
    if sys.platform != "win32":
        # Windows POSIX mode bits are not meaningful; skip the check.
        mode = salt_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_salt_file_parent_dir_created_with_mode_0700(tmp_path, monkeypatch):
    # Point at a nested non-existent dir so we exercise mkdir(parents=True).
    nested = tmp_path / "deep" / "config"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(nested))
    persona._load_salt.cache_clear()
    assert not nested.exists()
    persona.persona_id("blogger", "alice@example.com")
    assert nested.exists()
    if sys.platform != "win32":
        mode = nested.stat().st_mode & 0o777
        assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_persona_id_stable_across_calls_with_fresh_cache(tmp_path):
    # Mirrors the "events.db deletion does not rotate salt" contract
    # (D5): once the salt file exists, subsequent calls must read it
    # back and return the same digest even after cache eviction.
    first = persona.persona_id("blogger", "alice@example.com")
    persona._load_salt.cache_clear()
    second = persona.persona_id("blogger", "alice@example.com")
    assert first == second


def test_salt_file_not_rewritten_on_subsequent_call(tmp_path):
    salt_path = tmp_path / "persona.salt"
    persona.persona_id("blogger", "alice@example.com")
    original_bytes = salt_path.read_bytes()
    original_mtime = salt_path.stat().st_mtime_ns
    persona._load_salt.cache_clear()
    persona.persona_id("blogger", "bob@example.com")
    assert salt_path.read_bytes() == original_bytes
    assert salt_path.stat().st_mtime_ns == original_mtime


def test_salt_is_random_per_installation(tmp_path, monkeypatch):
    # Two separate config dirs must produce different salts, hence
    # different persona_ids for the same (provider, label) pair. This
    # confirms ``os.urandom`` is being used (not a constant).
    dir_a = tmp_path / "install_a"
    dir_b = tmp_path / "install_b"

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(dir_a))
    persona._load_salt.cache_clear()
    id_a = persona.persona_id("blogger", "alice@example.com")

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(dir_b))
    persona._load_salt.cache_clear()
    id_b = persona.persona_id("blogger", "alice@example.com")

    assert id_a != id_b


def test_truncated_salt_file_raises_corrupt_error(tmp_path):
    # A salt file with the wrong byte count would silently degrade
    # persona_id to an unsalted hash. Refuse to load it.
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    salt_path.write_bytes(b"\x00" * 10)  # too short
    persona._load_salt.cache_clear()
    with pytest.raises(persona.CorruptSaltError):
        persona.persona_id("blogger", "alice@example.com")


def test_zero_byte_salt_file_raises_corrupt_error(tmp_path):
    # 0-byte salt (partial write that lost everything, or a tar fixture that
    # touched the file without content). Same risk as truncation.
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    salt_path.write_bytes(b"")
    persona._load_salt.cache_clear()
    with pytest.raises(persona.CorruptSaltError):
        persona.persona_id("blogger", "alice@example.com")


def test_constant_byte_salt_rejected_as_corrupt(tmp_path):
    # A 32-byte salt of all 0x00 (or all 0xFF) passes the length check but
    # is publicly pre-imageable — an attacker who recognises the
    # placeholder pattern can recompute persona_id for any (provider,
    # label) pair without ever reading the salt. Refuse to load.
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    salt_path.write_bytes(b"\x00" * persona._SALT_BYTES)
    persona._load_salt.cache_clear()
    with pytest.raises(persona.CorruptSaltError):
        persona.persona_id("blogger", "alice@example.com")


def test_all_0xff_salt_rejected_as_corrupt(tmp_path):
    # Same risk class as the zero-fill case — failed-write padding /
    # template placeholder.
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    salt_path.write_bytes(b"\xff" * persona._SALT_BYTES)
    persona._load_salt.cache_clear()
    with pytest.raises(persona.CorruptSaltError):
        persona.persona_id("blogger", "alice@example.com")


def test_low_distinct_byte_salt_rejected(tmp_path):
    # Right at the floor: 15 distinct bytes (one below the 16 minimum)
    # must be rejected. Encodes the boundary that real random salts
    # cluster well above.
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pattern = bytes(range(15)) * 3  # 45 bytes
    salt_path.write_bytes(pattern[: persona._SALT_BYTES])  # 32 bytes, 15 distinct
    persona._load_salt.cache_clear()
    with pytest.raises(persona.CorruptSaltError):
        persona.persona_id("blogger", "alice@example.com")


def test_atomic_publish_leaves_no_tmpfile(tmp_path):
    # _ensure_salt writes to <name>.tmp.<pid> then links into place.
    # Verify the tmpfile is unlinked in the finally arm even on the
    # happy path; otherwise a long-running daemon would accumulate
    # stale tmpfiles.
    salt_path = tmp_path / "persona.salt"
    persona._load_salt.cache_clear()
    persona.persona_id("blogger", "alice@example.com")
    leftover = list(salt_path.parent.glob("persona.salt.tmp.*"))
    assert leftover == [], f"orphaned tmpfile(s): {leftover}"


def test_salt_file_size_is_exact_after_first_use(tmp_path):
    # Pin the durability contract: after first-use returns, the salt on
    # disk is exactly _SALT_BYTES long (no torn / short-written file).
    salt_path = tmp_path / "persona.salt"
    persona._load_salt.cache_clear()
    persona.persona_id("blogger", "alice@example.com")
    assert salt_path.stat().st_size == persona._SALT_BYTES


def test_parent_dir_chmod_tightens_existing_loose_perms(tmp_path, monkeypatch):
    # Pre-create the config dir at 0o755 (the default-umask shape that
    # blogger/medium token writers leave behind). _ensure_salt must
    # tighten it to 0o700, not leave it as the operator finds it.
    config_dir = tmp_path / "config"
    config_dir.mkdir(mode=0o755)
    if sys.platform == "win32":
        pytest.skip("POSIX mode bits not meaningful on Windows")
    # Confirm the precondition (umask can interfere otherwise).
    os.chmod(config_dir, 0o755)
    assert config_dir.stat().st_mode & 0o777 == 0o755
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(config_dir))
    persona._load_salt.cache_clear()
    persona.persona_id("blogger", "alice@example.com")
    mode = config_dir.stat().st_mode & 0o777
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_symlink_salt_to_dev_zero_rejected(tmp_path):
    # A misconfigured fixture / shared CI runner could symlink the salt
    # path to /dev/zero; the old read_bytes() would hang the process
    # consuming memory. The is_file() guard must reject it.
    if sys.platform == "win32":
        pytest.skip("/dev/zero is POSIX-only")
    if not Path("/dev/zero").exists():
        pytest.skip("/dev/zero unavailable on this host")
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.symlink("/dev/zero", str(salt_path))
    persona._load_salt.cache_clear()
    with pytest.raises(persona.CorruptSaltError):
        persona.persona_id("blogger", "alice@example.com")


def test_concurrent_first_use_falls_through_to_read(tmp_path, monkeypatch):
    # Simulate the race: another process creates the salt file between
    # ``path.exists()`` returning False and ``os.open(O_EXCL)``. The
    # second invocation must not crash — it should read the winner's
    # salt and return that.
    salt_path = tmp_path / "persona.salt"
    persona._load_salt.cache_clear()

    racer_salt = os.urandom(persona._SALT_BYTES)

    def write_racer_salt_then_open(*args, **kwargs):
        # Run the original os.open via the saved reference, but first
        # write the racer's salt so O_EXCL fails.
        salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = real_os_open(
            str(salt_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.write(fd, racer_salt)
        os.close(fd)
        return real_os_open(*args, **kwargs)

    real_os_open = os.open
    monkeypatch.setattr(persona.os, "open", write_racer_salt_then_open)

    loaded = persona._load_salt(salt_path)
    assert loaded == racer_salt


def test_existing_salt_file_is_read_back(tmp_path):
    # Pre-seed a known salt; persona_id must use it (not regenerate).
    # The seed must pass the distinct-byte floor (16+) — a constant-byte
    # fixture would be rejected as corrupt, which is itself a separate
    # contract pinned by test_constant_byte_salt_rejected_as_corrupt.
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fixed_salt = bytes(range(persona._SALT_BYTES))  # 0..31, 32 distinct bytes
    fd = os.open(str(salt_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, fixed_salt)
    finally:
        os.close(fd)
    persona._load_salt.cache_clear()
    pid_one = persona.persona_id("blogger", "alice@example.com")
    # Independently recompute the expected digest from the same salt.
    import hashlib

    h = hashlib.sha256()
    h.update(fixed_salt)
    h.update(b"blogger")
    h.update(b"\x00")
    h.update(b"alice@example.com")
    expected = h.hexdigest()[:16]
    assert pid_one == expected
