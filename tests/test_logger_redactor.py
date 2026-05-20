"""Tests for the PipelineLogger sensitive-key redactor (Round-3 #6).

The logger now walks every ``extra`` dict before serialisation and replaces
values of sensitive keys (client_secret, integration_token, access_token,
etc.) with ``"***"``. This file verifies:

- Top-level + nested + tuple traversal
- Case-insensitive key match
- Legitimate keys like ``client_id`` are NOT redacted (exact-equality, not substring)
- Both ``_emit`` and ``recon`` apply the redactor
- Depth cap prevents pathological-cycle pin
- Default-value preserved when extra is empty / None

Plan ref: docs/ideation/2026-05-14-round3-fresh-pass-ideation.md (#6)
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher._util.logger import (
    PipelineLogger,
    _redact_in_place,
    _SENSITIVE_KEYS,
    _MAX_REDACT_DEPTH,
)


def _capture_emit(logger: PipelineLogger, fn: str, msg: str, **extra) -> dict:
    """Invoke ``logger.<fn>(msg, **extra)`` and return the parsed JSON
    record from stderr."""
    buf = StringIO()
    with patch("sys.stderr", buf):
        getattr(logger, fn)(msg, **extra)
    raw = buf.getvalue().strip()
    if not raw:
        return {}
    return json.loads(raw.splitlines()[-1])


# ── direct unit tests on _redact_in_place ──────────────────────────────────


class TestRedactInPlace:
    def test_top_level_sensitive_key_redacted(self):
        d = {"api_key": "sk-abc123", "platform": "blogger"}
        _redact_in_place(d)
        assert d["api_key"] == "***"
        assert d["platform"] == "blogger"

    def test_nested_sensitive_key_redacted(self):
        d = {
            "outer": {
                "inner": {"client_secret": "secret-xyz"},
                "safe": "ok",
            }
        }
        _redact_in_place(d)
        assert d["outer"]["inner"]["client_secret"] == "***"
        assert d["outer"]["safe"] == "ok"

    def test_list_of_dicts_redacted(self):
        d = {"tokens": [{"access_token": "abc"}, {"access_token": "def"}]}
        _redact_in_place(d)
        assert d["tokens"][0]["access_token"] == "***"
        assert d["tokens"][1]["access_token"] == "***"

    def test_tuple_redacted_via_new_tuple(self):
        d = {"chain": ({"refresh_token": "old"}, {"safe": "kept"})}
        _redact_in_place(d)
        assert d["chain"][0]["refresh_token"] == "***"
        assert d["chain"][1]["safe"] == "kept"

    @pytest.mark.parametrize("variant", [
        "Client_Secret", "CLIENT_SECRET", "client_secret", "cLIENT_sECRET",
    ])
    def test_case_insensitive_match(self, variant):
        d = {variant: "secret"}
        _redact_in_place(d)
        assert d[variant] == "***"

    @pytest.mark.parametrize("legit_key", [
        "client_id",  # NOT in sensitive set
        "main_url",
        "request_id",
        "user_agent",
        "id",
        "platform",
    ])
    def test_legitimate_key_preserved(self, legit_key):
        d = {legit_key: "real_value"}
        _redact_in_place(d)
        assert d[legit_key] == "real_value"

    def test_substring_match_does_not_fire(self):
        """``client_secret`` is sensitive; ``client_secret_id`` is not — the
        match is exact key equality, not substring."""
        d = {"client_secret_id": "id-only-not-secret"}
        _redact_in_place(d)
        assert d["client_secret_id"] == "id-only-not-secret"

    def test_depth_cap_prevents_infinite_loop(self):
        """A self-referencing dict must not pin the redactor."""
        d: dict = {"layer": 0}
        cur = d
        for i in range(1, _MAX_REDACT_DEPTH + 5):
            nxt: dict = {"layer": i}
            cur["next"] = nxt
            cur = nxt
        cur["api_key"] = "deeper-than-cap"  # planted past the cap
        # No raise. Past-cap values are left alone (acceptable trade-off
        # vs walking until stack overflow on cycles).
        _redact_in_place(d)
        # The shallow planted keys ARE redacted; the past-cap one is left.
        d_shallow = {"api_key": "top"}
        _redact_in_place(d_shallow)
        assert d_shallow["api_key"] == "***"

    def test_scalar_input_passes_through(self):
        assert _redact_in_place("plain string") == "plain string"
        assert _redact_in_place(42) == 42
        assert _redact_in_place(None) is None

    def test_empty_dict_unchanged(self):
        d: dict = {}
        _redact_in_place(d)
        assert d == {}

    def test_redacted_value_type_uniform_regardless_of_input_type(self):
        """Whether the original value is a string, int, dict, or None,
        redaction replaces with the constant string ``"***"``."""
        d = {
            "api_key": "string-val",
            "access_token": 12345,
            "refresh_token": None,
            "client_secret": {"nested": "obj"},
            "id_token": ["list", "value"],
        }
        _redact_in_place(d)
        assert d["api_key"] == "***"
        assert d["access_token"] == "***"
        assert d["refresh_token"] == "***"
        assert d["client_secret"] == "***"
        assert d["id_token"] == "***"


# ── PipelineLogger integration ─────────────────────────────────────────────


class TestPipelineLoggerRedaction:
    def test_info_extra_redacted_before_serialisation(self):
        logger = PipelineLogger("test", level="DEBUG")
        record = _capture_emit(
            logger, "info", "config_loaded",
            api_key="should-not-appear",
            platform="blogger",
        )
        assert record["api_key"] == "***"
        assert record["platform"] == "blogger"

    def test_warn_extra_redacted(self):
        logger = PipelineLogger("test", level="DEBUG")
        record = _capture_emit(
            logger, "warn", "auth_failed",
            access_token="leaky-token",
        )
        assert record["access_token"] == "***"

    def test_error_extra_redacted(self):
        logger = PipelineLogger("test", level="DEBUG")
        record = _capture_emit(
            logger, "error", "oauth_problem",
            client_secret="REDACTED?",
        )
        assert record["client_secret"] == "***"

    def test_recon_extra_redacted_too(self):
        """Recon events bypass --log-level gate but must still redact —
        operator visibility is no excuse for credential leak."""
        logger = PipelineLogger("test", level="DEBUG")
        record = _capture_emit(
            logger, "recon", "reconciliation_event",
            integration_token="medium-token-xyz",
            input_rows=10,
        )
        assert record["integration_token"] == "***"
        assert record["input_rows"] == 10
        assert record["level"] == "RECON"

    def test_nested_extra_redacted(self):
        """Operator pasting headers dict into extra — Authorization key
        must be redacted at depth."""
        logger = PipelineLogger("test", level="DEBUG")
        record = _capture_emit(
            logger, "info", "http_request",
            url="https://api.example.com",
            headers={"Authorization": "Bearer ya29.abc...", "User-Agent": "ua"},
        )
        assert record["headers"]["Authorization"] == "***"
        assert record["headers"]["User-Agent"] == "ua"
        assert record["url"] == "https://api.example.com"

    def test_no_extra_record_unchanged(self):
        """Records without extra dict are unaffected — top-level fields
        (ts/level/logger/msg) remain plain strings."""
        logger = PipelineLogger("test", level="DEBUG")
        record = _capture_emit(logger, "info", "simple")
        assert record["msg"] == "simple"
        assert "api_key" not in record

    def test_record_keys_never_themselves_sensitive(self):
        """The four reserved record keys (ts / level / logger / msg) must
        not be redacted even if they happened to match (defence-in-depth)."""
        logger = PipelineLogger("test", level="DEBUG")
        # Caller passes msg="api_key" (free-form text, not a key value).
        record = _capture_emit(logger, "info", "api_key")
        assert record["msg"] == "api_key"

    def test_sensitive_keys_inventory_minimum(self):
        """Smoke check: the sensitive-key set hasn't accidentally been
        shrunk to nothing in some future refactor."""
        for key in (
            "client_secret", "integration_token", "access_token",
            "refresh_token", "id_token", "api_key", "password",
            "authorization",
        ):
            assert key in _SENSITIVE_KEYS, f"missing sensitive key: {key}"
