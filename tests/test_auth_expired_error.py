"""Tests for AuthExpiredError — Plan 2026-05-19-001 Unit 1.

Locks the contract:
- AuthExpiredError inherits from DependencyError (exit_code=3, with plan-012)
- channel is validated against CHANNELS frozenset at construction
- traversal payloads in channel reject construction (defense-in-depth)
- isinstance(exc, DependencyError) is True (catch-chain compatibility)
"""

from __future__ import annotations

import pytest

from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
    PipelineError,
    UsageError,
)
from backlink_publisher.cli._bind.channels import CHANNELS


class TestAuthExpiredErrorBasics:
    def test_inherits_from_dependency_error(self):
        exc = AuthExpiredError(channel="velog")
        assert isinstance(exc, DependencyError)
        assert isinstance(exc, PipelineError)
        assert not isinstance(exc, ExternalServiceError)

    def test_exit_code_is_three(self):
        exc = AuthExpiredError(channel="medium")
        assert exc.exit_code == 3

    def test_str_contains_channel_name(self):
        exc = AuthExpiredError(channel="blogger")
        assert "blogger" in str(exc)

    def test_channel_attribute_exposed(self):
        exc = AuthExpiredError(channel="velog")
        assert exc.channel == "velog"

    def test_reason_attribute_optional(self):
        exc = AuthExpiredError(channel="medium", reason="oauth_token_invalid")
        assert exc.reason == "oauth_token_invalid"
        assert "oauth_token_invalid" in str(exc)

    def test_reason_defaults_to_none(self):
        exc = AuthExpiredError(channel="medium")
        assert exc.reason is None


class TestChannelWhitelistEnforcement:
    @pytest.mark.parametrize("channel", sorted(CHANNELS))
    def test_known_channels_accepted(self, channel):
        exc = AuthExpiredError(channel=channel)
        assert exc.channel == channel

    def test_unknown_channel_raises_usage_error(self):
        with pytest.raises(UsageError):
            AuthExpiredError(channel="unknown")

    def test_traversal_payload_rejected(self):
        with pytest.raises(UsageError):
            AuthExpiredError(channel="../evil")

    def test_empty_channel_rejected(self):
        with pytest.raises(UsageError):
            AuthExpiredError(channel="")


class TestCatchChainCompatibility:
    """Integration: existing except DependencyError must still catch AuthExpiredError."""

    def test_caught_by_dependency_error_handler(self):
        try:
            raise AuthExpiredError(channel="velog")
        except DependencyError as exc:
            assert exc.channel == "velog"
            return
        pytest.fail("AuthExpiredError was not caught by except DependencyError")

    def test_not_caught_by_external_service_error_handler(self):
        with pytest.raises(AuthExpiredError):
            try:
                raise AuthExpiredError(channel="medium")
            except ExternalServiceError:
                pytest.fail(
                    "AuthExpiredError should NOT be caught by ExternalServiceError "
                    "(catch chain order matters)"
                )
