"""TelegraphAPIAdapter happy-path + edge + error tests.

Companion to ``test_adapter_telegraph_api_self_heal.py`` which covers
the 401 INVALID_TOKEN recovery path in isolation.

Plan: docs/plans/2026-05-19-002-feat-telegraph-channel-end-to-end-wiring-plan.md
U1 Test scenarios mapping → test functions below.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError


PAYLOAD = {
    "id": "tg-happy-1",
    "title": "Test Telegraph Post",
    "content_markdown": "# Heading\n\nA [link](https://example.com) in a paragraph.\n",
    "target_url": "https://example.com",
    "main_domain": "https://example.com/",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _api_response(ok=True, result=None, error=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    body = {"ok": ok}
    if result is not None:
        body["result"] = result
    if error is not None:
        body["error"] = error
    resp.json.return_value = body
    return resp


def _ok_page(url="https://telegra.ph/test-page-01-01"):
    return _api_response(ok=True, result={"url": url, "path": url.rsplit("/", 1)[-1]})


def _ok_account(token="FRESH_TOKEN"):
    return _api_response(ok=True, result={
        "access_token": token,
        "short_name": "backlink-publisher",
    })


def _seed_token(config_dir: Path, access_token="TKN", short_name="backlink-publisher"):
    p = config_dir / "telegraph-token.json"
    p.write_text(json.dumps({"access_token": access_token, "short_name": short_name}))
    os.chmod(p, 0o600)
    return p


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


# ── Happy path ────────────────────────────────────────────────────────────────


def test_happy_publish_returns_published_url(isolated_config_dir):
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    _seed_token(isolated_config_dir)
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.return_value = _ok_page(url="https://telegra.ph/happy-01-01")
        result = TelegraphAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())

    assert result.status == "published"
    assert result.published_url == "https://telegra.ph/happy-01-01"
    assert result.draft_url == ""
    assert result.adapter == "telegraph-api"
    assert result.platform == "telegraph"


def test_happy_draft_mode_returns_draft_url(isolated_config_dir):
    """Telegraph has no native draft state — we expose the URL as draft_url."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    _seed_token(isolated_config_dir)
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.return_value = _ok_page(url="https://telegra.ph/draft-01-01")
        result = TelegraphAPIAdapter().publish(PAYLOAD, mode="draft", config=Config())

    assert result.status == "drafted"
    assert result.draft_url == "https://telegra.ph/draft-01-01"
    assert result.published_url == ""


def test_token_bootstrap_when_no_file_exists(isolated_config_dir):
    """No token file → adapter calls createAccount, writes 0o600, publishes."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    # Sanity: no token file pre-test
    assert not (isolated_config_dir / "telegraph-token.json").exists()

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.side_effect = [
            _ok_account(token="BOOTSTRAP_TOKEN"),
            _ok_page(url="https://telegra.ph/bootstrap-01-01"),
        ]
        result = TelegraphAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())

    assert result.published_url == "https://telegra.ph/bootstrap-01-01"
    token_file = isolated_config_dir / "telegraph-token.json"
    assert token_file.exists()
    assert json.loads(token_file.read_text())["access_token"] == "BOOTSTRAP_TOKEN"
    assert (os.stat(token_file).st_mode & 0o777) == 0o600


def test_token_schema_keys_are_exactly_access_token_and_short_name(isolated_config_dir):
    """Schema lock-in: file MUST NOT contain extra fields (parity with spike)."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.side_effect = [_ok_account(token="SCHEMA_TOKEN"), _ok_page()]
        TelegraphAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())

    written = json.loads((isolated_config_dir / "telegraph-token.json").read_text())
    assert set(written.keys()) == {"access_token", "short_name"}


# ── Backward-compat migration (legacy phase0 filename) ───────────────────────


