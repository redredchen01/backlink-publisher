"""Structured logging for the backlink pipeline.

All diagnostic output goes to stderr via this module.
Never emits to stdout — stdout is reserved for structured JSONL data.

Sensitive-key redaction (Round-3 #6) — single chokepoint defense for
token leakage. Every ``extra`` dict passed to a logging method is walked
recursively before serialisation; any key whose name matches one of the
case-insensitive sensitive-key strings has its value replaced with the
constant ``"***"`` regardless of type. The match is exact-equality on the
casefolded key (not substring) so legitimate keys like ``client_id`` are
preserved while ``client_secret`` is redacted.

The walk is depth-bounded (max 8 levels) so a pathological self-referencing
extra dict can't pin the logger in a recursion loop.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

#: Case-insensitive exact-match against extra-dict keys. Any value under
#: one of these names — at any depth — is redacted to ``"***"`` before the
#: record is serialised.
#:
#: To extend: add the new key name casefolded. Use full-key names, not
#: substrings (otherwise ``client_id`` would collide with a hypothetical
#: ``id`` entry).
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "client_secret",
    "integration_token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "password",
    "secret",
    "token",  # bare 'token' — sometimes used as alias
    "authorization",  # http header pasted into extra
    "bearer",
})

#: Cap for the recursive walk so a pathological extra dict (cycle, deep
#: nesting) can't pin the logger. 8 levels covers any realistic structured
#: record without forcing the walker to track visited ids.
_MAX_REDACT_DEPTH: int = 8

_REDACTED: str = "***"


def _redact_in_place(value: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive values in ``value``.

    Handles dicts, lists, and tuples. Other types (scalars, sets, custom
    objects) pass through unchanged. Modifies dicts and lists in-place
    where possible; tuples are returned as new tuples since they're
    immutable.

    The depth cap is a defence against degenerate input — operators
    occasionally pass logger payloads built by reduce loops. Anything
    deeper than ``_MAX_REDACT_DEPTH`` is left alone (would be unusual
    for a real log record).
    """
    if depth >= _MAX_REDACT_DEPTH:
        return value
    if isinstance(value, dict):
        for key in list(value.keys()):
            if isinstance(key, str) and key.casefold() in _SENSITIVE_KEYS:
                value[key] = _REDACTED
            else:
                value[key] = _redact_in_place(value[key], depth + 1)
        return value
    if isinstance(value, list):
        for i in range(len(value)):
            value[i] = _redact_in_place(value[i], depth + 1)
        return value
    if isinstance(value, tuple):
        return tuple(_redact_in_place(item, depth + 1) for item in value)
    return value


class PipelineLogger:
    """Structured logger that writes to stderr with consistent format."""

    def __init__(self, name: str = "backlink-publisher", level: str = "INFO") -> None:
        self.name = name
        self.level = level
        self._levels = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}

    def _should_log(self, level: str) -> bool:
        return self._levels.get(level, 1) >= self._levels.get(self.level, 1)

    def _emit(self, level: str, message: str, extra: dict[str, Any] | None = None) -> None:
        if not self._should_log(level):
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "logger": self.name,
            "msg": message,
        }
        if extra:
            record.update(extra)
        _redact_in_place(record)
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)

    def debug(self, msg: str, **extra: Any) -> None:
        self._emit("DEBUG", msg, extra or None)

    def info(self, msg: str, **extra: Any) -> None:
        self._emit("INFO", msg, extra or None)

    def warn(self, msg: str, **extra: Any) -> None:
        self._emit("WARN", msg, extra or None)

    def error(self, msg: str, **extra: Any) -> None:
        self._emit("ERROR", msg, extra or None)

    def recon(self, msg: str, **extra: Any) -> None:
        """Always-emit reconciliation event — bypasses the level gate.

        Used by the Silent-Drop Tripwire: end-of-run input→output delta
        summary that the operator must see regardless of --log-level.
        Operator grep target: ``"level": "RECON"``.

        Sensitive-key redaction applies here too — recon events bypass
        the level gate but not the sensitive-key sanitisation.
        """
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": "RECON",
            "logger": self.name,
            "msg": msg,
        }
        if extra:
            record.update(extra)
        _redact_in_place(record)
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)


# Module-level singleton instances
plan_logger = PipelineLogger("plan-backlinks")
validate_logger = PipelineLogger("validate-backlinks")
publish_logger = PipelineLogger("publish-backlinks")
opencli_logger = PipelineLogger("opencli-runner")


def set_log_level(level: str) -> None:
    """Set log level for all pipeline loggers."""
    for logger in (plan_logger, validate_logger, publish_logger, opencli_logger):
        logger.level = level


def get_logger(name: str) -> PipelineLogger:
    """Get a named logger instance."""
    return PipelineLogger(name)