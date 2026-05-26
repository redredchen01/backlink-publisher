"""Characterization tests for ``_util/secrets.py`` (FRW 0600 token file).

Locks the 6-component credential-rotation contract documented in the module:
path re-resolution, fail-loud load, atomic 0600 write, flock-with-timeout,
microsecond orphan archive, and rotate-under-lock. Pure-additive: this module
had no dedicated test file (referenced only indirectly). No mocks for the
filesystem chain — the rotation/lock/archive behavior is only meaningful
against real files.

Backfill for the codebase-optimization backlog (O-series safe-now pattern,
sibling to ``test_exit_code_contract.py`` / ``test_webui_routes_oauth.py``).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat

import pytest

from backlink_publisher._util import secrets


@pytest.fixture()
def token_dir(tmp_path, monkeypatch):
    """Point the FRW token path at an isolated tmp config dir.

    ``frw_token_path`` re-reads ``BACKLINK_PUBLISHER_CONFIG_DIR`` on every
    call (the module's component-1 invariant), so setting the env var is
    enough to redirect every helper at the sandbox.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ── Component 1: path resolver re-reads the env var every call ──────────────


def test_token_path_honors_config_dir_env(token_dir):
    path = secrets.frw_token_path()
    assert path == token_dir / "frw-token.json"


def test_token_path_reresolves_when_env_changes(tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(a))
    assert secrets.frw_token_path() == a / "frw-token.json"
    # The path must NOT be frozen at first call — re-resolve on env change.
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(b))
    assert secrets.frw_token_path() == b / "frw-token.json"


# ── Component 2: fail-loud load ─────────────────────────────────────────────


def test_load_missing_raises_runtime_error_naming_frw_login(token_dir):
    with pytest.raises(RuntimeError) as exc:
        secrets.load_frw_token()
    assert "frw-login" in str(exc.value)


def test_load_malformed_json_raises_runtime_error(token_dir):
    path = secrets.frw_token_path()
    path.write_text("{not valid json")
    os.chmod(path, 0o600)
    with pytest.raises(RuntimeError) as exc:
        secrets.load_frw_token()
    assert "malformed" in str(exc.value)


def test_load_missing_api_key_field_raises(token_dir):
    path = secrets.frw_token_path()
    path.write_text(json.dumps({"other": "x"}))
    os.chmod(path, 0o600)
    with pytest.raises(RuntimeError) as exc:
        secrets.load_frw_token()
    assert "api_key" in str(exc.value)


def test_load_empty_api_key_raises(token_dir):
    path = secrets.frw_token_path()
    path.write_text(json.dumps({"api_key": ""}))
    os.chmod(path, 0o600)
    with pytest.raises(RuntimeError):
        secrets.load_frw_token()


def test_load_non_dict_payload_raises(token_dir):
    path = secrets.frw_token_path()
    path.write_text(json.dumps(["not", "a", "dict"]))
    os.chmod(path, 0o600)
    with pytest.raises(RuntimeError):
        secrets.load_frw_token()


def test_load_happy_returns_key(token_dir):
    secrets.write_frw_token("sk-happy-123")
    assert secrets.load_frw_token() == "sk-happy-123"


def test_load_loose_perms_warns_and_chmods(token_dir, caplog):
    """A cp-induced 0644 token must be auto-tightened to 0600, not rejected."""
    path = secrets.frw_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"api_key": "sk-loose"}))
    os.chmod(path, 0o644)

    with caplog.at_level("WARNING"):
        assert secrets.load_frw_token() == "sk-loose"

    assert _mode(path) == 0o600
    assert any("loose_perms" in r.message or "loose" in r.message.lower()
               for r in caplog.records)


# ── Component 3: atomic 0600 write ──────────────────────────────────────────


def test_write_empty_raises_value_error(token_dir):
    with pytest.raises(ValueError):
        secrets.write_frw_token("")


def test_write_whitespace_only_raises_value_error(token_dir):
    with pytest.raises(ValueError):
        secrets.write_frw_token("   \n\t ")


def test_write_strips_whitespace(token_dir):
    secrets.write_frw_token("  sk-padded  ")
    assert secrets.load_frw_token() == "sk-padded"


def test_write_bootstrap_creates_0600_file(token_dir):
    secrets.write_frw_token("sk-boot")
    path = secrets.frw_token_path()
    assert path.exists()
    assert _mode(path) == 0o600


def test_write_tightens_parent_dir_to_0700(token_dir):
    parent = secrets.frw_token_path().parent
    parent.mkdir(parents=True, exist_ok=True)
    os.chmod(parent, 0o755)
    secrets.write_frw_token("sk-parent")
    assert _mode(parent) == 0o700


def test_write_leaves_no_tmp_sibling(token_dir):
    secrets.write_frw_token("sk-clean")
    path = secrets.frw_token_path()
    leftover = list(path.parent.glob("frw-token.json.tmp"))
    assert leftover == []


# ── Components 5 + 6: rotate-under-lock with microsecond orphan archive ─────


def test_rotation_archives_old_key_and_writes_new(token_dir):
    secrets.write_frw_token("sk-old")
    secrets.write_frw_token("sk-new")

    path = secrets.frw_token_path()
    assert secrets.load_frw_token() == "sk-new"

    archives = list(path.parent.glob("frw-token.json.orphaned-*"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text())["api_key"] == "sk-old"
    assert _mode(archives[0]) == 0o600


def test_orphan_archive_suffix_is_microsecond_utc(token_dir):
    secrets.write_frw_token("sk-1")
    secrets.write_frw_token("sk-2")
    path = secrets.frw_token_path()
    archive = next(path.parent.glob("frw-token.json.orphaned-*"))
    # ``%Y%m%dT%H%M%S_%fZ`` — date T time _ microseconds Z
    suffix = archive.name.split(".orphaned-", 1)[1]
    assert re.fullmatch(r"\d{8}T\d{6}_\d{6}Z", suffix), suffix


def test_two_rotations_produce_two_distinct_archives(token_dir):
    secrets.write_frw_token("sk-1")
    secrets.write_frw_token("sk-2")
    secrets.write_frw_token("sk-3")
    path = secrets.frw_token_path()
    archives = list(path.parent.glob("frw-token.json.orphaned-*"))
    # μs precision must keep archives from colliding/overwriting.
    assert len(archives) == 2
    keys = {json.loads(a.read_text())["api_key"] for a in archives}
    assert keys == {"sk-1", "sk-2"}


# ── Component 4: flock with timeout ─────────────────────────────────────────


def test_write_aborts_when_lock_is_held(token_dir, monkeypatch):
    """A stuck peer holding the flock makes write abort (not block forever)."""
    monkeypatch.setattr(secrets, "_LOCK_TIMEOUT_S", 0.3)
    target = secrets.frw_token_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = secrets._lock_path(target)

    # Independent open file description on the same lock file → contends
    # even within this process.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError) as exc:
            secrets.write_frw_token("sk-contended")
        assert "lock" in str(exc.value).lower()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_write_succeeds_after_lock_released(token_dir, monkeypatch):
    """Once the contending lock frees, a subsequent write goes through."""
    monkeypatch.setattr(secrets, "_LOCK_TIMEOUT_S", 0.3)
    target = secrets.frw_token_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = secrets._lock_path(target)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    secrets.write_frw_token("sk-after-release")
    assert secrets.load_frw_token() == "sk-after-release"
