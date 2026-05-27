"""Meta-tests for the Layer 3 credential tripwire (Plan 2026-05-27-005 Unit 7).

These tests exercise the tripwire logic in isolation by driving
snapshot_protected_files / check_protected_files with a controlled
fake-real-root — they never touch the operator's actual credential files.

The meta-tests verify:
  1. watch_root != sandbox assertion fires correctly
  2. plant-a-byte: a single byte change is detected with relative filename,
     no contents in the failure message
  3. excluded paths (chrome-profile, -shm, -journal) do not trigger
  4. events.db WAL-based checkpoint churn does not false-positive
  5. security: no SHA-256 digest in failure output; no absolute paths
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import helpers from conftest (tests/ is not a package — from conftest import)
# ---------------------------------------------------------------------------
from conftest import (
    REAL_CONFIG_ROOT,
    _sandbox_config_dir as _SANDBOX_CONFIG_DIR,
    check_protected_files,
    snapshot_protected_files,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_root(tmp_path: Path) -> tuple[Path, Path]:
    """Return (config_root, cache_root) inside tmp_path."""
    config_root = tmp_path / "fake-config" / "backlink-publisher"
    cache_root = tmp_path / "fake-cache" / "backlink-publisher"
    config_root.mkdir(parents=True)
    cache_root.mkdir(parents=True)
    return config_root, cache_root


def _write_protected(root: Path, name: str, content: bytes = b"secret") -> Path:
    """Write a file with a protected name into root and return its path."""
    path = root / name
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_watch_root_is_not_sandbox() -> None:
    """REAL_CONFIG_ROOT must differ from the sandbox config dir.

    If this fails, Unit 3's HOME redirect did not capture real roots before
    running — the tripwire would silently watch the sandbox and become a no-op.
    """
    assert REAL_CONFIG_ROOT != _SANDBOX_CONFIG_DIR, (
        f"REAL_CONFIG_ROOT ({REAL_CONFIG_ROOT!r}) == sandbox config dir "
        f"({_SANDBOX_CONFIG_DIR!r}).  Unit 3's HOME redirect must capture "
        f"real roots via pwd.getpwuid BEFORE overwriting HOME."
    )


def test_plant_a_byte_detected(tmp_path: Path) -> None:
    """Anti-no-op: one-byte write to a protected file is detected.

    Verifies that check_protected_files() returns the relative filename of
    the modified file — no SHA-256, no contents, no absolute paths.
    """
    config_root, cache_root = _make_fake_root(tmp_path)
    cred_file = _write_protected(config_root, "llm-settings.json", b'{"api_key": "sk-original"}')

    initial = snapshot_protected_files(config_root, cache_root)

    # Plant one byte.
    cred_file.write_bytes(b'{"api_key": "sk-modified"}')

    changed = check_protected_files(initial, config_root, cache_root)

    assert "llm-settings.json" in changed, (
        f"Modified 'llm-settings.json' was not detected. changed={changed!r}"
    )
    # Security: the change report must not contain the raw digest or content.
    report = str(changed)
    assert "sk-original" not in report
    assert "sk-modified" not in report
    # SHA-256 hex digest strings are 64 chars of hex — no bare digest in the output.
    for item in changed:
        assert len(item) != 64, f"Changed item looks like a raw SHA-256 digest: {item!r}"


def test_no_change_not_detected(tmp_path: Path) -> None:
    """Unchanged file produces no alert."""
    config_root, cache_root = _make_fake_root(tmp_path)
    _write_protected(config_root, "config.toml", b"[general]\nname = 'bp'")

    initial = snapshot_protected_files(config_root, cache_root)
    changed = check_protected_files(initial, config_root, cache_root)

    assert changed == [], f"Unchanged files triggered false alarm: {changed!r}"


def test_new_protected_file_detected(tmp_path: Path) -> None:
    """A protected file that appears after the initial snapshot is detected."""
    config_root, cache_root = _make_fake_root(tmp_path)

    initial = snapshot_protected_files(config_root, cache_root)

    # Add a new token file.
    (config_root / "newchan-token.json").write_bytes(b'{"token": "abc"}')

    changed = check_protected_files(initial, config_root, cache_root)
    assert any("newchan-token.json" in c for c in changed), (
        f"Newly created 'newchan-token.json' was not detected. changed={changed!r}"
    )


def test_deleted_protected_file_detected(tmp_path: Path) -> None:
    """A protected file that disappears after the initial snapshot is detected."""
    config_root, cache_root = _make_fake_root(tmp_path)
    cred_file = _write_protected(config_root, "frw-token.json", b'{"token": "frw"}')

    initial = snapshot_protected_files(config_root, cache_root)
    cred_file.unlink()

    changed = check_protected_files(initial, config_root, cache_root)
    assert any("frw-token.json" in c for c in changed), (
        f"Deleted 'frw-token.json' was not detected. changed={changed!r}"
    )


def test_excluded_shm_journal_ignored(tmp_path: Path) -> None:
    """WAL sidecars (-shm, -journal) are excluded from the byte-hash."""
    config_root, cache_root = _make_fake_root(tmp_path)
    # Place fake WAL sidecar files — they must NOT be tracked.
    (config_root / "events.db-shm").write_bytes(b"wal-index-sidecar")
    (config_root / "events.db-journal").write_bytes(b"journal-data")

    initial = snapshot_protected_files(config_root, cache_root)
    # Modify the sidecars.
    (config_root / "events.db-shm").write_bytes(b"modified-wal-index")
    (config_root / "events.db-journal").write_bytes(b"modified-journal")

    changed = check_protected_files(initial, config_root, cache_root)
    sidecar_names = [c for c in changed if c.endswith(("-shm", "-journal"))]
    assert sidecar_names == [], (
        f"WAL sidecar changes should be excluded but were reported: {sidecar_names!r}"
    )


def test_chrome_profile_subtree_excluded_from_hash(tmp_path: Path) -> None:
    """Files inside real-chrome-profile/ are excluded from the byte-hash."""
    config_root, cache_root = _make_fake_root(tmp_path)
    profile_dir = config_root / "real-chrome-profile" / "Default"
    profile_dir.mkdir(parents=True)
    cookie_file = profile_dir / "Cookies"
    cookie_file.write_bytes(b"initial-cookie-db")

    initial = snapshot_protected_files(config_root, cache_root)
    # Modify a file inside the profile — this must NOT trigger the byte-hash.
    cookie_file.write_bytes(b"modified-cookie-db")

    changed = check_protected_files(initial, config_root, cache_root)
    # No byte-hash hit inside the profile dir.
    hash_hits = [c for c in changed if "real-chrome-profile" in c and "grew" not in c]
    assert hash_hits == [], (
        f"Chrome profile byte-hash should be excluded. Got: {hash_hits!r}"
    )


def test_chrome_profile_growth_detected(tmp_path: Path) -> None:
    """The coarse presence/size tripwire detects a growing chrome-profile subtree."""
    config_root, cache_root = _make_fake_root(tmp_path)

    initial = snapshot_protected_files(config_root, cache_root)

    # Create the profile dir and write a file (simulates instant_web.py mkdir).
    profile_dir = config_root / "real-chrome-profile" / "Default"
    profile_dir.mkdir(parents=True)
    (profile_dir / "Cookies").write_bytes(b"x" * 1024)

    changed = check_protected_files(initial, config_root, cache_root)
    assert any("real-chrome-profile" in c for c in changed), (
        f"Chrome profile growth was not detected. changed={changed!r}"
    )


def test_events_db_wal_logical_fingerprint(tmp_path: Path) -> None:
    """events.db logical fingerprint: WAL checkpoint churn is not a false-positive.

    A checkpoint moves committed rows from -wal into the main db with no
    logical change — the sorted-rows fingerprint must stay stable.

    This test verifies the snapshot is consistent across re-reads rather
    than testing the actual SQLite WAL mechanism (which would require a live
    db under write pressure).
    """
    config_root, cache_root = _make_fake_root(tmp_path)
    db_path = config_root / "events.db"

    # Create a minimal events.db.
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT)")
    conn.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute("INSERT INTO events VALUES (1, 'publish')")
    conn.execute("INSERT INTO articles VALUES (1, 'https://example.com')")
    conn.commit()
    conn.close()

    initial = snapshot_protected_files(config_root, cache_root)
    # Re-snapshot without changing anything.
    second = snapshot_protected_files(config_root, cache_root)

    assert initial == second, (
        "Two consecutive snapshots of an unchanged events.db differ — "
        "the logical fingerprint is not stable across reads."
    )


def test_events_db_change_detected(tmp_path: Path) -> None:
    """A real write to events.db (not just -wal churn) is detected."""
    config_root, cache_root = _make_fake_root(tmp_path)
    db_path = config_root / "events.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT)")
    conn.execute("INSERT INTO events VALUES (1, 'publish')")
    conn.commit()
    conn.close()

    initial = snapshot_protected_files(config_root, cache_root)

    # Append a new row (simulates a real write).
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO events VALUES (2, 'verify')")
    conn.commit()
    conn.close()

    changed = check_protected_files(initial, config_root, cache_root)
    assert any("events.db" in c for c in changed), (
        f"A new events.db row was not detected. changed={changed!r}"
    )


def test_snapshot_output_contains_no_raw_sha256(tmp_path: Path) -> None:
    """The changed-files report must not expose raw SHA-256 digest strings.

    A 64-character hex string in the output would be a verification oracle
    for known-format secrets (e.g., llm-settings.json api_key).
    """
    config_root, cache_root = _make_fake_root(tmp_path)
    _write_protected(config_root, "llm-settings.json", b'{"api_key": "sk-abc"}')

    initial = snapshot_protected_files(config_root, cache_root)
    (config_root / "llm-settings.json").write_bytes(b'{"api_key": "sk-xyz"}')

    changed = check_protected_files(initial, config_root, cache_root)

    for item in changed:
        # A raw SHA-256 hex digest is exactly 64 lowercase hex chars.
        is_sha256 = len(item) == 64 and all(c in "0123456789abcdef" for c in item)
        assert not is_sha256, (
            f"check_protected_files returned a raw SHA-256 digest: {item!r}. "
            f"Only relative filenames should appear in the output."
        )


def test_non_protected_file_ignored(tmp_path: Path) -> None:
    """A non-protected file (no glob match) does not appear in the snapshot."""
    config_root, cache_root = _make_fake_root(tmp_path)
    # Write a file that does NOT match any PROTECTED_GLOB.
    (config_root / "README.txt").write_bytes(b"not a credential")

    initial = snapshot_protected_files(config_root, cache_root)
    (config_root / "README.txt").write_bytes(b"modified readme")

    changed = check_protected_files(initial, config_root, cache_root)
    readme_hits = [c for c in changed if "README" in c]
    assert readme_hits == [], (
        f"Non-protected 'README.txt' should be ignored. Got: {readme_hits!r}"
    )
