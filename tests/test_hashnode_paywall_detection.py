"""Unit 8: Hashnode paywall detection tests (Plan 003 Phase 2)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.hashnode import (
    HashnodeAPIAdapter,
    _probe_hashnode_paywall,
    _paywall_cache,
)


def _mock_pro_tier_response():
    """Mock response for a Pro-tier Hashnode account."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "me": {
                "publication": {
                    "id": "pub_pro_123",
                    "name": "My Pro Publication",
                }
            }
        }
    }
    return resp


def _mock_free_tier_response():
    """Mock response for a free-tier Hashnode account (publication=null)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "me": {
                "publication": None
            }
        }
    }
    return resp


def _mock_network_error():
    """Raise a requests exception to simulate network failure."""
    import requests
    return requests.ConnectionError("Network unreachable")


@pytest.fixture(autouse=True)
def clear_paywall_cache():
    """Clear the module-level paywall cache between tests."""
    _paywall_cache.clear()
    yield
    _paywall_cache.clear()


class TestProbeHashnodePaywall:
    def test_pro_tier_returns_none(self):
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=_mock_pro_tier_response()):
            result = _probe_hashnode_paywall("pro_token")
        assert result is None

    def test_free_tier_returns_error_message(self):
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=_mock_free_tier_response()):
            result = _probe_hashnode_paywall("free_token")
        assert result is not None
        assert "Pro plan" in result
        assert "2026-05-13" in result
        assert "hashnode.com/changelog" in result

    def test_network_error_returns_none(self):
        import requests as _r
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", side_effect=_r.ConnectionError("timeout")):
            result = _probe_hashnode_paywall("any_token")
        assert result is None

    def test_non_200_returns_none(self):
        resp = MagicMock()
        resp.status_code = 401
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=resp):
            result = _probe_hashnode_paywall("bad_token")
        assert result is None

    def test_5xx_returns_none(self):
        resp = MagicMock()
        resp.status_code = 503
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=resp):
            result = _probe_hashnode_paywall("token")
        assert result is None

    def test_malformed_json_returns_none(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("bad json")
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=resp):
            result = _probe_hashnode_paywall("token")
        assert result is None

    def test_cache_hit_no_second_request(self):
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=_mock_pro_tier_response()) as mock_post:
            _probe_hashnode_paywall("cached_token")
            _probe_hashnode_paywall("cached_token")
        assert mock_post.call_count == 1

    def test_cache_ttl_expired_re_probes(self):
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=_mock_pro_tier_response()) as mock_post:
            _probe_hashnode_paywall("ttl_token")

        import hashlib
        token_hash = hashlib.sha256(b"ttl_token").hexdigest()
        # Force TTL expiry by backdating the cache entry
        result, _ = _paywall_cache[token_hash]
        _paywall_cache[token_hash] = (result, time.monotonic() - 400)

        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=_mock_pro_tier_response()) as mock_post2:
            _probe_hashnode_paywall("ttl_token")
        assert mock_post2.call_count == 1

    def test_different_tokens_probe_separately(self):
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=_mock_pro_tier_response()) as mock_post:
            _probe_hashnode_paywall("token_a")
            _probe_hashnode_paywall("token_b")
        assert mock_post.call_count == 2

    def test_missing_me_field_returns_none(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {}}
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=resp):
            result = _probe_hashnode_paywall("token")
        assert result is None

    def test_graphql_errors_only_returns_none(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "errors": [{"message": "Unauthorized"}]
        }
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=resp):
            result = _probe_hashnode_paywall("token")
        assert result is None


class TestHashnodePublishPaywallIntegration:
    """Integration: paywall probe wired into HashnodeAPIAdapter.publish()."""

    @pytest.fixture
    def config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        cfg = MagicMock()
        cfg.hashnode = MagicMock()
        cfg.hashnode.publication_id = "pub_123"
        cfg.hashnode_token_path = tmp_path / "hashnode-token.json"
        cfg.hashnode_token_path.write_text(json.dumps({"token": "hn_test_pat"}))
        return cfg

    def _mock_publish_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "data": {
                "publishPost": {
                    "post": {
                        "id": "p1",
                        "slug": "test-post",
                        "url": "https://myname.hashnode.dev/test-post",
                    }
                }
            }
        }
        return resp

    def test_pro_tier_publish_succeeds(self, config):
        adapter = HashnodeAPIAdapter()
        # First call = paywall probe (pro tier), second = publishPost mutation
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post") as mock_post:
            mock_post.side_effect = [
                _mock_pro_tier_response(),
                self._mock_publish_response(),
            ]
            result = adapter.publish(
                {"title": "Test", "id": "row1"},
                mode="live",
                config=config,
            )
        assert result.status == "published"
        assert "hashnode" in result.published_url or "hashnode.dev" in result.published_url

    def test_free_tier_raises_external_service_error(self, config):
        adapter = HashnodeAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", return_value=_mock_free_tier_response()):
            with pytest.raises(ExternalServiceError) as exc_info:
                adapter.publish(
                    {"title": "Test"},
                    mode="live",
                    config=config,
                )
        assert "Pro plan" in str(exc_info.value)
        assert "2026-05-13" in str(exc_info.value)

    def test_free_tier_does_not_call_publish_mutation(self, config):
        adapter = HashnodeAPIAdapter()
        post_calls = []
        def mock_post(*args, **kwargs):
            body = kwargs.get("json", {})
            query = body.get("query", "")
            post_calls.append(query)
            return _mock_free_tier_response()

        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", side_effect=mock_post):
            with pytest.raises(ExternalServiceError):
                adapter.publish(
                    {"title": "Test"},
                    mode="live",
                    config=config,
                )
        # Only the probe query should have been called, not publishPost
        assert len(post_calls) == 1
        assert "publication" in post_calls[0]
        assert "publishPost" not in post_calls[0]

    def test_draft_mode_skips_paywall_probe(self, config):
        adapter = HashnodeAPIAdapter()
        with patch("backlink_publisher.publishing.adapters.hashnode.http_post") as mock_post:
            result = adapter.publish(
                {"title": "Draft"},
                mode="draft",
                config=config,
            )
        assert result.status == "drafted"
        # Draft mode returns before paywall probe
        assert mock_post.call_count == 0

    def test_cache_prevents_double_probe_in_same_run(self, config):
        adapter = HashnodeAPIAdapter()
        post_calls = []
        def mock_post(*args, **kwargs):
            body = kwargs.get("json", {})
            query = body.get("query", "")
            post_calls.append(query)
            if "publication" in query:
                return _mock_pro_tier_response()
            return self._mock_publish_response()

        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", side_effect=mock_post):
            adapter.publish({"title": "Row 1"}, mode="live", config=config)
            # Second publish with same token — probe should be cached
            adapter.publish({"title": "Row 2"}, mode="live", config=config)

        probe_calls = [q for q in post_calls if "publication" in q]
        publish_calls = [q for q in post_calls if "publishPost" in q]
        assert len(probe_calls) == 1  # cached on second call
        assert len(publish_calls) == 2

    def test_network_error_probe_does_not_block_publish(self, config):
        """When probe fails with network error, publish still proceeds."""
        adapter = HashnodeAPIAdapter()
        import requests as _r
        post_calls = []
        def mock_post(*args, **kwargs):
            body = kwargs.get("json", {})
            query = body.get("query", "")
            post_calls.append(query)
            if "publication" in query:
                raise _r.ConnectionError("probe timeout")
            return self._mock_publish_response()

        with patch("backlink_publisher.publishing.adapters.hashnode.http_post", side_effect=mock_post):
            result = adapter.publish(
                {"title": "Should succeed"},
                mode="live",
                config=config,
            )
        assert result.status == "published"

    def test_available_contract_unchanged(self, config):
        """available() must not change — paywall probe is in publish() only."""
        # available() only checks config + token file existence
        assert HashnodeAPIAdapter.available(config) is True
