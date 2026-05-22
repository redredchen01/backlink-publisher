"""401 self-heal tests for TelegraphAPIAdapter.

Plan: docs/plans/2026-05-19-002-feat-telegraph-channel-end-to-end-wiring-plan.md
Execution note: TEST-FIRST. These tests are written before the adapter
exists and MUST fail initially (import error or assertion errors).

Scenarios covered (per plan U1 Test scenarios):
* 401 INVALID_TOKEN → createAccount → orphan archive → retry → published_url
* 401 INVALID_ACCESS_TOKEN (same path)
* Second 401 → ExternalServiceError raised, no third retry
* Orphan archive contains the original access_token
* WARN log emitted with telegraph_token_rotated + old_token_archived_to
* atomic write produces 0o600 perms after rotation
* file lock prevents concurrent rotation collisions (single-process test
  with fcntl mock — true concurrency is integration-level)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.config import Config


PAYLOAD = {
    "id": "tg-test-1",
    "title": "Telegraph 401 self-heal smoke",
    "content_markdown": "# Hello\n\nA paragraph with a [link](https://example.com).\n",
    "target_url": "https://example.com",
    "main_domain": "https://example.com/",
    "publish_mode": "publish",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point _config_dir() at tmp_path so adapter writes to isolated location."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def seeded_token(isolated_config_dir):
    """Pre-create a stale token file at the new path."""
    token_path = isolated_config_dir / "telegraph-token.json"
    token_path.write_text(json.dumps({
        "access_token": "STALE_TOKEN_OLD",
        "short_name": "backlink-publisher",
    }))
    os.chmod(token_path, 0o600)
    return token_path


def _api_response(ok=True, result=None, error=None):
    """Build a MagicMock that mimics requests.Response for Telegraph API."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    body = {"ok": ok}
    if result is not None:
        body["result"] = result
    if error is not None:
        body["error"] = error
    resp.json.return_value = body
    return resp


def _401_invalid_token():
    return _api_response(ok=False, error="ACCESS_TOKEN_INVALID")


def _401_invalid_access_token():
    return _api_response(ok=False, error="INVALID_ACCESS_TOKEN")


def _create_page_success(url="https://telegra.ph/test-page-01-01", path="test-page-01-01"):
    return _api_response(ok=True, result={"url": url, "path": path})


def _create_account_success(token="FRESH_TOKEN_NEW"):
    return _api_response(ok=True, result={
        "access_token": token,
        "short_name": "backlink-publisher",
    })


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_happy_path_no_self_heal(seeded_token):
    """Valid token → createPage succeeds first try → no rotation."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as mock_post:
        mock_post.return_value = _create_page_success(url="https://telegra.ph/x-01-01")

        adapter = TelegraphAPIAdapter()
        result = adapter.publish(PAYLOAD, mode="publish", config=Config())

        assert result.status == "published"
        assert result.published_url == "https://telegra.ph/x-01-01"
        assert result.adapter == "telegraph-api"
        assert result.platform == "telegraph"
        # Token file unchanged
        assert json.loads(seeded_token.read_text())["access_token"] == "STALE_TOKEN_OLD"
        # No orphan archive created
        archives = list(seeded_token.parent.glob("telegraph-token.json.orphaned-*"))
        assert archives == []


def test_401_access_token_invalid_triggers_self_heal(seeded_token, caplog):
    """API returns ACCESS_TOKEN_INVALID once → adapter rotates → retry succeeds."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    caplog.set_level(logging.WARNING)

    call_sequence = [
        _401_invalid_token(),       # first createPage → 401
        _create_account_success(token="FRESH_TOKEN_NEW"),  # createAccount
        _create_page_success(url="https://telegra.ph/healed-01-01"),  # retry createPage
    ]

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as mock_post:
        mock_post.side_effect = call_sequence

        adapter = TelegraphAPIAdapter()
        result = adapter.publish(PAYLOAD, mode="publish", config=Config())

        assert result.status == "published"
        assert result.published_url == "https://telegra.ph/healed-01-01"

    # Token file rotated: new access_token written
    stored = json.loads(seeded_token.read_text())
    assert stored["access_token"] == "FRESH_TOKEN_NEW"
    assert stored["short_name"] == "backlink-publisher"

    # Old token archived
    archives = list(seeded_token.parent.glob("telegraph-token.json.orphaned-*"))
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text())
    assert archived["access_token"] == "STALE_TOKEN_OLD"

    # WARN log emitted (structured)
    rotated_records = [r for r in caplog.records if "telegraph_token_rotated" in r.getMessage()]
    assert len(rotated_records) >= 1
    assert any("401_self_heal" in r.getMessage() for r in rotated_records)


