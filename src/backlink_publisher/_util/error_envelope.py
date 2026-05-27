"""Machine-readable error envelope shared by the CLI and the WebUI bridge.

Phase 1 of the thin-WebUI refactor (plan 2026-05-27-004). The fatal-error
chokepoints in :mod:`backlink_publisher._util.errors` emit one sentinel-prefixed
JSON line to stderr *in addition to* the existing human-readable text. The WebUI
bridge (:mod:`webui_app.helpers.cli_runner`) parses that line into a typed
``PipeResult.error`` instead of slicing ``stderr[:200]`` — so the operator sees
the real error class and full message, not a banner-truncated preview.

Dependency-free by design (stdlib only). ``errors.py`` is imported very early in
package init and ``retry.ErrorClass`` lives under ``publishing.adapters`` (which
imports ``errors``); keeping this module import-free of both lets the early
``errors`` chokepoints emit envelopes without a circular import, and lets the
long-lived Flask process import the exact same parse vocabulary. ``error_class``
is carried as a plain string (the exception's class name, e.g. ``"AuthExpiredError"``)
so the operator-facing type survives the boundary — classification happens at the
chokepoint, not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Distinctive, line-anchored marker. The payload is JSON (message is JSON-encoded,
# so embedded newlines collapse to \n and the envelope stays a single line).
SENTINEL = "__BLP_ERR__"


@dataclass(frozen=True)
class ErrorEnvelope:
    """The typed error contract crossing the CLI→WebUI boundary."""

    error_class: str
    exit_code: int
    message: str

    def serialize(self) -> str:
        """Return the single sentinel-prefixed stderr line (no trailing newline)."""
        payload = json.dumps(
            {
                "error_class": self.error_class,
                "exit_code": self.exit_code,
                "message": self.message,
            },
            ensure_ascii=False,
        )
        return f"{SENTINEL} {payload}"


def parse(stderr: str) -> ErrorEnvelope | None:
    """Extract the last well-formed envelope line from ``stderr``, or ``None``.

    Scans line by line so the envelope survives the ``config_echo`` banner and any
    RECON / diagnostic lines around it. Returns the *last* valid envelope — if a
    CLI emitted several (nested handlers), the outermost/most-recent fatal wins.
    Malformed sentinel lines are skipped, never raised on.
    """
    found: ErrorEnvelope | None = None
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped.startswith(SENTINEL):
            continue
        payload = stripped[len(SENTINEL) :].strip()
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            found = ErrorEnvelope(
                error_class=str(data["error_class"]),
                exit_code=int(data["exit_code"]),
                message=str(data["message"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return found
