"""Plan 2026-05-27-001 Unit 3 — one-shot purge of orphaned credential files
for removed channels (jianshu/zhihu/cnblogs).

The startup wiring in create_app() is gated by start_scheduler (false under
pytest), so these tests exercise the standalone purge function directly against
the conftest-sandboxed config dir.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backlink_publisher.config.loader import _config_dir
from webui_store.channel_status import (
    _PURGE_SENTINEL_NAME,
    _REMOVED_CREDENTIAL_SLUGS,
    purge_removed_channel_credentials,
)


@pytest.fixture(autouse=True)
def _per_test_config_dir(tmp_path, monkeypatch):
    """Unique config dir per test — the shared conftest sandbox would otherwise
    leak the one-shot sentinel + cred files across tests in this module."""
    d = tmp_path / "cfg"
    d.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(d))
    return d


def _cred(slug: str) -> Path:
    return _config_dir() / f"{slug}-credentials.json"


def _write(p: Path, content: str = "{}") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_deletes_all_orphaned_files_and_writes_sentinel():
    for slug in _REMOVED_CREDENTIAL_SLUGS:
        _write(_cred(slug))
    purge_removed_channel_credentials()
    for slug in _REMOVED_CREDENTIAL_SLUGS:
        assert not _cred(slug).exists(), f"{slug} cred should be gone"
    assert (_config_dir() / _PURGE_SENTINEL_NAME).exists()


def test_sentinel_present_is_noop():
    (_config_dir() / _PURGE_SENTINEL_NAME).parent.mkdir(parents=True, exist_ok=True)
    (_config_dir() / _PURGE_SENTINEL_NAME).write_text("done")
    target = _cred("jianshu")
    _write(target, '{"cookies":[]}')
    purge_removed_channel_credentials()
    assert target.exists(), "sentinel present → must not touch files"


def test_no_files_present_is_clean_and_stamps():
    purge_removed_channel_credentials()
    assert (_config_dir() / _PURGE_SENTINEL_NAME).exists()


def test_partial_presence_deletes_only_present():
    _write(_cred("zhihu"))
    purge_removed_channel_credentials()
    assert not _cred("zhihu").exists()
    assert not _cred("jianshu").exists()  # absent → fine


def test_unrelated_channel_file_untouched():
    keep = _cred("csdn")
    _write(keep)
    purge_removed_channel_credentials()
    assert keep.exists(), "live channel cred must not be swept"


def test_reintroduction_safe_after_sentinel():
    # First run sweeps + stamps.
    _write(_cred("zhihu"))
    purge_removed_channel_credentials()
    assert (_config_dir() / _PURGE_SENTINEL_NAME).exists()
    # A future re-registration writes a fresh zhihu cred; second run must NOT
    # delete it (sentinel disarms the one-shot).
    _write(_cred("zhihu"), '{"cookies":["fresh"]}')
    purge_removed_channel_credentials()
    assert _cred("zhihu").exists(), "post-sentinel re-add must be safe"


def test_symlink_is_refused_not_followed(tmp_path):
    outside = tmp_path / "outside-secret.json"
    outside.write_text("victim")
    link = _cred("jianshu")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    purge_removed_channel_credentials()
    assert outside.exists(), "symlink target outside config_dir must survive"


def test_unlink_failure_logs_warning_and_continues(monkeypatch, caplog):
    _write(_cred("jianshu"))

    real_unlink = Path.unlink

    def boom(self, *a, **k):
        if self.name.endswith("-credentials.json"):
            raise OSError("EBUSY")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom)
    with caplog.at_level(logging.WARNING):
        purge_removed_channel_credentials()  # must not raise
    assert any("jianshu-credentials.json" in r.message for r in caplog.records)
    # Sentinel still written so the one-shot does not retry forever.
    assert (_config_dir() / _PURGE_SENTINEL_NAME).exists()
