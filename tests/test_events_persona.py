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


def test_concurrent_first_use_falls_through_to_read(tmp_path, monkeypatch):
    # Simulate the race: another process creates the salt file between
    # ``path.exists()`` returning False and ``os.open(O_EXCL)``. The
    # second invocation must not crash — it should read the winner's
    # salt and return that.
    salt_path = tmp_path / "persona.salt"
    persona._load_salt.cache_clear()

    racer_salt = os.urandom(persona._SALT_BYTES)

    real_exists = type(salt_path).exists

    def fake_exists(self):
        if self == salt_path and not salt_path.exists():
            return False
        return real_exists(self)

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
    salt_path = tmp_path / "persona.salt"
    salt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fixed_salt = b"\xab" * persona._SALT_BYTES
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