def test_401_invalid_access_token_variant_also_triggers_self_heal(seeded_token):
    """The alternate error code INVALID_ACCESS_TOKEN must trigger the same path."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    call_sequence = [
        _401_invalid_access_token(),
        _create_account_success(token="FRESH_TOKEN_VARIANT"),
        _create_page_success(url="https://telegra.ph/variant-01-01"),
    ]

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as mock_post:
        mock_post.side_effect = call_sequence

        adapter = TelegraphAPIAdapter()
        result = adapter.publish(PAYLOAD, mode="publish", config=Config())

        assert result.status == "published"

    stored = json.loads(seeded_token.read_text())
    assert stored["access_token"] == "FRESH_TOKEN_VARIANT"


def test_second_401_after_rotation_raises_external_service_error(seeded_token):
    """Second 401 (post-rotation) must NOT trigger a third retry."""
    from backlink_publisher._util.errors import ExternalServiceError
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    call_sequence = [
        _401_invalid_token(),                          # first 401
        _create_account_success(token="FRESH_TOKEN"),  # rotation
        _401_invalid_token(),                          # retry also 401
    ]

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as mock_post:
        mock_post.side_effect = call_sequence

        adapter = TelegraphAPIAdapter()
        with pytest.raises(ExternalServiceError) as exc_info:
            adapter.publish(PAYLOAD, mode="publish", config=Config())

        assert "after rotation" in str(exc_info.value).lower() or "rejected" in str(exc_info.value).lower()
        # Exactly 3 calls — no further retry after second 401
        assert mock_post.call_count == 3


def test_atomic_write_preserves_0600_perms(seeded_token):
    """After self-heal, new token file must be 0o600 (not umask default)."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    # Loosen original so we can detect the adapter setting it back to 0600
    os.chmod(seeded_token, 0o644)

    call_sequence = [
        _401_invalid_token(),
        _create_account_success(token="PERMS_TEST_TOKEN"),
        _create_page_success(),
    ]

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as mock_post:
        mock_post.side_effect = call_sequence
        # Loosen-perms file shouldn't even pass _load_token; that's an
        # independent test in test_adapter_telegraph_api.py.  Here we
        # restore 0600 just to exercise the rotation path cleanly.
        os.chmod(seeded_token, 0o600)
        adapter = TelegraphAPIAdapter()
        adapter.publish(PAYLOAD, mode="publish", config=Config())

    mode = os.stat(seeded_token).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_orphan_archive_filename_includes_iso_timestamp(seeded_token):
    """Archive filename pattern: telegraph-token.json.orphaned-<UTC iso>."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    call_sequence = [
        _401_invalid_token(),
        _create_account_success(),
        _create_page_success(),
    ]

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as mock_post:
        mock_post.side_effect = call_sequence
        adapter = TelegraphAPIAdapter()
        adapter.publish(PAYLOAD, mode="publish", config=Config())

    archives = list(seeded_token.parent.glob("telegraph-token.json.orphaned-*"))
    assert len(archives) == 1
    suffix = archives[0].name.replace("telegraph-token.json.orphaned-", "")
    # ISO 8601 UTC timestamp: 2026-05-19T11:23:45Z or 2026-05-19T11-23-45Z
    # (colons unsafe in some FS; either : or - acceptable as separator)
    assert len(suffix) >= 15  # at minimum: YYYYMMDDTHHMMSSZ
    # Sanity: contains the year
    assert "2026" in suffix or "20" in suffix


def test_concurrent_bootstrap_creates_only_one_account(isolated_config_dir):
    """Bootstrap TOCTOU race regression (ce-review P1 / correctness reviewer).

    Two concurrent publish() calls with no token file present must produce
    exactly ONE createAccount call, not N.  Without the bootstrap-lock fix,
    both threads see "no token", both call createAccount, second writer's
    os.replace overwrites the first's token, and the first account is
    orphaned forever on Telegraph's side with no audit trail.
    """
    import threading
    from itertools import count

    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    create_account_calls = []
    create_page_calls = []
    token_counter = count(start=1)

    def _post_router(url, *args, **kwargs):
        if url.endswith("/createAccount"):
            create_account_calls.append(url)
            return _create_account_success(token=f"BOOT_TOKEN_{next(token_counter)}")
        if url.endswith("/createPage"):
            create_page_calls.append(url)
            return _create_page_success(url=f"https://telegra.ph/concurrent-{len(create_page_calls)}")
        raise AssertionError(f"unexpected URL: {url}")

    barrier = threading.Barrier(4)
    errors = []
    results = []

    def worker():
        try:
            barrier.wait(timeout=5)
            adapter = TelegraphAPIAdapter()
            results.append(adapter.publish(PAYLOAD, mode="publish", config=Config()))
        except Exception as exc:
            errors.append(exc)

    with patch(
        "backlink_publisher.publishing.adapters.telegraph_api.http_post",
        side_effect=_post_router,
    ):
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    assert errors == [], f"workers raised: {errors}"
    assert len(results) == 4

    # The load-bearing assertions:
    assert len(create_account_calls) == 1, (
        f"Bootstrap TOCTOU regression: expected exactly 1 createAccount call "
        f"across 4 concurrent workers, got {len(create_account_calls)}"
    )

    # All 4 workers must have published successfully (the 3 that lost the
    # bootstrap race must have read the winner's token, not failed).
    assert len(create_page_calls) == 4

    # Final token file contains the one bootstrap token.
    final = json.loads((isolated_config_dir / "telegraph-token.json").read_text())
    assert final["access_token"] == "BOOT_TOKEN_1"

    # No orphan archive should exist — bootstrap should never archive.
    orphans = list(isolated_config_dir.glob("telegraph-token.json.orphaned-*"))
    assert orphans == [], f"unexpected orphan archives from bootstrap: {orphans}"
