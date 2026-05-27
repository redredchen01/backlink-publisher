"""Unit 1 — typed-error envelope + shared chokepoint (plan 2026-05-27-004).

Covers the round-trip serialize/parse contract, the errors.py chokepoint
emission, and the parser's resilience to surrounding banner / RECON lines.
"""

from __future__ import annotations

import pytest

from backlink_publisher._util import errors
from backlink_publisher._util.error_envelope import SENTINEL, ErrorEnvelope, parse


# --- round-trip -----------------------------------------------------------

def test_serialize_parse_round_trip():
    env = ErrorEnvelope(error_class="AuthExpiredError", exit_code=3, message="msg")
    line = env.serialize()
    assert line.startswith(SENTINEL)
    assert parse(line) == env


def test_parse_returns_none_without_sentinel():
    assert parse("just some stderr text\nwith no envelope") is None


def test_parse_finds_envelope_amid_banner_lines():
    env = ErrorEnvelope(error_class="ContentRejectedError", exit_code=3, message="boom")
    stderr = (
        "[validate-backlinks] effective config:\n"
        "  config_dir: /tmp/x\n"
        "  log_level: INFO\n"
        f"{env.serialize()}\n"
        "run_id: abc123\n"
    )
    assert parse(stderr) == env


def test_parse_returns_last_envelope_when_multiple():
    first = ErrorEnvelope("UsageError", 1, "first")
    last = ErrorEnvelope("InternalError", 5, "last")
    assert parse(f"{first.serialize()}\n{last.serialize()}") == last


def test_message_with_newlines_and_sentinel_substring_round_trips():
    msg = f"line one\nline two mentioning {SENTINEL} inline\nline three"
    env = ErrorEnvelope("InputValidationError", 2, msg)
    line = env.serialize()
    assert "\n" not in line  # stays a single line (newlines JSON-escaped)
    assert parse(line) == env


def test_parse_skips_malformed_sentinel_line():
    assert parse(f"{SENTINEL} this is not json") is None
    assert parse(f"{SENTINEL} [1,2,3]") is None  # valid json, not a dict


# --- errors.py chokepoint emission ---------------------------------------

def test_handle_error_emits_envelope_with_specific_class(capsys):
    exc = errors.InputValidationError("3 rows failed validation")
    with pytest.raises(SystemExit) as ei:
        errors.handle_error(exc)
    assert ei.value.code == 2
    captured = capsys.readouterr()
    # human-readable text preserved
    assert "3 rows failed validation" in captured.err
    env = parse(captured.err)
    assert env is not None
    assert env.error_class == "InputValidationError"
    assert env.exit_code == 2
    assert env.message == "3 rows failed validation"


def test_handle_error_preserves_specific_dependency_subclass(capsys):
    # ContentRejectedError must NOT collapse to a coarse bucket — the operator
    # needs the real class name (the Phase 1 success criterion).
    exc = errors.ContentRejectedError(channel="blogger", reason="slug collision")
    with pytest.raises(SystemExit) as ei:
        errors.handle_error(exc)
    assert ei.value.code == 3
    env = parse(capsys.readouterr().err)
    assert env is not None
    assert env.error_class == "ContentRejectedError"
    assert env.exit_code == 3


def test_handle_error_auth_expired(capsys):
    exc = errors.AuthExpiredError(channel="blogger")
    with pytest.raises(SystemExit) as ei:
        errors.handle_error(exc)
    assert ei.value.code == 3
    env = parse(capsys.readouterr().err)
    assert env is not None
    assert env.error_class == "AuthExpiredError"
    assert env.exit_code == 3


def test_emit_error_maps_exit_code_to_class_name(capsys):
    with pytest.raises(SystemExit) as ei:
        errors.emit_error("dependency missing", exit_code=3)
    assert ei.value.code == 3
    captured = capsys.readouterr()
    assert "dependency missing" in captured.err
    env = parse(captured.err)
    assert env is not None
    assert env.error_class == "DependencyError"
    assert env.exit_code == 3
    assert env.message == "dependency missing"


def test_emit_error_unknown_exit_code_falls_back(capsys):
    with pytest.raises(SystemExit):
        errors.emit_error("weird", exit_code=42)
    env = parse(capsys.readouterr().err)
    assert env is not None
    assert env.error_class == "PipelineError"
    assert env.exit_code == 42


def test_emit_envelope_and_exit_emits_only_envelope(capsys):
    # For sites that already printed their own human text: the helper adds ONLY
    # the sentinel line (no human text of its own) and raises SystemExit.
    with pytest.raises(SystemExit) as ei:
        errors.emit_envelope_and_exit("ExternalServiceError", 4, "5 payloads failed")
    assert ei.value.code == 4
    captured = capsys.readouterr()
    # The only stderr line is the envelope — no duplicated human preamble.
    non_envelope = [
        ln for ln in captured.err.splitlines()
        if ln.strip() and not ln.strip().startswith("__BLP_ERR__")
    ]
    assert non_envelope == []
    env = parse(captured.err)
    assert env is not None
    assert env.error_class == "ExternalServiceError"
    assert env.exit_code == 4
    assert env.message == "5 payloads failed"


def test_handle_unexpected_error_carries_real_class(capsys):
    with pytest.raises(SystemExit) as ei:
        errors.handle_unexpected_error(ValueError("kaboom"))
    assert ei.value.code == 5
    captured = capsys.readouterr()
    assert "unexpected error: kaboom" in captured.err
    env = parse(captured.err)
    assert env is not None
    assert env.error_class == "ValueError"
    assert env.exit_code == 5


def test_cli_exit_zero_emits_no_envelope():
    # A success path emits no envelope (no false-positive error).
    assert parse("ok\n{\"some\": \"data\"}\n") is None