def test_legacy_phase0_token_migrates_to_canonical_name(isolated_config_dir):
    """Only ``telegraph-phase0-token.json`` exists → adapter migrates it."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    legacy = isolated_config_dir / "telegraph-phase0-token.json"
    legacy.write_text(json.dumps({"access_token": "LEGACY_TKN", "short_name": "x"}))
    os.chmod(legacy, 0o600)

    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.return_value = _ok_page()
        TelegraphAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())

    canonical = isolated_config_dir / "telegraph-token.json"
    assert canonical.exists()
    assert json.loads(canonical.read_text())["access_token"] == "LEGACY_TKN"
    assert not legacy.exists(), "legacy file should be removed after migration"


# ── Edge cases ────────────────────────────────────────────────────────────────


def test_empty_markdown_raises_external_service_error(isolated_config_dir):
    """Empty content → fail before any network call."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    _seed_token(isolated_config_dir)
    payload = {**PAYLOAD, "content_markdown": ""}
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        with pytest.raises(ExternalServiceError, match="empty"):
            TelegraphAPIAdapter().publish(payload, mode="publish", config=Config())
        assert p.call_count == 0, "must not hit Telegraph API on empty payload"


def test_oversize_payload_rejected_before_network(isolated_config_dir):
    """Pre-flight 60 KB budget check fires before createPage call."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    _seed_token(isolated_config_dir)
    huge_md = "Padding " * 12_000  # ~96 KB markdown → ~96 KB JSON nodes
    payload = {**PAYLOAD, "content_markdown": huge_md}
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        with pytest.raises(ExternalServiceError, match="60KB|budget|exceeds"):
            TelegraphAPIAdapter().publish(payload, mode="publish", config=Config())
        assert p.call_count == 0


def test_markdown_with_unsupported_html_publishes_via_unwrap(isolated_config_dir):
    """Unsupported tags are unwrapped by markdown_to_telegraph_nodes; publish OK."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    _seed_token(isolated_config_dir)
    md = "Has <table><tr><td>inner [link](https://x.com)</td></tr></table> tag.\n"
    payload = {**PAYLOAD, "content_markdown": md}
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.return_value = _ok_page()
        result = TelegraphAPIAdapter().publish(payload, mode="publish", config=Config())
    assert result.status == "published"


# ── Token file perms guard ────────────────────────────────────────────────────


def test_token_file_with_loose_perms_raises_dependency_error(isolated_config_dir):
    """Token file 0644 → DependencyError suggesting chmod."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    p = _seed_token(isolated_config_dir)
    os.chmod(p, 0o644)
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as post:
        with pytest.raises(DependencyError, match="0600"):
            TelegraphAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())
        assert post.call_count == 0


# ── Network / transient errors ────────────────────────────────────────────────


def _http_error(status):
    """Build a requests.HTTPError carrying a response with the given status."""
    resp = MagicMock()
    resp.status_code = status
    err = requests.HTTPError(f"HTTP {status}")
    err.response = resp
    return err


def test_network_timeout_propagates_as_external_service_error(isolated_config_dir):
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    _seed_token(isolated_config_dir)
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.side_effect = requests.ConnectTimeout("timed out")
        with pytest.raises(ExternalServiceError, match="network"):
            TelegraphAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())


def test_429_response_raises_external_service_error(isolated_config_dir):
    """createPage returns ok=false with rate-limit marker → ExternalServiceError."""
    from backlink_publisher.publishing.adapters.telegraph_api import TelegraphAPIAdapter

    _seed_token(isolated_config_dir)
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        p.return_value = _api_response(ok=False, error="FLOOD_WAIT_X")
        with pytest.raises(ExternalServiceError, match="rejected"):
            TelegraphAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())


# ── Verification helper ──────────────────────────────────────────────────────


def test_verify_telegraph_setup_passes_when_token_present(isolated_config_dir):
    from backlink_publisher.publishing.adapters.telegraph_api import verify_telegraph_setup

    _seed_token(isolated_config_dir)
    # Should not raise
    verify_telegraph_setup(Config())


def test_verify_telegraph_setup_silently_ok_when_token_missing(isolated_config_dir):
    """No token + writable config_dir → verify passes (adapter will bootstrap).

    Per plan: verify must NOT probe createAccount endpoint (avoids
    coupling adapter health to network state).
    """
    from backlink_publisher.publishing.adapters.telegraph_api import verify_telegraph_setup

    # No token seeded, no network mock — should NOT make a request.
    with patch("backlink_publisher.publishing.adapters.telegraph_api.http_post") as p:
        verify_telegraph_setup(Config())
        assert p.call_count == 0, "verify must not hit the network"
